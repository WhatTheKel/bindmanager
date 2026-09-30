import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User

from .validators import normalize_target, validate_record_conflicts, validate_record_value


class NameServer(models.Model):
    name = models.CharField(max_length=255, unique=True)
    address = models.GenericIPAddressField()
    config_dir = models.CharField(max_length=512, default='/etc/bind')
    # Bearer credential used by the pull agent (see apps/api/v1/agent_views.py)
    # running on this physical nameserver to authenticate itself and scope
    # which zones it's allowed to fetch. Generated once on first save.
    api_key = models.CharField(max_length=64, unique=True, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'nameservers'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = secrets.token_hex(32)
        super().save(*args, **kwargs)

    def regenerate_api_key(self):
        self.api_key = secrets.token_hex(32)
        self.save(update_fields=['api_key', 'updated_at'])


class Zone(models.Model):
    class ZoneType(models.TextChoices):
        FORWARD = 'forward', 'Forward'
        REVERSE = 'reverse', 'Reverse'

    class IPVersion(models.IntegerChoices):
        IPV4 = 4, 'IPv4'
        IPV6 = 6, 'IPv6'

    name = models.CharField(max_length=255, unique=True, db_index=True)
    zone_type = models.CharField(max_length=10, choices=ZoneType.choices, default=ZoneType.FORWARD)
    ip_version = models.IntegerField(choices=IPVersion.choices, default=IPVersion.IPV4)
    nameservers = models.ManyToManyField(NameServer, related_name='zones', blank=True)
    serial = models.PositiveBigIntegerField(default=1)
    refresh = models.PositiveIntegerField(default=3600)
    retry = models.PositiveIntegerField(default=900)
    expire = models.PositiveIntegerField(default=604800)
    minimum_ttl = models.PositiveIntegerField(default=86400)
    default_ttl = models.PositiveIntegerField(default=3600)
    is_dirty = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='zones_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'zones'
        indexes = [models.Index(fields=['is_dirty'])]

    def __str__(self):
        return self.name

    def mark_dirty(self):
        self.is_dirty = True
        self.save(update_fields=['is_dirty', 'updated_at'])


class Record(models.Model):
    class RecordType(models.TextChoices):
        A     = 'A',     'A'
        AAAA  = 'AAAA',  'AAAA'
        CNAME = 'CNAME', 'CNAME'
        MX    = 'MX',    'MX'
        NS    = 'NS',    'NS'
        PTR   = 'PTR',   'PTR'
        SOA   = 'SOA',   'SOA'
        SRV   = 'SRV',   'SRV'
        TXT   = 'TXT',   'TXT'
        CAA   = 'CAA',   'CAA'

    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name='records')
    name = models.CharField(max_length=255)
    record_type = models.CharField(max_length=10, choices=RecordType.choices, db_index=True)
    ttl = models.PositiveIntegerField(null=True, blank=True)
    value = models.TextField()
    priority = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='records_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'records'
        indexes = [
            models.Index(fields=['zone', 'record_type']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return f'{self.name} {self.record_type} {self.value}'

    def clean(self):
        # Runs for ModelForms (manage UI, Django admin); the API serializer
        # calls validate_record_value itself.
        try:
            validate_record_value(self.record_type, self.value)
            if self.zone_id:
                self.value = normalize_target(self.record_type, self.value, self.zone.name)
        except ValidationError as e:
            raise ValidationError({'value': e.messages})
        if self.zone_id and self.is_active:
            validate_record_conflicts(self.zone, self.record_type, self.name,
                                      self.value, self.priority, exclude_pk=self.pk)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.zone.mark_dirty()

    def delete(self, *args, **kwargs):
        zone = self.zone
        super().delete(*args, **kwargs)
        zone.mark_dirty()


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE     = 'create',     'Create'
        UPDATE     = 'update',     'Update'
        DELETE     = 'delete',     'Delete'
        SYNC       = 'sync',       'Sync'
        LOGIN      = 'login',      'Login'
        LOGOUT     = 'logout',     'Logout'
        LOGIN_FAIL = 'login_fail', 'Login Failed'

    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=20, choices=Action.choices)
    entity_type = models.CharField(max_length=50)
    entity_id = models.PositiveIntegerField(null=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'audit_log'

    def __str__(self):
        return f'{self.action} {self.entity_type}:{self.entity_id}'
