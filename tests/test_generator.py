"""
Tests for the zone file generator: serial bumping, TXT quoting, and full
zone rendering via build_zone().  No database or filesystem required.
"""
from unittest.mock import MagicMock, patch
from datetime import date

import pytest

from apps.dns_manager.zone_engine.generator import _bump_serial, _quote_txt, build_zone, render_zone


# ── _bump_serial ─────────────────────────────────────────────────────────────

class TestBumpSerial:
    def _today_base(self):
        return int(date.today().strftime('%Y%m%d')) * 100

    def test_old_serial_jumps_to_today(self):
        result = _bump_serial(1)
        assert result == self._today_base()

    def test_serial_at_today_base_increments_by_one(self):
        base = self._today_base()
        assert _bump_serial(base) == base + 1

    def test_serial_already_incremented_today_keeps_going(self):
        base = self._today_base()
        assert _bump_serial(base + 5) == base + 6

    def test_serial_never_goes_backwards(self):
        base = self._today_base()
        # Simulated "future" serial (e.g. from a previous day's overflow)
        future = base + 999
        assert _bump_serial(future) == future + 1


# ── _quote_txt ───────────────────────────────────────────────────────────────

class TestQuoteTxt:
    def test_bare_string_gets_quoted(self):
        assert _quote_txt('v=spf1 ~all') == '"v=spf1 ~all"'

    def test_already_quoted_string_is_unchanged(self):
        assert _quote_txt('"v=spf1 ~all"') == '"v=spf1 ~all"'

    def test_leading_trailing_whitespace_stripped(self):
        assert _quote_txt('  hello  ') == '"hello"'

    def test_internal_double_quotes_escaped(self):
        result = _quote_txt('say "hello"')
        assert result == '"say \\"hello\\""'

    def test_empty_string(self):
        assert _quote_txt('') == '""'

    def test_dkim_value_with_semicolons(self):
        val = 'v=DKIM1; k=rsa; p=MIGfMA0'
        assert _quote_txt(val) == f'"{val}"'


# ── build_zone ───────────────────────────────────────────────────────────────

def _make_zone(name='example.com', ns_names=None, records=None, serial=1):
    """Build a minimal mock Zone object accepted by build_zone()."""
    zone = MagicMock()
    zone.name = name
    zone.serial = serial
    zone.default_ttl = 3600
    zone.refresh = 3600
    zone.retry = 900
    zone.expire = 604800
    zone.minimum_ttl = 86400

    ns_objects = []
    for ns_name in (ns_names if ns_names is not None else ['ns1.example.com']):
        ns = MagicMock()
        ns.name = ns_name
        ns_objects.append(ns)
    zone.nameservers.all.return_value = ns_objects

    rec_objects = records or []
    zone.records.filter.return_value.order_by.return_value = rec_objects

    return zone


def _make_record(name='www', record_type='A', value='1.2.3.4', ttl=None, priority=None):
    r = MagicMock()
    r.name = name
    r.record_type = record_type
    r.value = value
    r.ttl = ttl
    r.priority = priority
    r.is_active = True
    return r


