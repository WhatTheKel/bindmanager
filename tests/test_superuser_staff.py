"""
A superuser is always saved with is_staff=True, however it was edited.
"""
import pytest
from django.contrib.auth import get_user_model

from apps.dns_manager.forms import UserEditForm

User = get_user_model()


@pytest.mark.django_db
def test_superuser_saved_without_staff_gets_staff():
    user = User.objects.create_user('su', password='x', is_superuser=True, is_staff=False)
    user.refresh_from_db()
    assert user.is_staff is True


@pytest.mark.django_db
def test_edit_form_cannot_untick_staff_on_superuser():
    user = User.objects.create_user('someone', password='x')
    form = UserEditForm(
        data={'username': 'someone', 'email': '', 'first_name': '', 'last_name': '',
              'is_superuser': 'on', 'is_active': 'on'},  # Staff left unticked
        instance=user,
    )
    assert form.is_valid(), form.errors
    form.save()
    user.refresh_from_db()
    assert user.is_superuser is True
    assert user.is_staff is True


@pytest.mark.django_db
def test_staff_alone_unchanged():
    user = User.objects.create_user('staffer', password='x', is_staff=True)
    user.refresh_from_db()
    assert user.is_staff is True
    assert user.is_superuser is False


@pytest.mark.django_db
def test_regular_user_unchanged():
    user = User.objects.create_user('plain', password='x')
    user.refresh_from_db()
    assert user.is_staff is False
