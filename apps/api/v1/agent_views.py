import re

from django.utils import timezone
from rest_framework import serializers, throttling
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.exceptions import NotFound

from apps.dns_manager.models import Zone
from .agent_auth import NameServerKeyAuthentication, IsNameServerAgent


class AgentRateThrottle(throttling.SimpleRateThrottle):
    """
    'agent' rate from DEFAULT_THROTTLE_RATES, counted per NameServer.

    Not ScopedRateThrottle: that reads the scope from the view's
    `throttle_scope` and silently allows everything when it's unset. Agent
    requests also have no Django user (request.user is AnonymousUser), so
    the stock throttles would lump every agent behind one IP together.
    """
    scope = 'agent'

    def get_cache_key(self, request, view):
        ident = getattr(request.auth, 'pk', None) or self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


_AGENT_UA_RE = re.compile(r'bindmanager-agent/([0-9A-Za-z.+-]{1,32})')


def record_agent_checkin(request) -> None:
    """Remember when this nameserver's agent last called in, and its version.

    Agents before 0.2.4 send Python's default User-Agent, so their version
    is stored as '' (shown as "unknown"). A queryset update: no save()
    signals, and updated_at stays as the time the nameserver was edited.
    """
    m = _AGENT_UA_RE.search(request.headers.get('User-Agent', ''))
    type(request.auth).objects.filter(pk=request.auth.pk).update(
        agent_version=m.group(1) if m else '', agent_last_seen=timezone.now(),
    )


class AgentZoneListSerializer(serializers.ModelSerializer):
    serial = serializers.IntegerField(source='published_serial')

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

    Every assigned zone that has a published version is listed, with that
    version's serial — including zones with edits still pending (is_dirty)
    or failing central validation. The agent removes any zone missing from
    this list, so leaving a pending zone out would delete it from the
    nameserver until the sync caught up. Agents only ever receive the
    published (validated) content; pending edits appear once they pass.
    """
    authentication_classes = [NameServerKeyAuthentication]
    permission_classes = [IsNameServerAgent]
    throttle_classes = [AgentRateThrottle]
    serializer_class = AgentZoneListSerializer
    pagination_class = None

    def get_queryset(self):
        return self.request.auth.zones.filter(published_serial__isnull=False).order_by('name')

    def list(self, request, *args, **kwargs):
        # Every agent run starts with this call, so it doubles as a check-in
        record_agent_checkin(request)
        return super().list(request, *args, **kwargs)


class AgentZoneDetailView(RetrieveAPIView):
    """
    GET /api/v1/agent/zones/<name>/

    Returns the zone's published content: the exact file that last passed
    central validation, at its serial, so the agent can validate-and-write
    it locally. Unaffected by edits that are still pending.
    """
    authentication_classes = [NameServerKeyAuthentication]
    permission_classes = [IsNameServerAgent]
    throttle_classes = [AgentRateThrottle]
    serializer_class = AgentZoneContentSerializer
    lookup_field = 'name'
    lookup_url_kwarg = 'name'
    pagination_class = None

    def get_queryset(self):
        return self.request.auth.zones.filter(published_serial__isnull=False)

    def get_object(self):
        try:
            zone = self.get_queryset().get(name=self.kwargs['name'])
        except Zone.DoesNotExist:
            raise NotFound('Zone not found or not assigned to this nameserver')
        return {
            'name': zone.name,
            'serial': zone.published_serial,
            'content': zone.published_content,
        }
