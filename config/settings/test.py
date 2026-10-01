from .base import *  # noqa

# Fast in-memory database for tests — no CREATE DATABASE privilege needed
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Disable password hashing to speed up user fixture creation
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# No Redis in tests
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
