import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.dns_manager.forms import ZoneForm
from apps.dns_manager.models import AuditLog, Record, Zone
from apps.dns_manager.ptr import find_reverse_zone, reverse_zone_for_subnet, snapshot, sync_ptr


class TestReverseZoneForSubnet:
    @pytest.mark.parametrize('subnet, expected', [
        ('192.0.2.0/24', ('2.0.192.in-addr.arpa', 4)),
        ('192.0.2.77/24', ('2.0.192.in-addr.arpa', 4)),     # host bits ignored
        ('10.20.0.0/16', ('20.10.in-addr.arpa', 4)),
        ('10.0.0.0/8', ('10.in-addr.arpa', 4)),
        ('2001:db8::/32', ('8.b.d.0.1.0.0.2.ip6.arpa', 6)),
        ('2001:db8:abcd::/48', ('d.c.b.a.8.b.d.0.1.0.0.2.ip6.arpa', 6)),
        (' 2001:DB8::/36 ', ('0.8.b.d.0.1.0.0.2.ip6.arpa', 6)),
    ])
    def test_derives_zone(self, subnet, expected):
        assert reverse_zone_for_subnet(subnet) == expected

    @pytest.mark.parametrize('subnet', ['192.0.2.0/22', '192.0.2.0/0', '2001:db8::/33', 'nope', ''])
    def test_rejects(self, subnet):
        with pytest.raises(ValidationError):
            reverse_zone_for_subnet(subnet)


@pytest.mark.django_db
class TestZoneFormSubnet:
    def test_fills_name_type_and_version(self):
        form = ZoneForm(data=_zone_data(name='', subnet='2001:db8::/32', zone_type='forward', ip_version=4))
        assert form.is_valid(), form.errors
        zone = form.save()
        assert (zone.name, zone.zone_type, zone.ip_version) == ('8.b.d.0.1.0.0.2.ip6.arpa', 'reverse', 6)

    def test_matching_name_is_fine(self):
        form = ZoneForm(data=_zone_data(name='2.0.192.in-addr.arpa.', subnet='192.0.2.0/24'))
        assert form.is_valid(), form.errors

    def test_conflicting_name_rejected(self):
        form = ZoneForm(data=_zone_data(name='example.com', subnet='192.0.2.0/24'))
        assert not form.is_valid()
        assert 'name' in form.errors

    def test_needs_name_or_subnet(self):
        form = ZoneForm(data=_zone_data(name=''))
        assert not form.is_valid()
        assert 'name' in form.errors

    def test_bad_subnet(self):
        form = ZoneForm(data=_zone_data(name='', subnet='192.0.2.0/22'))
        assert not form.is_valid()
        assert 'subnet' in form.errors

    def test_duplicate_derived_name(self):
        Zone.objects.create(name='2.0.192.in-addr.arpa', zone_type='reverse')
        form = ZoneForm(data=_zone_data(name='', subnet='192.0.2.0/24'))
        assert not form.is_valid()
        assert 'name' in form.errors


def _zone_data(**over):
    data = {'name': 'example.com', 'zone_type': 'forward', 'ip_version': 4, 'subnet': '',
            'refresh': 3600, 'retry': 900, 'expire': 604800, 'minimum_ttl': 86400, 'default_ttl': 3600}
    data.update(over)
    return data


@pytest.fixture
def rev4(db):
    return Zone.objects.create(name='2.0.192.in-addr.arpa', zone_type='reverse')


def _a(zone, name='www', ip='192.0.2.25', rtype='A', **kw):
    return Record.objects.create(zone=zone, name=name, record_type=rtype, value=ip, **kw)


def _ptrs(zone):
    return list(zone.records.filter(record_type='PTR').values_list('name', 'value'))


