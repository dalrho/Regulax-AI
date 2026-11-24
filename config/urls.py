from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.login.urls')),
    path('register/', include('apps.register.urls')),
    path('dashboard/', include('apps.dashboard.urls')),
    path('sops/', include('apps.documents.urls')),
    path('workspace/', include('apps.workspaces.urls')),
]

