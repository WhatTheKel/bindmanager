"""
The pull agent reports its version on every run; the app records it with
the check-in time and flags outdated, unknown, never-seen and stale agents.
"""
import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.dns_manager.models import NameServer, Zone

pytestmark = pytest.mark.django_db
AGENT_PATH = Path(__file__).resolve().parents[1] / 'agents' / 'bindmanager_agent.py'


def _load_agent():
    spec = importlib.util.spec_from_file_location('bindmanager_agent_v', AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _checkin(ns, user_agent):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'ApiKey {ns.api_key}')
    resp = client.get(reverse('agent-zone-list'), HTTP_USER_AGENT=user_agent)
    assert resp.status_code == 200
    ns.refresh_from_db()
    return ns


class TestAgentSide:
    def test_app_knows_the_agent_version_it_ships(self):
        agent = _load_agent()
        assert re.fullmatch(r'\d+\.\d+\.\d+', agent.AGENT_VERSION)
        assert settings.LATEST_AGENT_VERSION == agent.AGENT_VERSION

    def test_requests_carry_version_in_user_agent(self):
        agent = _load_agent()
        cfg = agent.Config(api_url='http://x', api_key='k', zones_dir=Path('/tmp'),
                           named_conf_include=None, rndc_bin='rndc', checkzone_bin='true',
                           checkconf_bin='true', lock_file=Path('/tmp/l'),
                           manifest_file=Path('/tmp/m'), timeout=5)
        with patch.object(agent.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'[]'
            agent.fetch_assigned_zones(cfg)
        sent = urlopen.call_args.args[0]
        assert sent.get_header('User-agent').startswith(f'bindmanager-agent/{agent.AGENT_VERSION} ')


class TestCheckin:
    def test_records_version_and_time(self, nameserver):
        ns = _checkin(nameserver, 'bindmanager-agent/0.2.4 (python 3.9)')
        assert ns.agent_version == '0.2.4'
        assert timezone.now() - ns.agent_last_seen < timezone.timedelta(seconds=5)

    def test_old_agent_recorded_without_version(self, nameserver):
        ns = _checkin(nameserver, 'Python-urllib/3.9')
        assert ns.agent_version == '' and ns.agent_last_seen is not None
        assert ns.agent_status == 'unknown'

    def test_checkin_does_not_touch_zones_or_updated_at(self, nameserver, zone):
        before = NameServer.objects.get(pk=nameserver.pk).updated_at
        ns = _checkin(nameserver, 'bindmanager-agent/0.2.4')
        assert ns.updated_at == before
        assert not Zone.objects.get(pk=zone.pk).is_dirty


class TestStatus:
    @pytest.fixture(autouse=True)
    def latest(self, settings):
        settings.LATEST_AGENT_VERSION = '0.2.10'

    def _ns(self, version='', seen=True, minutes_ago=1, active=True):
        return NameServer(name='n', address='192.0.2.1', is_active=active, agent_version=version,
                          agent_last_seen=(timezone.now() - timezone.timedelta(minutes=minutes_ago))
                          if seen else None)

    @pytest.mark.parametrize('version, seen, expected', [
        ('', False, 'never'),
        ('', True, 'unknown'),
        ('0.2.9', True, 'outdated'),      # compared as numbers, not text
        ('0.2.10', True, 'current'),
        ('0.3.0', True, 'current'),
    ])
    def test_status(self, version, seen, expected):
        assert self._ns(version, seen).agent_status == expected

    def test_stale_after_threshold(self):
        assert not self._ns('0.2.10', minutes_ago=3).agent_is_stale
        assert self._ns('0.2.10', minutes_ago=30).agent_is_stale
        assert not self._ns('0.2.10', minutes_ago=30, active=False).agent_is_stale
        assert not self._ns(seen=False).agent_is_stale


class TestNameserverPage:
    def test_badges_and_notice(self, client, settings):
        settings.LATEST_AGENT_VERSION = '0.2.4'
        staff = User.objects.create_user('s', password='x', is_staff=True)
        now = timezone.now()
        NameServer.objects.create(name='ok.example', address='192.0.2.1',
                                  agent_version='0.2.4', agent_last_seen=now)
        NameServer.objects.create(name='old.example', address='192.0.2.2',
                                  agent_version='', agent_last_seen=now)
        NameServer.objects.create(name='stale.example', address='192.0.2.3', agent_version='0.2.4',
                                  agent_last_seen=now - timezone.timedelta(hours=1))
        NameServer.objects.create(name='new.example', address='192.0.2.4')
        client.force_login(staff)
        html = client.get(reverse('dns_manager:manage_nameserver_list')).content.decode()
        assert 'latest agent 0.2.4' in html
        assert 'Unknown</span>' in html
        assert '>Stale<' in html
        assert '>Never<' in html
        assert '3 nameservers need attention' in html


def test_dashboard_shows_agent_notice(client):
    staff = User.objects.create_user('s', password='x', is_staff=True)
    NameServer.objects.create(name='old.example', address='192.0.2.2',
                              agent_version='', agent_last_seen=timezone.now())
    client.force_login(staff)
    html = client.get(reverse('dns_manager:manage_dashboard')).content.decode()
    assert '1 nameserver agent needs attention' in html


def test_last_seen_wording():
    ns = NameServer(name='n', address='192.0.2.1', agent_last_seen=timezone.now())
    assert ns.agent_last_seen_ago == 'just now'
    ns.agent_last_seen = timezone.now() - timezone.timedelta(minutes=3)
    assert ns.agent_last_seen_ago.endswith('minutes ago') and ns.agent_last_seen_ago.startswith('3')
    assert NameServer(name='m', address='192.0.2.2').agent_last_seen_ago == ''


def test_nameserver_page_shows_only_the_end_of_each_key(client):
    staff = User.objects.create_user('s', password='x', is_staff=True)
    ns = NameServer.objects.create(name='ns.example', address='192.0.2.9')
    client.force_login(staff)
    html = client.get(reverse('dns_manager:manage_nameserver_list')).content.decode()
    assert ns.api_key not in html
    assert f'…{ns.api_key[-4:]}' in html
    assert reverse('dns_manager:manage_nameserver_regenerate_key', args=[ns.pk]) in html


def test_new_nameserver_key_is_shown_once_with_copy_button(client):
    staff = User.objects.create_user('s', password='x', is_staff=True)
    client.force_login(staff)
    client.post(reverse('dns_manager:manage_nameserver_add'),
                {'name': 'ns.example', 'address': '192.0.2.9', 'config_dir': '/var/named', 'is_active': 'on'})
    ns = NameServer.objects.get(name='ns.example')
    url = reverse('dns_manager:manage_nameserver_list')
    first = client.get(url).content.decode()
    assert f'data-copy="{ns.api_key}"' in first
    assert 'aria-label="Copy API key for ns.example"' in first
    assert ns.api_key not in client.get(url).content.decode()   # only once


def test_regenerate_key_replaces_it_shows_it_once_and_audits(client):
    from apps.dns_manager.models import AuditLog
    staff = User.objects.create_user('s', password='x', is_staff=True)
    ns = NameServer.objects.create(name='ns.example', address='192.0.2.9')
    old_key = ns.api_key
    client.force_login(staff)
    regen = reverse('dns_manager:manage_nameserver_regenerate_key', args=[ns.pk])
    assert client.get(regen).status_code == 405            # POST only
    html = client.post(regen, follow=True).content.decode()
    ns.refresh_from_db()
    assert ns.api_key != old_key
    assert f'data-copy="{ns.api_key}"' in html and old_key not in html
    assert AuditLog.objects.filter(entity_type='nameserver', entity_id=ns.pk,
                                   detail__contains='Regenerated the API key').exists()


def test_regenerate_key_needs_staff(client):
    user = User.objects.create_user('u', password='x', is_staff=False)
    ns = NameServer.objects.create(name='ns.example', address='192.0.2.9')
    old_key = ns.api_key
    client.force_login(user)
    client.post(reverse('dns_manager:manage_nameserver_regenerate_key', args=[ns.pk]))
    ns.refresh_from_db()
    assert ns.api_key == old_key