@pytest.mark.django_db
class TestSyncPtr:
    def test_find_reverse_zone_picks_most_specific(self, rev4):
        wide = Zone.objects.create(name='192.in-addr.arpa', zone_type='reverse')
        assert find_reverse_zone('192.0.2.25') == (rev4, '25')
        assert find_reverse_zone('192.0.3.1') == (wide, '1.3.0')
        assert find_reverse_zone('198.51.100.1') == (None, None)

    def test_creates_ptr(self, zone, rev4):
        rec = _a(zone, ttl=300)
        changes = sync_ptr(rec)
        assert [c.action for c in changes] == ['create']
        ptr = rev4.records.get(record_type='PTR')
        assert (ptr.name, ptr.value, ptr.ttl) == ('25', 'www.example.com.', 300)

    def test_apex_name(self, zone, rev4):
        sync_ptr(_a(zone, name='@'))
        assert _ptrs(rev4) == [('25', 'example.com.')]

    def test_ipv6(self, zone):
        rev6 = Zone.objects.create(name='8.b.d.0.1.0.0.2.ip6.arpa', zone_type='reverse', ip_version=6)
        sync_ptr(_a(zone, rtype='AAAA', ip='2001:DB8::1'))
        assert _ptrs(rev6) == [('1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0', 'www.example.com.')]

    def test_already_there(self, zone, rev4):
        rec = _a(zone)
        sync_ptr(rec)
        assert [c.action for c in sync_ptr(rec)] == ['info']
        assert len(_ptrs(rev4)) == 1

    def test_updates_single_existing_ptr(self, zone, rev4):
        Record.objects.create(zone=rev4, name='25', record_type='PTR', value='old.example.com.')
        changes = sync_ptr(_a(zone))
        assert [c.action for c in changes] == ['update']
        assert _ptrs(rev4) == [('25', 'www.example.com.')]

    def test_leaves_multiple_existing_ptrs(self, zone, rev4):
        Record.objects.create(zone=rev4, name='25', record_type='PTR', value='a.example.com.')
        Record.objects.create(zone=rev4, name='25', record_type='PTR', value='b.example.com.')
        assert [c.action for c in sync_ptr(_a(zone))] == ['warning']
        assert len(_ptrs(rev4)) == 2

    def test_creates_missing_reverse_zone(self, zone, nameserver, staff_user):
        changes = sync_ptr(_a(zone), user=staff_user)
        assert [(c.action, c.entity_type) for c in changes] == [('create', 'zone'), ('create', 'record')]
        rev = Zone.objects.get(name='2.0.192.in-addr.arpa')
        assert (rev.zone_type, rev.ip_version, rev.created_by) == ('reverse', 4, staff_user)
        assert list(rev.nameservers.all()) == [nameserver]
        assert _ptrs(rev) == [('25', 'www.example.com.')]
        assert changes[0].entity_id == rev.pk

    def test_creates_missing_ipv6_reverse_zone_as_64(self, zone):
        sync_ptr(_a(zone, rtype='AAAA', ip='2001:db8:0:1::5'))
        rev = Zone.objects.get(ip_version=6)
        assert rev.name == '1.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa'
        assert _ptrs(rev) == [('5.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0', 'www.example.com.')]

    def test_auto_zone_without_nameservers_warns(self, db):
        bare = Zone.objects.create(name='example.org')
        changes = sync_ptr(_a(bare))
        assert [c.action for c in changes] == ['create', 'warning', 'create']
        assert "can't sync yet" in changes[1].message

    def test_absolute_legacy_name(self, zone, rev4):
        rec = Record.objects.create(zone=zone, name='old.example.com.', record_type='A', value='192.0.2.25')
        Record.objects.create(zone=rev4, name='25', record_type='PTR', value='old.example.com.')
        before = snapshot(rec)
        Record.objects.filter(pk=rec.pk).update(name='old', value='192.0.2.26')
        rec.refresh_from_db()
        assert [c.action for c in sync_ptr(rec, old=before)] == ['delete', 'create']
        assert _ptrs(rev4) == [('26', 'old.example.com.')]

    def test_uses_wider_existing_zone_instead_of_creating(self, zone):
        wide = Zone.objects.create(name='192.in-addr.arpa', zone_type='reverse')
        sync_ptr(_a(zone))
        assert Zone.objects.filter(zone_type='reverse').count() == 1
        assert _ptrs(wide) == [('25.2.0', 'www.example.com.')]

    def test_skips_wildcard_inactive_and_other_types(self, zone, rev4):
        assert sync_ptr(_a(zone, name='*'))[0].action == 'warning'
        assert sync_ptr(_a(zone, name='off', ip='192.0.2.9', is_active=False))[0].action == 'info'
        assert sync_ptr(Record.objects.create(zone=zone, name='m', record_type='CNAME', value='www')) == []
        assert _ptrs(rev4) == []

    def test_ip_change_moves_ptr(self, zone, rev4):
        rec = _a(zone)
        sync_ptr(rec)
        before = snapshot(rec)
        rec.value = '192.0.2.26'
        rec.save()
        actions = [c.action for c in sync_ptr(rec, old=before)]
        assert actions == ['delete', 'create']
        assert _ptrs(rev4) == [('26', 'www.example.com.')]

    def test_ip_change_keeps_other_hosts_ptr(self, zone, rev4):
        Record.objects.create(zone=rev4, name='25', record_type='PTR', value='other.example.com.')
        rec = _a(zone)
        before = snapshot(rec)
        rec.value = '192.0.2.26'
        rec.save()
        sync_ptr(rec, old=before)
        assert sorted(_ptrs(rev4)) == [('25', 'other.example.com.'), ('26', 'www.example.com.')]

    def test_rename_updates_ptr(self, zone, rev4):
        rec = _a(zone)
        sync_ptr(rec)
        before = snapshot(rec)
        rec.name = 'web'
        rec.save()
        assert [c.action for c in sync_ptr(rec, old=before)] == ['update']
        assert _ptrs(rev4) == [('25', 'web.example.com.')]

    def test_marks_reverse_zone_dirty(self, zone, rev4):
        Zone.objects.filter(pk=rev4.pk).update(is_dirty=False)
        sync_ptr(_a(zone))
        rev4.refresh_from_db()
        assert rev4.is_dirty


