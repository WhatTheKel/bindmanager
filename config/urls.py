from django.contrib import admin
from django.urls import path, include
from apps.accounts import views as account_views
from apps.accounts.views import SmartLoginView

admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth/', include('social_django.urls', namespace='social')),
    # Override login before the generic auth.urls include so our view wins
    path('accounts/login/', SmartLoginView.as_view(), name='login'),
    path('account/api-tokens/', account_views.api_tokens, name='api_tokens'),
    path('account/api-tokens/<int:pk>/revoke/', account_views.revoke_api_token,
         name='revoke_api_token'),
    path('account/users/<int:user_id>/revoke-tokens/', account_views.revoke_user_tokens,
         name='revoke_user_tokens'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('api/', include('apps.api.urls')),
    path('', include('apps.dns_manager.urls')),
]
