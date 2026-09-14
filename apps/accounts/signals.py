from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from apps.dns_manager.models import AuditLog


def _get_ip(request):
    """Return the real client IP, respecting X-Forwarded-For from nginx."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR') if request else None
    if xff:
        return xff.split(',')[0].strip()
    return (request.META.get('REMOTE_ADDR', 'unknown') if request else 'unknown')


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    ip = _get_ip(request)
    AuditLog.objects.create(
        user=user,
        action=AuditLog.Action.LOGIN,
        entity_type='user',
        entity_id=user.pk,
        detail=f'Login: {user.username} from {ip}',
    )


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is None:
        return
    ip = _get_ip(request)
    AuditLog.objects.create(
        user=user,
        action=AuditLog.Action.LOGOUT,
        entity_type='user',
        entity_id=user.pk,
        detail=f'Logout: {user.username} from {ip}',
    )


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request, **kwargs):
    ip = _get_ip(request)
    username = credentials.get('username', '?') if credentials else '?'
    AuditLog.objects.create(
        user=None,
        action=AuditLog.Action.LOGIN_FAIL,
        entity_type='user',
        entity_id=None,
        detail=f'Failed login for "{username}" from {ip}',
    )
