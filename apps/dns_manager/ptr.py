"""Reverse DNS helpers: subnet → reverse zone name, and keeping a PTR record
in step with an A/AAAA record."""
import ipaddress
from dataclasses import dataclass
from typing import List, Optional

from django.core.exceptions import ValidationError


def reverse_zone_for_subnet(subnet: str):
    """Return (zone_name, ip_version) for the reverse zone of `subnet`.

    192.0.2.0/24 → ("2.0.192.in-addr.arpa", 4); 2001:db8::/32 →
    ("8.b.d.0.1.0.0.2.ip6.arpa", 6). Host bits are ignored. Reverse zones
    are cut on label boundaries, so IPv4 prefixes must be a multiple of 8
    and IPv6 prefixes a multiple of 4.
    """
    raw = (subnet or '').strip()
    try:
        net = ipaddress.ip_network(raw, strict=False)
    except ValueError:
        raise ValidationError(
            f'"{raw}" is not a valid subnet. Use CIDR form, e.g. 192.0.2.0/24 or 2001:db8::/32.'
        )
    step = 8 if net.version == 4 else 4
    if net.prefixlen == 0 or net.prefixlen % step:
        if net.version == 4:
            raise ValidationError(
                f'A reverse zone covers whole octets, so an IPv4 subnet must be '
                f'/8, /16 or /24 (got /{net.prefixlen}). For a /{net.prefixlen}, '
                f'create one zone per /{(net.prefixlen // 8 + 1) * 8} inside it.'
            )
        raise ValidationError(
            f'A reverse zone covers whole hex digits, so an IPv6 prefix length must '
            f'be a multiple of 4, e.g. /32, /48, /56 or /64 (got /{net.prefixlen}).'
        )
    labels = net.network_address.reverse_pointer.split('.')
    address_labels = 4 if net.version == 4 else 32
    keep = net.prefixlen // step
    return '.'.join(labels[address_labels - keep:]), net.version


def find_reverse_zone(ip: str):
    """Return (zone, owner) for the most specific zone holding `ip`'s PTR
    name, or (None, None) if no zone covers it."""
    from .models import Zone

    ptr_name = ipaddress.ip_address(ip).reverse_pointer   # no trailing dot
    labels = ptr_name.split('.')
    suffixes = ['.'.join(labels[i:]) for i in range(len(labels) - 1)]
    zones = list(Zone.objects.filter(name__in=suffixes))
    if not zones:
        return None, None
    zone = max(zones, key=lambda z: len(z.name))
    owner = '@' if zone.name == ptr_name else ptr_name[:-(len(zone.name) + 1)]
    return zone, owner


def record_fqdn(name: str, zone_name: str) -> str:
    """Absolute name (with trailing dot) of a zone-relative record name."""
    zone = zone_name.rstrip('.')
    if name.endswith('.'):          # already absolute (older data)
        return name
    if name in ('', '@'):
        return f'{zone}.'
    return f'{name}.{zone}.'


@dataclass
class PtrChange:
    """One outcome of sync_ptr(). `action` is 'create', 'update', 'delete'
    (something was written: the PTR record, or with entity_type 'zone' the
    reverse zone made for it) or 'info' / 'warning' (nothing written)."""
    action: str
    message: str
    entity_id: Optional[int] = None
    entity_type: str = 'record'


def _old_ptr_target(old):
    """(ip, fqdn) the record pointed at before an edit, or None."""
    if not old or old.get('record_type') not in ('A', 'AAAA'):
        return None
    try:
        ip = str(ipaddress.ip_address(old['value'].strip()))
    except ValueError:
        return None
    return ip, record_fqdn(old['name'], old['zone_name']).lower()


def auto_reverse_subnet(ip: str) -> str:
    """The subnet a missing reverse zone is created for: the /24 (IPv4) or
    /64 (IPv6) holding `ip`."""
    addr = ipaddress.ip_address(ip)
    return str(ipaddress.ip_network(f'{addr}/{24 if addr.version == 4 else 64}', strict=False))


