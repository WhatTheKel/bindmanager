"""
agents/update-agent.sh, run in a sandbox: a fake installed agent, a fake
app API, and no systemd (so it never touches a real nameserver install).
"""
import http.server
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'agents' / 'update-agent.sh'
NEW_AGENT = ROOT / 'agents' / 'bindmanager_agent.py'

pytestmark = pytest.mark.skipif(
    shutil.which('bash') is None or os.geteuid() != 0,
    reason='needs bash and root (the script installs into the agent dir)')

# Mimics a pre-0.2.4 agent: still runs, but has no "AGENT_VERSION = '...'"
# line for the script to read the version from.
OLD_AGENT = re.sub(r"^AGENT_VERSION = '([^']*)'", r"AGENT_VERSION = str('\1')",
                   NEW_AGENT.read_text(), count=1, flags=re.M)
assert OLD_AGENT != NEW_AGENT.read_text()


class _Api(http.server.BaseHTTPRequestHandler):
    agents = []

    def do_GET(self):
        _Api.agents.append(self.headers.get('User-Agent', ''))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'[]')

    def log_message(self, *args):
        pass


@pytest.fixture
def sandbox(tmp_path):
    server = http.server.HTTPServer(('127.0.0.1', 0), _Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Api.agents = []
    (tmp_path / 'opt').mkdir()
    (tmp_path / 'bin').mkdir()
    agent = tmp_path / 'opt' / 'bindmanager_agent.py'
    agent.write_text(OLD_AGENT)
    cfg = tmp_path / 'config.ini'
    cfg.write_text(f"""[agent]
api_url = http://127.0.0.1:{server.server_port}/api/v1
api_key = test
zones_dir = {tmp_path}/zones
lock_file = {tmp_path}/lock
manifest_file = {tmp_path}/manifest.json
""")
    fake_systemctl = tmp_path / 'bin' / 'systemctl'      # "no unit installed"
    fake_systemctl.write_text('#!/bin/sh\nexit 1\n')
    fake_systemctl.chmod(0o755)
    yield {'dir': tmp_path, 'agent': agent, 'cfg': cfg}
    server.shutdown()


def run(sb, *args, cfg=None):
    env = {**os.environ, 'PATH': f"{sb['dir']}/bin:{os.environ['PATH']}"}
    return subprocess.run(
        ['bash', str(SCRIPT), '--dest', str(sb['agent']), '--config', str(cfg or sb['cfg']),
         '--python', sys.executable, *args],
        capture_output=True, text=True, env=env, timeout=60)


def version_of(path):
    for line in Path(path).read_text().splitlines():
        if line.startswith("AGENT_VERSION = '"):      # same rule as the script
            return line.split("'")[1]
    return None


def test_check_reports_update_and_changes_nothing(sandbox):
    r = run(sandbox, '--check', '--source', str(NEW_AGENT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert 'unknown (before 0.2.4)' in r.stdout and 'An update is available' in r.stdout
    assert version_of(sandbox['agent']) is None


def test_update_installs_backs_up_and_verifies(sandbox):
    r = run(sandbox, '--source', str(NEW_AGENT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert version_of(sandbox['agent']) == version_of(NEW_AGENT)
    assert len(list(sandbox['agent'].parent.glob('bindmanager_agent.py.bak-*'))) == 1
    assert 'dry-run: 0 changed' in r.stdout
    assert any(a.startswith('bindmanager-agent/') for a in _Api.agents)   # new agent talked to the app
    assert os.access(sandbox['agent'], os.X_OK)


def test_second_run_is_a_no_op(sandbox):
    run(sandbox, '--source', str(NEW_AGENT))
    r = run(sandbox, '--source', str(NEW_AGENT))
    assert r.returncode == 0 and 'already up to date' in r.stdout


def test_failed_dry_run_restores_old_agent(sandbox):
    bad = sandbox['dir'] / 'bad.ini'
    bad.write_text(sandbox['cfg'].read_text().replace('127.0.0.1:', '127.0.0.1:1#'))
    r = run(sandbox, '--source', str(NEW_AGENT), cfg=bad)
    assert r.returncode == 1
    assert 'Restoring the previous agent' in r.stderr
    assert sandbox['agent'].read_text() == OLD_AGENT


def test_broken_file_refused_before_install(sandbox):
    broken = sandbox['dir'] / 'broken.py'
    broken.write_text('def sync(:\n')
    r = run(sandbox, '--source', str(broken))
    assert r.returncode == 1
    assert sandbox['agent'].read_text() == OLD_AGENT
    assert not list(sandbox['agent'].parent.glob('*.bak-*'))


def test_downgrade_refused_unless_allowed(sandbox):
    run(sandbox, '--source', str(NEW_AGENT))
    old = sandbox['dir'] / 'old.py'
    old.write_text(OLD_AGENT)
    r = run(sandbox, '--source', str(old))
    assert r.returncode == 0 and 'not newer' in r.stdout
    assert version_of(sandbox['agent']) == version_of(NEW_AGENT)
    r = run(sandbox, '--source', str(old), '--allow-downgrade')
    assert r.returncode == 0 and version_of(sandbox['agent']) is None
