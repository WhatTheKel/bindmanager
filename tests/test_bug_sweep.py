"""
Regression tests for the 2026-10-01 bug sweep:
- zone setting / nameserver changes re-sync the zone
- record names and zone names are validated and made relative/normalised
- MX/SRV need a priority, SRV values are checked, SOA can't be added
- sync_zone: per-zone lock, no audit flood, new zones added, removed zones cleaned
- records moved between zones re-sync the old zone
- the pull agent keeps going when one zone fails
"""
import importlib.util
import logging
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.api.v1.serializers import RecordSerializer, ZoneDetailSerializer
from apps.dns_manager.forms import RecordForm, ZoneForm
from apps.dns_manager.models import AuditLog, NameServer, Record, Zone
from apps.dns_manager.tasks import cleanup_removed_zones, sync_dirty_zones, sync_zone
from apps.dns_manager.validators import normalize_owner, normalize_zone_name
from apps.dns_manager.zone_engine import writer

pytestmark = pytest.mark.django_db

ZONE_DATA = {'zone_type': 'forward', 'ip_version': 4, 'refresh': 3600, 'retry': 900,
             'expire': 604800, 'minimum_ttl': 86400, 'default_ttl': 3600}


def _clean(zone):
    Zone.objects.filter(pk=zone.pk).update(is_dirty=False)
    zone.refresh_from_db()
    return zone


def _dirty(zone):
    return Zone.objects.get(pk=zone.pk).is_dirty


# ── 1 / 10: changes that alter the zone file re-sync it ──────────────────────

class TestZoneChangesResync:
    def test_soa_edit_through_form_marks_dirty(self, zone, nameserver):
        form = ZoneForm(data={**ZONE_DATA, 'name': zone.name, 'refresh': 7200,
                              'nameservers': [nameserver.pk]}, instance=zone)
        assert form.is_valid(), form.errors
        form.save()
        assert _dirty(zone)

    def test_saving_unchanged_zone_stays_clean(self, zone, nameserver):
        form = ZoneForm(data={**ZONE_DATA, 'name': zone.name,
                              'nameservers': [nameserver.pk]}, instance=zone)
        assert form.is_valid(), form.errors
        form.save()
        assert not _dirty(zone)

    def test_api_zone_update_marks_dirty(self, zone):
        s = ZoneDetailSerializer(zone, data={'default_ttl': 300}, partial=True)
        assert s.is_valid(), s.errors
        s.save()
        assert _dirty(zone)

    def test_assigning_nameserver_marks_dirty(self, zone):
        ns2 = NameServer.objects.create(name='ns2.example.com', address='192.0.2.2')
        zone.nameservers.add(ns2)
        assert _dirty(zone)

    def test_unassigning_from_nameserver_side_marks_dirty(self, zone, nameserver):
        nameserver.zones.remove(zone)
        assert _dirty(zone)

    def test_nameserver_rename_marks_zones_dirty_and_bumps_updated_at(self, zone, nameserver):
        before = zone.updated_at
        nameserver.name = 'ns9.example.com'
        nameserver.save()
        zone.refresh_from_db()
        assert zone.is_dirty and zone.updated_at > before

    def test_nameserver_address_change_does_not_resync(self, zone, nameserver):
        nameserver.address = '192.0.2.99'
        nameserver.save()
        assert not _dirty(zone)

    def test_nameserver_delete_marks_zones_dirty(self, zone, nameserver):
        nameserver.delete()
        assert _dirty(zone)


# ── 2: record names ──────────────────────────────────────────────────────────

class TestRecordNames:
    @pytest.mark.parametrize('name, expected', [
        ('www', 'www'),
        ('www.example.com', 'www'),
        ('www.example.com.', 'www'),
        ('WWW.Example.COM', 'WWW'),
        ('example.com', '@'),
        ('example.com.', '@'),
        ('', '@'),
        ('@', '@'),
        ('mail.eu', 'mail.eu'),
        ('*.dev', '*.dev'),
        ('_dmarc', '_dmarc'),
        ('_sip._tcp', '_sip._tcp'),
    ])
    def test_normalised(self, name, expected):
        assert normalize_owner(name, 'example.com') == expected

    @pytest.mark.parametrize('name', ['has space', 'www.other.org.', 'a..b', '-bad', 'bad-',
                                      'x' * 64, 'mid.*.wild', 'ü'])
    def test_rejected(self, name):
        with pytest.raises(ValidationError):
            normalize_owner(name, 'example.com')

    def test_form_saves_relative_name(self, zone):
        form = RecordForm(data={'name': 'www.example.com', 'record_type': 'A',
                                'value': '192.0.2.9', 'is_active': True}, instance=Record(zone=zone))
        assert form.is_valid(), form.errors
        assert form.save().name == 'www'

    def test_form_rejects_bad_name_on_name_field(self, zone):
        form = RecordForm(data={'name': 'has space', 'record_type': 'A',
                                'value': '192.0.2.9', 'is_active': True}, instance=Record(zone=zone))
        assert not form.is_valid()
        assert 'name' in form.errors

    def test_api_normalises_name(self, zone):
        s = RecordSerializer(data={'zone': zone.pk, 'name': 'www.example.com.',
                                   'record_type': 'A', 'value': '192.0.2.9'})
        assert s.is_valid(), s.errors
        assert s.save().name == 'www'

    def test_duplicate_detected_across_spellings(self, zone):
        Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.9')
        form = RecordForm(data={'name': 'www.example.com', 'record_type': 'A',
                                'value': '192.0.2.9', 'is_active': True}, instance=Record(zone=zone))
        assert not form.is_valid()


