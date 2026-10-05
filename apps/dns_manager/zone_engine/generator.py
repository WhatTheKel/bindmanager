import re
from datetime import date
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

_TEMPLATE_DIR = Path(__file__).parent / 'templates'
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
    undefined=StrictUndefined,
    # Drop the newline/indent left behind by {% for %}/{% if %} lines, so the
    # zone file has no stray blank lines between records.
    trim_blocks=True,
    lstrip_blocks=True,
)


def _bump_serial(current_serial: int) -> int:
    today = int(date.today().strftime('%Y%m%d')) * 100
    if current_serial >= today:
        return current_serial + 1
    return today


# A TXT value already written as one or more "quoted" strings.
_QUOTED_STRINGS_RE = re.compile(r'^"(?:[^"\\]|\\.)*"(?:\s+"(?:[^"\\]|\\.)*")*$')
_QUOTED_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
# One character of a zone-file string: \DDD, \X or a plain character.
_TXT_UNIT_RE = re.compile(r'\\\d{3}|\\.|.', re.DOTALL)
TXT_STRING_MAX = 255   # bytes in one DNS character-string


def _split_txt_string(escaped: str) -> list[str]:
    """Cut one escaped string body into pieces of at most 255 bytes, never
    splitting an escape sequence or a multi-byte character."""
    chunks, current, size = [], '', 0
    for unit in _TXT_UNIT_RE.findall(escaped):
        n = 1 if unit.startswith('\\') else len(unit.encode('utf-8'))
        if size + n > TXT_STRING_MAX:
            chunks.append(current)
            current, size = '', 0
        current += unit
        size += n
    chunks.append(current)
    return chunks


def _quote_txt(value: str) -> str:
    """Render a TXT value as one or more quoted strings of at most 255 bytes.

    A value already in quotes ("a" "b") keeps its strings, splitting any that
    are too long; anything else is escaped and quoted. One string over 255
    bytes makes named-checkzone reject the whole zone, and long values (a
    2048-bit DKIM key, say) are normal.
    """
    v = value.strip()
    if _QUOTED_STRINGS_RE.match(v):
        bodies = _QUOTED_STRING_RE.findall(v)
    elif v.startswith('"') and v.endswith('"'):
        return v   # quoted in some other way: left as written, as before
    else:
        bodies = [v.replace('\\', '\\\\').replace('"', '\\"')]
    return ' '.join(f'"{chunk}"' for body in bodies for chunk in _split_txt_string(body))


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
    The serial is owned exclusively by the Celery sync pipeline's
    build_zone(). Agents are served Zone.published_content instead (the file
    that last passed validation); this is used by migration 0004 to publish
    zones that were already in sync.
    """
    zone_ctx, record_ctx = _build_context(zone)
    return _render(zone_ctx, record_ctx, zone.serial)
