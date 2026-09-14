from django import template

register = template.Library()

_TYPE_CLASS = {
    'A':      'badge-blue',
    'AAAA':   'badge-blue',
    'CNAME':  'badge-teal',
    'MX':     'badge-amber',
    'TXT':    'badge-neutral',
    'NS':     'badge-neutral',
    'PTR':    'badge-purple',
    'SRV':    'badge-orange',
    'CAA':    'badge-orange',
    'SOA':    'badge-red',
    'DKIM':   'badge-green',
    'DMARC':  'badge-green',
    'SPF':    'badge-green',
    'TLSA':   'badge-purple',
    'SSHFP':  'badge-purple',
    'NAPTR':  'badge-orange',
}


@register.filter
def rtype_class(record_type):
    return _TYPE_CLASS.get((record_type or '').upper(), 'badge-neutral')


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """Return updated query string with given params merged in.

    Pass param=None to remove it. Example:
        href="?{% url_replace page=page_obj.next_page_number %}"
    """
    request = context.get('request')
    if not request:
        return ''
    query = request.GET.copy()
    for key, value in kwargs.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()
