"""
Personal API tokens: created on the "API tokens" page (also by SSO users,
who have no password), shown once, stored only as a hash, used as
`Authorization: Token bmt_…` with the owner's role, expirable, revocable.
"""
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ApiToken
from apps.accounts.views import expiry_choices
from apps.dns_manager.models import AuditLog, Zone

pytestmark = pytest.mark.django_db
NGINX = '172.18.0.5'


def _api(raw, xff='198.51.100.7'):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Token {raw}', HTTP_X_FORWARDED_FOR=xff, REMOTE_ADDR=NGINX)
    return c


@pytest.fixture
def sso_user():
    u = User.objects.create_user('sso.person', is_staff=True)
    u.set_unusable_password()          # how SSO-created accounts look
    u.save()
    return u


class TestModel:
    def test_only_hash_stored(self, staff_user):
        token, raw = ApiToken.create_for(staff_user, 'ci')
        assert raw.startswith('bmt_') and len(raw) > 40
        row = ApiToken.objects.filter(pk=token.pk).values().get()
        assert raw not in str(row.values())
        assert ApiToken.find(raw) == token
        assert ApiToken.find(raw + 'x') is None
        assert ApiToken.find('not-a-token') is None


class TestApiAuth:
    def test_sso_user_can_use_api_with_token(self, sso_user, zone):
        _, raw = ApiToken.create_for(sso_user, 'script')
        resp = _api(raw).get('/api/v1/zones/')
        assert resp.status_code == 200
        assert resp.json()['results'][0]['name'] == zone.name

    def test_token_has_owner_role_read_only_user(self, regular_user, zone):
        _, raw = ApiToken.create_for(regular_user, 'read')
        c = _api(raw)
        assert c.get('/api/v1/zones/').status_code == 200
        assert c.post('/api/v1/zones/', {'name': 'x.example'}, format='json').status_code == 403

    def test_staff_token_can_write_and_audit_names_the_user(self, staff_user):
        _, raw = ApiToken.create_for(staff_user, 'write')
        resp = _api(raw).post('/api/v1/zones/', {'name': 'new.example'}, format='json')
        assert resp.status_code == 201, resp.content
        assert Zone.objects.filter(name='new.example').exists()
        assert AuditLog.objects.filter(user=staff_user, detail__contains='new.example').exists()

    @pytest.mark.parametrize('header', ['Token bmt_wrong', 'Token ', 'Token nonsense'])
    def test_bad_tokens_rejected(self, header):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=header)
        assert c.get('/api/v1/zones/').status_code == 401

    def test_expired_token_rejected(self, staff_user):
        _, raw = ApiToken.create_for(staff_user, 'old', timezone.now() - timedelta(seconds=1))
        resp = _api(raw).get('/api/v1/zones/')
        assert resp.status_code == 401 and 'expired' in resp.json()['detail']

    def test_deactivated_user_token_rejected(self, staff_user):
        _, raw = ApiToken.create_for(staff_user, 'x')
        staff_user.is_active = False
        staff_user.save()
        assert _api(raw).get('/api/v1/zones/').status_code == 401

    def test_last_used_records_real_ip(self, staff_user):
        token, raw = ApiToken.create_for(staff_user, 'x')
        _api(raw, xff='6.6.6.6, 198.51.100.7').get('/api/v1/zones/')
        token.refresh_from_db()
        assert token.last_used_at is not None and token.last_used_ip == '198.51.100.7'


