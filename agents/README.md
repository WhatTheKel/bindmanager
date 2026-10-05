# BindManager pull agent

Deployed on each **physical BIND nameserver** — for a single-server setup
this is the same box as the BindManager app itself (see `INSTALL-DEBIAN.md`'s
Part 4); for additional nameservers it's a separate box. This directory
covers the bare-metal + systemd path. If you'd rather run the agent in a
container instead — either alongside a fresh BIND9 install
(`docker/bind9-node/`) or against a BIND9 the host already has
(`docker/bind9-agent/`) — see `INSTALL-DEBIAN.md`'s Part 4 and "Scaling beyond
one server" section for all three options. Pulls the zones assigned to
that server from the REST API, writes and reloads them locally. See the
top-level docstring in `bindmanager_agent.py` for the full sync algorithm.

This exists because BindManager's own Celery worker (`apps/dns_manager/tasks.py`)
only ever writes zone files and calls `rndc reload` on whatever single host
it runs on — it has no mechanism to push to other servers. The agent closes
that gap by having each nameserver pull its own config instead — a
cron'd/timer'd client authenticating with a per-server API key, pulling
only the zones assigned to it, validating, and reloading locally.

## Setup (per nameserver)

1. In BindManager: **Manage > Nameservers**, create (or find) the row for
   this server, copy its **Agent API Key** (shown in full only once, right
   after saving; **Regenerate** on the row makes a new one).
2. Copy `bindmanager_agent.py` to the server (e.g. `/opt/bindmanager-agent/`).
   Stdlib only — no `pip install` required.
3. Copy `config.example.ini` to `/etc/bindmanager-agent/config.ini`, fill in
   `api_url` and `api_key`, `chmod 600` it. The example's `zones_dir` and
   `named_conf_include` are **Debian/Ubuntu** paths (`/etc/bind/...`) — on
   RHEL use `/var/named/bindmanager` and `/etc/named/bindmanager.conf`
   instead (see [`INSTALL-NAMESERVER-RHEL8.md`](../INSTALL-NAMESERVER-RHEL8.md) Part 4.3). A wrong
   `named_conf_include` makes the agent write zone files and then crash
   before BIND is told about them.
   Needs Python 3.8+ — on RHEL 8 install `python39` and point the unit's
   `ExecStart` at `/usr/bin/python3.9`.
4. If using `named_conf_include`, add one line to `named.conf`:
   ```
   include "/etc/bind/named.bindmanager.conf";
   ```
5. Install `systemd/bindmanager-agent.{service,timer}` (adjust `ExecStart`
   path), then:
   ```
   systemctl daemon-reload
   systemctl enable --now bindmanager-agent.timer
   ```
   Or run it from cron instead: `*/2 * * * * /usr/bin/python3 /opt/bindmanager-agent/bindmanager_agent.py`

6. Sanity check before trusting it: `bindmanager_agent.py --dry-run -v`

## What it will and won't touch

- Only writes `<zones_dir>/<zone>.zone` files and (optionally) the generated
  `named_conf_include` fragment — never edits your main `named.conf`.
- Only ever fetches zones assigned to *this* nameserver (scoped server-side
  by API key), and only the version that last passed BindManager's own
  central `named-checkzone` run. A zone whose edits are still pending, or
  failing that check, stays listed and keeps serving its last good version
  — it is never removed for that.
- If one zone can't be fetched or fails the local `named-checkzone`, the
  other zones are still updated; that zone keeps its existing file, is
  retried on the next run, and the run exits non-zero so the failure shows
  up in `systemctl status` / the journal.
- Deleting a NameServer's assignment to a zone (or the zone itself) removes
  the local `.zone` file and drops it from the generated config include on
  the next run.

## Updating

**Manage → Nameservers** in the app shows each agent's reported version
and last check-in, and marks agents older than the one the app ships as
**Outdated** (**Unknown** for agents from before 0.2.4, which
don't report a version). `bindmanager_agent.py --version` prints it
locally. Only those need updating — on the nameserver, as root:

```bash
cd /path/to/bindmanager && git pull && ./agents/update-agent.sh
```

`update-agent.sh` backs up the old agent, installs the new one, dry-runs it
against the app (restoring the old one on failure) and runs it once.
`--check` only reports; `--help` lists the options (no-git download,
`--ref`, `--source`). To do it by hand: copy the new
`bindmanager_agent.py` over the old one — no restart, the next timer/cron
run picks it up. Leave `config.ini` alone (add any new settings from
`config.example.ini` by hand). If you re-copy the systemd unit, re-apply
any `ExecStart` edit you made (e.g. `python3.9` on RHEL 8). See
[`UPDATING.md`](../UPDATING.md) Part 2.

## Rotating a compromised key

In **Manage > Nameservers**, click **Regenerate** on the row (or, in the
Django admin, the **"Regenerate API key"** action). The new key is shown once.
Update `config.ini` on that server and re-run the agent — the old key stops
working immediately.
