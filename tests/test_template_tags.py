"""
Tests for custom template tags in dns_tags.
- rtype_class filter
- url_replace tag
"""
import pytest
from django.test import RequestFactory

from apps.dns_manager.templatetags.dns_tags import rtype_class, url_replace


# ── rtype_class filter ───────────────────────────────────────────────────────

class TestRtypeClass:
    @pytest.mark.parametrize('rtype,expected', [
        ('A',     'badge-blue'),
        ('AAAA',  'badge-blue'),
        ('CNAME', 'badge-teal'),
        ('MX',    'badge-amber'),
        ('TXT',   'badge-neutral'),
        ('NS',    'badge-neutral'),
        ('PTR',   'badge-purple'),
        ('SRV',   'badge-orange'),
        ('CAA',   'badge-orange'),
        ('SOA',   'badge-red'),
    ])
    def test_known_type_returns_correct_class(self, rtype, expected):
        assert rtype_class(rtype) == expected

    def test_unknown_type_returns_neutral(self):
        assert rtype_class('UNKNOWN') == 'badge-neutral'

    def test_lowercase_input_is_handled(self):
        assert rtype_class('a') == 'badge-blue'

    def test_none_input_returns_neutral(self):
        assert rtype_class(None) == 'badge-neutral'

    def test_empty_string_returns_neutral(self):
        assert rtype_class('') == 'badge-neutral'


# ── url_replace tag ──────────────────────────────────────────────────────────

class TestUrlReplace:
    def _ctx(self, params: dict):
        """Build a template context dict with a GET request."""
        request = RequestFactory().get('/', params)
        return {'request': request}

    def test_adds_new_param(self):
        ctx = self._ctx({'q': 'example'})
        result = url_replace(ctx, page=2)
        assert 'page=2' in result
        assert 'q=example' in result

    def test_replaces_existing_param(self):
        ctx = self._ctx({'page': '1', 'q': 'hello'})
        result = url_replace(ctx, page=3)
        assert 'page=3' in result
        assert 'page=1' not in result

    def test_removes_param_when_value_is_none(self):
        ctx = self._ctx({'zt': 'forward', 'q': 'example'})
        result = url_replace(ctx, zt=None)
        assert 'zt' not in result
        assert 'q=example' in result

    def test_preserves_unrelated_params(self):
        ctx = self._ctx({'zt': 'forward', 'q': 'hello', 'page': '2'})
        result = url_replace(ctx, page=3)
        assert 'zt=forward' in result
        assert 'q=hello' in result
        assert 'page=3' in result

    def test_empty_context_returns_empty_string(self):
        result = url_replace({})
        assert result == ''

    def test_no_existing_params(self):
        ctx = self._ctx({})
        result = url_replace(ctx, page=1)
        assert result == 'page=1'

    def test_multiple_replacements(self):
        ctx = self._ctx({'zt': 'forward', 'page': '1'})
        result = url_replace(ctx, zt=None, page=2)
        assert 'zt' not in result
        assert 'page=2' in result