class TestTokensPage:
    def test_requires_login(self, client):
        resp = client.get(reverse('api_tokens'))
        assert resp.status_code == 302 and '/accounts/login/' in resp['Location']

    def test_create_shows_token_once(self, client, sso_user):
        client.force_login(sso_user)
        resp = client.post(reverse('api_tokens'), {'name': 'ansible', 'days': 30})
        assert resp.status_code == 302
        token = ApiToken.objects.get(user=sso_user)
        assert token.expires_at - timezone.now() > timedelta(days=29)

        first = client.get(reverse('api_tokens')).content.decode()
        raw = first.split('data-copy="')[1].split('"')[0]
        assert raw.startswith('bmt_') and ApiToken.find(raw) == token
        second = client.get(reverse('api_tokens')).content.decode()
        assert raw not in second and token.key_prefix in second

        audit = AuditLog.objects.get(entity_type='api_token', action='create')
        assert raw not in audit.detail and token.key_prefix in audit.detail

    @pytest.mark.parametrize('data', [{'name': '', 'days': 30}, {'name': 'x', 'days': 365},
                                      {'name': 'x', 'days': 0}, {'name': 'x', 'days': 'abc'}])
    def test_invalid_input_rejected(self, client, staff_user, data):
        client.force_login(staff_user)
        client.post(reverse('api_tokens'), data)
        assert not ApiToken.objects.exists()

    def test_per_user_limit(self, client, staff_user, settings):
        settings.API_TOKENS_PER_USER = 2
        client.force_login(staff_user)
        for i in range(3):
            client.post(reverse('api_tokens'), {'name': f't{i}', 'days': 30})
        assert ApiToken.objects.filter(user=staff_user).count() == 2

    def test_revoke_own_token(self, client, staff_user):
        token, raw = ApiToken.create_for(staff_user, 'x')
        client.force_login(staff_user)
        assert client.post(reverse('revoke_api_token', args=[token.pk])).status_code == 302
        assert not ApiToken.objects.exists()
        assert _api(raw).get('/api/v1/zones/').status_code == 401

    def test_cannot_revoke_someone_elses_token(self, client, staff_user, regular_user):
        token, _ = ApiToken.create_for(regular_user, 'theirs')
        client.force_login(staff_user)
        assert client.post(reverse('revoke_api_token', args=[token.pk])).status_code == 404
        assert ApiToken.objects.filter(pk=token.pk).exists()

    def test_revoke_needs_post(self, client, staff_user):
        token, _ = ApiToken.create_for(staff_user, 'x')
        client.force_login(staff_user)
        assert client.get(reverse('revoke_api_token', args=[token.pk])).status_code == 405


class TestAdminRevokeAll:
    def test_superuser_revokes_all(self, client, staff_user):
        boss = User.objects.create_superuser('boss', 'b@example.com', 'x')
        for i in range(3):
            ApiToken.create_for(staff_user, f't{i}')
        client.force_login(boss)
        page = client.get(reverse('dns_manager:manage_user_edit', args=[staff_user.pk])).content.decode()
        assert 'API tokens (3)' in page
        client.post(reverse('revoke_user_tokens', args=[staff_user.pk]))
        assert not ApiToken.objects.filter(user=staff_user).exists()

    def test_non_superuser_forbidden(self, client, staff_user, regular_user):
        ApiToken.create_for(regular_user, 't')
        client.force_login(staff_user)
        assert client.post(reverse('revoke_user_tokens', args=[regular_user.pk])).status_code == 403
        assert ApiToken.objects.filter(user=regular_user).exists()


class TestExpiryChoices:
    def test_capped_by_max_days(self, settings):
        settings.API_TOKEN_MAX_DAYS = 90
        assert [d for d, _ in expiry_choices()] == [7, 30, 90]

    def test_odd_max_is_offered(self, settings):
        settings.API_TOKEN_MAX_DAYS = 45
        assert [d for d, _ in expiry_choices()] == [7, 30, 45]

    def test_zero_allows_no_expiry(self, settings):
        settings.API_TOKEN_MAX_DAYS = 0
        assert expiry_choices()[-1] == (0, 'No expiry')


def test_user_menu_links_to_tokens(client, regular_user):
    client.force_login(regular_user)
    html = client.get(reverse('dns_manager:zone_list')).content.decode()
    assert reverse('api_tokens') in html
