from django.contrib import messages
from django.db.models import Case, IntegerField, Value, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from candidates.permissions import HR_ADMIN, RECRUITER, GroupRequiredMixin
from candidates.views import GENERAL_APPLICATION

from . import jd_extraction
from .forms import JobForm
from .models import Job

# ---------------------------------------------------------------------------
# Public careers portal
# ---------------------------------------------------------------------------


class VacancyListView(ListView):
    model = Job
    template_name = 'jobs/vacancy_list.html'
    context_object_name = 'jobs'

    def get_queryset(self):
        return Job.objects.filter(status=Job.Status.OPEN, is_archived=False)


class VacancyDetailView(DetailView):
    model = Job
    context_object_name = 'job'
    slug_field = 'job_code'
    slug_url_kwarg = 'job_code'

    def get_queryset(self):
        return Job.objects.all()

    def get_template_names(self):
        if self.request.GET.get('partial') == '1' or self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['jobs/_vacancy_detail_content.html']
        return ['jobs/vacancy_detail.html']


# ---------------------------------------------------------------------------
# HR admin: Vacancy Management
# ---------------------------------------------------------------------------


class JobManageListView(GroupRequiredMixin, ListView):
    model = Job
    template_name = 'jobs/job_manage_list.html'
    context_object_name = 'jobs'

    def get_queryset(self):
        # General Application isn't a real vacancy (candidates land there
        # only when intake couldn't match them to an open one - see
        # candidates.views.GENERAL_APPLICATION) - still listed here so it
        # can be opened/edited, but always sorted last rather than wherever
        # its creation date would otherwise place it.
        is_general = Case(
            When(title__iexact=GENERAL_APPLICATION, then=Value(1)),
            default=Value(0), output_field=IntegerField())
        return Job.objects.annotate(_is_general=is_general).order_by('_is_general', '-created_on')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        u = self.request.user
        ctx['is_hr_admin'] = u.is_superuser or u.groups.filter(name=HR_ADMIN).exists()
        ctx['total_vacancies'] = Job.objects.exclude(title__iexact=GENERAL_APPLICATION).count()
        return ctx


class JobCreateView(GroupRequiredMixin, CreateView):
    model = Job
    form_class = JobForm
    template_name = 'jobs/job_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER)
    success_url = reverse_lazy('job_manage_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, f'Vacancy "{form.instance.title}" created.')
        return super().form_valid(form)


class JobUpdateView(GroupRequiredMixin, UpdateView):
    model = Job
    form_class = JobForm
    template_name = 'jobs/job_form.html'
    allowed_groups = (HR_ADMIN, RECRUITER)
    success_url = reverse_lazy('job_manage_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['breadcrumb_current'] = f'Edit {self.object.title}'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f'Vacancy "{form.instance.title}" updated.')
        return super().form_valid(form)


class JobExtractJDView(GroupRequiredMixin, View):
    """Called from the Vacancy form's JD file input the moment a file is
    chosen (before Save) - reads it and returns Description/Requirements text
    to pre-fill those fields, so HR edits an already-populated draft instead
    of typing a JD from scratch. Never persists anything itself; the browser
    fills the two textareas and the actual save still happens through the
    normal form submit, same as everywhere else edits are reviewed before
    being committed."""
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request):
        jd_file = request.FILES.get('jd_file')
        if not jd_file:
            return JsonResponse({'status': 'error', 'message': 'No file was uploaded.'}, status=400)

        try:
            fields = jd_extraction.extract_fields(
                jd_file.read(), title_hint=(request.POST.get('title') or '').strip())
        except jd_extraction.JDExtractionError as exc:
            return JsonResponse({'status': 'error', 'message': str(exc)})

        return JsonResponse({'status': 'ok', **fields})


class JobCloseView(GroupRequiredMixin, View):
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.status = Job.Status.CLOSED
        job.save(update_fields=['status'])
        messages.success(request, f'Vacancy "{job.title}" closed.')
        return redirect('job_manage_list')


class JobArchiveView(GroupRequiredMixin, View):
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.is_archived = True
        job.save(update_fields=['is_archived'])
        messages.success(request, f'Vacancy "{job.title}" archived.')
        return redirect('job_manage_list')


class JobReopenView(GroupRequiredMixin, View):
    allowed_groups = (HR_ADMIN, RECRUITER)

    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.status = Job.Status.OPEN
        job.is_archived = False
        job.save(update_fields=['status', 'is_archived'])
        messages.success(request, f'Vacancy "{job.title}" reopened.')
        return redirect('job_manage_list')
