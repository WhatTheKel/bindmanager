from django.contrib import admin
from .models import NameServer, Zone, Record, AuditLog


@admin.register(NameServer)
class NameServerAdmin(admin.ModelAdmin):
    list_display = ('name', 'address', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    readonly_fields = ('api_key',)
    actions = ['regenerate_api_key']

    @admin.action(description='Regenerate API key (invalidates the old one)')
    def regenerate_api_key(self, request, queryset):
        for ns in queryset:
            ns.regenerate_api_key()


class RecordInline(admin.TabularInline):
    model = Record
    extra = 0
    fields = ('name', 'record_type', 'ttl', 'priority', 'value', 'is_active')


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'zone_type', 'ip_version', 'is_dirty', 'serial', 'updated_at')
    list_filter = ('zone_type', 'ip_version', 'is_dirty')
    search_fields = ('name',)
    inlines = [RecordInline]
    actions = ['mark_dirty']

    @admin.action(description='Mark selected zones as dirty (queue for sync)')
    def mark_dirty(self, request, queryset):
        queryset.update(is_dirty=True)


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin):
    list_display = ('zone', 'name', 'record_type', 'ttl', 'value', 'is_active')
    list_filter = ('record_type', 'is_active')
    search_fields = ('name', 'value', 'zone__name')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'action', 'entity_type', 'entity_id')
    list_filter = ('action', 'entity_type')
    readonly_fields = ('created_at', 'user', 'action', 'entity_type', 'entity_id', 'detail')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