@pytest.mark.django_db
class TestManageViews:
    def test_add_record_with_ptr(self, client, staff_user, zone, rev4):
        client.force_login(staff_user)
        resp = client.post(reverse('dns_manager:manage_record_add', args=[zone.pk]), {
            'name': 'www', 'record_type': 'A', 'value': '192.0.2.25', 'is_active': 'on', 'sync_ptr': 'on',
        })
        assert resp.status_code == 302
        assert _ptrs(rev4) == [('25', 'www.example.com.')]
        log = AuditLog.objects.get(detail__startswith='Added PTR')
        assert log.entity_id == rev4.records.get(record_type='PTR').pk
        assert '→ www.example.com. (from' in log.detail   # not "…com.." (fqdn + full stop)

    def test_add_record_creates_reverse_zone(self, client, staff_user, zone):
        client.force_login(staff_user)
        client.post(reverse('dns_manager:manage_record_add', args=[zone.pk]), {
            'name': 'web', 'record_type': 'A', 'value': '198.51.100.4', 'is_active': 'on', 'sync_ptr': 'on',
        })
        rev = Zone.objects.get(name='100.51.198.in-addr.arpa')
        assert _ptrs(rev) == [('4', 'web.example.com.')]
        assert AuditLog.objects.filter(entity_type='zone', entity_id=rev.pk,
                                       detail__startswith='Created reverse zone').exists()

    def test_ptr_failure_rolls_back_record(self, client, staff_user, zone, monkeypatch):
        import apps.dns_manager.manage_views as mv
        def boom(*a, **k):
            raise RuntimeError('boom')
        monkeypatch.setattr(mv, 'sync_ptr', boom)
        client.force_login(staff_user)
        client.raise_request_exception = False
        resp = client.post(reverse('dns_manager:manage_record_add', args=[zone.pk]), {
            'name': 'www', 'record_type': 'A', 'value': '192.0.2.25', 'is_active': 'on', 'sync_ptr': 'on',
        })
        assert resp.status_code == 500
        assert not Record.objects.filter(name='www').exists()

    def test_add_form_defaults_to_a(self, client, staff_user, zone):
        client.force_login(staff_user)
        resp = client.get(reverse('dns_manager:manage_record_add', args=[zone.pk]))
        assert b'<option value="A" selected>' in resp.content
        assert b'id_sync_ptr' in resp.content
        assert b'id="ptr-group">' in resp.content            # shown, not hidden
        assert b'id="priority-group" hidden>' in resp.content
        assert b'.ptr-row' not in resp.content                # no CSS rule hides the rows

    def test_edit_mx_form_shows_priority(self, client, staff_user, zone):
        rec = Record.objects.create(zone=zone, name='@', record_type='MX', value='mail', priority=10)
        client.force_login(staff_user)
        resp = client.get(reverse('dns_manager:manage_record_edit', args=[zone.pk, rec.pk]))
        assert b'id="priority-group">' in resp.content
        assert b'id="ptr-group" hidden>' in resp.content

    def test_add_record_without_ptr(self, client, staff_user, zone, rev4):
        client.force_login(staff_user)
        client.post(reverse('dns_manager:manage_record_add', args=[zone.pk]), {
            'name': 'www', 'record_type': 'A', 'value': '192.0.2.25', 'is_active': 'on',
        })
        assert _ptrs(rev4) == []

    def test_edit_record_moves_ptr(self, client, staff_user, zone, rev4):
        rec = _a(zone)
        sync_ptr(rec)
        client.force_login(staff_user)
        client.post(reverse('dns_manager:manage_record_edit', args=[zone.pk, rec.pk]), {
            'name': 'www', 'record_type': 'A', 'value': '192.0.2.30', 'is_active': 'on', 'sync_ptr': 'on',
        })
        assert _ptrs(rev4) == [('30', 'www.example.com.')]

    def test_add_zone_from_subnet(self, client, staff_user):
        client.force_login(staff_user)
        data = _zone_data(name='', subnet='192.0.2.0/24')
        resp = client.post(reverse('dns_manager:manage_zone_add'), data)
        assert resp.status_code == 302
        assert Zone.objects.get().name == '2.0.192.in-addr.arpa'


