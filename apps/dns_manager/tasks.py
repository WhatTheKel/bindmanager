from celery import shared_task
from .models import Zone, AuditLog
from .zone_engine.generator import build_zone
from .zone_engine.writer import atomic_write, reload_zone


@shared_task
def sync_dirty_zones():
    """Dispatch one sync_zone task per dirty zone."""
    pks = list(Zone.objects.filter(is_dirty=True).values_list('pk', flat=True))
    for pk in pks:
        sync_zone.delay(pk)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_zone(self, zone_pk: int):
    """Sync a single zone to BIND. Retries independently on failure."""
    try:
        zone = Zone.objects.prefetch_related('records', 'nameservers').get(pk=zone_pk)
    except Zone.DoesNotExist:
        return

    try:
        content, new_serial = build_zone(zone)
        atomic_write(zone.name, content)
        reload_zone(zone.name)
        published = dict(serial=new_serial, published_content=content,
                         published_serial=new_serial)
        # Only clear is_dirty if nothing changed since we read the zone:
        # mark_dirty() bumps updated_at, so an edit made mid-sync (not in
        # `content`) leaves the zone dirty and it syncs again next round.
        unchanged = Zone.objects.filter(pk=zone_pk, updated_at=zone.updated_at)
        if not unchanged.update(is_dirty=False, **published):
            Zone.objects.filter(pk=zone_pk).update(**published)
        AuditLog.objects.create(
            action=AuditLog.Action.SYNC,
            entity_type='zone',
            entity_id=zone_pk,
            detail=f'Synced zone {zone.name} serial={new_serial}',
        )
    except Exception as exc:
        AuditLog.objects.create(
            action=AuditLog.Action.SYNC,
            entity_type='zone',
            entity_id=zone_pk,
            detail=f'Sync failed for {zone.name}: {exc}',
        )
        raise self.retry(exc=exc)
