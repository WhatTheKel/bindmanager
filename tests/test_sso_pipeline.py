"""
The SSO login pipeline must never attach an SSO identity to an existing
account — neither to whoever is already logged in, nor by matching email.
Runs the real SOCIAL_AUTH_PIPELINE against the Authentik backend.
"""
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from social_django.models import UserSocialAuth
from social_django.utils import load_strategy

from apps.accounts.backends import AuthentikOpenIdConnect

User = get_user_model()


def _run_pipeline(response, session_user=None):
    request = RequestFactory().get('/complete/authentik/')
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    backend = AuthentikOpenIdConnect(strategy=load_strategy(request))
    return backend.run_pipeline(
        settings.SOCIAL_AUTH_PIPELINE, response=response, user=session_user, request=request,
    )


def _response(sub='sso-user-1', email='person@example.com', username='person'):
    return {'sub': sub, 'email': email, 'preferred_username': username}


@pytest.mark.django_db
def test_sso_login_while_logged_in_does_not_link_to_session_user():
    admin = User.objects.create_superuser('admin', 'admin@example.com', 'x')
    result = _run_pipeline(_response(), session_user=admin)

    sso_user = result['user']
    assert sso_user != admin
    assert not UserSocialAuth.objects.filter(user=admin).exists()
    assert UserSocialAuth.objects.get(provider='authentik', uid='sso-user-1').user == sso_user


@pytest.mark.django_db
def test_sso_login_does_not_associate_by_email():
    local = User.objects.create_superuser('local', 'person@example.com', 'x')
    result = _run_pipeline(_response(email='person@example.com'))

    assert result['user'] != local
    assert not UserSocialAuth.objects.filter(user=local).exists()


@pytest.mark.django_db
def test_new_sso_user_is_staff_not_superuser():
    user = _run_pipeline(_response())['user']
    assert user.is_staff is True
    assert user.is_superuser is False


@pytest.mark.django_db
def test_returning_sso_user_gets_same_account():
    first = _run_pipeline(_response())['user']
    second = _run_pipeline(_response())['user']
    assert first == second
    assert UserSocialAuth.objects.filter(provider='authentik').count() == 1
