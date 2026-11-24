from django.urls import path

from . import views

urlpatterns = [
    path('settings/', views.settings_view, name='workspace_settings'),
    path('members/', views.members_view, name='workspace_members'),
    path('members/<int:membership_id>/role/', views.member_role_view, name='workspace_member_role'),
    path('members/<int:membership_id>/remove/', views.member_remove_view, name='workspace_member_remove'),
]
