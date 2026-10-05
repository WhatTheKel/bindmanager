from django.core.management.base import BaseCommand
from apps.dns_manager.models import Zone, AuditLog
from apps.dns_manager.zone_engine.generator import build_zone
from apps.dns_manager.zone_engine.writer import atomic_write, reload_zone


class Command(BaseCommand):
    help = 'Write zone files for all dirty zones and trigger rndc reload'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='Force sync all zones regardless of dirty flag')
        parser.add_argument('--zone', type=str, help='Sync a single named zone')

    def handle(self, *args, **options):
        qs = Zone.objects.prefetch_related('records', 'nameservers')
        if options['zone']:
            qs = qs.filter(name=options['zone'])
        elif not options['all']:
            qs = qs.filter(is_dirty=True)

        synced = 0
        for zone in qs:
            try:
                content, new_serial = build_zone(zone)
                atomic_write(zone.name, content)
                reload_zone(zone.name, new_serial)
                Zone.objects.filter(pk=zone.pk).update(is_dirty=False, serial=new_serial)
                AuditLog.objects.create(
                    action=AuditLog.Action.SYNC,
                    entity_type='zone',
                    entity_id=zone.pk,
                    detail=f'serial={new_serial}',
                )
                self.stdout.write(self.style.SUCCESS(f'  OK  {zone.name} (serial {new_serial})'))
                synced += 1
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'FAIL  {zone.name}: {exc}'))

        self.stdout.write(f'\n{synced} zone(s) synced.')
