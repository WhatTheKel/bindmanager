import fcntl
import os
import subprocess
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
    result = subprocess.run(
        ['named-checkzone', zone_name, str(zone_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # named-checkzone reports its errors on stdout, not stderr.
        output = (result.stdout + result.stderr).strip()
        raise ValueError(f'named-checkzone failed for {zone_name}:\n{output}')


def reload_zone(zone_name: str) -> None:
    result = subprocess.run(
        [settings.RNDC_BIN, 'reload', zone_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f'rndc reload failed for {zone_name}:\n{result.stderr}')
