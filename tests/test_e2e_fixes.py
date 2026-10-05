"""
Fixes for problems a live end-to-end run against real BIND servers found:
long TXT values, zones named refused to load while everything reported
success, CAA / host-name / CNAME-target validation, the deadlock on parallel
record writes, and PTR records left behind by deletes and zone renames.
"""
import importlib.util
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.dns_manager.models import AuditLog, Record, Zone
from apps.dns_manager.ptr import remove_ptr, rename_ptrs, snapshot
from apps.dns_manager.validators import (
    normalize_owner, normalize_target, validate_record_conflicts, validate_record_value,
)
from apps.dns_manager.zone_engine import writer
from apps.dns_manager.zone_engine.generator import _quote_txt

AGENT_PATH = Path(__file__).resolve().parents[1] / 'agents' / 'bindmanager_agent.py'


def _proc(rc=0, out=''):
    return CompletedProcess([], rc, stdout=out, stderr='')


# ── TXT values over 255 bytes ────────────────────────────────────────────────

class TestLongTxt:
    def _strings(self, rendered):
        import re
        return re.findall(r'"((?:[^"\\]|\\.)*)"', rendered)

    def test_long_value_split_into_255_byte_strings(self):
        value = 'v=DKIM1; k=rsa; p=' + 'A' * 400
        parts = self._strings(_quote_txt(value))
        assert [len(p) for p in parts] == [255, len(value) - 255]
        assert ''.join(parts) == value

    def test_short_value_unchanged(self):
        assert _quote_txt('v=spf1 -all') == '"v=spf1 -all"'

    def test_quoted_strings_kept_and_long_one_split(self):
        rendered = _quote_txt('"short" "' + 'x' * 300 + '"')
        assert [len(p) for p in self._strings(rendered)] == [5, 255, 45]

    def test_escape_never_split(self):
        parts = self._strings(_quote_txt('x' * 255 + '"y'))
        assert parts == ['x' * 255, '\\"y']
        parts = self._strings(_quote_txt('x' * 254 + '"y'))
        assert parts == ['x' * 254 + '\\"', 'y']   # \" is one byte, so it still fits

    def test_multibyte_character_never_split(self):
        parts = self._strings(_quote_txt('x' * 254 + 'é'))
        assert parts == ['x' * 254, 'é']

    def test_backslash_kept(self):
        # Unescaped, BIND reads "a\b" as "ab"
        assert _quote_txt('a\\b') == '"a\\\\b"'


# ── Central check matches what named loads ───────────────────────────────────

class TestCentralLoadCheck:
    def test_checkzone_fails_on_bad_host_names(self, tmp_path):
        with patch.object(writer.subprocess, 'run', return_value=_proc()) as run:
            writer._validate('example.com', tmp_path / 'example.com.zone')
        assert run.call_args.args[0][:3] == ['named-checkzone', '-k', 'fail']

    def test_reload_waits_for_new_serial(self):
        with patch.object(writer, '_rndc', side_effect=[_proc(0), _proc(0, 'serial: 4\n'),
                                                        _proc(0, 'serial: 5\n')]), \
             patch.object(writer.time, 'sleep'):
            writer.reload_zone('example.com', 5)

    def test_reload_raises_when_named_keeps_old_serial(self, monkeypatch):
        monkeypatch.setattr(writer, 'LOAD_WAIT_SECONDS', 0)
        with patch.object(writer, '_rndc', side_effect=[_proc(0), _proc(0, 'serial: 4\n')]):
            with pytest.raises(RuntimeError, match='did not load example.com serial 5 '
                                                   r'\(still serving serial 4\)'):
                writer.reload_zone('example.com', 5)

    @pytest.mark.django_db
    def test_failed_load_keeps_zone_dirty_and_unpublished(self, zone):
        from apps.dns_manager.tasks import sync_zone
        zone.mark_dirty()
        with patch('apps.dns_manager.tasks.atomic_write'), \
             patch.object(writer, '_rndc', side_effect=[_proc(0)] + [_proc(0, 'serial: 1\n')] * 50), \
             patch.object(writer, 'LOAD_WAIT_SECONDS', 0):
            sync_zone.apply(args=[zone.pk])
        zone.refresh_from_db()
        assert zone.is_dirty and zone.published_serial is None
        assert AuditLog.objects.filter(detail__contains='did not load').exists()


# ── Validation ───────────────────────────────────────────────────────────────

