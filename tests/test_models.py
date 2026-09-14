"""
Tests for model behaviour that can't be derived from reading the code:
- Zone.mark_dirty() only touches two fields (update_fields)
- Record.save() cascades is_dirty to the parent zone
- Record.delete() cascades is_dirty to the parent zone
- AuditLog.action accepts values up to 20 chars
"""
import pytest
from django.contrib.auth.models import User

from apps.dns_manager.models import Zone, Record, NameServer, AuditLog


@pytest.fixture
def zone(db):
    return Zone.objects.create(name='example.com', serial=1, is_dirty=False)


@pytest.fixture
def record(zone):
    return Record.objects.create(
        zone=zone,
        name='www',
        record_type='A',
        value='1.2.3.4',
        is_active=True,
    )


# ── Zone.mark_dirty ──────────────────────────────────────────────────────────

class TestZoneMarkDirty:
    def test_sets_is_dirty_true(self, zone):
        assert zone.is_dirty is False
        zone.mark_dirty()
        zone.refresh_from_db()
        assert zone.is_dirty is True

    def test_does_not_change_serial(self, zone):
        original_serial = zone.serial
        zone.mark_dirty()
        zone.refresh_from_db()
        assert zone.serial == original_serial

    def test_idempotent(self, zone):
        zone.mark_dirty()
        zone.mark_dirty()
        zone.refresh_from_db()
        assert zone.is_dirty is True


# ── Record → Zone dirty cascade ──────────────────────────────────────────────

class TestRecordCascade:
    def test_saving_record_marks_zone_dirty(self, zone):
        assert zone.is_dirty is False
        Record.objects.create(
            zone=zone, name='mail', record_type='A', value='5.6.7.8'
        )
        zone.refresh_from_db()
        assert zone.is_dirty is True

    def test_updating_record_marks_zone_dirty(self, record, zone):
        zone.is_dirty = False
        zone.save(update_fields=['is_dirty'])

        record.value = '9.9.9.9'
        record.save()
        zone.refresh_from_db()
        assert zone.is_dirty is True

    def test_deleting_record_marks_zone_dirty(self, record, zone):
        zone.is_dirty = False
        zone.save(update_fields=['is_dirty'])

        record.delete()
        zone.refresh_from_db()
        assert zone.is_dirty is True


# ── AuditLog ─────────────────────────────────────────────────────────────────

class TestAuditLog:
    def test_action_accepts_login_fail(self, db):
        log = AuditLog.objects.create(
            action=AuditLog.Action.LOGIN_FAIL,
            entity_type='user',
            detail='test',
        )
        assert log.pk is not None

    def test_action_field_max_length_is_20(self):
        field = AuditLog._meta.get_field('action')
        assert field.max_length == 20

    def test_all_action_choices_fit_in_field(self):
        max_len = AuditLog._meta.get_field('action').max_length
        for value, _ in AuditLog.Action.choices:
            assert len(value) <= max_len, f'Action "{value}" exceeds max_length={max_len}'

    def test_log_without_user_is_allowed(self, db):
        log = AuditLog.objects.create(
            user=None,
            action=AuditLog.Action.SYNC,
            entity_type='zone',
            entity_id=1,
            detail='system sync',
        )
        assert log.pk is not None


# ── NameServer ───────────────────────────────────────────────────────────────

class TestNameServer:
    def test_str_returns_name(self, db):
        ns = NameServer.objects.create(name='ns1.example.com', address='1.2.3.4')
        assert str(ns) == 'ns1.example.com'

    def test_default_config_dir(self, db):
        ns = NameServer.objects.create(name='ns2.example.com', address='1.2.3.5')
        assert ns.config_dir == '/etc/bind'
