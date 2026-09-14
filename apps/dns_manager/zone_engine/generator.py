from datetime import date
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

_TEMPLATE_DIR = Path(__file__).parent / 'templates'
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
    undefined=StrictUndefined,
)


def _bump_serial(current_serial: int) -> int:
    today = int(date.today().strftime('%Y%m%d')) * 100
    if current_serial >= today:
        return current_serial + 1
    return today


def _quote_txt(value: str) -> str:
    """Ensure a TXT record value is wrapped in double quotes."""
    v = value.strip()
    if v.startswith('"') and v.endswith('"'):
        return v
    return '"' + v.replace('"', '\\"') + '"'


def _build_context(zone) -> tuple[dict, list]:
    ns_list = list(zone.nameservers.all())
    primary_ns = f'{ns_list[0].name}.' if ns_list else f'ns1.{zone.name}.'

    zone_ctx = {
        'name': zone.name,
        'default_ttl': zone.default_ttl,
        'refresh': zone.refresh,
        'retry': zone.retry,
        'expire': zone.expire,
        'minimum_ttl': zone.minimum_ttl,
        'primary_ns': primary_ns,
        'nameservers': [ns.name for ns in ns_list],
    }

    records = list(zone.records.filter(is_active=True).order_by('record_type', 'name'))
    record_ctx = []
    for r in records:
        value = r.value
        if r.record_type == 'TXT':
            value = _quote_txt(value)
        record_ctx.append({
            'name': r.name,
            'record_type': r.record_type,
            'ttl': r.ttl,
            'priority': r.priority,
            'value': value,
        })
    return zone_ctx, record_ctx


def _render(zone_ctx: dict, record_ctx: list, serial: int) -> str:
    template = _jinja_env.get_template('zone.j2')
    return template.render(zone=zone_ctx, records=record_ctx, serial=serial)


def build_zone(zone) -> tuple[str, int]:
    new_serial = _bump_serial(zone.serial)
    zone_ctx, record_ctx = _build_context(zone)
    content = _render(zone_ctx, record_ctx, new_serial)
    return content, new_serial


def render_zone(zone) -> str:
    """
    Render zone content at its *current* stored serial, without bumping it.
    Used to serve zone data to pull agents (apps/api/v1/agent_views.py) — the
    serial is owned exclusively by the Celery sync pipeline's build_zone(),
    so repeated agent polls must never advance it themselves.
    """
    zone_ctx, record_ctx = _build_context(zone)
    return _render(zone_ctx, record_ctx, zone.serial)
