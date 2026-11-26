import logging

from django import forms
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from candidates.models import Candidate
from candidates.permissions import HIRING_MANAGER, INTERVIEWER

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


def stage_round(candidate):
    """The round a candidate at Round 1 / Round 2 (or on hold from one) is
    due for - ROUND1/ROUND2 - else None (any other stage: no constraint)."""
    if candidate is None:
        return None
    S = Candidate.Status
    status = candidate.hold_from_status if candidate.status == S.SCREENING_HOLD else candidate.status
    return {S.ROUND1: Interview.RoundType.ROUND1, S.INTERVIEW: Interview.RoundType.ROUND2}.get(status)


NOT_AT_A_ROUND = '{name} isn\'t at Round 1 or Round 2, so there is no interview round to allocate.'


def _fix_round_field(form, candidate, resolving_request=None):
    """Round is never picked by hand - it used to be a dropdown that always
    started on Round 1, so a Round 2 interview got saved as Round 1, stayed
    'open' and blocked every later Round 2 allocation (Anna Theresa Antony,
    2026-11-25). It is shown read-only and decided here:
      - editing an existing interview/allocation: keeps its own round
      - Manual Slot Allocate resolving an allocation: that allocation's round
      - otherwise: the candidate's current stage (stage_round)
    A disabled field ignores whatever is posted and saves this value."""
    field = form.fields['round_type']
    if form.instance.pk:
        value = form.instance.round_type
    elif resolving_request is not None:
        value = resolving_request.round_type
    else:
        value = stage_round(candidate)
    form.fixed_round = value
    form.round_label = dict(Interview.RoundType.choices).get(value, value) if value else '-'
    field.disabled = True
    field.required = False
    if value:
        field.initial = value
        field.choices = [(value, dict(Interview.RoundType.choices).get(value, value))]
        # Set on the instance too: Django's construct_instance skips a field
        # absent from POST when the model has a default - and this field's
        # default is ROUND1, the very mistake this prevents.
        form.instance.round_type = value
    else:
        field.choices = [('', '-')]


def _clean_fixed_round(form, cleaned):
    if not form.fixed_round:
        raise forms.ValidationError(NOT_AT_A_ROUND.format(
            name=form.candidate.full_name if form.candidate else 'This candidate'))
    cleaned['round_type'] = form.fixed_round


def _setup_interviewer_field(field, current_id=None):
    """Who can be picked as interviewer: active accounts in the Interviewer
    or Hiring Manager group - plus `current_id`, so editing an interview
    whose interviewer has since been deactivated doesn't fail validation.
    Each is labelled "Name (email)": two accounts can share a name (an old
    and a new login for the same person), and the email is what decides who
    actually gets the notification."""
    active = models.Q(is_active=True)
    if current_id:
        active |= models.Q(pk=current_id)
    field.queryset = (get_user_model().objects
                      .filter(active, groups__name__in=(INTERVIEWER, HIRING_MANAGER))
                      .order_by('first_name', 'last_name').distinct())
    field.label_from_instance = lambda u: (
        f'{u.get_full_name() or u.username} ({u.email})' if u.email else (u.get_full_name() or u.username))


def _setup_co_interviewer_field(field, current_id=None):
    """Interviewer 2 (optional): same people as Interviewer."""
    _setup_interviewer_field(field, current_id)
    field.required = False
    field.label = 'Interviewer 2 (optional)'
    field.empty_label = 'None'


def _clean_co_interviewer(cleaned):
    interviewer, co = cleaned.get('interviewer'), cleaned.get('co_interviewer')
    if co and interviewer and co.pk == interviewer.pk:
        raise forms.ValidationError({'co_interviewer': 'Interviewer 2 must be a different person.'})


def availability_error(person, when, *, exclude_pk=None, you=False):
    """Why `person` can't take an interview at `when`, or None: another open
    interview (as interviewer or Interviewer 2), a slot held by a pending
    candidate pick, or - when Graph is configured - their Outlook calendar."""
    clash = Interview.conflicts_for(person, when, exclude_pk=exclude_pk).first()
    if clash:
        return interviewer_conflict_message(clash, person)
    held = InterviewSlot.held_conflicts_for(person, when).first()
    if held:
        return held_slot_conflict_message(held, person)
    # Phase 2: also check the real Outlook calendar. Best-effort - a Graph
    # outage must never block scheduling, only skip this extra check.
    if graph_client.is_configured() and person.email:
        try:
            busy = graph_client.is_interviewer_busy(person.email, when, when + INTERVIEW_DURATION)
        except graph_client.GraphError as exc:
            logger.warning('Skipping Outlook calendar check for %s: %s', person.email, exc)
        else:
            if busy:
                if you:
                    return f'Your Outlook calendar shows you busy at {when:%d %b %Y %H:%M}. Pick a different time.'
                name = person.get_full_name() or person.get_username()
                return (f'{name}\'s Outlook calendar shows them busy at that time. '
                        f'Pick a different time or interviewer.')
    return None


