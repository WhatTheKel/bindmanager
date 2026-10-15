from django.core.management.base import BaseCommand
from apps.dns_manager.models import Zone
from apps.dns_manager.tasks import sync_zone


class Command(BaseCommand):
    help = 'Write zone files for all dirty zones, reload them and publish them to the agents'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='Force sync all zones regardless of dirty flag')
        parser.add_argument('--zone', type=str, help='Sync a single named zone')

    def handle(self, *args, **options):
        qs = Zone.objects.order_by('name')
        if options['zone']:
            qs = qs.filter(name=options['zone'])
        elif not options['all']:
            qs = qs.filter(is_dirty=True)

        # The same code path as the Celery task, so the result is published
        # to the agents and the zone's sync lock is respected.
        synced = 0
        for pk, name in qs.values_list('pk', 'name'):
            detail = sync_zone(pk)
            if detail is None:
                self.stdout.write(self.style.WARNING(f'SKIP  {name}: being synced by another task'))
            elif detail.startswith('Sync failed'):
                self.stderr.write(self.style.ERROR(f'FAIL  {detail}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'  OK  {detail}'))
                synced += 1

        self.stdout.write(f'\n{synced} zone(s) synced.')
