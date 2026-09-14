from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from . import agent_views


class _NoRootRouter(DefaultRouter):
    include_root_view = False  # we supply our own docs root


router = _NoRootRouter()
router.register('zones',       views.ZoneViewSet,       basename='zone')
router.register('records',     views.RecordViewSet,     basename='record')
router.register('nameservers', views.NameServerViewSet, basename='nameserver')
router.register('audit',       views.AuditLogViewSet,   basename='audit')

urlpatterns = [
    path('', views.api_docs, name='api_docs'),
    # Pull-agent endpoints (apps/api/v1/agent_views.py) — API-key authenticated,
    # not part of the staff/JWT-facing router above.
    path('agent/zones/', agent_views.AgentZoneListView.as_view(), name='agent-zone-list'),
    path('agent/zones/<str:name>/', agent_views.AgentZoneDetailView.as_view(), name='agent-zone-detail'),
    path('', include(router.urls)),
]
