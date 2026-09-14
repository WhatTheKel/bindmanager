"""
Tests for the pull-agent API endpoints (apps/api/v1/agent_views.py) that the
standalone agents/bindmanager_agent.py script polls from each nameserver.
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.dns_manager.models import NameServer, Zone, Record


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def other_nameserver(db):
    return NameServer.objects.create(name='ns2.example.com', address='192.0.2.2')


def _auth(client, ns):
    client.credentials(HTTP_AUTHORIZATION=f'ApiKey {ns.api_key}')


class TestNameServerApiKey:
    def test_key_generated_on_create(self, nameserver):
        assert nameserver.api_key
        assert len(nameserver.api_key) == 64

    def test_keys_are_unique_per_nameserver(self, nameserver, other_nameserver):
        assert nameserver.api_key != other_nameserver.api_key

    def test_regenerate_changes_key(self, nameserver):
        old_key = nameserver.api_key
        nameserver.regenerate_api_key()
        assert nameserver.api_key != old_key


class TestAgentZoneList:
    def test_requires_authentication(self, api_client, zone):
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.status_code == 401

    def test_rejects_invalid_key(self, api_client, zone):
        api_client.credentials(HTTP_AUTHORIZATION='ApiKey not-a-real-key')
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.status_code == 401

    def test_returns_only_assigned_clean_zones(self, api_client, nameserver, zone):
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.status_code == 200
        names = [z['name'] for z in resp.json()]
        assert names == [zone.name]

    def test_excludes_dirty_zones(self, api_client, nameserver, zone):
        zone.is_dirty = True
        zone.save(update_fields=['is_dirty'])
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.json() == []

    def test_excludes_zones_assigned_to_other_nameservers(self, api_client, other_nameserver, zone):
        # `zone` fixture is only linked to `nameserver`, not `other_nameserver`
        _auth(api_client, other_nameserver)
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.json() == []


class TestAgentZoneDetail:
    def test_returns_rendered_content(self, api_client, nameserver, zone):
        Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.10')
        # Record.save() marks the zone dirty; simulate the Celery sync
        # pipeline having already validated it before an agent may fetch it.
        zone.is_dirty = False
        zone.save(update_fields=['is_dirty'])
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-detail', args=[zone.name]))
        assert resp.status_code == 200
        body = resp.json()
        assert body['name'] == zone.name
        assert body['serial'] == zone.serial
        assert 'www' in body['content']
        assert '192.0.2.10' in body['content']

    def test_does_not_bump_serial_on_repeated_fetch(self, api_client, nameserver, zone):
        _auth(api_client, nameserver)
        first = api_client.get(reverse('agent-zone-detail', args=[zone.name])).json()
        second = api_client.get(reverse('agent-zone-detail', args=[zone.name])).json()
        assert first['serial'] == second['serial'] == zone.serial

    def test_404_for_unassigned_zone(self, api_client, other_nameserver, zone):
        _auth(api_client, other_nameserver)
        resp = api_client.get(reverse('agent-zone-detail', args=[zone.name]))
        assert resp.status_code == 404

    def test_404_for_dirty_zone(self, api_client, nameserver, zone):
        zone.is_dirty = True
        zone.save(update_fields=['is_dirty'])
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-detail', args=[zone.name]))
        assert resp.status_code == 404
