from rest_framework import serializers
from apps.dns_manager.models import Zone, Record, NameServer, AuditLog


class NameServerSerializer(serializers.ModelSerializer):
    class Meta:
        model = NameServer
        fields = ['id', 'name', 'address', 'config_dir', 'is_active']


class RecordSerializer(serializers.ModelSerializer):
    zone_name = serializers.CharField(source='zone.name', read_only=True)

    class Meta:
        model = Record
        fields = [
            'id', 'zone', 'zone_name', 'name', 'record_type',
            'ttl', 'value', 'priority', 'is_active', 'created_at',
        ]
        read_only_fields = ['created_at', 'zone_name']


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

    class Meta:
        model  = Zone
        fields = [
            'id', 'name', 'zone_type', 'ip_version', 'serial',
            'refresh', 'retry', 'expire', 'minimum_ttl', 'default_ttl',
            'is_dirty', 'nameservers', 'nameserver_ids', 'records',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['serial', 'is_dirty', 'created_at', 'updated_at']

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
