import logging

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from candidates.permissions import HR_ADMIN, INTERVIEWER

from . import graph_client
from .models import (
    INTERVIEW_DURATION, Interview, InterviewRequest, InterviewSlot, held_slot_conflict_message,
    interviewer_conflict_message, open_interview_message, open_interview_request_message,
)

logger = logging.getLogger(__name__)


class BootstrapFormMixin:
    def _add_bootstrap_classes(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.MultiWidget):
                # A class set here lands on the *parent* widget's own attrs,
                # which MultiWidget.get_context() then merges into every
                # sub-widget's attrs, last-value-wins - silently overwriting
                # each sub-widget's own more specific class (e.g.
                # InterviewSlotProposalForm's flatpickr-date/flatpickr-time)
                # with a bare "form-control". Each sub-widget already carries
                # its own class via its own attrs, so nothing to add here.
                continue
            elif isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'form-check-input')
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault('class', 'form-select')
            else:
                widget.attrs.setdefault('class', 'form-control')


def _simplified_round_type_choices(current_value):
    """Round Type as just Round 1 / Round 2 - the model still supports the
    older Technical/Managerial/Final/HR Round sub-types (see
    Interview.RoundType), but offering all 5 side by side as if they were
    independent rounds read as "a lot of rounds" with no clear meaning.
    `current_value` (the bound instance's existing round_type, or None for a
    new one) stays selectable too if it's one of those older sub-types, so
    editing/resaving an existing interview/request that already has one
    doesn't fail with a "not a valid choice" error - only new allocations
    ever see just the two."""
    choices = [
        (Interview.RoundType.ROUND1, Interview.RoundType.ROUND1.label),
        (Interview.RoundType.ROUND2, Interview.RoundType.ROUND2.label),
    ]
    if current_value and current_value not in (Interview.RoundType.ROUND1, Interview.RoundType.ROUND2):
        choices.append((current_value, dict(Interview.RoundType.choices).get(current_value, current_value)))
    return choices


