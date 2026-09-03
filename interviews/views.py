import logging

from django.contrib import messages
from django.db.models import BooleanField, Case, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from candidates import services
from candidates.models import Candidate, Note
from candidates.permissions import ANY_STAFF, HR_ADMIN, INTERVIEWER, RECRUITER, GroupRequiredMixin

from . import invites
from .forms import InterviewForm, InterviewResultForm
from .models import Interview, InterviewReschedule, open_interview_message

logger = logging.getLogger(__name__)


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


class InterviewSchedulerListView(GroupRequiredMixin, ListView):
    """Scheduled interviews still awaiting a result. Once a result is marked the
    interview is completed and drops off this list. Overdue-but-unmarked
    interviews stay listed (shown as 'Result Pending')."""
    model = Interview
    template_name = 'interviews/scheduler.html'
    context_object_name = 'interviews'
    allowed_groups = ANY_STAFF

    def get_queryset(self):
        qs = (Interview.objects.filter(
                status__in=Interview.OPEN_STATUSES, result=Interview.Result.PENDING)
              .select_related('candidate', 'candidate__job', 'interviewer')
              .annotate(is_upcoming=Case(
                  When(scheduled_date__gt=timezone.now(), then=Value(True)),
                  default=Value(False), output_field=BooleanField())))
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(candidate__full_name__icontains=q)
        return qs.order_by('scheduled_date')


class InterviewScheduleView(GroupRequiredMixin, CreateView):
    """Full page for direct navigation/no-JS; the candidate profile page's
    popup instead loads this same view's form as an HTML fragment (flagged by
    the X-Requested-With header) and, on a valid save, gets back the
    invite-email draft fragment instead of a redirect."""
    model = Interview
    form_class = InterviewForm
    template_name = 'interviews/interview_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def dispatch(self, request, *args, **kwargs):
        self.candidate = get_object_or_404(Candidate, pk=kwargs['candidate_id'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        # Turn the request away before the form is even shown, so HR sees why
        # straight away instead of filling it in and being rejected on save.
        # (The same rule is enforced on POST by InterviewForm.clean.)
        open_interview = Interview.open_for(self.candidate).first()
        if open_interview:
            if _is_ajax(request):
                # The modal's loader always expects an HTML fragment - keep
                # this consistent with every other AJAX response here.
                return HttpResponse(format_html(
                    '<div class="alert alert-danger mb-0">{}</div>',
                    open_interview_message(open_interview)))
            messages.error(request, open_interview_message(open_interview))
            return redirect('candidate_timeline', pk=self.candidate.pk)
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if _is_ajax(self.request):
            return ['interviews/_schedule_form.html']
        return [self.template_name]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['candidate'] = self.candidate
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['candidate'] = self.candidate
        return ctx

    def form_valid(self, form):
        form.instance.candidate = self.candidate
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        if _is_ajax(self.request):
            return _invite_draft_response(self.request, self.object, form.cleaned_data.get('candidate_email'))
        messages.success(self.request, f'Interview scheduled for {self.candidate.full_name}.')
        return response

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if _is_ajax(self.request):
            response.status_code = 400
        return response

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.candidate.pk])


def _invite_draft_response(request, interview, to_email):
    """The step-2 fragment: an editable invite email draft for the interview
    just scheduled/rescheduled, rendered with real values (candidate name,
    role, date, sender)."""
    draft = {
        'to_email': (to_email or interview.candidate.email or '').strip(),
        'cc_list': invites.default_cc_list(interview),
        'subject': invites.default_subject(interview),
        'body': invites.default_body(interview, request.user),
    }
    html = render_to_string('interviews/_invite_draft.html', {
        'interview': interview, 'draft': draft,
    }, request=request)
    return HttpResponse(html)


def _same_minute(a, b):
    """The date picker only offers minutes, so anything finer that a row happens
    to carry (imported rows can have seconds) is not a move the user made."""
    return a.replace(second=0, microsecond=0) == b.replace(second=0, microsecond=0)