class TestValidation:
    @pytest.mark.parametrize('value', ['0 issue "letsencrypt.org"', '128 iodef "mailto:a@example.com"',
                                       '0 issuewild ";"'])
    def test_caa_ok(self, value):
        validate_record_value('CAA', value)

    @pytest.mark.parametrize('value', ['nonsense', '0 issue', '256 issue "x"', 'x issue "y"'])
    def test_caa_rejected(self, value):
        with pytest.raises(ValidationError):
            validate_record_value('CAA', value)

    @pytest.mark.parametrize('rtype', ['A', 'AAAA', 'MX'])
    def test_underscore_in_host_owner_rejected(self, rtype):
        with pytest.raises(ValidationError, match='no "_"'):
            normalize_owner('_foo', 'example.com', rtype)

    def test_underscore_owner_fine_for_other_types(self):
        assert normalize_owner('_dmarc', 'example.com', 'TXT') == '_dmarc'
        assert normalize_owner('*.w', 'example.com', 'A') == '*.w'

    @pytest.mark.parametrize('rtype,value', [('MX', 'bad_host.example.org.'), ('NS', 'n_s.example.org.'),
                                             ('SRV', '5 80 s_rv.example.org.'), ('MX', 'bad!')])
    def test_bad_host_target_rejected(self, rtype, value):
        with pytest.raises(ValidationError):
            normalize_target(rtype, value, 'example.com')

    def test_cname_target_may_have_underscore(self):
        assert normalize_target('CNAME', '_acme.example.org.', 'example.com') == '_acme.example.org.'

    def test_cname_target_bad_characters_rejected(self):
        with pytest.raises(ValidationError):
            normalize_target('CNAME', 'bad!', 'example.com')

    @pytest.mark.django_db
    @pytest.mark.parametrize('value', ['loop', 'loop.example.com.'])
    def test_cname_to_itself_rejected(self, zone, value):
        with pytest.raises(ValidationError, match='own name'):
            validate_record_conflicts(zone, 'CNAME', 'loop', value)

    @pytest.mark.django_db
    @pytest.mark.parametrize('rtype,value', [('MX', 'alias'), ('MX', 'alias.example.com.'),
                                             ('SRV', '5 80 alias'), ('NS', 'alias')])
    def test_target_that_is_a_cname_rejected(self, zone, rtype, value):
        Record.objects.create(zone=zone, name='alias', record_type='CNAME', value='www')
        with pytest.raises(ValidationError, match='RFC 2181'):
            validate_record_conflicts(zone, rtype, '@', value, priority=10)

    @pytest.mark.django_db
    def test_target_with_address_records_fine(self, zone):
        Record.objects.create(zone=zone, name='mail', record_type='A', value='192.0.2.25')
        validate_record_conflicts(zone, 'MX', '@', 'mail', priority=10)

    @pytest.mark.django_db
    def test_api_rejects_underscore_a_record(self, zone, staff_user):
        api = APIClient()
        api.force_authenticate(staff_user)
        resp = api.post('/api/v1/records/', {'zone': zone.pk, 'name': '_x', 'record_type': 'A',
                                              'value': '192.0.2.1'}, format='json')
        assert resp.status_code == 400 and 'name' in resp.data


# ── PTR records left behind ──────────────────────────────────────────────────

@pytest.fixture
def rev4(db):
    return Zone.objects.create(name='2.0.192.in-addr.arpa', zone_type='reverse')


def _ptrs(zone):
    return sorted(zone.records.filter(record_type='PTR').values_list('name', 'value'))


