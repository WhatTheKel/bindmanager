# BindManager — Adding a RHEL 8 Nameserver

This runbook turns a **fresh RHEL 8 VM (no BIND installed yet)** into an
authoritative nameserver managed by an existing BindManager deployment.
It is the RHEL equivalent of [`INSTALL-DEBIAN.md`](INSTALL-DEBIAN.md) Part 4, which
assumes Debian/Ubuntu.

The BindManager app itself is **not** installed here — it runs once,
elsewhere (see [`INSTALL-RHEL8.md`](INSTALL-RHEL8.md) or
[`INSTALL-DEBIAN.md`](INSTALL-DEBIAN.md) Parts 1–3). Want the app *and*
BIND on this same RHEL 8 box? Use [`INSTALL-RHEL8.md`](INSTALL-RHEL8.md)
instead. This VM only gets:

- BIND (`named`), answering DNS on port 53
- the pull agent (`agents/bindmanager_agent.py`), run by a systemd timer
  every 2 minutes, which fetches the zones assigned to this server over
  the REST API, validates them, writes them and reloads `named`

```
 BindManager app (existing)            This RHEL 8 VM
 ┌──────────────────────┐   HTTP :81   ┌─────────────────────────────┐
 │ web / worker / beat  │◄─────────────│ bindmanager-agent (timer)   │
 │ REST API /api/v1     │  API key     │   writes /var/named/...     │
 └──────────────────────┘              │   rndc reload               │
                                       │ named ── answers DNS :53 ◄──┼── resolvers
                                       └─────────────────────────────┘
```

## How RHEL 8 differs from the Debian guide

