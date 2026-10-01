#!/usr/bin/env python3
"""
BindManager pull agent.

Runs on each physical BIND nameserver (not on the BindManager app server).
On every invocation it:

  1. Authenticates to BindManager's REST API with this server's NameServer
     API key (Manage > Nameservers in the web UI, or Django admin).
  2. Fetches the list of zones assigned to this server + their current serial.
  3. For any zone whose serial differs from what's on disk, fetches the
     rendered zone content, validates it with named-checkzone, and writes it
     atomically (temp file + os.replace, same pattern BindManager's own
     writer.py uses server-side).
  4. Regenerates a BIND config include listing every assigned zone, so zones
     added/removed centrally show up here without hand-editing named.conf.
  5. Runs `rndc reload` — a single full reload if the zone topology changed
     (add/remove), otherwise a per-zone `rndc reload <zone>` for each zone
     whose content changed.

Only stdlib is used so this can run on a bare nameserver with no pip installs.

Deploy: cron or a systemd timer calling `bindmanager_agent.py --once` every
few minutes. See agents/systemd/ and agents/config.example.ini.
"""
from __future__ import annotations

import argparse
import configparser
import fcntl
import json
import logging
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Bump to the app release number whenever this file changes (UPDATING.md,
# "Releasing"). Sent as the User-Agent on every request; the app shows it
# per nameserver in Manage > Nameservers and flags agents older than its own
# copy of this file.
AGENT_VERSION = '0.2.4'

log = logging.getLogger('bindmanager_agent')

# Matches the "; Serial" comment zone.j2 renders next to the SOA serial
# (apps/dns_manager/zone_engine/templates/zone.j2). If that template's
# comment format ever changes, local_serial() below returns None forever —
# harmless (every zone just looks "changed" every poll, gets rewritten and
# reloaded needlessly) but worth knowing if reload activity looks wrong.
_SERIAL_RE = re.compile(r'(\d+)\s*;\s*[Ss]erial')


@dataclass
class Config:
    api_url: str          # e.g. https://bindmanager.example.com/api/v1
    api_key: str
    zones_dir: Path
    named_conf_include: Path | None
    rndc_bin: str
    checkzone_bin: str
    checkconf_bin: str
    lock_file: Path
    manifest_file: Path
    timeout: int

    @classmethod
    def from_file(cls, path: Path) -> 'Config':
        cp = configparser.ConfigParser()
        if not cp.read(path):
            raise SystemExit(f'Could not read config file: {path}')
        s = cp['agent']
        include = s.get('named_conf_include', fallback='')
        return cls(
            api_url=s['api_url'].rstrip('/'),
            api_key=s['api_key'],
            zones_dir=Path(s.get('zones_dir', '/etc/bind/zones')),
            named_conf_include=Path(include) if include else None,
            # Bare command names, PATH-resolved — matches writer.py's own
            # named-checkzone call server-side. Absolute paths vary by distro
            # (e.g. Debian bookworm ships named-checkzone/-checkconf under
            # /usr/bin, not /usr/sbin); override in config.ini if $PATH
            # doesn't cover it for your init system.
            rndc_bin=s.get('rndc_bin', 'rndc'),
            checkzone_bin=s.get('checkzone_bin', 'named-checkzone'),
            checkconf_bin=s.get('checkconf_bin', 'named-checkconf'),
            lock_file=Path(s.get('lock_file', '/var/lock/bindmanager_agent.lock')),
            manifest_file=Path(s.get('manifest_file', '/var/lib/bindmanager-agent/managed_zones.json')),
            timeout=s.getint('timeout', fallback=15),
        )


class ApiError(Exception):
    pass


def _api_get(cfg: Config, path: str) -> dict | list:
    req = urllib.request.Request(
        f'{cfg.api_url}{path}',
        headers={
            'Authorization': f'ApiKey {cfg.api_key}',
            'Accept': 'application/json',
            'User-Agent': f'bindmanager-agent/{AGENT_VERSION} '
                          f'(python {sys.version_info.major}.{sys.version_info.minor})',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise ApiError(f'{path} -> HTTP {exc.code}: {exc.read().decode("utf-8", "replace")}') from exc
    except urllib.error.URLError as exc:
        raise ApiError(f'{path} -> {exc.reason}') from exc


def fetch_assigned_zones(cfg: Config) -> list[dict]:
    return _api_get(cfg, '/agent/zones/')


def fetch_zone_content(cfg: Config, name: str) -> dict:
    return _api_get(cfg, f'/agent/zones/{name}/')


def load_manifest(cfg: Config) -> dict[str, int]:
    if not cfg.manifest_file.exists():
        return {}
    return json.loads(cfg.manifest_file.read_text(encoding='utf-8'))


def save_manifest(cfg: Config, manifest: dict[str, int]) -> None:
    cfg.manifest_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')


def local_serial(cfg: Config, name: str) -> int | None:
    zone_file = cfg.zones_dir / f'{name}.zone'
    if not zone_file.exists():
        return None
    match = _SERIAL_RE.search(zone_file.read_text(encoding='utf-8', errors='replace'))
    return int(match.group(1)) if match else None


def write_zone_file(cfg: Config, name: str, content: str) -> None:
    cfg.zones_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.zones_dir / f'{name}.zone'
    tmp = cfg.zones_dir / f'{name}.zone.tmp'
    tmp.write_text(content, encoding='utf-8')
    try:
        result = subprocess.run(
            [cfg.checkzone_bin, name, str(tmp)], capture_output=True, text=True,
        )
        if result.returncode != 0:
            # named-checkzone/-checkconf report errors on stdout, not stderr
            raise RuntimeError(f'named-checkzone rejected {name}:\n{result.stdout}{result.stderr}')
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def remove_zone_file(cfg: Config, name: str) -> None:
    (cfg.zones_dir / f'{name}.zone').unlink(missing_ok=True)


def write_named_conf_include(cfg: Config, zones: list[dict]) -> None:
    if cfg.named_conf_include is None:
        return
    lines = [
        '// Managed by bindmanager_agent.py — do not edit by hand.',
        '// Add `include "%s";` to named.conf once.' % cfg.named_conf_include,
        '',
    ]
    for z in sorted(zones, key=lambda z: z['name']):
        zone_file = cfg.zones_dir / f"{z['name']}.zone"
        lines.append(
            f'zone "{z["name"]}" {{ type master; file "{zone_file}"; }};'
        )
    content = '\n'.join(lines) + '\n'

    tmp = cfg.named_conf_include.with_suffix('.tmp')
    tmp.write_text(content, encoding='utf-8')
    try:
        result = subprocess.run(
            [cfg.checkconf_bin, str(tmp)], capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f'named-checkconf rejected generated include:\n{result.stdout}{result.stderr}')
        tmp.replace(cfg.named_conf_include)
    finally:
        tmp.unlink(missing_ok=True)


def reload_zone(cfg: Config, name: str) -> None:
    result = subprocess.run([cfg.rndc_bin, 'reload', name], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'rndc reload failed for {name}:\n{result.stderr}')


def reload_all(cfg: Config) -> None:
    result = subprocess.run([cfg.rndc_bin, 'reload'], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'rndc reload failed:\n{result.stderr}')


def sync(cfg: Config, dry_run: bool = False) -> int:
    assigned = fetch_assigned_zones(cfg)
    assigned_by_name = {z['name']: z for z in assigned}
    manifest = load_manifest(cfg)

    changed: list[str] = []
    failed: list[str] = []
    for name, zone in assigned_by_name.items():
        on_disk = local_serial(cfg, name)
        if on_disk == zone['serial']:
            continue
        log.info('zone %s changed (disk=%s api=%s)', name, on_disk, zone['serial'])
        if dry_run:
            changed.append(name)
            continue
        # One bad zone must not hold back every other zone's update: log it,
        # keep serving whatever file this server already has, and move on.
        try:
            detail = fetch_zone_content(cfg, name)
            write_zone_file(cfg, name, detail['content'])
        except Exception as exc:
            log.error('zone %s not updated: %s', name, exc)
            failed.append(name)
            continue
        changed.append(name)

    removed = [name for name in manifest if name not in assigned_by_name]
    for name in removed:
        log.info('zone %s no longer assigned — removing', name)
        if not dry_run:
            remove_zone_file(cfg, name)

    if dry_run:
        log.info('dry-run: %d changed, %d removed', len(changed), len(removed))
        return 0

    # Only zones with a file on disk can be loaded; a new zone whose first
    # write failed is left out until a later run succeeds.
    present = {name: z for name, z in assigned_by_name.items()
               if (cfg.zones_dir / f'{name}.zone').exists()}
    topology_changed = bool(removed) or any(name not in manifest for name in present)

    write_named_conf_include(cfg, list(present.values()))

    if topology_changed:
        reload_all(cfg)
    else:
        for name in changed:
            reload_zone(cfg, name)

    new_manifest = {name: z['serial'] for name, z in present.items()}
    save_manifest(cfg, new_manifest)

    log.info('sync complete: %d changed, %d removed, %d failed, %d total assigned',
              len(changed), len(removed), len(failed), len(assigned_by_name))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/bindmanager-agent/config.ini'))
    parser.add_argument('--dry-run', action='store_true', help="Report what would change, touch nothing")
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--version', action='version', version=f'bindmanager-agent {AGENT_VERSION}')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    cfg = Config.from_file(args.config)
    cfg.lock_file.parent.mkdir(parents=True, exist_ok=True)

    with open(cfg.lock_file, 'w') as lock_fp:
        try:
            fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.warning('another run is already in progress — exiting')
            return 1

        try:
            return sync(cfg, dry_run=args.dry_run)
        except ApiError as exc:
            log.error('API request failed: %s', exc)
            return 1
        except Exception:
            log.exception('sync failed')
            return 1
        finally:
            fcntl.flock(lock_fp, fcntl.LOCK_UN)


if __name__ == '__main__':
    sys.exit(main())
