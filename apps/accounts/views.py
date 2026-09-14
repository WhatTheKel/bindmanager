from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponse

_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 300  # 5-minute lockout window


class SmartLoginView(LoginView):
    """
    After login, send staff to /dashboard/ when next= is blank or just '/'.
    Deep-links (e.g. session-expired on a manage page) are still honoured.
    Failed attempts are rate-limited per IP: 10 attempts per 5 minutes.
    """
    def get_success_url(self):
        next_url = (self.request.POST.get('next') or
                    self.request.GET.get('next') or '').strip()
        if self.request.user.is_staff and next_url in ('', '/'):
            return '/dashboard/'
        return super().get_success_url()

    def form_invalid(self, form):
        ip = self._client_ip()
        key = f'login_fail:{ip}'
        attempts = cache.get(key, 0) + 1
        cache.set(key, attempts, _WINDOW_SECONDS)
        if attempts >= _MAX_ATTEMPTS:
            return HttpResponse(
                'Too many failed login attempts. Please wait 5 minutes before trying again.',
                content_type='text/plain',
                status=429,
            )
        return super().form_invalid(form)

    def _client_ip(self):
        xff = self.request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return self.request.META.get('REMOTE_ADDR', 'unknown')