@pytest.mark.django_db
class TestApi:
    @pytest.fixture
    def api(self, staff_user):
        c = APIClient()
        c.force_authenticate(staff_user)
        return c

    def test_zone_from_subnet(self, api):
        resp = api.post('/api/v1/zones/', {'subnet': '2001:db8::/32'}, format='json')
        assert resp.status_code == 201, resp.data
        assert (resp.data['name'], resp.data['zone_type'], resp.data['ip_version']) == \
            ('8.b.d.0.1.0.0.2.ip6.arpa', 'reverse', 6)

    def test_zone_needs_name_or_subnet(self, api):
        assert api.post('/api/v1/zones/', {}, format='json').status_code == 400

    def test_zone_subnet_duplicate(self, api, rev4):
        resp = api.post('/api/v1/zones/', {'subnet': '192.0.2.0/24'}, format='json')
        assert resp.status_code == 400

    def test_record_sync_ptr(self, api, zone, rev4):
        resp = api.post('/api/v1/records/', {
            'zone': zone.pk, 'name': 'www', 'record_type': 'A', 'value': '192.0.2.25', 'sync_ptr': True,
        }, format='json')
        assert resp.status_code == 201, resp.data
        assert resp.data['ptr'][0]['action'] == 'create'
        assert _ptrs(rev4) == [('25', 'www.example.com.')]

        resp = api.patch(f'/api/v1/records/{resp.data["id"]}/',
                         {'value': '192.0.2.26', 'sync_ptr': True}, format='json')
        assert resp.status_code == 200, resp.data
        assert [p['action'] for p in resp.data['ptr']] == ['delete', 'create']
        assert _ptrs(rev4) == [('26', 'www.example.com.')]
        deleted = AuditLog.objects.get(action='delete', detail__contains='Removed PTR')
        assert deleted.entity_id is not None

    def test_record_without_sync_ptr(self, api, zone, rev4):
        resp = api.post('/api/v1/records/', {
            'zone': zone.pk, 'name': 'www', 'record_type': 'A', 'value': '192.0.2.25',
        }, format='json')
        assert resp.status_code == 201
        assert 'ptr' not in resp.data
        assert _ptrs(rev4) == []
