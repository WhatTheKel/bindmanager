# BindManager — Adding a RHEL 8 Nameserver

This runbook turns a **fresh RHEL 8 VM (no BIND installed yet)** into an
authoritative nameserver managed by an existing BindManager deployment.
It is the RHEL equivalent of [`INSTALL.md`](INSTALL.md) Part 4, which
assumes Debian/Ubuntu.

The BindManager app itself is **not** installed here — it runs once,
elsewhere (see [`INSTALL.md`](INSTALL.md) Parts 1–3). This VM only gets:

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

| | Debian/Ubuntu (`INSTALL.md`) | RHEL 8 (this guide) |
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
| A running BindManager deployment | With the Celery Beat sync task configured (`INSTALL.md` Part 3) — see [the sync checkpoint](#before-you-start-confirm-the-app-is-syncing) |
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
of a change, fix that first — see `INSTALL.md` Part 3 and its
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
   - `address` — this VM's IP or FQDN (e.g. `ns2.example.com` / `192.0.2.20`)
   - `config_dir` — `/var/named/bindmanager`
2. Save, then copy the row's **Agent API Key**.
3. Assign the zones this server should serve.

---

## Part 4 — Install the pull agent

Copy three things from a checkout of this repo onto the VM (e.g. via
`scp`), then:

```bash
mkdir -p /opt/bindmanager-agent /etc/bindmanager-agent
cp agents/bindmanager_agent.py /opt/bindmanager-agent/
cp agents/config.example.ini /etc/bindmanager-agent/config.ini
cp agents/systemd/bindmanager-agent.{service,timer} /etc/systemd/system/

# Run the agent with Python 3.9, not the system 3.6
sed -i 's|/usr/bin/python3 |/usr/bin/python3.9 |' /etc/systemd/system/bindmanager-agent.service

chmod 600 /etc/bindmanager-agent/config.ini   # it holds a bearer credential
```

Edit `/etc/bindmanager-agent/config.ini`:

```ini
[agent]
api_url = http://bindmanager.example.com:81/api/v1
api_key = <the Agent API Key from Part 3>
zones_dir = /var/named/bindmanager
named_conf_include = /etc/named/bindmanager.conf
rndc_bin = rndc
checkzone_bin = named-checkzone
checkconf_bin = named-checkconf
```

The remaining defaults (`lock_file`, `manifest_file`, `timeout`) work
as-is on RHEL 8.

The shipped systemd unit already lists `After=... named.service`, and runs
as root — which is what writing into `/var/named` and running `rndc` need.

---

## Part 5 — Dry-run, then enable

Check connectivity and credentials without touching anything:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://bindmanager.example.com:81/
python3.9 /opt/bindmanager-agent/bindmanager_agent.py \
  --config /etc/bindmanager-agent/config.ini --dry-run -v
```

The dry run should list the zones assigned in Part 3 (or an empty list if
none are assigned or none have synced centrally yet). Then:

```bash
systemctl daemon-reload
systemctl enable --now bindmanager-agent.timer
systemctl list-timers bindmanager-agent.timer
```

---

## Part 6 — Verify end-to-end

After ~2 minutes:

```bash
journalctl -u bindmanager-agent.service -n 30
ls -laZ /var/named/bindmanager/          # .zone files, labelled named_zone_t
cat /etc/named/bindmanager.conf          # one zone stanza per assigned zone
dig @localhost example.com SOA
```

Then from **another machine**, to prove the firewall and `listen-on`
changes took effect:

```bash
dig @192.0.2.20 example.com SOA +norec
```

Look for the `aa` (authoritative answer) flag in the response header.

Only once that works should you advertise the server: add it to the
zones' NS records in BindManager and, if it's a new public nameserver,
register it (and any glue) with the domain registrar.

---

## Troubleshooting (RHEL-specific)

See also the Troubleshooting table in [`INSTALL.md`](INSTALL.md) for
agent/API issues that aren't OS-specific (401s, 404s, empty zone lists).

| Symptom | Cause | Fix |
|---|---|---|
| Agent fails immediately with `SyntaxError` / `TypeError` mentioning annotations, or `unlink() got an unexpected keyword argument` | Running under Python 3.6 | Confirm `ExecStart` uses `/usr/bin/python3.9`; `systemctl daemon-reload` |
| Zone files written but `named` logs `permission denied` loading them | Wrong SELinux label (files outside `/var/named`, or copied in from elsewhere) | `restorecon -Rv /var/named/bindmanager`; check `ausearch -m avc -ts recent` |
| `dig @localhost` works, `dig @<vm-ip>` from elsewhere times out | Firewall closed, or `listen-on` still `127.0.0.1` | Part 2 firewall step; `ss -lunp | grep :53` should show the VM's IP |
| `dig @<vm-ip>` returns `REFUSED` | `allow-query` still `localhost` | Part 2 |
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
- [ ] If the key ever leaks: **Manage → Nameservers → Regenerate API key**,
      update `config.ini`, `systemctl restart bindmanager-agent.timer`
