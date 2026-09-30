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
