"""
Which nameservers serve a zone's published version: the agent reports what
named serves (POST /api/v1/agent/status/), Zone.serving_status compares it.
"""
import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.dns_manager.models import NameServer, Zone


def _publish(zone, serial, minutes_ago=0):
    Zone.objects.filter(pk=zone.pk).update(
        published_serial=serial, published_content='; x\n',
        published_at=timezone.now() - timedelta(minutes=minutes_ago))
    zone.refresh_from_db()


def _report(ns, serving, minutes_ago=0):
    NameServer.objects.filter(pk=ns.pk).update(
        serving=serving, serving_as_of=timezone.now() - timedelta(minutes=minutes_ago))


def _states(zone):
    zone = Zone.objects.prefetch_related('nameservers').get(pk=zone.pk)
    return [(ns.name, state) for ns, state, _ in zone.serving_status()]


class TestServingStatus:
    def test_live(self, zone, nameserver):
        _publish(zone, 5, minutes_ago=2)
        _report(nameserver, {zone.name: 5})
        assert _states(zone) == [(nameserver.name, 'live')]

    def test_updating_until_agent_runs(self, zone, nameserver):
        _report(nameserver, {zone.name: 4}, minutes_ago=1)
        _publish(zone, 5)
        assert _states(zone) == [(nameserver.name, 'updating')]

    def test_behind_when_agent_ran_since(self, zone, nameserver):
        _publish(zone, 5, minutes_ago=1)
        _report(nameserver, {zone.name: 4})
        assert _states(zone) == [(nameserver.name, 'behind')]

    def test_not_loaded_is_behind(self, zone, nameserver):
        _publish(zone, 5, minutes_ago=1)
        _report(nameserver, {zone.name: None})
        assert _states(zone) == [(nameserver.name, 'behind')]

    def test_behind_when_agent_silent_too_long(self, zone, nameserver):
        _report(nameserver, {zone.name: 4}, minutes_ago=60)
        _publish(zone, 5, minutes_ago=30)
        assert _states(zone) == [(nameserver.name, 'behind')]

    def test_unknown_for_old_agents(self, zone, nameserver):
        _publish(zone, 5)
        assert _states(zone) == [(nameserver.name, 'unknown')]

    def test_inactive_nameserver_ignored(self, zone, nameserver):
        _publish(zone, 5)
        NameServer.objects.filter(pk=nameserver.pk).update(is_active=False)
        assert _states(zone) == []


class TestAgentStatusEndpoint:
    @pytest.fixture
    def client(self, nameserver):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'ApiKey {nameserver.api_key}')
        return c

    def test_requires_agent_key(self, zone):
        resp = APIClient().post(reverse('agent-status'), {'serving': {}}, format='json')
        assert resp.status_code == 401

    def test_stores_assigned_zones_only(self, client, nameserver, zone):
        client.get(reverse('agent-zone-list'))              # the run's check-in
        resp = client.post(reverse('agent-status'),
                           {'serving': {zone.name: 7, 'other.example': 1}}, format='json')
        assert resp.status_code == 204
        nameserver.refresh_from_db()
        assert nameserver.serving == {zone.name: 7}
        assert nameserver.serving_as_of == nameserver.agent_last_seen

    @pytest.mark.parametrize('body', [
        {}, {'serving': []}, {'serving': {'example.com': '7'}}, {'serving': {'example.com': True}},
    ])
    def test_rejects_bad_body(self, client, zone, body):
        assert client.post(reverse('agent-status'), body, format='json').status_code == 400

    def test_zone_api_shows_serving(self, zone, nameserver, staff_user):
        _publish(zone, 5, minutes_ago=1)
        _report(nameserver, {zone.name: 5})
        c = APIClient()
        c.force_authenticate(staff_user)
        data = c.get(f'/api/v1/zones/{zone.pk}/').json()
        assert data['published_serial'] == 5
        assert data['serving'] == [{'nameserver': nameserver.name, 'state': 'live', 'serial': 5}]

    def test_dashboard_lists_zones_behind(self, client, zone, nameserver, staff_user):
        _publish(zone, 5, minutes_ago=1)
        _report(nameserver, {zone.name: 4})
        client.force_login(staff_user)
        resp = client.get(reverse('dns_manager:manage_dashboard'))
        assert resp.context['behind_zones'] == [(zone, [nameserver.name])]


