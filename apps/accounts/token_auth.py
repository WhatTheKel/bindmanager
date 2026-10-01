from django.utils import timezone
from rest_framework import authentication, exceptions

from .client_ip import client_ip
from .models import ApiToken

_KEYWORD = 'Token'


class ApiTokenAuthentication(authentication.BaseAuthentication):
    """`Authorization: Token bmt_…` — a personal API token (ApiToken).

    The request acts as the token's user, with that user's role. Expired
    tokens and tokens of deactivated users are refused.
    """

    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith(f'{_KEYWORD} '):
            return None
        raw = header[len(_KEYWORD) + 1:].strip()
        token = ApiToken.find(raw)
        if token is None:
            raise exceptions.AuthenticationFailed('Invalid API token.')
        if token.is_expired:
            raise exceptions.AuthenticationFailed('API token has expired.')
        if not token.user.is_active:
            raise exceptions.AuthenticationFailed('User account is disabled.')

        # Record use, at most once a minute per token (avoid a write per call)
        now = timezone.now()
        if not token.last_used_at or (now - token.last_used_at).total_seconds() > 60:
            ApiToken.objects.filter(pk=token.pk).update(
                last_used_at=now, last_used_ip=client_ip(request)[:45])
        return (token.user, token)

    def authenticate_header(self, request):
        return _KEYWORD
