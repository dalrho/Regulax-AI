from django.urls import path

from . import views

urlpatterns = [
    path('', views.sop_library_view, name='sop_library'),
    path('<int:sop_id>/', views.sop_detail_view, name='sop_detail'),
    path('<int:sop_id>/archive/', views.sop_archive_view, name='sop_archive'),
]
