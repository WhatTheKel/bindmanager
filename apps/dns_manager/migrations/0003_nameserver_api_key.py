import secrets

from django.db import migrations, models


def _backfill_api_keys(apps, schema_editor):
    NameServer = apps.get_model('dns_manager', 'NameServer')
    for ns in NameServer.objects.filter(api_key=''):
        ns.api_key = secrets.token_hex(32)
        ns.save(update_fields=['api_key'])


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('dns_manager', '0002_alter_auditlog_action_maxlength'),
    ]

    operations = [
        migrations.AddField(
            model_name='nameserver',
            name='api_key',
            field=models.CharField(blank=True, default='', editable=False, max_length=64),
        ),
        migrations.RunPython(_backfill_api_keys, _noop_reverse),
        migrations.AlterField(
            model_name='nameserver',
            name='api_key',
            field=models.CharField(blank=True, editable=False, max_length=64, unique=True),
        ),
    ]
