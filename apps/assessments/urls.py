from django.urls import path

from . import views

urlpatterns = [
    path('', views.assessment_history_view, name='assessment_history'),
    path('new/', views.assessment_create_view, name='assessment_create'),
    path('<int:assessment_id>/', views.assessment_detail_view, name='assessment_detail'),
    path('<int:assessment_id>/edit/', views.assessment_edit_view, name='assessment_edit'),
]
