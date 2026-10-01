"""Per-IP lockout after repeated failed password logins.

Shared by the login page and the API's /api/token/ endpoint. Failures are
counted from Django's user_login_failed signal (both call authenticate()),
and both check is_locked() *before* checking the password, so a locked-out
address can't keep guessing — not even the right password gets through.

The counter lives in the shared cache (Redis), so all gunicorn workers see
the same count. If the cache is unreachable the lockout fails open (logins
keep working) and the problem is logged.
"""
import logging

from django.core.cache import cache

from .client_ip import client_ip

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
WINDOW_SECONDS = 300      # counted from the latest failure

LOCKED_MESSAGE = 'Too many failed login attempts. Please wait 5 minutes before trying again.'


def _key(request) -> str:
    return f'login_fail:{client_ip(request)}'


def is_locked(request) -> bool:
    try:
        return (cache.get(_key(request)) or 0) >= MAX_ATTEMPTS
    except Exception:
        log.exception('login lockout check failed (cache unavailable?)')
        return False


def record_failure(request) -> None:
    if request is None:
        return
    key = _key(request)
    try:
        if not cache.add(key, 1, WINDOW_SECONDS):
            cache.incr(key)
            cache.touch(key, WINDOW_SECONDS)
    except Exception:
        log.exception('could not record failed login (cache unavailable?)')
