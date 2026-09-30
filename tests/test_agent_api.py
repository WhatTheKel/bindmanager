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


def _publish(zone, content='; published\n', serial=None):
    """Simulate a successful central sync having published the zone."""
    zone.published_content = content
    zone.published_serial = serial if serial is not None else zone.serial
    zone.is_dirty = False
    zone.save(update_fields=['published_content', 'published_serial', 'is_dirty'])
    return zone


class TestAgentZoneList:
    def test_requires_authentication(self, api_client, zone):
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.status_code == 401

    def test_rejects_invalid_key(self, api_client, zone):
        api_client.credentials(HTTP_AUTHORIZATION='ApiKey not-a-real-key')
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.status_code == 401

    def test_returns_assigned_published_zones(self, api_client, nameserver, zone):
        _publish(zone)
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.status_code == 200
        assert [z['name'] for z in resp.json()] == [zone.name]

    def test_pending_zone_stays_listed_with_published_serial(self, api_client, nameserver, zone):
        # The agent deletes any zone missing from this list, so a zone with
        # edits waiting for the central sync must still be listed.
        _publish(zone, serial=2026010101)
        Record.objects.create(zone=zone, name='new', record_type='A', value='192.0.2.50')
        zone.refresh_from_db()
        assert zone.is_dirty
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.json() == [{'name': zone.name, 'zone_type': zone.zone_type, 'serial': 2026010101}]

    def test_never_published_zone_not_listed(self, api_client, nameserver, zone):
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.json() == []

    def test_excludes_zones_assigned_to_other_nameservers(self, api_client, other_nameserver, zone):
        # `zone` fixture is only linked to `nameserver`, not `other_nameserver`
        _publish(zone)
        _auth(api_client, other_nameserver)
        resp = api_client.get(reverse('agent-zone-list'))
        assert resp.json() == []


class TestAgentZoneDetail:
    def test_returns_published_content(self, api_client, nameserver, zone):
        _publish(zone, content='; validated file\nwww IN A 192.0.2.10\n', serial=2026010101)
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-detail', args=[zone.name]))
        assert resp.status_code == 200
        assert resp.json() == {'name': zone.name, 'serial': 2026010101,
                               'content': '; validated file\nwww IN A 192.0.2.10\n'}

    def test_pending_edit_not_served(self, api_client, nameserver, zone):
        _publish(zone, content='; validated file\n')
        Record.objects.create(zone=zone, name='pending', record_type='A', value='192.0.2.99')
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-detail', args=[zone.name]))
        assert resp.status_code == 200
        assert resp.json()['content'] == '; validated file\n'

    def test_does_not_bump_serial_on_repeated_fetch(self, api_client, nameserver, zone):
        _publish(zone)
        _auth(api_client, nameserver)
        first = api_client.get(reverse('agent-zone-detail', args=[zone.name])).json()
        second = api_client.get(reverse('agent-zone-detail', args=[zone.name])).json()
        assert first['serial'] == second['serial'] == zone.published_serial

    def test_404_for_unassigned_zone(self, api_client, other_nameserver, zone):
        _publish(zone)
        _auth(api_client, other_nameserver)
        resp = api_client.get(reverse('agent-zone-detail', args=[zone.name]))
        assert resp.status_code == 404

    def test_404_for_never_published_zone(self, api_client, nameserver, zone):
        _auth(api_client, nameserver)
        resp = api_client.get(reverse('agent-zone-detail', args=[zone.name]))
        assert resp.status_code == 404


class TestAgentRateThrottle:
    """The 'agent' rate must actually apply, counted per NameServer key."""

    @pytest.fixture(autouse=True)
    def low_rate(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from apps.api.v1.agent_views import AgentRateThrottle
        cache.clear()
        with patch.object(AgentRateThrottle, 'THROTTLE_RATES', {'agent': '2/minute'}):
            yield
        cache.clear()

    def test_throttles_after_rate_exceeded(self, api_client, nameserver, zone):
        _auth(api_client, nameserver)
        url = reverse('agent-zone-list')
        assert [api_client.get(url).status_code for _ in range(3)] == [200, 200, 429]

    def test_each_nameserver_has_its_own_budget(self, api_client, nameserver, other_nameserver, zone):
        url = reverse('agent-zone-list')
        _auth(api_client, nameserver)
        api_client.get(url)
        api_client.get(url)
        assert api_client.get(url).status_code == 429
        _auth(api_client, other_nameserver)
        assert api_client.get(url).status_code == 200
