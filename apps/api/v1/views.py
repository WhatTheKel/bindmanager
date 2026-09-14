from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render
from rest_framework import viewsets, permissions, filters, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Count, Q

from apps.dns_manager.models import Zone, Record, NameServer, AuditLog
from .serializers import (
    ZoneListSerializer, ZoneDetailSerializer,
    RecordSerializer, NameServerSerializer, AuditLogSerializer,
)


@user_passes_test(lambda u: u.is_superuser)
def api_docs(request):
    return render(request, 'api/docs.html', {
        'base_url': request.build_absolute_uri('/api/'),
    })


class IsStaffOrReadOnly(permissions.BasePermission):
    """Authenticated users can read; only staff can write."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_staff


class AuditMixin:
    """
    Mixin that writes an AuditLog entry for every create / update / destroy
    performed through the API. Set audit_entity_type on the viewset.
    """
    audit_entity_type = ''

    def _audit_label(self, instance):
        return str(instance)

    def _write_log(self, action, entity_id, label):
        AuditLog.objects.create(
            user=self.request.user if self.request.user.is_authenticated else None,
            action=action,
            entity_type=self.audit_entity_type,
            entity_id=entity_id,
            detail=f'[API] {action} {self.audit_entity_type}: {label}',
        )

    def perform_create(self, serializer):
        model = serializer.Meta.model
        field_names = {f.name for f in model._meta.get_fields()}
        kwargs = {'created_by': self.request.user} if 'created_by' in field_names else {}
        instance = serializer.save(**kwargs)
        self._write_log(AuditLog.Action.CREATE, instance.pk, self._audit_label(instance))

    def perform_update(self, serializer):
        instance = serializer.save()
        self._write_log(AuditLog.Action.UPDATE, instance.pk, self._audit_label(instance))

    def perform_destroy(self, instance):
        label = self._audit_label(instance)
        pk    = instance.pk
        instance.delete()
        self._write_log(AuditLog.Action.DELETE, pk, label)


class ZoneViewSet(AuditMixin, viewsets.ModelViewSet):
    audit_entity_type  = 'zone'
    permission_classes = [IsStaffOrReadOnly]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['name']
    ordering_fields    = ['name', 'zone_type', 'updated_at', 'serial']

    def get_queryset(self):
        qs = Zone.objects.order_by('zone_type', 'name')

        zone_type = self.request.query_params.get('zone_type')
        if zone_type in ('forward', 'reverse'):
            qs = qs.filter(zone_type=zone_type)

        is_dirty = self.request.query_params.get('is_dirty')
        if is_dirty in ('true', '1'):
            qs = qs.filter(is_dirty=True)
        elif is_dirty in ('false', '0'):
            qs = qs.filter(is_dirty=False)

        if self.action == 'list':
            return qs.annotate(
                record_count=Count('records', distinct=True),
                ns_count=Count('nameservers', distinct=True),
            )
        return qs.prefetch_related('records', 'nameservers')

    def get_serializer_class(self):
        if self.action == 'list':
            return ZoneListSerializer
        return ZoneDetailSerializer

    def _audit_label(self, instance):
        return instance.name

    @action(detail=True, methods=['get'], url_path='records')
    def zone_records(self, request, pk=None):
        """GET /api/v1/zones/<id>/records/ — records scoped to a single zone."""
        zone = self.get_object()
        qs   = zone.records.order_by('record_type', 'name')

        rt = request.query_params.get('record_type')
        if rt:
            qs = qs.filter(record_type=rt)

        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(value__icontains=search))

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(RecordSerializer(page, many=True).data)
        return Response(RecordSerializer(qs, many=True).data)


class RecordViewSet(AuditMixin, viewsets.ModelViewSet):
    audit_entity_type  = 'record'
    serializer_class   = RecordSerializer
    permission_classes = [IsStaffOrReadOnly]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['name', 'value']
    ordering_fields    = ['name', 'record_type', 'zone__name', 'ttl']

    def get_queryset(self):
        qs = Record.objects.select_related('zone').order_by('zone__name', 'record_type', 'name')

        zone_id = self.request.query_params.get('zone')
        if zone_id:
            qs = qs.filter(zone_id=zone_id)

        rt = self.request.query_params.get('record_type')
        if rt:
            qs = qs.filter(record_type=rt)

        is_active = self.request.query_params.get('is_active')
        if is_active in ('true', '1'):
            qs = qs.filter(is_active=True)
        elif is_active in ('false', '0'):
            qs = qs.filter(is_active=False)

        return qs

    def _audit_label(self, instance):
        return f'{instance.record_type} {instance.name} → {instance.zone.name}'


class NameServerViewSet(AuditMixin, viewsets.ModelViewSet):
    audit_entity_type  = 'nameserver'
    queryset           = NameServer.objects.order_by('name')
    serializer_class   = NameServerSerializer
    permission_classes = [IsStaffOrReadOnly]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['name', 'address']
    ordering_fields    = ['name', 'address', 'is_active']

    def _audit_label(self, instance):
        return instance.name


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only audit log."""
    queryset           = AuditLog.objects.select_related('user').order_by('-created_at')
    serializer_class   = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['entity_type', 'detail']
    ordering_fields    = ['created_at']

    def get_queryset(self):
        qs = super().get_queryset()

        entity_type = self.request.query_params.get('entity_type')
        if entity_type:
            qs = qs.filter(entity_type=entity_type)

        action = self.request.query_params.get('action')
        if action:
            qs = qs.filter(action=action)

        return qs
