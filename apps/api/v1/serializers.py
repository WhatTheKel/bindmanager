from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from apps.dns_manager.models import Zone, Record, NameServer, AuditLog
from apps.dns_manager.ptr import reverse_zone_for_subnet, snapshot, sync_ptr
from apps.dns_manager.validators import (
    normalize_owner, normalize_target, normalize_zone_name, validate_priority,
    validate_record_conflicts, validate_record_value,
)


class NameServerSerializer(serializers.ModelSerializer):
    agent_status = serializers.CharField(read_only=True)

    class Meta:
        model = NameServer
        fields = ['id', 'name', 'address', 'config_dir', 'is_active',
                  'agent_version', 'agent_last_seen', 'agent_status']
        read_only_fields = ['agent_version', 'agent_last_seen']


class RecordSerializer(serializers.ModelSerializer):
    zone_name = serializers.CharField(source='zone.name', read_only=True)
    # Write-only: on an A/AAAA record, create or update its PTR record too.
    sync_ptr = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Record
        fields = [
            'id', 'zone', 'zone_name', 'name', 'record_type',
            'ttl', 'value', 'priority', 'is_active', 'created_at', 'sync_ptr',
        ]
        read_only_fields = ['created_at', 'zone_name']

    ptr_changes = ()

    def create(self, validated_data):
        do_sync = validated_data.pop('sync_ptr', False)
        record = super().create(validated_data)
        if do_sync:
            self.ptr_changes = sync_ptr(record, user=validated_data.get('created_by'))
        return record

    def update(self, instance, validated_data):
        do_sync = validated_data.pop('sync_ptr', False)
        before = snapshot(instance)
        record = super().update(instance, validated_data)
        if do_sync:
            request = self.context.get('request')
            self.ptr_changes = sync_ptr(record, user=getattr(request, 'user', None), old=before)
        return record

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if self.ptr_changes:
            data['ptr'] = [{'action': c.action, 'message': c.message} for c in self.ptr_changes]
        return data

    def validate(self, attrs):
        # PATCH may send only some fields — fall back to the instance.
        def field(name, default=None):
            return attrs.get(name, getattr(self.instance, name, default))

        record_type, value, name = field('record_type'), field('value'), field('name')
        zone = field('zone')
        errors = {}
        try:
            validate_record_value(record_type, value)
            if zone is not None:
                value = normalize_target(record_type, value, zone.name)
        except DjangoValidationError as e:
            errors['value'] = e.messages
        try:
            validate_priority(record_type, field('priority'))
        except DjangoValidationError as e:
            errors['priority'] = e.messages
        if zone is not None:
            try:
                name = normalize_owner(name, zone.name)
            except DjangoValidationError as e:
                errors['name'] = e.messages
        if errors:
            raise serializers.ValidationError(errors)
        if value != field('value'):
            attrs['value'] = value
        if name != field('name'):
            attrs['name'] = name

        if zone is not None and field('is_active', True):
            try:
                validate_record_conflicts(
                    zone, record_type, field('name'), value, field('priority'),
                    exclude_pk=getattr(self.instance, 'pk', None),
                )
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.message_dict)
        return attrs


# Lightweight serializer used in zone list — no nested records
class ZoneListSerializer(serializers.ModelSerializer):
    record_count = serializers.IntegerField(read_only=True)
    ns_count     = serializers.IntegerField(read_only=True)

    class Meta:
        model  = Zone
        fields = [
            'id', 'name', 'zone_type', 'ip_version', 'serial',
            'is_dirty', 'record_count', 'ns_count', 'updated_at',
        ]


# Full serializer used for retrieve / create / update
class ZoneDetailSerializer(serializers.ModelSerializer):
    records      = RecordSerializer(many=True, read_only=True)
    nameservers  = NameServerSerializer(many=True, read_only=True)
    # Write-only field: pass a list of nameserver IDs to assign them
    nameserver_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=NameServer.objects.all(),
        write_only=True,
        required=False,
    )
    # Write-only: a subnet (192.0.2.0/24, 2001:db8::/32) to derive a reverse
    # zone's name, zone_type and ip_version from, instead of sending them.
    subnet = serializers.CharField(write_only=True, required=False)

    class Meta:
        model  = Zone
        fields = [
            'id', 'name', 'zone_type', 'ip_version', 'serial',
            'refresh', 'retry', 'expire', 'minimum_ttl', 'default_ttl',
            'is_dirty', 'nameservers', 'nameserver_ids', 'records',
            'created_at', 'updated_at', 'subnet',
        ]
        read_only_fields = ['serial', 'is_dirty', 'created_at', 'updated_at']
        extra_kwargs = {'name': {'required': False}}

    def validate_name(self, value):
        try:
            return normalize_zone_name(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)

    def validate(self, attrs):
        subnet = attrs.pop('subnet', '').strip()
        if subnet:
            try:
                name, version = reverse_zone_for_subnet(subnet)
            except DjangoValidationError as e:
                raise serializers.ValidationError({'subnet': e.messages})
            if attrs.get('name') and attrs['name'] != name:
                raise serializers.ValidationError({'name': [
                    f'{subnet} is the reverse zone "{name}", not "{attrs["name"]}". '
                    f'Send the name or the subnet, not both.'
                ]})
            if Zone.objects.filter(name=name).exclude(pk=getattr(self.instance, 'pk', None)).exists():
                raise serializers.ValidationError({'name': [f'Zone "{name}" already exists.']})
            attrs.update(name=name, zone_type=Zone.ZoneType.REVERSE, ip_version=version)
        elif self.instance is None and not attrs.get('name'):
            raise serializers.ValidationError({'name': ['Send a zone name, or a subnet for a reverse zone.']})
        return attrs

    def create(self, validated_data):
        ns_list = validated_data.pop('nameserver_ids', [])
        zone = super().create(validated_data)
        if ns_list:
            zone.nameservers.set(ns_list)
        return zone

    def update(self, instance, validated_data):
        ns_list = validated_data.pop('nameserver_ids', None)
        zone = super().update(instance, validated_data)
        if ns_list is not None:
            zone.nameservers.set(ns_list)
        return zone


class AuditLogSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True, default='system')

    class Meta:
        model  = AuditLog
        fields = ['id', 'action', 'entity_type', 'entity_id', 'detail', 'username', 'created_at']
        read_only_fields = fields
