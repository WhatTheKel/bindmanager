from rest_framework import serializers, throttling
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.exceptions import NotFound

from apps.dns_manager.models import Zone
from apps.dns_manager.zone_engine.generator import render_zone
from .agent_auth import NameServerKeyAuthentication, IsNameServerAgent


class AgentRateThrottle(throttling.ScopedRateThrottle):
    scope = 'agent'


class AgentZoneListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Zone
        fields = ['name', 'zone_type', 'serial']


class AgentZoneContentSerializer(serializers.Serializer):
    name = serializers.CharField()
    serial = serializers.IntegerField()
    content = serializers.CharField()


class AgentZoneListView(ListAPIView):
    """
    GET /api/v1/agent/zones/

    Called by the pull agent script on each physical nameserver. Returns the
    lightweight {name, zone_type, serial} for every zone assigned to *this*
    NameServer (scoped by API key) so the agent can diff against what it has
    on disk and only fetch full content for zones that actually changed.

    Only zones that have already passed the central Celery sync pipeline's
    named-checkzone validation (is_dirty=False) are ever listed here — an
    agent should never pull content that hasn't been validated centrally yet.
    """
    authentication_classes = [NameServerKeyAuthentication]
    permission_classes = [IsNameServerAgent]
    throttle_classes = [AgentRateThrottle]
    serializer_class = AgentZoneListSerializer
    pagination_class = None

    def get_queryset(self):
        return self.request.auth.zones.filter(is_dirty=False).order_by('name')


class AgentZoneDetailView(RetrieveAPIView):
    """
    GET /api/v1/agent/zones/<name>/

    Returns the fully rendered zone file content at the zone's current
    serial (not bumped — see zone_engine.generator.render_zone) so the agent
    can validate-and-write it locally.
    """
    authentication_classes = [NameServerKeyAuthentication]
    permission_classes = [IsNameServerAgent]
    throttle_classes = [AgentRateThrottle]
    serializer_class = AgentZoneContentSerializer
    lookup_field = 'name'
    lookup_url_kwarg = 'name'
    pagination_class = None

    def get_queryset(self):
        return self.request.auth.zones.filter(is_dirty=False)

    def get_object(self):
        try:
            zone = self.get_queryset().get(name=self.kwargs['name'])
        except Zone.DoesNotExist:
            raise NotFound('Zone not found or not assigned to this nameserver')
        return {
            'name': zone.name,
            'serial': zone.serial,
            'content': render_zone(zone),
        }
