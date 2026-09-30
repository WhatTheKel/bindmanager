"""
Hostname targets (CNAME, NS, PTR, MX, SRV) without a trailing dot are
relative to the zone in a zone file, so "www.example.com" in zone
example.com would become "www.example.com.example.com.". Targets ending
with the zone's name get the missing dot; other dotted names without one
are rejected rather than guessed at.
"""
import pytest
from django.core.exceptions import ValidationError

from apps.api.v1.serializers import RecordSerializer
from apps.dns_manager.forms import RecordForm
from apps.dns_manager.models import Record
from apps.dns_manager.validators import normalize_target


class TestNormalizeTarget:
    @pytest.mark.parametrize('record_type, value, expected', [
        ('CNAME', 'www.example.com', 'www.example.com.'),
        ('CNAME', 'WWW.Example.COM', 'WWW.Example.COM.'),
        ('CNAME', 'example.com', 'example.com.'),
        ('MX', 'mail.example.com', 'mail.example.com.'),
        ('NS', 'ns2.example.com', 'ns2.example.com.'),
        ('SRV', '5 5060 sip.example.com', '5 5060 sip.example.com.'),
    ])
    def test_in_zone_name_gets_trailing_dot(self, record_type, value, expected):
        assert normalize_target(record_type, value, 'example.com') == expected

    @pytest.mark.parametrize('record_type, value', [
        ('CNAME', 'www'),                      # relative, single label
        ('CNAME', 'www.example.com.'),         # already absolute
        ('CNAME', 'www.other.org.'),           # outside host, absolute
        ('CNAME', '@'),
        ('MX', 'mail'),
        ('SRV', '5 5060 sip.other.org.'),
        ('A', '192.0.2.1'),                    # not a hostname type
        ('TXT', 'hello.world'),
    ])
    def test_unambiguous_values_unchanged(self, record_type, value):
        assert normalize_target(record_type, value, 'example.com') == value

    @pytest.mark.parametrize('record_type, value', [
        ('CNAME', 'www.other.org'),
        ('CNAME', 'mail.eu'),
        ('CNAME', 'www.notexample.com'),       # suffix match must be on a label boundary
        ('PTR', 'host.example.net'),
        ('SRV', '5 5060 sip.other.org'),
    ])
    def test_ambiguous_dotted_names_rejected(self, record_type, value):
        with pytest.raises(ValidationError) as exc:
            normalize_target(record_type, value, 'example.com')
        assert 'trailing dot' in exc.value.messages[0]

    def test_zone_name_with_trailing_dot(self):
        assert normalize_target('CNAME', 'www.example.com', 'example.com.') == 'www.example.com.'


@pytest.mark.django_db
class TestTargetsThroughFormAndApi:
    def _form(self, zone, **data):
        base = {'name': 'et', 'record_type': 'CNAME', 'is_active': True}
        base.update(data)
        return RecordForm(data=base, instance=Record(zone=zone))

    def test_form_saves_in_zone_target_with_dot(self, zone):
        form = self._form(zone, value='www.example.com')
        assert form.is_valid(), form.errors
        assert form.save().value == 'www.example.com.'

    def test_form_rejects_outside_name_without_dot(self, zone):
        form = self._form(zone, value='www.other.org')
        assert not form.is_valid()
        assert 'value' in form.errors

    def test_api_create_adds_dot(self, zone):
        s = RecordSerializer(data={'zone': zone.pk, 'name': 'et', 'record_type': 'CNAME',
                                   'value': 'www.example.com'})
        assert s.is_valid(), s.errors
        assert s.save().value == 'www.example.com.'

    def test_api_rejects_outside_name_without_dot(self, zone):
        s = RecordSerializer(data={'zone': zone.pk, 'name': 'et', 'record_type': 'CNAME',
                                   'value': 'www.other.org'})
        assert not s.is_valid()
        assert 'value' in s.errors

    def test_duplicate_detected_after_normalising(self, zone):
        Record.objects.create(zone=zone, name='et', record_type='CNAME', value='www.example.com.')
        form = self._form(zone, name='et2', value='www.example.com')
        assert form.is_valid(), form.errors   # different owner, fine
        form = self._form(zone, value='www.example.com')
        assert not form.is_valid()            # same CNAME again
