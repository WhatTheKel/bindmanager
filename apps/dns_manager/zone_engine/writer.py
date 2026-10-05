import fcntl
import os
import re
import subprocess
import time
from pathlib import Path
from django.conf import settings


def atomic_write(zone_name: str, content: str) -> None:
    zone_dir = Path(settings.BIND_ZONES_DIR)
    zone_dir.mkdir(parents=True, exist_ok=True)

    dest = zone_dir / f'{zone_name}.zone'
    tmp = zone_dir / f'{zone_name}.zone.tmp'
    lock_path = zone_dir / f'{zone_name}.lock'

    lock_file = open(lock_path, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            tmp.write_text(content, encoding='utf-8')
            _validate(zone_name, tmp)
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    finally:
        lock_file.close()
        lock_path.unlink(missing_ok=True)


def _validate(zone_name: str, zone_file: Path) -> None:
    # -k fail: named refuses a primary zone with a bad host name (check-names
    # defaults to "fail" for primaries) but named-checkzone only warns about
    # one, so without it a zone could pass here and then never load.
    result = subprocess.run(
        ['named-checkzone', '-k', 'fail', zone_name, str(zone_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # named-checkzone reports its errors on stdout, not stderr.
        output = (result.stdout + result.stderr).strip()
        raise ValueError(f'named-checkzone failed for {zone_name}:\n{output}')


def _rndc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([settings.RNDC_BIN, *args], capture_output=True, text=True)


_LOADED_SERIAL_RE = re.compile(r'^serial:\s*(\d+)', re.MULTILINE)
LOAD_WAIT_SECONDS = 10


def loaded_serial(zone_name: str):
    """The serial the hidden primary is serving for `zone_name`, or None."""
    result = _rndc('zonestatus', zone_name)
    match = _LOADED_SERIAL_RE.search(result.stdout or '')
    return int(match.group(1)) if result.returncode == 0 and match else None


def _wait_for_serial(zone_name: str, serial: int) -> None:
    """Raise unless named loads `serial` within LOAD_WAIT_SECONDS.

    `rndc reload` only queues the load and exits 0 even when named then
    rejects the file and keeps serving the old version, so the reload alone
    proves nothing.
    """
    deadline = time.monotonic() + LOAD_WAIT_SECONDS
    while True:
        current = loaded_serial(zone_name)
        if current == serial:
            return
        if time.monotonic() >= deadline:
            serving = f'still serving serial {current}' if current else 'not serving it at all'
            raise RuntimeError(
                f'BIND did not load {zone_name} serial {serial} ({serving}). '
                f'The zone file passed named-checkzone but named rejected it; '
                f'the bind container log has the reason.')
        time.sleep(0.5)


def reload_zone(zone_name: str, serial: int = None) -> None:
    """Load the zone's new file on the hidden primary; with `serial`, also
    wait until named is actually serving it."""
    result = _rndc('reload', zone_name)
    if result.returncode != 0 and 'not found' in (result.stdout + result.stderr):
        # A brand-new zone: the hidden primary doesn't know it yet (its
        # zone-watcher only polls every 15s), so add it now.
        zone_file = Path(settings.BIND_ZONES_DIR) / f'{zone_name}.zone'
        result = _rndc('addzone', zone_name, f'{{ type master; file "{zone_file}"; }};')
        if result.returncode != 0 and 'already exists' in (result.stdout + result.stderr):
            result = _rndc('reload', zone_name)   # the watcher beat us to it
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise RuntimeError(f'rndc reload failed for {zone_name}:\n{output}')
    if serial is not None:
        _wait_for_serial(zone_name, serial)


def remove_zone(zone_name: str) -> None:
    """Delete a zone's central file and drop it from the hidden primary.

    The file goes first, so the zone-watcher can't re-add it. A zone that
    was never loaded is fine ("not found" is ignored).
    """
    (Path(settings.BIND_ZONES_DIR) / f'{zone_name}.zone').unlink(missing_ok=True)
    result = _rndc('delzone', zone_name)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 and 'not found' not in output:
        raise RuntimeError(f'rndc delzone failed for {zone_name}:\n{output}')


def central_zone_names() -> set[str]:
    """Zone names that have a file in the central zones directory."""
    zone_dir = Path(settings.BIND_ZONES_DIR)
    if not zone_dir.is_dir():
        return set()
    return {f.name[:-len('.zone')] for f in zone_dir.glob('*.zone')}
