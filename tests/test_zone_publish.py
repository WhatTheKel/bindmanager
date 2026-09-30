"""
The central sync publishes each validated zone file (Zone.published_*), which
is all pull agents are ever served. Covers the sync task, an edit arriving
while a sync runs, and the migration that backfills existing zones.
"""
import importlib
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.dns_manager.models import Record, Zone
from apps.dns_manager.tasks import sync_zone

backfill_published = importlib.import_module(
    'apps.dns_manager.migrations.0004_zone_published_version').backfill_published


@pytest.fixture
def no_bind():
    with patch('apps.dns_manager.tasks.atomic_write') as write, \
         patch('apps.dns_manager.tasks.reload_zone'):
        yield write


@pytest.mark.django_db
class TestSyncPublishes:
    def test_successful_sync_publishes_file_and_serial(self, zone, no_bind):
        Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.10')
        sync_zone.apply(args=[zone.pk])
        zone.refresh_from_db()
        written = no_bind.call_args.args[1]
        assert zone.is_dirty is False
        assert zone.published_content == written
        assert zone.published_serial == zone.serial > 1
        assert 'www  IN  A  192.0.2.10' in zone.published_content

    def test_failed_sync_keeps_previous_publication(self, zone, no_bind):
        zone.published_content, zone.published_serial = '; good\n', 5
        zone.save()
        Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.10')
        no_bind.side_effect = RuntimeError('named-checkzone failed')
        sync_zone.apply(args=[zone.pk])
        zone.refresh_from_db()
        assert zone.is_dirty is True
        assert (zone.published_content, zone.published_serial) == ('; good\n', 5)

    def test_edit_during_sync_leaves_zone_dirty(self, zone, no_bind):
        Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.10')

        def edit_mid_sync(*args):
            # Someone saves another record after the zone file was built
            Zone.objects.filter(pk=zone.pk).update(
                is_dirty=True, updated_at=timezone.now() + timezone.timedelta(seconds=1))
        no_bind.side_effect = edit_mid_sync

        sync_zone.apply(args=[zone.pk])
        zone.refresh_from_db()
        assert zone.is_dirty is True              # will sync again
        assert zone.published_serial == zone.serial > 1   # but what passed is published


@pytest.mark.django_db
class TestBackfillMigration:
    def test_uses_central_file_when_serial_matches(self, zone, tmp_path, settings):
        settings.BIND_ZONES_DIR = str(tmp_path)
        Zone.objects.filter(pk=zone.pk).update(serial=2026010105, is_dirty=True)
        (tmp_path / 'example.com.zone').write_text('x\n  2026010105   ; Serial\n')
        backfill_published(django_apps, None)
        zone.refresh_from_db()
        assert zone.published_content == 'x\n  2026010105   ; Serial\n'
        assert zone.published_serial == 2026010105

    def test_renders_clean_zone_without_matching_file(self, zone, tmp_path, settings):
        settings.BIND_ZONES_DIR = str(tmp_path)
        (tmp_path / 'example.com.zone').write_text('old\n  1999010100 ; Serial\n')
        backfill_published(django_apps, None)
        zone.refresh_from_db()
        assert '$ORIGIN example.com.' in zone.published_content
        assert zone.published_serial == zone.serial

    def test_dirty_zone_without_file_stays_unpublished(self, zone, tmp_path, settings):
        settings.BIND_ZONES_DIR = str(tmp_path)
        Zone.objects.filter(pk=zone.pk).update(is_dirty=True)
        backfill_published(django_apps, None)
        zone.refresh_from_db()
        assert zone.published_serial is None
