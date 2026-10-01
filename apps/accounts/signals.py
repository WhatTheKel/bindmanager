from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import pre_save
from django.dispatch import receiver
from apps.dns_manager.models import AuditLog

from . import lockout
from .client_ip import client_ip


@receiver(pre_save, sender=get_user_model())
def superuser_is_staff(sender, instance, **kwargs):
    """A superuser is always staff too.

    The app gates the dashboard, management panel and API writes on is_staff
    alone, so a superuser without it (easy to produce by unticking Staff in
    the user form or /admin/) could only view zones. Enforced here so every
    path — forms, /admin/, the shell — gets it.
    """
    if instance.is_superuser:
        instance.is_staff = True


# The real client address — not the spoofable first X-Forwarded-For entry
_get_ip = client_ip


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
def on_login_failed(sender, credentials, request=None, **kwargs):
    lockout.record_failure(request)
    ip = _get_ip(request)
    username = credentials.get('username', '?') if credentials else '?'
    AuditLog.objects.create(
        user=None,
        action=AuditLog.Action.LOGIN_FAIL,
        entity_type='user',
        entity_id=None,
        detail=f'Failed login for "{username}" from {ip}',
    )
