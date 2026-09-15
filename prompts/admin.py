from django import forms
from django.contrib import admin
from django.db import models

from .models import PromptTemplate


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ('label', 'key', 'updated_at', 'updated_by')
    fields = ('label', 'key', 'description', 'text', 'updated_at', 'updated_by')
    readonly_fields = ('label', 'key', 'description', 'updated_at', 'updated_by')
    formfield_overrides = {
        models.TextField: {'widget': forms.Textarea(attrs={'rows': 20, 'cols': 100})},
    }

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
