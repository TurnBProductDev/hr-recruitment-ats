from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ('job_code', 'title', 'location', 'status', 'is_archived', 'closing_date', 'created_on')
    list_filter = ('status', 'is_archived', 'location')
    search_fields = ('job_code', 'title', 'location')
    readonly_fields = ('job_code', 'created_on')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
