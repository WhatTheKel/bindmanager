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
   this server, copy its **Agent API Key**.
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
  by API key) and only once they're `is_dirty=False` — i.e. already
  validated by BindManager's own `named-checkzone` run centrally.
- Deleting a NameServer's assignment to a zone (or the zone itself) removes
  the local `.zone` file and drops it from the generated config include on
  the next run.

## Updating

Only needed when a BindManager update changes `agents/`. Copy the new
`bindmanager_agent.py` over the old one — no restart, the next timer/cron
run picks it up. Leave `config.ini` alone (add any new settings from
`config.example.ini` by hand). If you re-copy the systemd unit, re-apply
any `ExecStart` edit you made (e.g. `python3.9` on RHEL 8). See
[`UPDATING.md`](../UPDATING.md) Part 2.

## Rotating a compromised key

In the Django admin: `/admin/` > **Name servers** > tick the row > action
**"Regenerate API key"** (the Manage UI shows keys but can't rotate them).
Update `config.ini` on that server and re-run the agent — the old key stops
working immediately.
