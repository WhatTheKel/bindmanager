from pathlib import Path

from django.conf import settings


def sso_providers(request):
    branding_dir = Path(getattr(settings, 'BRANDING_DIR', ''))

    logo_url = getattr(settings, 'BRANDING_LOGO_URL', '')
    if not logo_url:
        for ext in ('svg', 'png', 'webp', 'jpg', 'jpeg'):
            if (branding_dir / f'logo.{ext}').exists():
                logo_url = f'/branding/logo.{ext}'
                break

    favicon_url = getattr(settings, 'BRANDING_FAVICON_URL', '')
    if not favicon_url:
        for name in ('favicon.ico', 'favicon.png'):
            if (branding_dir / name).exists():
                favicon_url = f'/branding/{name}'
                break

    return {
        'OKTA_SSO_ENABLED':      getattr(settings, 'OKTA_SSO_ENABLED', False),
        'AUTHENTIK_SSO_ENABLED': getattr(settings, 'AUTHENTIK_SSO_ENABLED', False),
        'BRANDING_LOGO_URL':     logo_url,
        'BRANDING_FAVICON_URL':  favicon_url,
        'BRANDING_APP_NAME':     getattr(settings, 'BRANDING_APP_NAME', 'BindManager'),
        'APP_VERSION':           getattr(settings, 'APP_VERSION', ''),
    }
