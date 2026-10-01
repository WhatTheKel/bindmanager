import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone

from .validators import (
    normalize_owner, normalize_target, validate_priority,
    validate_record_conflicts, validate_record_value,
)


def _version_tuple(version: str) -> tuple:
    parts = []
    for p in version.split('.'):
        digits = ''.join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _mark_zones_dirty(zone_qs):
    """Queue zones for re-sync. Bumps updated_at too, so a sync already in
    progress sees the change and leaves the zone dirty (see tasks.sync_zone)."""
    zone_qs.update(is_dirty=True, updated_at=timezone.now())


class NameServer(models.Model):
    name = models.CharField(max_length=255, unique=True)
    address = models.GenericIPAddressField()
    config_dir = models.CharField(max_length=512, default='/etc/bind')
    # Bearer credential used by the pull agent (see apps/api/v1/agent_views.py)
    # running on this physical nameserver to authenticate itself and scope
    # which zones it's allowed to fetch. Generated once on first save.
    api_key = models.CharField(max_length=64, unique=True, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    # Reported by the pull agent on each run (see AgentZoneListView)
    agent_version = models.CharField(max_length=32, blank=True, default='')
    agent_last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'nameservers'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = secrets.token_hex(32)
        renamed = (self.pk is not None and
                   NameServer.objects.filter(pk=self.pk).exclude(name=self.name).exists())
        super().save(*args, **kwargs)
        if renamed:
            _mark_zones_dirty(self.zones.all())

    def delete(self, *args, **kwargs):
        # Its NS records disappear from every zone it served
        _mark_zones_dirty(self.zones.all())
        return super().delete(*args, **kwargs)

    @property
    def agent_status(self) -> str:
        """'never' (no check-in yet), 'unknown' (agent too old to report a
        version), 'outdated' (older than the agent this app ships) or 'current'."""
        from django.conf import settings
        if not self.agent_last_seen:
            return 'never'
        if not self.agent_version:
            return 'unknown'
        latest = getattr(settings, 'LATEST_AGENT_VERSION', '')
        if latest and _version_tuple(self.agent_version) < _version_tuple(latest):
            return 'outdated'
        return 'current'

    @property
    def agent_last_seen_ago(self) -> str:
        """'just now' / '3 minutes ago' — timesince alone says '0 minutes'."""
        from django.utils.timesince import timesince
        if not self.agent_last_seen:
            return ''
        if (timezone.now() - self.agent_last_seen).total_seconds() < 60:
            return 'just now'
        return f'{timesince(self.agent_last_seen)} ago'

    @property
    def agent_is_stale(self) -> bool:
        """True if an active nameserver's agent hasn't checked in recently."""
        from django.conf import settings
        if not self.is_active or not self.agent_last_seen:
            return False
        limit = timezone.timedelta(minutes=getattr(settings, 'AGENT_STALE_AFTER_MINUTES', 10))
        return timezone.now() - self.agent_last_seen > limit

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
    # The last zone file that passed central validation, and its serial.
    # Pull agents are only ever served this, so a pending (dirty) or failing
    # edit never reaches — or removes the zone from — the nameservers.
    published_content = models.TextField(blank=True, default='')
    published_serial = models.PositiveBigIntegerField(null=True, blank=True)
    # Set while a sync_zone task works on the zone, so overlapping dispatches
    # (beat runs every minute) skip it instead of writing concurrently.
    sync_lock_until = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='zones_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'zones'
        indexes = [models.Index(fields=['is_dirty'])]

    def __str__(self):
        return self.name

    # Fields written into the zone file; changing any of them needs a re-sync.
    CONTENT_FIELDS = ('name', 'refresh', 'retry', 'expire', 'minimum_ttl', 'default_ttl')

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if self.pk is not None and not self._state.adding and (
                update_fields is None or set(update_fields) & set(self.CONTENT_FIELDS)):
            old = Zone.objects.filter(pk=self.pk).values(*self.CONTENT_FIELDS).first()
            if old and any(old[f] != getattr(self, f) for f in self.CONTENT_FIELDS):
                self.is_dirty = True
                if update_fields is not None:
                    kwargs['update_fields'] = set(update_fields) | {'is_dirty', 'updated_at'}
        super().save(*args, **kwargs)

    def mark_dirty(self):
        self.is_dirty = True
        self.save(update_fields=['is_dirty', 'updated_at'])


@receiver(m2m_changed, sender=Zone.nameservers.through)
def _nameservers_changed(sender, instance, action, reverse, pk_set, **kwargs):
    """Assigning or removing nameservers changes the zone's NS records."""
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    if not reverse:                       # zone.nameservers.add/remove/set/clear
        _mark_zones_dirty(Zone.objects.filter(pk=instance.pk))
    elif pk_set:                          # nameserver.zones.add/remove
        _mark_zones_dirty(Zone.objects.filter(pk__in=pk_set))
    elif action == 'post_clear':          # nameserver.zones.clear(): pk_set unknown
        _mark_zones_dirty(Zone.objects.all())


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
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='records_created')
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
        # calls the same validators itself.
        errors = {}
        try:
            validate_record_value(self.record_type, self.value)
            if self.zone_id:
                self.value = normalize_target(self.record_type, self.value, self.zone.name)
        except ValidationError as e:
            errors['value'] = e.messages
        try:
            validate_priority(self.record_type, self.priority)
        except ValidationError as e:
            errors['priority'] = e.messages
        if self.zone_id:
            try:
                self.name = normalize_owner(self.name, self.zone.name)
            except ValidationError as e:
                errors['name'] = e.messages
        if errors:
            raise ValidationError(errors)
        if self.zone_id and self.is_active:
            validate_record_conflicts(self.zone, self.record_type, self.name,
                                      self.value, self.priority, exclude_pk=self.pk)

    def save(self, *args, **kwargs):
        old_zone_id = (Record.objects.filter(pk=self.pk).values_list('zone_id', flat=True).first()
                       if self.pk else None)
        super().save(*args, **kwargs)
        self.zone.mark_dirty()
        if old_zone_id and old_zone_id != self.zone_id:
            # Moved to another zone: the old one no longer has it
            _mark_zones_dirty(Zone.objects.filter(pk=old_zone_id))

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