class InterviewRescheduleView(GroupRequiredMixin, UpdateView):
    """See InterviewScheduleView's docstring - same full-page/AJAX-fragment
    dual behaviour."""
    model = Interview
    form_class = InterviewForm
    template_name = 'interviews/interview_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER)

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Snapshot before the form writes over it - the row is rewritten in
        # place, so this is the only chance to see what it used to say.
        self.before = {'scheduled_date': obj.scheduled_date, 'interviewer_id': obj.interviewer_id}
        return obj

    def get_template_names(self):
        if _is_ajax(self.request):
            return ['interviews/_schedule_form.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['candidate'] = self.object.candidate
        return ctx

    def form_valid(self, form):
        moved = not _same_minute(form.instance.scheduled_date, self.before['scheduled_date'])
        reassigned = form.instance.interviewer_id != self.before['interviewer_id']
        if moved:
            form.instance.status = Interview.Status.RESCHEDULED
        response = super().form_valid(form)
        if moved or reassigned:
            InterviewReschedule.objects.create(
                interview=self.object,
                previous_date=self.before['scheduled_date'], new_date=self.object.scheduled_date,
                previous_interviewer_id=self.before['interviewer_id'],
                new_interviewer_id=self.object.interviewer_id,
                changed_by=self.request.user)
        if _is_ajax(self.request):
            return _invite_draft_response(self.request, self.object, form.cleaned_data.get('candidate_email'))
        messages.success(self.request, 'Interview rescheduled.' if (moved or reassigned) else 'Interview updated.')
        return response

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if _is_ajax(self.request):
            response.status_code = 400
        return response

    def get_success_url(self):
        return reverse('candidate_timeline', args=[self.object.candidate_id])


class InterviewResultView(GroupRequiredMixin, UpdateView):
    model = Interview
    form_class = InterviewResultForm
    template_name = 'interviews/interview_result_form.html'
    allowed_groups = (HR_ADMIN, INTERVIEWER)

    # On Pass, advance the candidate one stage down the pipeline rather than
    # hiring outright: Round 1 -> Interview (Round 2) -> Final Selection, where HR
    # makes the actual hire decision. Anything not listed falls back to Hired.
    PASS_NEXT = {
        Candidate.Status.SHORTLISTED: Candidate.Status.ROUND1,
        Candidate.Status.ROUND1: Candidate.Status.INTERVIEW,
        Candidate.Status.INTERVIEW: Candidate.Status.FINAL_SELECTION,
        Candidate.Status.FINAL_SELECTION: Candidate.Status.HIRED,
    }

    def form_valid(self, form):
        # Marking a result completes the interview and drives the candidate's
        # pipeline: Pass -> next stage, Fail -> Rejected.
        form.instance.status = Interview.Status.COMPLETED
        response = super().form_valid(form)
        interview = self.object
        candidate = interview.candidate
        performed_by = self.request.user.get_full_name() or self.request.user.get_username()
        round_label = interview.get_round_type_display()
        if interview.result == Interview.Result.PASS_:
            next_status = self.PASS_NEXT.get(candidate.status, Candidate.Status.HIRED)
            services.change_status(candidate, next_status, user=self.request.user,
                                   remarks=f'{round_label} interview passed.', performed_by=performed_by)
            messages.success(self.request, f'{candidate.full_name} passed — moved to "{candidate.status_label}".')
        elif interview.result == Interview.Result.FAIL:
            services.change_status(candidate, Candidate.Status.REJECTED, user=self.request.user,
                                   remarks=f'{round_label} interview failed.', performed_by=performed_by)
            messages.success(self.request, f'{candidate.full_name} failed and was moved to Rejected.')
        else:
            messages.success(self.request, 'Interview result recorded.')
        return response

    def get_success_url(self):
        return reverse('interview_scheduler')


class InterviewSendInviteView(GroupRequiredMixin, View):
    """Sends the interview invite - subject/body/recipient come from what HR
    reviewed and possibly edited in the popup; CC is always derived
    server-side (fixed HR address + the selected interviewer) so it can't be
    tampered with or accidentally dropped. Includes a .ics attachment so it
    lands in Outlook as a real meeting, not just a plain email.

    Console backend in dev (see settings.EMAIL_BACKEND) - invites print to
    the log instead of sending until real SMTP credentials are configured."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        interview = get_object_or_404(Interview, pk=pk)
        candidate = interview.candidate
        is_ajax = _is_ajax(request)
        # The scheduler list page's "Send Invite" button posts none of these -
        # just a bare CSRF token - so every one of them needs a default, not
        # just subject.
        to_email = (request.POST.get('to_email') or candidate.email or '').strip()
        subject = request.POST.get('subject', '').strip() or invites.default_subject(interview)
        body = request.POST.get('body', '').strip() or invites.default_body(interview, request.user)

        error = None
        if not to_email:
            error = 'No candidate email to send the invite to.'
        else:
            cc_list = invites.default_cc_list(interview)
            try:
                invites.send_invite(interview, to_email=to_email, cc_emails=cc_list,
                                    subject=subject, body=body, sender=request.user)
            except Exception as exc:  # noqa: BLE001 - surfaced to the HR user, not swallowed
                logger.exception('Failed to send interview invite for interview %s', interview.pk)
                error = f'Could not send the invite: {exc}'
            else:
                Note.objects.create(
                    candidate=candidate, author=request.user,
                    text=f'Interview invite emailed to {to_email}'
                         f'{" (cc: " + ", ".join(cc_list) + ")" if cc_list else ""}.')

        if error:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': error}, status=502)
            messages.error(request, error)
            return redirect('candidate_timeline', pk=candidate.pk)

        if is_ajax:
            return JsonResponse({'ok': True})
        messages.success(request, f'Invite sent to {to_email}.')
        return redirect('candidate_timeline', pk=candidate.pk)
