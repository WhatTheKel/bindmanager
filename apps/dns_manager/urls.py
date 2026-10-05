from django.urls import path
from django.views.generic import RedirectView
from . import views, manage_views

app_name = 'dns_manager'

urlpatterns = [
    # Public views
    path('', views.zone_list, name='zone_list'),
    path('zones/<int:pk>/', views.zone_detail, name='zone_detail'),

    # Dashboard — standalone page, no manage sidebar
    path('dashboard/', manage_views.dashboard, name='manage_dashboard'),

    # Custom management panel (/manage/ redirects to zones as the natural entry point)
    path('manage/', RedirectView.as_view(pattern_name='dns_manager:manage_zone_list', permanent=False)),

    path('manage/zones/', manage_views.zone_list, name='manage_zone_list'),
    path('manage/zones/add/', manage_views.zone_add, name='manage_zone_add'),
    path('manage/zones/<int:pk>/', manage_views.zone_detail, name='manage_zone_detail'),
    path('manage/zones/<int:pk>/edit/', manage_views.zone_edit, name='manage_zone_edit'),
    path('manage/zones/<int:pk>/delete/', manage_views.zone_delete, name='manage_zone_delete'),

    path('manage/zones/<int:zone_pk>/records/add/', manage_views.record_add, name='manage_record_add'),
    path('manage/zones/<int:zone_pk>/records/<int:pk>/edit/', manage_views.record_edit, name='manage_record_edit'),
    path('manage/zones/<int:zone_pk>/records/<int:pk>/delete/', manage_views.record_delete, name='manage_record_delete'),

    path('manage/nameservers/', manage_views.nameserver_list, name='manage_nameserver_list'),
    path('manage/nameservers/add/', manage_views.nameserver_add, name='manage_nameserver_add'),
    path('manage/nameservers/<int:pk>/edit/', manage_views.nameserver_edit, name='manage_nameserver_edit'),
    path('manage/nameservers/<int:pk>/delete/', manage_views.nameserver_delete, name='manage_nameserver_delete'),
    path('manage/nameservers/<int:pk>/regenerate-key/', manage_views.nameserver_regenerate_key,
         name='manage_nameserver_regenerate_key'),

    path('manage/audit/', manage_views.audit_log, name='manage_audit_log'),

    path('manage/users/', manage_views.user_list, name='manage_user_list'),
    path('manage/users/add/', manage_views.user_add, name='manage_user_add'),
    path('manage/users/<int:pk>/edit/', manage_views.user_edit, name='manage_user_edit'),
    path('manage/users/<int:pk>/delete/', manage_views.user_delete, name='manage_user_delete'),
]
