import ipaddress

from django.core.exceptions import ValidationError

# Record types whose value must be a hostname. An IP here is always a mistake
# (usually meant to be an A record): BIND reads it as a relative name, e.g.
# `ns1 NS 192.0.2.1` becomes a delegation to "192.0.2.1.<zone>", which only
# warns in named-checkzone and silently breaks resolution.
_HOSTNAME_TYPES = {'NS', 'CNAME', 'PTR', 'MX'}


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def validate_record_value(record_type: str, value: str) -> None:
    """Raise ValidationError if `value` can't be valid for `record_type`."""
    value = (value or '').strip()

    if record_type == 'A':
        try:
            ipaddress.IPv4Address(value)
        except ValueError:
            raise ValidationError(f'"{value}" is not a valid IPv4 address.')

    elif record_type == 'AAAA':
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            raise ValidationError(f'"{value}" is not a valid IPv6 address.')

    elif record_type in _HOSTNAME_TYPES and _is_ip(value.rstrip('.')):
        raise ValidationError(
            f'{record_type} records point to a hostname, not an IP address. '
            f'To give a name an IP, add an A (or AAAA) record instead.'
        )


# Types whose value (or, for SRV, last field) is a hostname BIND will make
# relative to $ORIGIN unless it ends with a dot.
_TARGET_TYPES = {'CNAME', 'NS', 'PTR', 'MX', 'SRV'}


def normalize_target(record_type: str, value: str, zone_name: str) -> str:
    """Return `value` with an unambiguous hostname target, or raise.

    In a zone file a name without a trailing dot is relative, so a CNAME to
    "www.example.com" in zone example.com is read as
    "www.example.com.example.com." — a name that doesn't exist, and that
    named-checkzone doesn't flag. Rules for the target:

    - ends with a dot, "@", or a single label ("www"): left as is;
    - ends with the zone's own name ("www.example.com"): the missing dot is
      added, since that's the only thing it can mean;
    - any other dotted name ("www.other.org", "mail.eu"): rejected, because
      it could be an outside host missing its dot or a longer relative name,
      and guessing wrong silently breaks resolution.
    """
    if record_type not in _TARGET_TYPES:
        return value
    value = (value or '').strip()
    if not value:
        return value
    head, sep, target = value.rpartition(' ') if record_type == 'SRV' else ('', '', value)
    if target.endswith('.') or target == '@' or '.' not in target:
        return value

    zone = zone_name.lower().rstrip('.')
    if target.lower() == zone or target.lower().endswith('.' + zone):
        return f'{head}{sep}{target}.'

    relative = f'{target}.{zone}.'
    raise ValidationError(
        f'"{target}" has no trailing dot, so DNS would read it as '
        f'"{relative}". For a host outside this zone, end it with a dot: '
        f'"{target}.". If you really mean "{relative}", enter that in full, '
        f'including the final dot.'
    )


def _owner(name: str, zone_name: str) -> str:
    """Normalise a record name to its zone-relative owner ('@' for the apex)."""
    name = (name or '').strip().lower()
    zone = zone_name.lower().rstrip('.')
    if name in ('', '@', f'{zone}.'):
        return '@'
    if name.endswith(f'.{zone}.'):
        return name[:-len(zone) - 2]
    return name


def _same_value(record_type: str, a: str, b: str) -> bool:
    a, b = (a or '').strip(), (b or '').strip()
    if record_type in ('A', 'AAAA'):
        try:
            return ipaddress.ip_address(a) == ipaddress.ip_address(b)
        except ValueError:
            pass
    if record_type in ('TXT', 'CAA'):
        return a == b
    return a.lower() == b.lower()


def validate_record_conflicts(zone, record_type, name, value,
                              priority=None, exclude_pk=None) -> None:
    """
    Reject a record that named-checkzone would fail, or that repeats one
    already in the zone. Only active records are compared, since inactive
    ones aren't written to the zone file.

    Raises ValidationError keyed by field ('name' or 'value').
    """
    from django.db.models import Q

    owner = _owner(name, zone.name)
    label = '@ (the zone apex)' if owner == '@' else f'"{owner}"'

    if record_type == 'CNAME' and owner == '@':
        raise ValidationError({'name': (
            'The zone apex (@) can\'t have a CNAME, because it always has SOA '
            'and NS records. Use A/AAAA records for the apex instead.'
        )})

    if owner == '@':
        variants = ['@', '', f'{zone.name.rstrip(".")}.']
    else:
        variants = [owner, f'{owner}.{zone.name.rstrip(".")}.']
    same_name = Q()
    for v in variants:
        same_name |= Q(name__iexact=v)

    others = zone.records.filter(same_name, is_active=True)
    if exclude_pk:
        others = others.exclude(pk=exclude_pk)

    for rec in others.filter(record_type=record_type):
        if (_same_value(record_type, rec.value, value)
                and (record_type not in ('MX', 'SRV') or rec.priority == priority)):
            raise ValidationError({'value': (
                f'{label} already has this {record_type} record. '
                f'To add another, use a different value.'
            )})

    if record_type == 'CNAME':
        existing = others.first()
        if existing:
            raise ValidationError({'name': (
                f'{label} already has a {existing.record_type} record. A name '
                f'with a CNAME can\'t have any other records (including '
                f'another CNAME) — delete or rename the other record first.'
            )})
    else:
        cname = others.filter(record_type='CNAME').first()
        if cname:
            raise ValidationError({'name': (
                f'{label} is a CNAME (to {cname.value}), so it can\'t have '
                f'other records. Delete the CNAME first, or use another name.'
            )})