| | Debian/Ubuntu (`INSTALL-DEBIAN.md`) | RHEL 8 (this guide) |
|---|---|---|
| Packages | `bind9 bind9-utils bind9-dnsutils` | `bind bind-utils` |
| Service | `bind9` | `named` |
| Main config | `/etc/bind/named.conf` | `/etc/named.conf` |
| Zones directory | `/etc/bind/zones` | `/var/named/bindmanager` |
| Agent include file | `/etc/bind/named.bindmanager.conf` | `/etc/named/bindmanager.conf` |
| Python for the agent | system `python3` | **`python3.9`** — the default `python3` (3.6) is too old |
| Firewall | `ufw` | `firewalld` |
| MAC | usually none | **SELinux enforcing** — paths above are chosen so the default policy works |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| RHEL 8 VM with a static IP | Registered/subscribed so `dnf` can reach BaseOS + AppStream |
| Root or sudo access | |
| A running BindManager deployment | With the Celery Beat sync task configured (`INSTALL-DEBIAN.md` Part 3) — see [the sync checkpoint](#before-you-start-confirm-the-app-is-syncing) |
| Network path VM → app | TCP 81 (or whatever fronts the app) from this VM to the BindManager host |
| Network path resolvers → VM | UDP + TCP 53 inbound |

### Before you start: confirm the app is syncing

The agent only ever receives zones that BindManager's own sync has already
validated (`is_dirty=False`). If the central Celery sync isn't running,
this server will get **no zones at all**, no matter how correctly it's set
up. On the BindManager host:

```bash
docker compose exec web python manage.py shell -c "
from apps.dns_manager.models import Zone
print(Zone.objects.count(), 'zones,', Zone.objects.filter(is_dirty=True).count(), 'dirty')
"
```

If the dirty count doesn't drop to (near) zero within a couple of minutes
of a change, fix that first — see `INSTALL-DEBIAN.md` Part 3 and its
Troubleshooting table.

---

## Part 1 — Install packages

```bash
dnf install -y bind bind-utils python39
```

- `bind` — `named`, `rndc`, `named-checkzone`, `named-checkconf`
- `bind-utils` — `dig`, for testing
- `python39` — the agent uses Python 3.8+ features
  (`from __future__ import annotations`, `Path.unlink(missing_ok=...)`),
  so RHEL 8's default Python 3.6 crashes on startup. `python39` installs
  `/usr/bin/python3.9` alongside it; nothing else on the system changes.

---

## Part 2 — Configure BIND as an authoritative server

RHEL's stock `/etc/named.conf` is a **localhost-only caching resolver**.
Edit the `options { ... }` block so it answers authoritatively for
everyone and recurses for no one:

```
options {
        listen-on port 53 { any; };
        listen-on-v6 port 53 { any; };
        directory       "/var/named";
        ...
        allow-query     { any; };
        recursion no;
        notify no;
        ...
};
```

| Setting | Default | Why change it |
|---|---|---|
| `listen-on` / `listen-on-v6` | `127.0.0.1` / `::1` | Must listen on the real interfaces to answer resolvers |
| `allow-query` | `localhost` | Must answer anyone querying zones it's authoritative for |
| `recursion` | `yes` | An internet-facing open resolver is an abuse/amplification risk; an authoritative server shouldn't recurse |
| `notify` | `yes` | The agent writes every zone as `type master` on every server, each fed independently by BindManager — there are no secondaries to NOTIFY |

Leave the rest (`directory`, `pid-file`, logging, the `.` hint zone and the
`include "/etc/named.rfc1912.zones";` line) as shipped.

At the **very end** of `/etc/named.conf`, add the agent's include, once:

```
include "/etc/named/bindmanager.conf";
```

Create the zones directory and an empty include file (the agent populates
it on first run), fix SELinux labels, validate, and start:

```bash
mkdir -p /var/named/bindmanager
touch /etc/named/bindmanager.conf
restorecon -Rv /var/named/bindmanager /etc/named/bindmanager.conf

named-checkconf
systemctl enable --now named
rndc status          # should print "server is up and running"
```

`rndc` works out of the box: the `named-setup-rndc` unit generates
`/etc/rndc.key` the first time `named` starts.

### Why these paths (SELinux)

The agent writes each zone to `<name>.zone.tmp` in the target directory,
validates it, then renames it into place — so new files **inherit the
SELinux label of their directory**. Under the default targeted policy:

- `/var/named/...` → `named_zone_t` — readable by `named`
- `/etc/named/...` → `named_conf_t` — readable by `named`

Use these locations and SELinux can stay enforcing with no custom policy.
If you put zones elsewhere (e.g. `/etc/bind/zones` as in the Debian
guide), `named` will be denied reading them and zones will fail to load.

### Open the firewall

```bash
firewall-cmd --permanent --add-service=dns
firewall-cmd --reload
```

(`dns` covers both UDP and TCP 53.)

---

## Part 3 — Register this server in BindManager

In the BindManager UI:

1. **Manage → Nameservers → + Add Nameserver.**
   - `name` — the fully-qualified name this server will have in DNS (e.g.
     `ns1.example.com`). This is what goes into zone files: every zone
     assigned to this server gets `NS ns1.example.com.`, and the first
     assigned nameserver becomes the SOA primary.
   - `address` — this VM's IP (e.g. `192.0.2.20`) — informational only
   - `config_dir` — `/var/named/bindmanager` — informational only (the
     agent uses `zones_dir` from its own `config.ini`, Part 4.3)
   - `is_active` — checked
2. Save, then copy the row's **Agent API Key**.
3. Don't assign zones yet — do the dry run in Part 5 with none assigned
   first, then add a test zone in Part 6.

---

## Part 4 — Install the pull agent

### 4.1 Get the agent files onto the VM

The VM needs only four files from this repo. Either clone it:

```bash
dnf install -y git
git clone https://github.com/WhatTheKel/bindmanager.git /root/bindmanager
cd /root/bindmanager
```

…or, without git, fetch just those four files:

```bash
base=https://raw.githubusercontent.com/WhatTheKel/bindmanager/main/agents
mkdir -p /root/bindmanager/agents/systemd && cd /root/bindmanager
curl -fsSL $base/bindmanager_agent.py               -o agents/bindmanager_agent.py
curl -fsSL $base/config.example.ini                 -o agents/config.example.ini
curl -fsSL $base/systemd/bindmanager-agent.service  -o agents/systemd/bindmanager-agent.service
curl -fsSL $base/systemd/bindmanager-agent.timer    -o agents/systemd/bindmanager-agent.timer
```

(No internet on the VM? `scp` the same four files from any machine with a
checkout.)

### 4.2 Install them

Run from the repo root (`/root/bindmanager`):

```bash
mkdir -p /opt/bindmanager-agent /etc/bindmanager-agent
\cp -f agents/bindmanager_agent.py /opt/bindmanager-agent/
\cp -f agents/config.example.ini /etc/bindmanager-agent/config.ini
\cp -f agents/systemd/bindmanager-agent.{service,timer} /etc/systemd/system/

# Run the agent with Python 3.9, not the system 3.6
sed -i 's|/usr/bin/python3 |/usr/bin/python3.9 |' /etc/systemd/system/bindmanager-agent.service

chmod 600 /etc/bindmanager-agent/config.ini   # it holds a bearer credential
```

(The leading `\` skips RHEL root's `cp -i` alias, so re-running doesn't stop
to ask about overwriting.)

### 4.3 Configure it

> **Change every path, not just `api_url`/`api_key`.** `config.example.ini`
> ships with **Debian** paths (`/etc/bind/...`). On RHEL `/etc/bind` doesn't
> exist, and a leftover `named_conf_include = /etc/bind/...` makes the agent
> write the zone file successfully and then crash — so BIND never learns
> about the zone and answers `REFUSED`. Use the `sed` block below rather
> than editing by hand, so nothing is missed.

Set your two values, then apply all four settings in one go:

```bash
APP_URL=http://bindmanager.example.com:81     # the BindManager app, as reachable from this VM
API_KEY='paste-the-agent-api-key-here'        # from Part 3

sed -i \
  -e "s|^api_url.*|api_url = ${APP_URL}/api/v1|" \
  -e "s|^api_key.*|api_key = ${API_KEY}|" \
  -e "s|^zones_dir.*|zones_dir = /var/named/bindmanager|" \
  -e "s|^named_conf_include.*|named_conf_include = /etc/named/bindmanager.conf|" \
  /etc/bindmanager-agent/config.ini
```

Check it — nothing may mention `/etc/bind/`:

```bash
grep -E '^(api_url|zones_dir|named_conf_include)' /etc/bindmanager-agent/config.ini
grep -n '/etc/bind/' /etc/bindmanager-agent/config.ini && echo "FIX THESE" || echo "paths OK"
```

The remaining defaults (`rndc_bin`, `checkzone_bin`, `checkconf_bin`,
`lock_file`, `manifest_file`, `timeout`) work as-is on RHEL 8.

The shipped systemd unit already lists `After=... named.service`, and runs
as root — which is what writing into `/var/named` and running `rndc` need.

---

## Part 5 — Dry-run, then enable

Check connectivity and credentials without touching anything:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://bindmanager.example.com:81/   # expect 200 or 302
python3.9 /opt/bindmanager-agent/bindmanager_agent.py \
  --config /etc/bindmanager-agent/config.ini --dry-run -v
```

With no zones assigned yet, success looks like:

```
INFO dry-run: 0 changed, 0 removed, topology_changed=False
```

That line alone proves the VM reached the app **and** the API key was
accepted (a bad key fails with HTTP 401; a network problem with
`URLError`). Then:

```bash
systemctl daemon-reload
systemctl enable --now bindmanager-agent.timer
systemctl list-timers bindmanager-agent.timer
```

---

## Part 6 — Add a test zone and verify end-to-end

### 6.1 Create the zone

In BindManager, **Manage → Zones → Add Zone**:

- name: your test domain (e.g. `example.com`)
- nameservers: tick this server (there's no separate "primary NS" field —
  the SOA primary is taken from the first assigned nameserver)

Then add records:

| Name | Type | Value | Why |
|---|---|---|---|
| `ns1` | **A** | `192.0.2.20` (this VM's IP) | The address for the nameserver's own name ("glue") |
| `www` | A | any valid IP | Something to test with |

You do **not** add an NS record yourself — BindManager generates
`@ IN NS ns1.example.com.` from the zone's assigned nameservers.

> **Common mistake:** creating `ns1` as an **NS** record with the IP as its
> value. An NS record always points at a *hostname*, so BIND reads the IP as
> the name `192.0.2.20.example.com` and treats `ns1` as a delegated
> sub-zone — `ns1.example.com` then never resolves. The app now rejects
> this (and invalid IPs like `192.0.2.266`) when you save the record.

### 6.2 Wait for it to flow through

Two hops, each on a timer:

1. **App sync** (Celery Beat, every minute) validates the zone with
   `named-checkzone` and marks it clean.
2. **Agent** (every 2 minutes) pulls it to this VM.

So allow ~3 minutes, or trigger the agent immediately:

```bash
systemctl start bindmanager-agent.service
journalctl -u bindmanager-agent.service -n 10 --no-pager
```

Success looks like:

```
INFO zone example.com changed (disk=None api=2026093001)
INFO sync complete: 1 changed, 0 removed, 1 total assigned
```

### 6.3 Check it on the VM

```bash
ls -laZ /var/named/bindmanager/          # example.com.zone, labelled named_zone_t
cat /etc/named/bindmanager.conf          # zone "example.com" { type master; ... };
dig @localhost example.com SOA +norec
```

### 6.4 Check it from another machine

This proves the firewall and `listen-on` / `allow-query` changes work:

```bash
dig @192.0.2.20 example.com SOA +norec
dig @192.0.2.20 www.example.com A +norec
dig @192.0.2.20 ns1.example.com A +norec
```

Each should return `status: NOERROR` with the `aa` (authoritative answer)
flag. If `ns1` comes back with no answer and no `aa`, it's the NS-vs-A
mistake above.

Only once that works should you advertise the server: add it to the
zones' NS records in BindManager and, if it's a new public nameserver,
register it (and its glue) with the domain registrar.

---

## Troubleshooting (RHEL-specific)

See also the Troubleshooting table in [`INSTALL-DEBIAN.md`](INSTALL-DEBIAN.md) for
agent/API issues that aren't OS-specific (401s, 404s, empty zone lists).

**First, tell the two kinds of `REFUSED` apart** with
`journalctl -u named -n 20`:

- `query (cache) 'example.com/SOA/IN' denied` — BIND doesn't know it's
  authoritative for the zone, so it treated the query as recursive and
  refused it. **The zone isn't loaded** — look at the agent's log.
- No such line, and `dig @localhost` works but remote `dig` doesn't —
  `allow-query` / `listen-on` still restrict to localhost.

| Symptom | Cause | Fix |
|---|---|---|
| Agent log: `FileNotFoundError: ... '/etc/bind/named.bindmanager.tmp'` (zone file *is* written, but `/etc/named/bindmanager.conf` stays empty, `dig` gives `REFUSED`) | `named_conf_include` still has the Debian default path | Part 4.3 `sed` block; then `systemctl start bindmanager-agent.service` — it recovers on its own, no cleanup needed |
| Agent log: `FileNotFoundError` mentioning `/etc/bind/zones` | `zones_dir` still has the Debian default path | Same as above |
| Agent fails immediately with `SyntaxError` / `TypeError` mentioning annotations, or `unlink() got an unexpected keyword argument` | Running under Python 3.6 | Confirm `ExecStart` uses `/usr/bin/python3.9`; `systemctl daemon-reload` |
| `dig` returns `REFUSED` and named logs `query (cache) ... denied` | Zone not loaded (see above) | Check `journalctl -u bindmanager-agent.service`, `cat /etc/named/bindmanager.conf` |
| `dig @localhost` works, remote `dig` returns `REFUSED` | `allow-query` still `localhost` | Part 2, then `rndc reconfig` |
| `dig @localhost` works, remote `dig` times out | Firewall closed, or `listen-on` still `127.0.0.1` | Part 2 firewall step; `ss -lunp \| grep :53` should show the VM's IP |
| Agent dry run lists nothing even after adding a zone | Zone still unsynced on the app side (`is_dirty=True`) — often a record that fails `named-checkzone` | On the app host: `docker compose logs worker \| grep checkzone` — the error now includes the reason |
| `ns1.<zone>` gets no answer and no `aa` flag, while other names work | `ns1` was created as an **NS** record with an IP value | Change it to an **A** record (see 6.1) |
| Zone files written but `named` logs `permission denied` loading them | Wrong SELinux label (files outside `/var/named`, or copied in from elsewhere) | `restorecon -Rv /var/named/bindmanager`; check `ausearch -m avc -ts recent` |
| `rndc: connect failed` | `named` not running, or `/etc/rndc.key` missing | `systemctl status named named-setup-rndc` |
| `named-checkconf` fails after adding the include | Include file path doesn't exist yet | `touch /etc/named/bindmanager.conf && restorecon -v /etc/named/bindmanager.conf` |
| Agent can't reach the app (`URLError`, connection refused/timed out) | Network/firewall between VM and BindManager host | `curl` test in Part 5; check routing and any firewall on the app side |

---

## Security checklist

- [ ] `recursion no;` — this server is not an open resolver
- [ ] `chmod 600 /etc/bindmanager-agent/config.ini`
- [ ] SELinux left **enforcing** (`getenforce`)
- [ ] The agent's API key travels over plain HTTP by default — acceptable
      on a trusted internal network only; otherwise put TLS in front of the
      app and use an `https://` `api_url`
- [ ] If the key ever leaks: Django admin (`/admin/` → **Name servers**) →
      tick the row → **Regenerate API key** action, then update
      `api_key` in `config.ini` (the old key stops working immediately;
      the next timer run uses the new one)
