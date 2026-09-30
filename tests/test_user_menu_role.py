"""
The top-right user menu shows the same role label as Manage → Users.
"""
import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


def _menu_badge(client, user):
    client.force_login(user)
    html = client.get(reverse('dns_manager:zone_list')).content.decode()
    header = re.search(r'class="user-menu-header">(.*?)</div>', html, re.S).group(1)
    return re.findall(r'class="badge [^"]*"[^>]*>([^<]+)<', header)


@pytest.mark.django_db
@pytest.mark.parametrize('flags, expected', [
    ({'is_superuser': True}, ['Superuser']),
    ({'is_staff': True}, ['Staff']),
    ({}, ['User']),
])
def test_user_menu_role_badge(client, flags, expected):
    user = User.objects.create_user('u', password='x', **flags)
    assert _menu_badge(client, user) == expected
