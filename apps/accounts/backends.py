from social_core.backends.open_id_connect import OpenIdConnectAuth


class AuthentikOpenIdConnect(OpenIdConnectAuth):
    """OIDC backend for Authentik.

    Set AUTHENTIK_OIDC_ENDPOINT to your application's OIDC issuer URL,
    e.g. https://auth.example.com/application/o/bindmanager/
    """
    name = 'authentik'
    DEFAULT_SCOPE = ['openid', 'profile', 'email']
