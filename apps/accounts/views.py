from django.contrib.auth.views import LoginView
from django.http import HttpResponse, JsonResponse
from rest_framework_simplejwt.views import TokenObtainPairView

from . import lockout


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
