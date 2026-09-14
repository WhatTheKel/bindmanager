from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions, permissions

from apps.dns_manager.models import NameServer

_KEYWORD = 'ApiKey'


class NameServerKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticates a pull agent running on a physical BIND nameserver.

    Expects `Authorization: ApiKey <token>`. On success, request.user is an
    AnonymousUser (no Django account involved) and request.auth is the
    authenticated NameServer instance — views scope querysets off it so an
    agent can only ever see the zones assigned to *its own* NameServer row.
    """

    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith(f'{_KEYWORD} '):
            return None

        key = header[len(_KEYWORD) + 1:].strip()
        if not key:
            raise exceptions.AuthenticationFailed('Empty API key')

        try:
            nameserver = NameServer.objects.get(api_key=key, is_active=True)
        except NameServer.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid API key')

        return (AnonymousUser(), nameserver)

    def authenticate_header(self, request):
        return _KEYWORD


class IsNameServerAgent(permissions.BasePermission):
    """Grants access only to requests authenticated via NameServerKeyAuthentication."""

    def has_permission(self, request, view):
        return isinstance(request.auth, NameServer)