# ── 3: priority, SRV, SOA ────────────────────────────────────────────────────

class TestPriorityAndTypes:
    def _form(self, zone, **data):
        base = {'name': '@', 'is_active': True}
        base.update(data)
        return RecordForm(data=base, instance=Record(zone=zone))

    @pytest.mark.parametrize('rtype, value', [('MX', 'mail'), ('SRV', '5 5060 sip')])
    def test_priority_required(self, zone, rtype, value):
        form = self._form(zone, record_type=rtype, value=value)
        assert not form.is_valid()
        assert 'priority' in form.errors

    def test_mx_with_priority_ok(self, zone):
        assert self._form(zone, record_type='MX', value='mail', priority=10).is_valid()

    @pytest.mark.parametrize('value', ['sip.example.com.', '5 sip', 'a b sip', '5 70000 sip', '5 5060 192.0.2.1'])
    def test_bad_srv_values_rejected(self, zone, value):
        form = self._form(zone, name='_sip._tcp', record_type='SRV', value=value, priority=10)
        assert not form.is_valid()
        assert 'value' in form.errors

    def test_good_srv_ok(self, zone):
        form = self._form(zone, name='_sip._tcp', record_type='SRV', value='5 5060 sip', priority=10)
        assert form.is_valid(), form.errors

    def test_soa_record_rejected(self, zone):
        form = self._form(zone, record_type='SOA', value='ns1 hostmaster 1 2 3 4 5')
        assert not form.is_valid()

    def test_api_mx_without_priority_rejected(self, zone):
        s = RecordSerializer(data={'zone': zone.pk, 'name': '@', 'record_type': 'MX', 'value': 'mail'})
        assert not s.is_valid()
        assert 'priority' in s.errors


# ── 4: zone names ────────────────────────────────────────────────────────────

class TestZoneNames:
    @pytest.mark.parametrize('name, expected', [
        ('example.org', 'example.org'),
        ('Example.ORG.', 'example.org'),
        ('  example.org  ', 'example.org'),
        ('2.0.192.in-addr.arpa', '2.0.192.in-addr.arpa'),
    ])
    def test_normalised(self, name, expected):
        assert normalize_zone_name(name) == expected

    @pytest.mark.parametrize('name', ['Bad Name.com', 'a..b.com', '', '.', '-x.com', 'x_y!.com'])
    def test_rejected(self, name):
        with pytest.raises(ValidationError):
            normalize_zone_name(name)

    def test_form_stores_normalised_name(self):
        form = ZoneForm(data={**ZONE_DATA, 'name': 'New.Example.'})
        assert form.is_valid(), form.errors
        assert form.save().name == 'new.example'

    def test_form_case_insensitive_uniqueness(self, zone):
        form = ZoneForm(data={**ZONE_DATA, 'name': 'EXAMPLE.COM.'})
        assert not form.is_valid()
        assert 'name' in form.errors

    def test_api_rejects_bad_name(self):
        s = ZoneDetailSerializer(data={**ZONE_DATA, 'name': 'Bad Name.com'})
        assert not s.is_valid()
        assert 'name' in s.errors


# ── 5 / 7: sync task ─────────────────────────────────────────────────────────

@pytest.fixture
def no_bind():
    with patch('apps.dns_manager.tasks.atomic_write') as write, \
         patch('apps.dns_manager.tasks.reload_zone'):
        yield write


def _sync_logs(zone):
    return list(AuditLog.objects.filter(action='sync', entity_id=zone.pk)
                .order_by('id').values_list('detail', flat=True))