def _create_reverse_zone(ip, forward_zone, user):
    """Create the reverse zone for `ip`'s /24 or /64, served by the same
    nameservers as the forward zone."""
    from .models import Zone

    subnet = auto_reverse_subnet(ip)
    name, version = reverse_zone_for_subnet(subnet)
    zone = Zone.objects.create(name=name, zone_type=Zone.ZoneType.REVERSE,
                               ip_version=version, created_by=user)
    zone.nameservers.set(forward_zone.nameservers.all())
    return zone, subnet


def sync_ptr(record, user=None, old=None) -> List[PtrChange]:
    """Create or update the PTR record for an A/AAAA `record`.

    `old` is the record's state before an edit ({'record_type', 'name',
    'value', 'zone_name'}); when its IP changed, the PTR this record had at
    the old IP is removed. If no reverse zone covers the address, one is
    created for its /24 (IPv4) or /64 (IPv6). A PTR for another host at the same IP is
    replaced only when it's the only one there, so a multi-PTR address is
    never rewritten by guesswork. Nothing raises: problems come back as
    'warning' changes, since the A/AAAA record itself was saved fine.
    """
    from .models import Record

    if record.record_type not in ('A', 'AAAA'):
        return []
    if '*' in record.name:
        return [PtrChange('warning', 'A wildcard name has no single host for a PTR record; none created.')]
    if not record.is_active:
        return [PtrChange('info', 'The record is inactive, so its PTR record was left alone.')]

    ip = str(ipaddress.ip_address(record.value.strip()))
    fqdn = record_fqdn(record.name, record.zone.name)
    changes = []

    old_target = _old_ptr_target(old)
    if old_target and old_target[0] != ip:
        old_zone, old_owner = find_reverse_zone(old_target[0])
        if old_zone:
            for stale in old_zone.records.filter(record_type='PTR', name__iexact=old_owner):
                if stale.value.strip().lower() == old_target[1]:
                    stale_pk = stale.pk
                    stale.delete()
                    changes.append(PtrChange(
                        'delete', f'Removed PTR {old_owner}.{old_zone.name} → {stale.value} (old address).',
                        stale_pk))

    zone, owner = find_reverse_zone(ip)
    if zone is None:
        zone, subnet = _create_reverse_zone(ip, record.zone, user)
        zone, owner = find_reverse_zone(ip)
        nameservers = ', '.join(ns.name for ns in zone.nameservers.order_by('name')) or 'none yet'
        changes.append(PtrChange(
            'create', f'Created reverse zone {zone.name} for {subnet} '
                      f'(nameservers: {nameservers}).', zone.pk, 'zone'))
        if not zone.nameservers.exists():
            changes.append(PtrChange(
                'warning', f'{zone.name} has no nameservers, so it can\'t sync yet. '
                           f'Assign nameservers to it under Edit Zone.'))

    label = f'{owner}.{zone.name}' if owner != '@' else zone.name
    existing = list(zone.records.filter(record_type='PTR', name__iexact=owner))
    if any(r.value.strip().lower() == fqdn.lower() for r in existing):
        changes.append(PtrChange('info', f'PTR {label} → {fqdn} already exists.'))
        return changes

    previous = None
    if old_target:
        previous = next((r for r in existing if r.value.strip().lower() == old_target[1]), None)
    if previous is None and len(existing) == 1:
        previous = existing[0]
    elif previous is None and existing:
        changes.append(PtrChange(
            'warning', f'{label} already has {len(existing)} PTR records, so none was changed. '
                       f'Edit them in {zone.name}.'))
        return changes

    ptr = previous or Record(zone=zone, name=owner, record_type='PTR', created_by=user)
    before = ptr.value if previous else None
    ptr.value = fqdn
    ptr.ttl = record.ttl
    ptr.is_active = True
    try:
        ptr.full_clean(exclude=['created_by'])
    except ValidationError as e:
        changes.append(PtrChange('warning', f'PTR {label} not saved: {"; ".join(e.messages)}'))
        return changes
    ptr.save()
    if previous:
        changes.append(PtrChange('update', f'Updated PTR {label}: {before} → {fqdn}', ptr.pk))
    else:
        changes.append(PtrChange('create', f'Added PTR {label} → {fqdn}', ptr.pk))
    return changes


def snapshot(record) -> dict:
    """The fields sync_ptr() needs to know what a record was before an edit."""
    return {
        'record_type': record.record_type,
        'name': record.name,
        'value': record.value,
        'zone_name': record.zone.name,
    }
