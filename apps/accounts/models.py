import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

TOKEN_PREFIX = 'bmt_'


def _hash(raw: str) -> str:
    # Tokens are 256-bit random values, so a plain SHA-256 is enough; the
    # raw token is never stored and can't be recovered from the hash.
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiToken(models.Model):
    """A personal API token: acts as its user, for scripts and tools.

    Lets SSO users (who have no BindManager password, so can't use
    /api/token/) call the API. Sent as `Authorization: Token bmt_…`.
    Only a hash is stored; the token itself is shown once, at creation.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='api_tokens')
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    # First characters of the token, shown so the user can tell tokens apart
    key_prefix = models.CharField(max_length=12, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.CharField(max_length=45, blank=True, default='')

    class Meta:
        db_table = 'api_tokens'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.key_prefix}…) for {self.user}'

    @classmethod
    def create_for(cls, user, name: str, expires_at=None):
        """Create a token and return (token, raw_key). raw_key is not stored."""
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = cls.objects.create(user=user, name=name, key_hash=_hash(raw),
                                   key_prefix=raw[:len(TOKEN_PREFIX) + 6],
                                   expires_at=expires_at)
        return token, raw

    @classmethod
    def find(cls, raw: str):
        """The token for raw_key, or None."""
        if not raw or not raw.startswith(TOKEN_PREFIX):
            return None
        return cls.objects.select_related('user').filter(key_hash=_hash(raw)).first()

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()
