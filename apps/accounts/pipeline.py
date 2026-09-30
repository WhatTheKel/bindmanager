def ignore_session_user(backend, user=None, *args, **kwargs):
    """Treat every SSO sign-in as a fresh login, never "connect to this account".

    social-auth passes the currently logged-in user into the pipeline, and
    social_user/associate_user then attach the SSO identity to *that* user.
    Clicking an SSO button in a browser where someone else is logged in
    would permanently link the SSO account to them (e.g. to a local
    superuser). Dropping the session user makes the pipeline find or create
    the SSO identity's own account; the login that follows replaces the
    session. Must run before social_user.
    """
    if user is not None:
        return {'user': None}


def set_staff_flag(backend, user, is_new=False, *args, **kwargs):
    if is_new and not user.is_staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])
