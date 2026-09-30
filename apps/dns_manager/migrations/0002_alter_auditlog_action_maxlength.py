from django.db import migrations


def _alter_forward(apps, schema_editor):
    # SQLite creates the column at max_length=20 from 0001_initial, no ALTER needed.
    # On MySQL the live table was created externally with max_length=10.
    if schema_editor.connection.vendor == 'mysql':
        schema_editor.execute(
            'ALTER TABLE audit_log MODIFY COLUMN action VARCHAR(20) NOT NULL'
        )


def _alter_reverse(apps, schema_editor):
    if schema_editor.connection.vendor == 'mysql':
        schema_editor.execute(
            'ALTER TABLE audit_log MODIFY COLUMN action VARCHAR(10) NOT NULL'
        )


class Migration(migrations.Migration):

    # MySQL can't roll back DDL, so Django refuses to run the ALTER inside the
    # migration's transaction — without this, fresh MySQL installs fail here.
    atomic = False

    dependencies = [
        ('dns_manager', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(_alter_forward, _alter_reverse),
    ]
