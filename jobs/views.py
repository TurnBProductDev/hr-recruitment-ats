from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from candidates.permissions import HR_ADMIN, GroupRequiredMixin

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
        return Job.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        u = self.request.user
        ctx['is_hr_admin'] = u.is_superuser or u.groups.filter(name=HR_ADMIN).exists()
        return ctx


class JobCreateView(GroupRequiredMixin, CreateView):
    model = Job
    form_class = JobForm
    template_name = 'jobs/job_form.html'
    allowed_groups = (HR_ADMIN,)
    success_url = reverse_lazy('job_manage_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, f'Vacancy "{form.instance.title}" created.')
        return super().form_valid(form)


class JobUpdateView(GroupRequiredMixin, UpdateView):
    model = Job
    form_class = JobForm
    template_name = 'jobs/job_form.html'
    allowed_groups = (HR_ADMIN,)
    success_url = reverse_lazy('job_manage_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['breadcrumb_current'] = f'Edit {self.object.title}'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f'Vacancy "{form.instance.title}" updated.')
        return super().form_valid(form)


class JobCloseView(GroupRequiredMixin, View):
    allowed_groups = (HR_ADMIN,)

    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.status = Job.Status.CLOSED
        job.save(update_fields=['status'])
        messages.success(request, f'Vacancy "{job.title}" closed.')
        return redirect('job_manage_list')


class JobArchiveView(GroupRequiredMixin, View):
    allowed_groups = (HR_ADMIN,)

    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.is_archived = True
        job.save(update_fields=['is_archived'])
        messages.success(request, f'Vacancy "{job.title}" archived.')
        return redirect('job_manage_list')


class JobReopenView(GroupRequiredMixin, View):
    allowed_groups = (HR_ADMIN,)

    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.status = Job.Status.OPEN
        job.is_archived = False
        job.save(update_fields=['status', 'is_archived'])
        messages.success(request, f'Vacancy "{job.title}" reopened.')
        return redirect('job_manage_list')