class InterviewForm(BootstrapFormMixin, forms.ModelForm):
    # Not a model field - just where the invite email goes. Kept separate
    # from Candidate.email so editing it here (e.g. a typo, or a personal
    # address the candidate asked to use instead) never overwrites the
    # candidate's stored contact email.
    candidate_email = forms.EmailField(label='Candidate Email', required=False)

    class Meta:
        model = Interview
        fields = ['round_type', 'interviewer', 'scheduled_date', 'mode', 'meeting_link']
        widgets = {
            'scheduled_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, candidate=None, resolving_request=None, **kwargs):
        super().__init__(*args, **kwargs)
        # On a reschedule the candidate comes from the interview being edited.
        self.candidate = candidate or (self.instance.candidate if self.instance.candidate_id else None)
        # Set when this form is reached from a Round 1/2 stage card's
        # in-progress InterviewRequest (e.g. "Reschedule" -> Manual Slot
        # Allocate) - that request's own open-ness must not count as a
        # clash against itself; see clean() below. It gets resolved
        # (status=SCHEDULED, interview=...) by whoever saves this form -
        # see InterviewScheduleView.form_valid.
        self.resolving_request = resolving_request
        self.fields['round_type'].choices = _simplified_round_type_choices(
            self.instance.round_type if self.instance.pk else None)
        if self.candidate and not self.is_bound:
            self.fields['candidate_email'].initial = self.candidate.email
            if resolving_request and not self.instance.pk:
                self.fields['round_type'].initial = resolving_request.round_type
                self.fields['interviewer'].initial = resolving_request.interviewer_id
                self.fields['mode'].initial = resolving_request.mode
        # Interviewer group + HR Admin are assignable - HR Admin can take
        # interviews themselves, not just delegate them - ordered by name.
        # distinct() since groups__name__in joins per matching group, and an
        # account in both groups would otherwise list twice.
        User = get_user_model()
        self.fields['interviewer'].queryset = (
            User.objects.filter(groups__name__in=(INTERVIEWER, HR_ADMIN))
            .order_by('first_name', 'last_name').distinct())
        self.fields['interviewer'].label_from_instance = (
            lambda u: u.get_full_name() or u.username)
        self.fields['interviewer'].empty_label = 'Unassigned'
        self._add_bootstrap_classes()

    def clean(self):
        # One open interview per candidate. Rescheduling the open interview is
        # fine (it is excluded), scheduling a second one alongside it is not.
        cleaned = super().clean()
        if self.candidate:
            clash = Interview.open_for(self.candidate)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            clash = clash.first()
            if clash:
                raise forms.ValidationError(open_interview_message(clash))
            request_clash = InterviewRequest.open_for(self.candidate)
            if self.resolving_request:
                request_clash = request_clash.exclude(pk=self.resolving_request.pk)
            request_clash = request_clash.first()
            if request_clash:
                raise forms.ValidationError(open_interview_request_message(request_clash))

        interviewer = cleaned.get('interviewer')
        scheduled_date = cleaned.get('scheduled_date')
        if interviewer and scheduled_date:
            clash = Interview.conflicts_for(
                interviewer, scheduled_date, exclude_pk=self.instance.pk).first()
            if clash:
                raise forms.ValidationError(interviewer_conflict_message(clash))

            # Manual/direct scheduling and rescheduling must respect a slot
            # currently proposed to (or already picked by) a candidate for a
            # *different* request - see InterviewSlot.held_conflicts_for.
            held = InterviewSlot.held_conflicts_for(interviewer, scheduled_date).first()
            if held:
                raise forms.ValidationError(held_slot_conflict_message(held))

            # Phase 2: also check the interviewer's real Outlook calendar, not
            # just other interviews scheduled through this app. Best-effort -
            # a Graph outage must never block scheduling, only skip this extra
            # check (the Phase 1 same-app check above still applies either way).
            if graph_client.is_configured() and interviewer.email:
                try:
                    busy = graph_client.is_interviewer_busy(
                        interviewer.email, scheduled_date, scheduled_date + INTERVIEW_DURATION)
                except graph_client.GraphError as exc:
                    logger.warning('Skipping Outlook calendar check for %s: %s', interviewer.email, exc)
                else:
                    if busy:
                        name = interviewer.get_full_name() or interviewer.get_username()
                        raise forms.ValidationError(
                            f'{name}\'s Outlook calendar shows them busy at that time. '
                            f'Pick a different time or interviewer.')
        return cleaned


class InterviewResultForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Interview
        fields = ['status', 'result', 'score', 'feedback']
        widgets = {'feedback': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A verdict must actually be picked - Pending (the not-yet-decided
        # default) isn't offered here, and Feedback backs it up, so both are
        # required to save. See InterviewResultView.form_valid, which branches
        # on Pass/Fail/Hold being one of these three.
        self.fields['result'].choices = [
            ('', '— Select —'),
            *(c for c in Interview.Result.choices if c[0] != Interview.Result.PENDING),
        ]
        self.fields['result'].required = True
        if self.instance.result == Interview.Result.PENDING:
            self.initial['result'] = ''
        self.fields['feedback'].required = True
        self._add_bootstrap_classes()


class InterviewAllocationForm(BootstrapFormMixin, forms.ModelForm):
    """Step 1 of the new flow: HR only picks who interviews the candidate -
    no date yet, that comes from the interviewer's own proposed slots (see
    InterviewSlotProposalForm). Also reused, bound to an existing
    InterviewRequest via `instance=`, for the Reschedule popup on the Round
    1/2 stage card - letting HR change the interviewer/mode instead of being
    stuck re-asking the same one - see InterviewRequestRescheduleView."""
    class Meta:
        model = InterviewRequest
        fields = ['round_type', 'interviewer', 'mode']

    def __init__(self, *args, candidate=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.candidate = candidate
        self.fields['round_type'].choices = _simplified_round_type_choices(
            self.instance.round_type if self.instance.pk else None)
        # Interviewer group + HR Admin - see InterviewForm's __init__ above.
        User = get_user_model()
        self.fields['interviewer'].queryset = (
            User.objects.filter(groups__name__in=(INTERVIEWER, HR_ADMIN))
            .order_by('first_name', 'last_name').distinct())
        self.fields['interviewer'].label_from_instance = (
            lambda u: u.get_full_name() or u.username)
        self._add_bootstrap_classes()

    def clean(self):
        cleaned = super().clean()
        if self.candidate:
            clash = Interview.open_for(self.candidate).first()
            if clash:
                raise forms.ValidationError(open_interview_message(clash))
            # Editing an existing (already open) request - e.g. Reschedule -
            # must not treat that same row as a clash against itself.
            request_clash = InterviewRequest.open_for(self.candidate).exclude(pk=self.instance.pk).first()
            if request_clash:
                raise forms.ValidationError(open_interview_request_message(request_clash))
        return cleaned


class SplitDateTimeSlotWidget(forms.SplitDateTimeWidget):
    """SplitDateTimeWidget's own default template (multiwidget.html) just
    concatenates both sub-inputs with no separation - this one lays them out
    as labelled Date/Time columns instead (see the template).

    Rendering `{{ form.slot_1 }}` directly (not `.subwidgets.0`/`.1`) is
    required here: MultiWidget never overrides Widget.subwidgets() (that's a
    different mechanism from the `widget.subwidgets` context list its own
    template loops over), so BoundField.subwidgets - the `.0`/`.1` template
    lookup - yields the whole multiwidget as a single item instead of one
    per sub-widget. portal_propose_slots.html used to rely on that and
    silently got both inputs concatenated into "Date" and nothing in "Time",
    with neither carrying its flatpickr-date/flatpickr-time class."""
    template_name = 'interviews/widgets/split_datetime_slot.html'


class InterviewSlotProposalForm(BootstrapFormMixin, forms.Form):
    """Step 2: the interviewer proposes 2-3 one-hour slots for an
    InterviewRequest. Not a ModelForm - it fans out into several InterviewSlot
    rows rather than editing one model instance."""
    # A separate date input + time input (rather than one combined
    # datetime-local field) - easier to pick a day on, and the time input
    # can be typed directly instead of fought with a combined stepper.
    # type="text" (not the native date/time inputs) - portal_propose_slots.html
    # attaches Flatpickr to the flatpickr-date/flatpickr-time class, so both
    # get the same nice calendar/time-wheel UI on desktop as on mobile,
    # instead of the browser's own (inconsistent, clunkier-on-desktop) native
    # date/time pickers. Flatpickr still submits the same Y-m-d/H:i text this
    # form already expects, so nothing else here needs to change.
    slot_1 = forms.SplitDateTimeField(label='Slot 1', widget=SplitDateTimeSlotWidget(
        date_attrs={'type': 'text', 'class': 'form-control flatpickr-date'},
        time_attrs={'type': 'text', 'class': 'form-control flatpickr-time'}))
    slot_2 = forms.SplitDateTimeField(label='Slot 2', required=False, widget=SplitDateTimeSlotWidget(
        date_attrs={'type': 'text', 'class': 'form-control flatpickr-date'},
        time_attrs={'type': 'text', 'class': 'form-control flatpickr-time'}))
    slot_3 = forms.SplitDateTimeField(label='Slot 3', required=False, widget=SplitDateTimeSlotWidget(
        date_attrs={'type': 'text', 'class': 'form-control flatpickr-date'},
        time_attrs={'type': 'text', 'class': 'form-control flatpickr-time'}))

    def __init__(self, *args, interviewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.interviewer = interviewer
        self._add_bootstrap_classes()

    def clean(self):
        cleaned = super().clean()
        slots = [cleaned[f] for f in ('slot_1', 'slot_2', 'slot_3') if cleaned.get(f)]
        if len(slots) < 2:
            raise forms.ValidationError('Propose at least 2 slots so HR has a choice.')
        if len(set(slots)) != len(slots):
            raise forms.ValidationError('Each proposed slot must be a different time.')
        now = timezone.now()
        for i, slot in enumerate(slots):
            if slot <= now:
                raise forms.ValidationError('Proposed slots must be in the future.')
            for other in slots[i + 1:]:
                if abs((slot - other)) < INTERVIEW_DURATION:
                    raise forms.ValidationError('Proposed slots must not overlap each other.')
        if self.interviewer:
            for slot in slots:
                clash = Interview.conflicts_for(self.interviewer, slot).first()
                if clash:
                    raise forms.ValidationError(interviewer_conflict_message(clash))
                held = InterviewSlot.held_conflicts_for(self.interviewer, slot).first()
                if held:
                    raise forms.ValidationError(held_slot_conflict_message(held))
                if graph_client.is_configured() and self.interviewer.email:
                    try:
                        busy = graph_client.is_interviewer_busy(
                            self.interviewer.email, slot, slot + INTERVIEW_DURATION)
                    except graph_client.GraphError as exc:
                        logger.warning('Skipping Outlook calendar check for %s: %s', self.interviewer.email, exc)
                    else:
                        if busy:
                            raise forms.ValidationError(
                                f'Your Outlook calendar shows you busy at {slot:%d %b %Y %H:%M}. '
                                f'Pick a different time.')
        cleaned['slots'] = sorted(slots)
        return cleaned


class CandidateSlotPickForm(BootstrapFormMixin, forms.Form):
    """The public, token-secured page: the candidate picks one of the
    interviewer's proposed slots. No email/meeting-link fields here - those
    are only relevant once HR actually approves the pick (see
    InterviewRequestApproveView)."""
    slot = forms.ModelChoiceField(queryset=None, widget=forms.RadioSelect, empty_label=None)

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_obj = request
        self.fields['slot'].queryset = request.slots.all()
        self.fields['slot'].label_from_instance = (
            lambda s: f'{timezone.localtime(s.start_datetime).strftime("%A, %d %b %Y, %I:%M %p")} IST')
        self._add_bootstrap_classes()
