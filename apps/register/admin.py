from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'company_name', 'user', 'compliance_acknowledged', 'created_at')
    search_fields = ('full_name', 'company_name', 'user__username', 'user__email')