@pytest.mark.django_db
class TestPtrCleanup:
    def _host(self, zone, rev, name='www', ip='192.0.2.25'):
        rec = Record.objects.create(zone=zone, name=name, record_type='A', value=ip)
        fqdn = f'{zone.name}.' if name == '@' else f'{name}.{zone.name}.'
        Record.objects.create(zone=rev, name=ip.rsplit('.', 1)[1], record_type='PTR', value=fqdn)
        return rec

    def test_delete_removes_matching_ptr(self, zone, rev4):
        rec = self._host(zone, rev4)
        before, pk = snapshot(rec), rec.pk
        rec.delete()
        changes = remove_ptr(before, pk)
        assert [c.action for c in changes] == ['delete'] and _ptrs(rev4) == []

    def test_delete_leaves_ptr_pointing_elsewhere(self, zone, rev4):
        rec = Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.25')
        Record.objects.create(zone=rev4, name='25', record_type='PTR', value='other.example.org.')
        before = snapshot(rec)
        rec.delete()
        assert remove_ptr(before) == [] and _ptrs(rev4) == [('25', 'other.example.org.')]

    def test_ui_delete_removes_ptr_and_audits(self, client, staff_user, zone, rev4):
        rec = self._host(zone, rev4)
        client.force_login(staff_user)
        client.post(reverse('dns_manager:manage_record_delete', args=[zone.pk, rec.pk]))
        assert _ptrs(rev4) == []
        assert AuditLog.objects.filter(action='delete', detail__startswith='Removed PTR 25.2.0.192').exists()

    def test_api_delete_removes_ptr(self, zone, rev4, staff_user):
        rec = self._host(zone, rev4)
        api = APIClient()
        api.force_authenticate(staff_user)
        assert api.delete(f'/api/v1/records/{rec.pk}/').status_code == 204
        assert _ptrs(rev4) == []
        assert AuditLog.objects.filter(detail__startswith='[API] Removed PTR').exists()

    def test_rename_updates_ptrs(self, zone, rev4):
        self._host(zone, rev4)
        self._host(zone, rev4, name='@', ip='192.0.2.10')
        Zone.objects.filter(pk=zone.pk).update(name='example.net')
        changes = rename_ptrs('example.com', 'example.net')
        assert len(changes) == 2
        assert _ptrs(rev4) == [('10', 'example.net.'), ('25', 'www.example.net.')]

    def test_rename_skips_names_in_a_child_zone(self, zone, rev4):
        child = Zone.objects.create(name='sub.example.com')
        self._host(child, rev4, name='h')
        Zone.objects.filter(pk=zone.pk).update(name='example.net')
        assert rename_ptrs('example.com', 'example.net') == []
        assert _ptrs(rev4) == [('25', 'h.sub.example.com.')]

    def test_ui_zone_rename_updates_ptrs(self, client, staff_user, zone, rev4):
        self._host(zone, rev4)
        client.force_login(staff_user)
        client.post(reverse('dns_manager:manage_zone_edit', args=[zone.pk]), {
            'name': 'example.net', 'zone_type': 'forward', 'ip_version': 4,
            'nameservers': list(zone.nameservers.values_list('pk', flat=True)),
            'refresh': 3600, 'retry': 900, 'expire': 604800, 'minimum_ttl': 86400, 'default_ttl': 3600,
        })
        assert _ptrs(rev4) == [('25', 'www.example.net.')]

    def test_api_zone_rename_updates_ptrs(self, zone, rev4, staff_user):
        self._host(zone, rev4)
        api = APIClient()
        api.force_authenticate(staff_user)
        assert api.patch(f'/api/v1/zones/{zone.pk}/', {'name': 'example.net'}, format='json').status_code == 200
        assert _ptrs(rev4) == [('25', 'www.example.net.')]


# ── Agent: a file named won't load is rolled back ────────────────────────────

def _load_agent():
    spec = importlib.util.spec_from_file_location('bindmanager_agent_fix', AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestAgentLoadCheck:
    @pytest.fixture
    def agent(self, tmp_path, monkeypatch):
        mod = _load_agent()
        monkeypatch.setattr(mod, 'LOAD_WAIT_SECONDS', 0)
        monkeypatch.setattr(mod.time, 'sleep', lambda s: None)
        zones = tmp_path / 'zones'
        zones.mkdir()
        (zones / 'example.com.zone').write_text('old 1 ; Serial\n')
        manifest = tmp_path / 'manifest.json'
        manifest.write_text('{"example.com": 1}')
        cfg = mod.Config(api_url='http://app/api/v1', api_key='k', zones_dir=zones,
                         named_conf_include=None, rndc_bin='rndc', checkzone_bin='named-checkzone',
                         checkconf_bin='named-checkconf', lock_file=tmp_path / 'lock',
                         manifest_file=manifest, timeout=5)
        monkeypatch.setattr(mod, 'fetch_assigned_zones', lambda c: [{'name': 'example.com', 'serial': 2}])
        monkeypatch.setattr(mod, 'fetch_zone_content',
                            lambda c, n: {'name': n, 'serial': 2, 'content': 'new 2 ; Serial\n'})
        return mod, cfg

    def _run(self, mod, cfg, zonestatus):
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            if args[:2] == ['rndc', 'zonestatus']:
                return zonestatus
            return _proc(0)
        with patch.object(mod.subprocess, 'run', side_effect=fake_run):
            return mod.sync(cfg), calls

    def test_checkzone_uses_k_fail(self, agent):
        mod, cfg = agent
        _, calls = self._run(mod, cfg, _proc(0, 'serial: 2\n'))
        assert ['named-checkzone', '-k', 'fail'] == calls[0][:3]

    def test_loaded_zone_kept(self, agent):
        mod, cfg = agent
        rc, _ = self._run(mod, cfg, _proc(0, 'serial: 2\n'))
        assert rc == 0 and 'new 2' in (cfg.zones_dir / 'example.com.zone').read_text()

    def test_rejected_zone_restored_and_reported(self, agent, caplog):
        mod, cfg = agent
        rc, calls = self._run(mod, cfg, _proc(0, 'serial: 1\n'))
        assert rc == 1
        assert (cfg.zones_dir / 'example.com.zone').read_text() == 'old 1 ; Serial\n'
        assert calls.count(['rndc', 'reload', 'example.com']) == 2   # new file, then the old again
        assert 'did not load serial 2' in caplog.text

    def test_status_unavailable_trusts_reload(self, agent):
        mod, cfg = agent
        rc, _ = self._run(mod, cfg, _proc(1, 'rndc: connection refused'))
        assert rc == 0 and 'new 2' in (cfg.zones_dir / 'example.com.zone').read_text()
