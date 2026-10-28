from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dns_manager', '0006_nameserver_agent_checkin'),
    ]

    operations = [
        migrations.AddField(
            model_name='nameserver',
            name='serving',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='nameserver',
            name='serving_as_of',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='zone',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
