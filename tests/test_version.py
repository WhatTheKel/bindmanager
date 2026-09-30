"""
Tests for the app version shown in the page footer.
"""
from django.conf import settings
from django.test import RequestFactory

from apps.accounts.context_processors import sso_providers


def test_version_read_from_version_file():
    expected = (settings.BASE_DIR / 'VERSION').read_text().strip()
    assert expected
    assert settings.APP_VERSION == expected


def test_version_in_template_context():
    context = sso_providers(RequestFactory().get('/'))
    assert context['APP_VERSION'] == settings.APP_VERSION
