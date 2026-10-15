"""Bugs found in the 2026-10-15 review: a line break in a TXT value, inactive
nameservers dropped by the zone form, `manage.py sync_zones` not publishing,
TTL 0 rendered as the zone default, and the password-reset pages."""
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.dns_manager.forms import ZoneForm
from apps.dns_manager.models import NameServer, Record, Zone
from apps.dns_manager.validators import validate_record_value
from apps.dns_manager.zone_engine.generator import build_zone


@pytest.fixture
def no_bind():
    with patch('apps.dns_manager.tasks.atomic_write'), \
         patch('apps.dns_manager.tasks.reload_zone'):
        yield


class TestLineBreaksRejected:
    @pytest.mark.parametrize('value', ['v=DKIM1; k=rsa;\np=MIIB', 'a\r\nb', 'a\tb', 'a\x00b'])
    def test_txt_with_control_characters_rejected(self, value):
        with pytest.raises(ValidationError, match='one line'):
            validate_record_value('TXT', value)

    def test_trailing_newline_is_fine(self):
        validate_record_value('TXT', 'v=spf1 -all\n')

    def test_api_rejects_it(self, client, staff_user, zone):
        client.force_login(staff_user)
        r = client.post('/api/v1/records/', {'zone': zone.pk, 'name': '@', 'record_type': 'TXT',
                                             'value': 'one\ntwo'}, content_type='application/json')
        assert r.status_code == 400 and 'value' in r.json()


class TestZoneFormKeepsInactiveNameservers:
    def _data(self, *ns):
        return {'name': 'example.com', 'zone_type': 'forward', 'ip_version': 4,
                'nameservers': [n.pk for n in ns], 'refresh': 3600, 'retry': 900,
                'expire': 604800, 'minimum_ttl': 86400, 'default_ttl': 3600}

    def test_edit_keeps_assigned_inactive_nameserver(self, zone, nameserver):
        off = NameServer.objects.create(name='ns2.example.com', address='192.0.2.2', is_active=False)
        zone.nameservers.add(off)
        form = ZoneForm(self._data(nameserver, off), instance=zone)
        assert form.is_valid(), form.errors
        form.save()
        assert set(zone.nameservers.all()) == {nameserver, off}

    def test_inactive_nameserver_not_offered_to_other_zones(self, zone, nameserver):
        NameServer.objects.create(name='ns2.example.com', address='192.0.2.2', is_active=False)
        assert list(ZoneForm().fields['nameservers'].queryset) == [nameserver]
        assert list(ZoneForm(instance=zone).fields['nameservers'].queryset) == [nameserver]


class TestSyncZonesCommand:
    def test_publishes_for_agents(self, zone, no_bind):
        Zone.objects.filter(pk=zone.pk).update(is_dirty=True)
        call_command('sync_zones')
        zone.refresh_from_db()
        assert not zone.is_dirty
        assert zone.published_serial == zone.serial
        assert 'example.com' in zone.published_content

    def test_failure_keeps_zone_dirty(self, zone):
        Zone.objects.filter(pk=zone.pk).update(is_dirty=True)
        with patch('apps.dns_manager.tasks.atomic_write', side_effect=ValueError('bad')):
            call_command('sync_zones')
        zone.refresh_from_db()
        assert zone.is_dirty and zone.published_serial is None


@pytest.mark.django_db
def test_ttl_zero_is_written(zone):
    Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.1', ttl=0)
    Record.objects.create(zone=zone, name='web', record_type='A', value='192.0.2.2')
    content, _ = build_zone(zone)
    assert 'www  0  IN  A  192.0.2.1' in content
    assert 'web  IN  A  192.0.2.2' in content


@pytest.mark.django_db
@pytest.mark.parametrize('url', ['/accounts/password_reset/', '/accounts/password_change/',
                                 '/accounts/reset/done/'])
def test_password_reset_pages_gone(client, url):
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_logout_still_works(client, staff_user):
    client.force_login(staff_user)
    r = client.post('/accounts/logout/')
    assert r.status_code == 302 and '_auth_user_id' not in client.session