class TestBuildZone:

    # SOA correctness

    def test_soa_uses_first_nameserver(self):
        zone = _make_zone(ns_names=['ns1.example.com', 'ns2.example.com'])
        content, _ = build_zone(zone)
        assert 'SOA ns1.example.com.' in content

    def test_soa_falls_back_when_no_nameservers(self):
        zone = _make_zone(ns_names=[])
        content, _ = build_zone(zone)
        assert 'SOA ns1.example.com.' in content

    def test_soa_hostmaster_uses_zone_name(self):
        zone = _make_zone()
        content, _ = build_zone(zone)
        assert 'hostmaster.example.com.' in content

    def test_ns_records_emitted_for_all_nameservers(self):
        zone = _make_zone(ns_names=['ns1.example.com', 'ns2.example.com'])
        content, _ = build_zone(zone)
        assert 'NS  ns1.example.com.' in content
        assert 'NS  ns2.example.com.' in content

    def test_origin_directive(self):
        zone = _make_zone()
        content, _ = build_zone(zone)
        assert '$ORIGIN example.com.' in content

    def test_ttl_directive(self):
        zone = _make_zone()
        content, _ = build_zone(zone)
        assert '$TTL 3600' in content

    # Serial return value

    def test_returns_bumped_serial(self):
        zone = _make_zone(serial=1)
        _, new_serial = build_zone(zone)
        today_base = int(date.today().strftime('%Y%m%d')) * 100
        assert new_serial >= today_base

    def test_serial_appears_in_zone_content(self):
        zone = _make_zone(serial=1)
        content, new_serial = build_zone(zone)
        assert str(new_serial) in content

    # A record

    def test_a_record_rendered(self):
        zone = _make_zone(records=[_make_record('www', 'A', '1.2.3.4')])
        content, _ = build_zone(zone)
        assert 'www' in content
        assert 'IN  A  1.2.3.4' in content

    # A record with custom TTL

    def test_a_record_with_custom_ttl(self):
        zone = _make_zone(records=[_make_record('www', 'A', '1.2.3.4', ttl=300)])
        content, _ = build_zone(zone)
        assert '300' in content

    # MX record

    def test_mx_record_includes_priority(self):
        zone = _make_zone(records=[_make_record('@', 'MX', 'mail.example.com', priority=10)])
        content, _ = build_zone(zone)
        assert 'IN  MX  10  mail.example.com' in content

    def test_mx_record_without_priority_still_renders(self):
        zone = _make_zone(records=[_make_record('@', 'MX', 'mail.example.com', priority=0)])
        content, _ = build_zone(zone)
        assert 'IN  MX  0  mail.example.com' in content

    # SRV record

    def test_srv_record_includes_priority(self):
        zone = _make_zone(records=[
            _make_record('_sip._tcp', 'SRV', '10 5060 sip.example.com', priority=20)
        ])
        content, _ = build_zone(zone)
        assert 'IN  SRV  20  10 5060 sip.example.com' in content

    # TXT record

    def test_txt_record_is_quoted(self):
        zone = _make_zone(records=[_make_record('@', 'TXT', 'v=spf1 include:example.com ~all')])
        content, _ = build_zone(zone)
        assert 'IN  TXT  "v=spf1 include:example.com ~all"' in content

    def test_txt_record_already_quoted_is_not_double_quoted(self):
        zone = _make_zone(records=[_make_record('@', 'TXT', '"v=spf1 ~all"')])
        content, _ = build_zone(zone)
        assert '""v=spf1 ~all""' not in content
        assert '"v=spf1 ~all"' in content

    # CNAME record

    def test_cname_record_rendered(self):
        zone = _make_zone(records=[_make_record('www', 'CNAME', 'example.com.')])
        content, _ = build_zone(zone)
        assert 'IN  CNAME  example.com.' in content

    # AAAA record

    def test_aaaa_record_rendered(self):
        zone = _make_zone(records=[_make_record('www', 'AAAA', '2001:db8::1')])
        content, _ = build_zone(zone)
        assert 'IN  AAAA  2001:db8::1' in content

    # CAA record

    def test_caa_record_rendered(self):
        zone = _make_zone(records=[_make_record('@', 'CAA', '0 issue "letsencrypt.org"')])
        content, _ = build_zone(zone)
        assert 'IN  CAA  0 issue "letsencrypt.org"' in content


# ── render_zone ────────────────────────────────────────────────────────────
# Used to serve zone content to pull agents (apps/api/v1/agent_views.py) —
# unlike build_zone(), it must never advance the serial as a side effect.

class TestRenderZone:

    def test_uses_current_serial_without_bumping(self):
        zone = _make_zone(serial=42)
        content = render_zone(zone)
        assert '42' in content
        assert zone.serial == 42  # untouched

    def test_repeated_calls_return_identical_content(self):
        zone = _make_zone(serial=42, records=[_make_record('www', 'A', '1.2.3.4')])
        assert render_zone(zone) == render_zone(zone)

    def test_a_record_rendered(self):
        zone = _make_zone(serial=42, records=[_make_record('www', 'A', '1.2.3.4')])
        content = render_zone(zone)
        assert 'IN  A  1.2.3.4' in content