class InterviewForm(BootstrapFormMixin, forms.ModelForm):
    # Not a model field - just where the invite email goes. Kept separate
    # from Candidate.email so editing it here (e.g. a typo, or a personal
    # address the candidate asked to use instead) never overwrites the
    # candidate's stored contact email.
    candidate_email = forms.EmailField(label='Candidate Email', required=False)

    class Meta:
        model = Interview
        fields = ['round_type', 'interviewer', 'co_interviewer', 'scheduled_date', 'mode', 'meeting_link']
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
        _fix_round_field(self, self.candidate, resolving_request)
        if self.candidate and not self.is_bound:
            self.fields['candidate_email'].initial = self.candidate.email
            if resolving_request and not self.instance.pk:
                self.fields['interviewer'].initial = resolving_request.interviewer_id
                self.fields['co_interviewer'].initial = resolving_request.co_interviewer_id
                self.fields['mode'].initial = resolving_request.mode
        # Interviewer group + Hiring Manager are assignable - Hiring Manager
        # can take interviews themselves, not just delegate them - ordered by
        # name. HR Admin is deliberately not included - Admin is the
        # oversight role (sees every interviewer's assignments in the portal,
        # see interviews/portal_views.py's _is_admin), not an assignee.
        # distinct() since groups__name__in joins per matching group, and an
        # account in both groups would otherwise list twice.
        _setup_interviewer_field(self.fields['interviewer'], self.instance.interviewer_id)
        self.fields['interviewer'].empty_label = 'Unassigned'
        co_id = self.instance.co_interviewer_id or (resolving_request.co_interviewer_id if resolving_request else None)
        _setup_co_interviewer_field(self.fields['co_interviewer'], co_id)
        self._add_bootstrap_classes()

    def clean(self):
        # One open interview per candidate. Rescheduling the open interview is
        # fine (it is excluded), scheduling a second one alongside it is not.
        cleaned = super().clean()
        _clean_fixed_round(self, cleaned)
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

        _clean_co_interviewer(cleaned)
        scheduled_date = cleaned.get('scheduled_date')
        # Both interviewers must be free: no other open interview (in either
        # role), no slot held for a *different* request's candidate pick (see
        # InterviewSlot.held_conflicts_for), and not busy in Outlook.
        for person in (cleaned.get('interviewer'), cleaned.get('co_interviewer')):
            if person and scheduled_date:
                error = availability_error(person, scheduled_date, exclude_pk=self.instance.pk)
                if error:
                    raise forms.ValidationError(error)
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
        # Interviewers score out of 10 (shown as "x / 10" everywhere else).
        self.fields['score'].widget.attrs.update(min='0', max='10', step='0.5', placeholder='0 - 10')
        self._add_bootstrap_classes()

    def clean_score(self):
        score = self.cleaned_data.get('score')
        if score is not None and not 0 <= score <= 10:
            raise forms.ValidationError('Enter a score from 0 to 10.')
        return score


class InterviewAllocationForm(BootstrapFormMixin, forms.ModelForm):
    """Step 1 of the new flow: HR only picks who interviews the candidate -
    no date yet, that comes from the interviewer's own proposed slots (see
    InterviewSlotProposalForm). Also reused, bound to an existing
    InterviewRequest via `instance=`, for the Reschedule popup on the Round
    1/2 stage card - letting HR change the interviewer/mode instead of being
    stuck re-asking the same one - see InterviewRequestRescheduleView."""
    class Meta:
        model = InterviewRequest
        fields = ['round_type', 'interviewer', 'co_interviewer', 'mode']

    def __init__(self, *args, candidate=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.candidate = candidate
        _fix_round_field(self, candidate)
        # Interviewer group + Hiring Manager - see InterviewForm's __init__ above.
        _setup_interviewer_field(self.fields['interviewer'], self.instance.interviewer_id)
        _setup_co_interviewer_field(self.fields['co_interviewer'], self.instance.co_interviewer_id)
        self._add_bootstrap_classes()

    def clean(self):
        cleaned = super().clean()
        _clean_co_interviewer(cleaned)
        _clean_fixed_round(self, cleaned)
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
    """Step 2: the interviewer proposes 2-3 thirty-minute slots for an
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

    def __init__(self, *args, interviewer=None, co_interviewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.interviewer = interviewer
        # Interviewer 2 isn't proposing, but every slot must suit them too.
        self.co_interviewer = co_interviewer
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
        for slot in slots:
            if self.interviewer:
                error = availability_error(self.interviewer, slot, you=True)
                if error:
                    raise forms.ValidationError(error)
            if self.co_interviewer:
                error = availability_error(self.co_interviewer, slot)
                if error:
                    raise forms.ValidationError(
                        f'Interviewer 2 isn\'t free at {timezone.localtime(slot):%d %b %Y %H:%M}: {error}')
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
