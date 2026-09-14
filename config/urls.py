from django.contrib import admin
from django.urls import path, include
from apps.accounts.views import SmartLoginView

admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth/', include('social_django.urls', namespace='social')),
    # Override login before the generic auth.urls include so our view wins
    path('accounts/login/', SmartLoginView.as_view(), name='login'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('api/', include('apps.api.urls')),
    path('', include('apps.dns_manager.urls')),
]
