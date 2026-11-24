from django.contrib import admin

from .models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'site', 'product_category', 'jurisdiction', 'created_at')
    search_fields = ('company_name', 'site')

