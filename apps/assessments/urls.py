from django.urls import path

from . import views

urlpatterns = [
    path('', views.assessment_history_view, name='assessment_history'),
    path('new/', views.assessment_create_view, name='assessment_create'),
    path('<int:assessment_id>/', views.assessment_detail_view, name='assessment_detail'),
    path('<int:assessment_id>/edit/', views.assessment_edit_view, name='assessment_edit'),
    path('<int:assessment_id>/run/', views.assessment_run_view, name='assessment_run'),
    path('<int:assessment_id>/retry/', views.assessment_retry_view, name='assessment_retry'),
    path('<int:assessment_id>/progress/', views.assessment_progress_view, name='assessment_progress'),
    path('<int:assessment_id>/requirements/<int:requirement_id>/exclude/', views.requirement_exclude_view, name='requirement_exclude'),
    path('<int:assessment_id>/findings/<int:finding_id>/', views.finding_detail_view, name='finding_detail'),
    path('<int:assessment_id>/findings/<int:finding_id>/review/', views.finding_review_view, name='finding_review'),
]
