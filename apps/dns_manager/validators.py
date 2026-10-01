import ipaddress
import re

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

    elif record_type == 'SOA':
        raise ValidationError(
            'The SOA record is generated from the zone\'s own settings '
            '(Edit Zone), so it can\'t be added as a record.'
        )

    elif record_type == 'SRV':
        parts = value.split()
        if (len(parts) != 3 or not all(p.isdigit() and int(p) <= 65535 for p in parts[:2])):
            raise ValidationError(
                'SRV value must be "weight port target", e.g. "5 5060 sip.example.com." '
                '(the priority goes in the Priority field).'
            )
        if _is_ip(parts[2].rstrip('.')):
            raise ValidationError('The SRV target must be a hostname, not an IP address.')


def validate_priority(record_type: str, priority) -> None:
    """MX and SRV lines need a priority, or the zone file gets "MX None host"."""
    if record_type in ('MX', 'SRV'):
        if priority is None or priority == '':
            raise ValidationError(f'{record_type} records need a priority (e.g. 10).')
        if not 0 <= int(priority) <= 65535:
            raise ValidationError('Priority must be between 0 and 65535.')


# One DNS label: letters, digits, hyphen and underscore (for _dmarc, _sip…),
# not starting or ending with a hyphen.
_LABEL_RE = re.compile(r'^(?!-)[A-Za-z0-9_-]{1,63}(?<!-)$')


def _check_labels(name: str, what: str, allow_wildcard: bool = False) -> None:
    labels = name.split('.')
    if len(name) > 253 or any(not l for l in labels):
        raise ValidationError(f'"{name}" is not a valid {what} (empty label or too long).')
    for i, label in enumerate(labels):
        if allow_wildcard and i == 0 and label == '*':
            continue
        if not _LABEL_RE.match(label):
            raise ValidationError(
                f'"{name}" is not a valid {what}: use letters, digits, "-" and "_" '
                f'separated by dots (no spaces).'
            )


def normalize_zone_name(name: str) -> str:
    """Lower-case, drop a trailing dot, and check it's a valid domain name."""
    name = (name or '').strip().lower().rstrip('.')
    if not name:
        raise ValidationError('Enter the zone name, e.g. example.com.')
    _check_labels(name, 'zone name')
    return name


def normalize_owner(name: str, zone_name: str) -> str:
    """Return the record name relative to the zone ('@' for the apex), or raise.

    A name without a trailing dot is relative to the zone in a zone file, so
    "www.example.com" typed as a name in example.com would become
    "www.example.com.example.com.". Names that end with the zone's own name
    (with or without the dot) are made relative; names ending with a dot must
    be inside the zone.
    """
    raw = (name or '').strip()
    zone = zone_name.lower().rstrip('.')
    if raw in ('', '@'):
        return '@'
    low = raw.lower()
    if low.rstrip('.') == zone:
        return '@'
    for suffix in (f'.{zone}.', f'.{zone}'):
        if low.endswith(suffix):
            raw = raw[:-len(suffix)]
            break
    else:
        if raw.endswith('.'):
            raise ValidationError(
                f'"{raw}" is outside this zone ({zone}). Enter a name in the zone, '
                f'e.g. "www" or "www.{zone}".'
            )
    _check_labels(raw, 'record name', allow_wildcard=True)
    return raw


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
