import pytest
from django.contrib.auth.models import User


@pytest.fixture
def staff_user(db):
    return User.objects.create_user('staff', password='x', is_staff=True)


@pytest.fixture
def regular_user(db):
    return User.objects.create_user('regular', password='x', is_staff=False)


@pytest.fixture
def nameserver(db):
    from apps.dns_manager.models import NameServer
    return NameServer.objects.create(name='ns1.example.com', address='192.0.2.1')


@pytest.fixture
def zone(db, nameserver):
    from apps.dns_manager.models import Zone
    z = Zone.objects.create(name='example.com', serial=1, is_dirty=False)
    z.nameservers.add(nameserver)
    return z
