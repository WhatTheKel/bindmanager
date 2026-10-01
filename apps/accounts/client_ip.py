from django.conf import settings


def client_ip(request) -> str:
    """The address the request really came from, as seen by our own proxy.

    X-Forwarded-For is a list the client can start with anything it likes;
    each proxy then appends the address it received the request from. Only
    the entries added by proxies we run are trustworthy, so with
    TRUSTED_PROXY_COUNT proxies (1 = the bundled nginx) the client is the
    TRUSTED_PROXY_COUNT-th entry from the right — never the first one.
    """
    if request is None:
        return 'unknown'
    remote = request.META.get('REMOTE_ADDR') or 'unknown'
    count = getattr(settings, 'TRUSTED_PROXY_COUNT', 1)
    if count <= 0:
        return remote
    hops = [h.strip() for h in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if h.strip()]
    if len(hops) >= count:
        return hops[-count]
    return remote      # didn't come through all our proxies
