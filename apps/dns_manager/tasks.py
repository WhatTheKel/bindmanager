import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from .models import Zone, AuditLog
from .zone_engine.generator import build_zone
from .zone_engine.writer import atomic_write, central_zone_names, reload_zone, remove_zone

log = logging.getLogger(__name__)

# How long a sync may hold a zone before another may take over (a worker
# that died mid-sync must not block the zone forever). Syncs take seconds.
SYNC_LOCK_TTL = timedelta(minutes=5)


@shared_task
def sync_dirty_zones():
    """Dispatch one sync_zone task per dirty zone (runs every minute via beat).

    A failed sync is simply retried by the next run while the zone stays
    dirty; zones already being synced are skipped. Also removes central zone
    files left behind by deleted or renamed zones.
    """
    now = timezone.now()
    pks = list(
        Zone.objects.filter(is_dirty=True)
        .filter(Q(sync_lock_until__isnull=True) | Q(sync_lock_until__lt=now))
        .values_list('pk', flat=True)
    )
    for pk in pks:
        sync_zone.delay(pk)
    cleanup_removed_zones()


def cleanup_removed_zones():
    known = set(Zone.objects.values_list('name', flat=True))
    for name in sorted(central_zone_names() - known):
        try:
            remove_zone(name)
            log.info('removed central zone %s (no longer in the database)', name)
        except Exception:
            log.exception('could not remove central zone %s', name)


def _log_sync(zone_pk: int, detail: str) -> None:
    """Audit a sync result, but record a repeated identical failure only once.

    A zone that keeps failing is retried every minute; without this each
    attempt would add an entry and bury real activity in the audit log.
    """
    last = (AuditLog.objects.filter(action=AuditLog.Action.SYNC, entity_type='zone',
                                    entity_id=zone_pk)
            .order_by('-created_at', '-id').values_list('detail', flat=True).first())
    if detail.startswith('Sync failed') and detail == last:
        return
    AuditLog.objects.create(action=AuditLog.Action.SYNC, entity_type='zone',
                            entity_id=zone_pk, detail=detail)


@shared_task
def sync_zone(zone_pk: int):
    """Validate, write and load one zone, then publish it for the agents."""
    now = timezone.now()
    # Take the zone's sync lock atomically; if another sync holds it, leave
    # the zone to that one (it stays dirty if it changed meanwhile).
    got_lock = Zone.objects.filter(pk=zone_pk).filter(
        Q(sync_lock_until__isnull=True) | Q(sync_lock_until__lt=now)
    ).update(sync_lock_until=now + SYNC_LOCK_TTL)
    if not got_lock:
        return

    try:
        zone = Zone.objects.prefetch_related('records', 'nameservers').get(pk=zone_pk)
        try:
            content, new_serial = build_zone(zone)
            atomic_write(zone.name, content)
            reload_zone(zone.name, new_serial)
        except Exception as exc:
            log.warning('sync of %s failed: %s', zone.name, exc)
            _log_sync(zone_pk, f'Sync failed for {zone.name}: {exc}')
            return

        published = dict(serial=new_serial, published_content=content,
                         published_serial=new_serial)
        # Only clear is_dirty if nothing changed since we read the zone:
        # every change bumps updated_at, so an edit made mid-sync (not in
        # `content`) leaves the zone dirty and it syncs again next round.
        unchanged = Zone.objects.filter(pk=zone_pk, updated_at=zone.updated_at)
        if not unchanged.update(is_dirty=False, **published):
            Zone.objects.filter(pk=zone_pk).update(**published)
        _log_sync(zone_pk, f'Synced zone {zone.name} serial={new_serial}')
    except Zone.DoesNotExist:
        return
    finally:
        Zone.objects.filter(pk=zone_pk).update(sync_lock_until=None)