class TestZonePage:
    @pytest.mark.parametrize('report, published_ago, badge', [
        ({'example.com': 5}, 1, '✓ ns1.example.com'),
        ({'example.com': 4}, 1, 'ns1.example.com behind'),
        (None, 1, '? ns1.example.com'),
    ])
    def test_shows_state_per_nameserver(self, client, zone, nameserver, staff_user,
                                        report, published_ago, badge):
        _publish(zone, 5, minutes_ago=published_ago)
        if report is not None:
            _report(nameserver, report)
        client.force_login(staff_user)
        resp = client.get(reverse('dns_manager:manage_zone_detail', args=[zone.pk]))
        assert resp.status_code == 200
        assert badge in resp.content.decode()

    def test_updating_badge(self, client, zone, nameserver, staff_user):
        _report(nameserver, {zone.name: 4}, minutes_ago=1)
        _publish(zone, 5)
        client.force_login(staff_user)
        resp = client.get(reverse('dns_manager:manage_zone_detail', args=[zone.pk]))
        assert '⏳ ns1.example.com' in resp.content.decode()


def _load_agent():
    path = Path(__file__).resolve().parents[1] / 'agents' / 'bindmanager_agent.py'
    spec = importlib.util.spec_from_file_location('bindmanager_agent', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestAgentReport:
    @pytest.fixture
    def agent(self, tmp_path):
        mod = _load_agent()
        cfg = mod.Config(api_url='http://app/api/v1', api_key='k', zones_dir=tmp_path,
                         named_conf_include=None, rndc_bin='rndc', checkzone_bin='true',
                         checkconf_bin='true', lock_file=tmp_path / 'lock',
                         manifest_file=tmp_path / 'm.json', timeout=5)
        return mod, cfg

    def test_reports_loaded_serials(self, agent):
        mod, cfg = agent
        status = {'a.example': 3, 'b.example': None}

        def loaded(cfg, name):
            if name == 'c.example':
                raise mod.StatusUnavailable('no access')
            return status[name]
        with patch.object(mod, 'loaded_serial', side_effect=loaded), \
             patch.object(mod, '_api_request') as api:
            mod.report_serving(cfg, ['a.example', 'b.example', 'c.example'])
        api.assert_called_once_with(cfg, '/agent/status/', {'serving': status})

    def test_report_failure_does_not_raise(self, agent, caplog):
        mod, cfg = agent
        with patch.object(mod, 'loaded_serial', side_effect=FileNotFoundError('rndc')):
            mod.report_serving(cfg, ['a.example'])
        assert 'could not report' in caplog.text

    def test_reports_even_when_sync_fails(self, agent):
        mod, cfg = agent
        with patch.object(mod, 'fetch_assigned_zones', return_value=[{'name': 'a.example', 'serial': 1}]), \
             patch.object(mod, '_sync', side_effect=RuntimeError('rndc reload failed')), \
             patch.object(mod, 'report_serving') as report:
            with pytest.raises(RuntimeError):
                mod.sync(cfg)
        report.assert_called_once()

    def test_post_sends_json(self, agent):
        mod, cfg = agent

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b''
        with patch.object(mod.urllib.request, 'urlopen', return_value=Resp()) as urlopen:
            mod._api_request(cfg, '/agent/status/', {'serving': {'a.example': 3}})
        req = urlopen.call_args[0][0]
        assert req.get_method() == 'POST'
        assert req.full_url == 'http://app/api/v1/agent/status/'
        assert json.loads(req.data) == {'serving': {'a.example': 3}}
