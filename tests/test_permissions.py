"""
Tests for the IsStaffOrReadOnly API permission class.
No database or request infrastructure needed — pure logic.
"""
from unittest.mock import MagicMock

import pytest

from apps.api.v1.views import IsStaffOrReadOnly

SAFE_METHODS    = ('GET', 'HEAD', 'OPTIONS')
WRITE_METHODS   = ('POST', 'PUT', 'PATCH', 'DELETE')


def _make_request(method, is_authenticated=True, is_staff=False):
    request = MagicMock()
    request.method = method
    request.user = MagicMock()
    request.user.is_authenticated = is_authenticated
    request.user.is_staff = is_staff
    return request


@pytest.fixture
def permission():
    return IsStaffOrReadOnly()


class TestIsStaffOrReadOnly:

    # Anonymous users

    @pytest.mark.parametrize('method', SAFE_METHODS)
    def test_anonymous_denied_read(self, permission, method):
        req = _make_request(method, is_authenticated=False)
        assert permission.has_permission(req, None) is False

    @pytest.mark.parametrize('method', WRITE_METHODS)
    def test_anonymous_denied_write(self, permission, method):
        req = _make_request(method, is_authenticated=False)
        assert permission.has_permission(req, None) is False

    # Authenticated non-staff users

    @pytest.mark.parametrize('method', SAFE_METHODS)
    def test_non_staff_allowed_read(self, permission, method):
        req = _make_request(method, is_authenticated=True, is_staff=False)
        assert permission.has_permission(req, None) is True

    @pytest.mark.parametrize('method', WRITE_METHODS)
    def test_non_staff_denied_write(self, permission, method):
        req = _make_request(method, is_authenticated=True, is_staff=False)
        assert permission.has_permission(req, None) is False

    # Staff users

    @pytest.mark.parametrize('method', SAFE_METHODS)
    def test_staff_allowed_read(self, permission, method):
        req = _make_request(method, is_authenticated=True, is_staff=True)
        assert permission.has_permission(req, None) is True

    @pytest.mark.parametrize('method', WRITE_METHODS)
    def test_staff_allowed_write(self, permission, method):
        req = _make_request(method, is_authenticated=True, is_staff=True)
        assert permission.has_permission(req, None) is True
