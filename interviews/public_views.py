"""Public, unauthenticated pages reached only from an emailed link - not
gated by GroupRequiredMixin/login at all, since candidates have no account in
this system. InterviewRequest.candidate_token in the URL is the only
credential (mirrors Django's own password-reset token pattern), regenerated
every time slots are (re-)proposed so an old emailed link can never resurface
against a later round of slots.
"""
import logging

from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from candidates import logic_app_mail

from . import slot_emails
from .forms import CandidateSlotPickForm
from .models import Interview, InterviewRequest, candidate_round_label

logger = logging.getLogger(__name__)


class CandidateSlotPickView(View):
    """GET only ever renders a page - deliberately read-only, since corporate
    email security scanners (Microsoft Defender Safe Links and similar) often
    pre-fetch links to scan them before the recipient ever opens the email;
    if picking a slot happened on GET, a scanner would silently consume it
    before the candidate saw it. Only POST records a pick."""
    template_name = 'interviews/candidate_slot_pick.html'

    def _lookup(self, token):
        return (InterviewRequest.objects.filter(candidate_token=token)
                .select_related('candidate', 'interviewer', 'candidate_selected_slot').first())

    def _state(self, request_obj):
        if not request_obj:
            return 'invalid'
        if request_obj.status == InterviewRequest.Status.AWAITING_HR_APPROVAL:
            return 'already_selected'
        if request_obj.status != InterviewRequest.Status.AWAITING_SELECTION:
            # SCHEDULED/CANCELLED/AWAITING_SLOTS - HR already approved or
            # asked for a reschedule (which rotates the token, so a request
            # genuinely back at AWAITING_SLOTS can't still own this token;
            # this only covers a stale page reload from before that happened).
            return 'invalid'
        if request_obj.candidate_link_expired:
            return 'expired'
        return 'open'

    def get(self, request, token):
        request_obj = self._lookup(token)
        return self._render(request, request_obj)

    def post(self, request, token):
        request_obj = self._lookup(token)
        state = self._state(request_obj)
        if state != 'open':
            return self._render(request, request_obj, state=state)

        form = CandidateSlotPickForm(request.POST, request=request_obj)
        if not form.is_valid():
            return self._render(request, request_obj, form=form, state=state)

        slot = form.cleaned_data['slot']
        # Best-effort re-check: the interviewer could have been booked
        # elsewhere (a direct/manual schedule) since these slots were
        # proposed - if so, ask the candidate to pick a different one rather
        # than silently double-booking.
        if Interview.conflicts_for(request_obj.interviewer, slot.start_datetime).exists():
            form.add_error(None, 'That time is no longer available - please pick a different one.')
            return self._render(request, request_obj, form=form, state=state)

        request_obj.candidate_selected_slot = slot
        request_obj.candidate_selected_at = timezone.now()
        request_obj.status = InterviewRequest.Status.AWAITING_HR_APPROVAL
        request_obj.save(update_fields=['candidate_selected_slot', 'candidate_selected_at', 'status'])

        if request_obj.created_by and request_obj.created_by.email:
            profile_url = request.build_absolute_uri(
                reverse('candidate_timeline', args=[request_obj.candidate_id]))
            try:
                slot_emails.notify_hr_candidate_selected(request_obj, profile_url)
            except logic_app_mail.EmailSendError as exc:
                logger.warning(
                    'Could not email HR that a slot was selected for request %s: %s', request_obj.pk, exc)

        # Redirect (not render) after a successful pick, so a page refresh
        # replays the now-"already_selected" GET instead of resubmitting.
        return redirect('candidate_slot_pick', token=token)

    def _render(self, request, request_obj, form=None, state=None):
        state = state or self._state(request_obj)
        ctx = {
            'interview_request': request_obj, 'state': state,
            # Never show the internal round_type/"Round 1"/"Round 2" naming
            # to the candidate - see candidate_round_label.
            'round_label': candidate_round_label(request_obj.round_type) if request_obj else '',
        }
        if state == 'open':
            ctx['form'] = form or CandidateSlotPickForm(request=request_obj)
        return render(request, self.template_name, ctx, status=404 if state == 'invalid' else 200)
