"""
Login protection: the client IP can't be faked with X-Forwarded-For, the
lockout blocks before the password is checked (so even the right password
is refused while locked), and the API token endpoint shares the lockout.
"""
import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.accounts import lockout
from apps.accounts.client_ip import client_ip
from apps.dns_manager.models import AuditLog

pytestmark = pytest.mark.django_db

# What nginx produces: it appends the address it saw ($remote_addr), and
# Django sees nginx's own container address as REMOTE_ADDR.
NGINX = '172.18.0.5'


def _req(xff=None, remote=NGINX):
    meta = {'REMOTE_ADDR': remote}
    if xff is not None:
        meta['HTTP_X_FORWARDED_FOR'] = xff
    return RequestFactory().get('/', **meta)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


class TestClientIp:
    def test_uses_the_address_nginx_appended(self):
        assert client_ip(_req('198.51.100.7')) == '198.51.100.7'

    def test_fake_first_entry_ignored(self):
        # client sent "X-Forwarded-For: 1.2.3.4"; nginx appended the real one
        assert client_ip(_req('1.2.3.4, 198.51.100.7')) == '198.51.100.7'

    def test_two_trusted_proxies(self, settings):
        settings.TRUSTED_PROXY_COUNT = 2
        assert client_ip(_req('1.2.3.4, 203.0.113.9, 10.0.0.2')) == '203.0.113.9'

    def test_no_header_falls_back_to_peer(self):
        assert client_ip(_req()) == NGINX

    def test_zero_proxies_ignores_header(self, settings):
        settings.TRUSTED_PROXY_COUNT = 0
        assert client_ip(_req('1.2.3.4', remote='198.51.100.7')) == '198.51.100.7'


@pytest.fixture
def user():
    return User.objects.create_user('alice', password='right-password')


def _login(client, password, xff):
    return client.post('/accounts/login/', {'username': 'alice', 'password': password},
                       HTTP_X_FORWARDED_FOR=xff, REMOTE_ADDR=NGINX)


class TestLoginPage:
    def test_locked_after_ten_failures(self, client, user):
        codes = [_login(client, 'wrong', '198.51.100.7').status_code for _ in range(10)]
        assert codes[:9] == [200] * 9 and codes[9] == 429

    def test_correct_password_refused_while_locked(self, client, user):
        for _ in range(10):
            _login(client, 'wrong', '198.51.100.7')
        resp = _login(client, 'right-password', '198.51.100.7')
        assert resp.status_code == 429
        assert '_auth_user_id' not in client.session

    def test_fake_xff_does_not_reset_the_count(self, client, user):
        # attacker sends a different fake first entry each time
        for i in range(10):
            _login(client, 'wrong', f'10.9.9.{i}, 198.51.100.7')
        assert _login(client, 'right-password', '10.9.9.99, 198.51.100.7').status_code == 429

    def test_other_clients_unaffected(self, client, user):
        for _ in range(10):
            _login(client, 'wrong', '198.51.100.7')
        assert _login(client, 'right-password', '198.51.100.8').status_code == 302

    def test_audit_log_records_real_ip_not_fake(self, client, user):
        _login(client, 'wrong', '6.6.6.6, 198.51.100.7')
        detail = AuditLog.objects.get(action='login_fail').detail
        assert '198.51.100.7' in detail and '6.6.6.6' not in detail


class TestApiToken:
    def _token(self, password, xff='198.51.100.7'):
        return APIClient().post('/api/token/', {'username': 'alice', 'password': password},
                                format='json', HTTP_X_FORWARDED_FOR=xff, REMOTE_ADDR=NGINX)

    def test_failures_count_towards_the_same_lockout(self, user, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK,
                                   'DEFAULT_THROTTLE_CLASSES': []}   # isolate the lockout
        for _ in range(10):
            assert self._token('wrong').status_code == 401
        assert self._token('right-password').status_code == 429

    def test_shared_with_login_page(self, client, user):
        for _ in range(10):
            _login(client, 'wrong', '198.51.100.7')
        assert self._token('right-password').status_code == 429

    def test_works_when_not_locked(self, user):
        assert self._token('right-password').status_code == 200


def test_drf_throttle_ident_ignores_fake_xff():
    from rest_framework.request import Request
    from rest_framework.throttling import AnonRateThrottle
    t = AnonRateThrottle()
    a = t.get_ident(Request(_req('1.1.1.1, 198.51.100.7')))
    b = t.get_ident(Request(_req('2.2.2.2, 198.51.100.7')))
    assert a == b == '198.51.100.7'


def test_lockout_fails_open_if_cache_down(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError('redis down')
    monkeypatch.setattr(lockout.cache, 'get', boom)
    monkeypatch.setattr(lockout.cache, 'add', boom)
    req = _req('198.51.100.7')
    lockout.record_failure(req)          # no exception
    assert lockout.is_locked(req) is False