class TestSyncTask:
    def test_locked_zone_is_skipped(self, zone, no_bind):
        zone.mark_dirty()
        Zone.objects.filter(pk=zone.pk).update(
            sync_lock_until=timezone.now() + timezone.timedelta(minutes=1))
        sync_zone.apply(args=[zone.pk])
        assert not no_bind.called
        assert _dirty(zone)

    def test_expired_lock_is_taken_and_released(self, zone, no_bind):
        zone.mark_dirty()
        Zone.objects.filter(pk=zone.pk).update(
            sync_lock_until=timezone.now() - timezone.timedelta(minutes=1))
        sync_zone.apply(args=[zone.pk])
        zone.refresh_from_db()
        assert no_bind.called and not zone.is_dirty and zone.sync_lock_until is None

    def test_dispatcher_skips_locked_zones(self, zone):
        zone.mark_dirty()
        Zone.objects.filter(pk=zone.pk).update(
            sync_lock_until=timezone.now() + timezone.timedelta(minutes=1))
        with patch('apps.dns_manager.tasks.sync_zone.delay') as delay, \
             patch('apps.dns_manager.tasks.cleanup_removed_zones'):
            sync_dirty_zones()
        assert not delay.called

    def test_repeated_failure_logged_once(self, zone, no_bind):
        zone.mark_dirty()
        no_bind.side_effect = ValueError('named-checkzone failed: bad')
        for _ in range(3):
            sync_zone.apply(args=[zone.pk])
        assert _sync_logs(zone) == ['Sync failed for example.com: named-checkzone failed: bad']
        assert Zone.objects.get(pk=zone.pk).sync_lock_until is None

    def test_new_failure_and_recovery_are_logged(self, zone, no_bind):
        zone.mark_dirty()
        no_bind.side_effect = ValueError('error one')
        sync_zone.apply(args=[zone.pk])
        no_bind.side_effect = ValueError('error two')
        sync_zone.apply(args=[zone.pk])
        no_bind.side_effect = None
        sync_zone.apply(args=[zone.pk])
        no_bind.side_effect = ValueError('error two')
        zone.mark_dirty()
        sync_zone.apply(args=[zone.pk])
        logs = _sync_logs(zone)
        assert len(logs) == 4 and logs[2].startswith('Synced') and logs[3].endswith('error two')


def _proc(rc=0, out=''):
    return CompletedProcess([], rc, stdout=out, stderr='')


class TestCentralBind:
    def test_new_zone_added_when_reload_says_not_found(self, settings, tmp_path):
        settings.BIND_ZONES_DIR = str(tmp_path)
        with patch.object(writer, '_rndc', side_effect=[_proc(1, "rndc: 'reload' failed: not found"),
                                                        _proc(0)]) as rndc:
            writer.reload_zone('new.example')
        assert rndc.call_args_list[1].args[:2] == ('addzone', 'new.example')
        assert f'file "{tmp_path}/new.example.zone"' in rndc.call_args_list[1].args[2]

    def test_reload_other_errors_raise(self):
        with patch.object(writer, '_rndc', return_value=_proc(1, 'connection refused')):
            with pytest.raises(RuntimeError):
                writer.reload_zone('example.com')

    def test_cleanup_removes_only_orphan_files(self, zone, settings, tmp_path):
        settings.BIND_ZONES_DIR = str(tmp_path)
        (tmp_path / 'example.com.zone').write_text('kept')
        (tmp_path / 'deleted.example.zone').write_text('orphan')
        with patch.object(writer, '_rndc', return_value=_proc(1, 'not found')) as rndc:
            cleanup_removed_zones()
        assert (tmp_path / 'example.com.zone').exists()
        assert not (tmp_path / 'deleted.example.zone').exists()
        rndc.assert_called_once_with('delzone', 'deleted.example')


# ── 6 / 8: creator optional, record moved between zones ──────────────────────

class TestRecordsMisc:
    def test_record_without_creator_passes_full_clean(self, zone):
        rec = Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.9')
        rec.full_clean()     # used to fail: created_by "cannot be blank"

    def test_moving_record_resyncs_old_zone(self, zone):
        other = Zone.objects.create(name='other.example', is_dirty=False)
        rec = Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.9')
        _clean(zone)
        s = RecordSerializer(rec, data={'zone': other.pk}, partial=True)
        assert s.is_valid(), s.errors
        s.save()
        assert _dirty(zone) and _dirty(other)


# ── 9: agent keeps going when one zone fails ─────────────────────────────────

def _load_agent():
    path = Path(__file__).resolve().parents[1] / 'agents' / 'bindmanager_agent.py'
    spec = importlib.util.spec_from_file_location('bindmanager_agent', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # dataclasses look the module up here
    spec.loader.exec_module(mod)
    return mod


class TestAgentPartialFailure:
    def test_one_bad_zone_does_not_block_others(self, tmp_path, caplog):
        agent = _load_agent()
        cfg = agent.Config(api_url='http://x', api_key='k', zones_dir=tmp_path / 'zones',
                           named_conf_include=None, rndc_bin='rndc', checkzone_bin='true',
                           checkconf_bin='true', lock_file=tmp_path / 'lock',
                           manifest_file=tmp_path / 'manifest.json', timeout=5)
        assigned = [{'name': 'good.example', 'zone_type': 'forward', 'serial': 2},
                    {'name': 'bad.example', 'zone_type': 'forward', 'serial': 2}]

        def content(cfg, name):
            if name == 'bad.example':
                raise agent.ApiError('HTTP 500')
            return {'name': name, 'serial': 2, 'content': '@ IN SOA x. y. ( 2 ; Serial\n'}

        with patch.object(agent, 'fetch_assigned_zones', return_value=assigned), \
             patch.object(agent, 'fetch_zone_content', side_effect=content), \
             patch.object(agent, 'reload_all') as reload_all, \
             caplog.at_level(logging.ERROR):
            rc = agent.sync(cfg)

        assert rc == 1                                        # failure reported
        assert (tmp_path / 'zones' / 'good.example.zone').exists()   # others still updated
        assert reload_all.called
        assert agent.load_manifest(cfg) == {'good.example': 2}       # bad one retried next run
        assert 'bad.example not updated' in caplog.text
