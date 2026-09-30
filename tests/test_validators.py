"""
Tests for record value validation and zone-check error reporting:
- A / AAAA values must be real IP addresses (e.g. 192.0.2.266 is rejected)
- NS / CNAME / PTR / MX values must be hostnames, not IPs — `ns1 NS <ip>`
  only warns in named-checkzone but breaks resolution of the name
- The same rules apply through the manage UI form and the REST API
- named-checkzone failures include its stdout, where it prints the reason
"""
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from apps.api.v1.serializers import RecordSerializer
from apps.dns_manager.forms import RecordForm
from apps.dns_manager.models import Record
from apps.dns_manager.validators import validate_record_value
from apps.dns_manager.zone_engine.writer import _validate


# ── validate_record_value ────────────────────────────────────────────────────

class TestValidateRecordValue:
    @pytest.mark.parametrize('record_type,value', [
        ('A', '192.0.2.10'),
        ('AAAA', '2001:db8::1'),
        ('NS', 'ns1.example.com.'),
        ('NS', 'ns1'),
        ('CNAME', 'www.example.com.'),
        ('MX', 'mail.example.com.'),
        ('PTR', 'host.example.com.'),
        ('TXT', '192.0.2.10'),          # free text — not checked
    ])
    def test_valid_values_pass(self, record_type, value):
        validate_record_value(record_type, value)

    @pytest.mark.parametrize('record_type,value', [
        ('A', '192.0.2.266'),
        ('A', 'www.example.com'),
        ('A', '2001:db8::1'),
        ('AAAA', '192.0.2.10'),
        ('AAAA', 'not-an-ip'),
    ])
    def test_invalid_addresses_rejected(self, record_type, value):
        with pytest.raises(ValidationError):
            validate_record_value(record_type, value)

    @pytest.mark.parametrize('record_type', ['NS', 'CNAME', 'PTR', 'MX'])
    @pytest.mark.parametrize('value', ['192.0.2.20', '192.0.2.20.', '2001:db8::1'])
    def test_ip_rejected_for_hostname_types(self, record_type, value):
        with pytest.raises(ValidationError, match='A \\(or AAAA\\) record'):
            validate_record_value(record_type, value)


# ── Manage UI form (via Record.clean) ────────────────────────────────────────

@pytest.mark.django_db
class TestRecordForm:
    def _form(self, zone, **overrides):
        data = {'name': 'ns1', 'record_type': 'A', 'value': '192.0.2.20', 'is_active': True}
        data.update(overrides)
        return RecordForm(data=data, instance=Record(zone=zone))

    def test_valid_a_record(self, zone):
        assert self._form(zone).is_valid()

    def test_ns_with_ip_shows_error_on_value(self, zone):
        form = self._form(zone, record_type='NS')
        assert not form.is_valid()
        assert 'value' in form.errors

    def test_out_of_range_ip_rejected(self, zone):
        form = self._form(zone, name='www', value='192.0.2.266')
        assert not form.is_valid()
        assert 'value' in form.errors


# ── REST API serializer ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRecordSerializer:
    def test_create_rejects_ns_with_ip(self, zone):
        s = RecordSerializer(data={
            'zone': zone.pk, 'name': 'ns1', 'record_type': 'NS', 'value': '192.0.2.20',
        })
        assert not s.is_valid()
        assert 'value' in s.errors

    def test_partial_update_checks_against_existing_type(self, zone):
        rec = Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.10')
        s = RecordSerializer(rec, data={'value': '192.0.2.266'}, partial=True)
        assert not s.is_valid()
        assert 'value' in s.errors

    def test_partial_update_valid(self, zone):
        rec = Record.objects.create(zone=zone, name='www', record_type='A', value='192.0.2.10')
        s = RecordSerializer(rec, data={'value': '192.0.2.11'}, partial=True)
        assert s.is_valid(), s.errors


# ── named-checkzone error reporting ──────────────────────────────────────────

class TestCheckzoneErrorMessage:
    def test_failure_includes_stdout_reason(self, tmp_path):
        failed = CompletedProcess(
            args=[], returncode=1,
            stdout="zone example.com/IN: bad dotted quad '192.0.2.266'\n", stderr='',
        )
        with patch('apps.dns_manager.zone_engine.writer.subprocess.run', return_value=failed):
            with pytest.raises(ValueError, match='bad dotted quad'):
                _validate('example.com', tmp_path / 'example.com.zone')
