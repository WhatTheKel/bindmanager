from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.dns_manager.models import AuditLog

from . import lockout
from .models import ApiToken


def _locked_response():
    return HttpResponse(lockout.LOCKED_MESSAGE, content_type='text/plain', status=429)


class SmartLoginView(LoginView):
    """
    After login, send staff to /dashboard/ when next= is blank or just '/'.
    Deep-links (e.g. session-expired on a manage page) are still honoured.
    Failed attempts are limited per client IP: 10 per 5 minutes (lockout.py).
    """
    def post(self, request, *args, **kwargs):
        # Checked before the password: while locked out, every attempt —
        # including a correct one — gets the same 429.
        if lockout.is_locked(request):
            return _locked_response()
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        # The failure itself was counted by the user_login_failed signal
        if lockout.is_locked(self.request):
            return _locked_response()
        return super().form_invalid(form)

    def get_success_url(self):
        next_url = (self.request.POST.get('next') or
                    self.request.GET.get('next') or '').strip()
        if self.request.user.is_staff and next_url in ('', '/'):
            return '/dashboard/'
        return super().get_success_url()


class LockoutTokenObtainPairView(TokenObtainPairView):
    """POST /api/token/ — the same per-IP lockout as the login page."""
    def post(self, request, *args, **kwargs):
        if lockout.is_locked(request):
            return JsonResponse({'detail': lockout.LOCKED_MESSAGE}, status=429)
        return super().post(request, *args, **kwargs)


# ── Personal API tokens ──────────────────────────────────────────────────────

_NEW_TOKEN_SESSION_KEY = 'new_api_token'


def expiry_choices():
    """(days, label) pairs allowed by API_TOKEN_MAX_DAYS; 0 days = no expiry."""
    max_days = settings.API_TOKEN_MAX_DAYS
    days = [7, 30, 90, 365]
    if max_days > 0:
        days = sorted({d for d in days if d <= max_days} | {max_days})
    choices = [(d, f'{d} days') for d in days]
    if max_days == 0:
        choices.append((0, 'No expiry'))
    return choices


def _audit(request, action, token_or_pk, detail):
    AuditLog.objects.create(
        user=request.user, action=action, entity_type='api_token',
        entity_id=getattr(token_or_pk, 'pk', token_or_pk), detail=detail,
    )


@login_required
def api_tokens(request):
    """List, create and revoke the current user's personal API tokens."""
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()[:100]
        allowed = dict(expiry_choices())
        try:
            days = int(request.POST.get('days', ''))
        except ValueError:
            days = -1
        if not name:
            messages.error(request, 'Give the token a name, e.g. where it will be used.')
        elif days not in allowed:
            messages.error(request, 'Choose an expiry from the list.')
        elif request.user.api_tokens.count() >= settings.API_TOKENS_PER_USER:
            messages.error(request, f'You already have {settings.API_TOKENS_PER_USER} tokens. '
                                    f'Revoke one you no longer use first.')
        else:
            expires = timezone.now() + timedelta(days=days) if days else None
            token, raw = ApiToken.create_for(request.user, name, expires)
            _audit(request, AuditLog.Action.CREATE, token,
                   f'Created API token "{name}" ({token.key_prefix}…), '
                   f'{"no expiry" if not days else f"expires in {days} days"}')
            # Shown once on the next page load, then gone
            request.session[_NEW_TOKEN_SESSION_KEY] = {'name': name, 'key': raw}
            return redirect('api_tokens')
        return redirect('api_tokens')

    return render(request, 'accounts/api_tokens.html', {
        'tokens': request.user.api_tokens.all(),
        'new_token': request.session.pop(_NEW_TOKEN_SESSION_KEY, None),
        'expiry_choices': expiry_choices(),
        'default_days': 30 if 30 in dict(expiry_choices()) else expiry_choices()[0][0],
        'max_tokens': settings.API_TOKENS_PER_USER,
        'api_base': request.build_absolute_uri('/api/v1/'),
    })


@require_POST
@login_required
def revoke_api_token(request, pk):
    """Revoke one of the current user's own tokens."""
    token = get_object_or_404(ApiToken, pk=pk, user=request.user)
    _audit(request, AuditLog.Action.DELETE, token,
           f'Revoked API token "{token.name}" ({token.key_prefix}…)')
    token.delete()
    messages.success(request, f'API token "{token.name}" revoked.')
    return redirect('api_tokens')


@require_POST
@login_required
def revoke_user_tokens(request, user_id):
    """Superusers: revoke every API token of a user (e.g. when they leave)."""
    if not request.user.is_superuser:
        return HttpResponse(status=403)
    from django.contrib.auth import get_user_model
    target = get_object_or_404(get_user_model(), pk=user_id)
    count, _ = target.api_tokens.all().delete()
    _audit(request, AuditLog.Action.DELETE, target.pk,
           f'Revoked all {count} API token(s) of user {target.username}')
    messages.success(request, f'Revoked {count} API token(s) of "{target.username}".')
    return redirect('dns_manager:manage_user_edit', pk=target.pk)
