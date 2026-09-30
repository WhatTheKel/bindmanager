# BindManager — Installation Guide (RHEL 8, Single-Server, From Scratch)

A start-to-finish runbook for **one brand-new RHEL 8 VM, no existing zone
data**: BIND, the BindManager app, and the pull agent that connects them
all run on this one box. It's the RHEL counterpart of
[`INSTALL-DEBIAN.md`](INSTALL-DEBIAN.md), which explains the architecture
in more depth.

Already have the app running somewhere and just want another RHEL 8 box
answering DNS? Use [`INSTALL-NAMESERVER-RHEL8.md`](INSTALL-NAMESERVER-RHEL8.md)
instead — this guide's Parts 0 and 4–5 are that guide pointed at `localhost`.

```
 ┌──────────────────────── This RHEL 8 VM ────────────────────────┐
 │                                                                  │
 │  Docker Compose (Part 2): nginx :81 · web · worker · beat        │
 │                           + hidden "bind" container (no DNS)     │
 │        ▲                                                         │
 │        │ HTTP localhost:81 (API key)                             │
 │  bindmanager-agent (Part 4, systemd timer, every 2 min)          │
 │        │ writes /var/named/bindmanager/*.zone, rndc reload        │
 │        ▼                                                         │
 │  named (Part 0) ── answers DNS on :53 ◄───────────── resolvers   │
 │                                                                  │
 │  MariaDB + Redis (Part 1) — native services, reached by the      │
 │  containers via host.docker.internal                             │
 └──────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| RHEL 8 VM with a static IP | Registered/subscribed so `dnf` can reach BaseOS + AppStream |
| Root access | Commands below are run as root |
| Internet access from the VM | For `dnf`, the Docker CE repo, GitHub and Docker Hub |
| SELinux | Leave it **enforcing** — the paths below are chosen to work with the default policy |

## How RHEL 8 differs from the Debian guide

| | Debian/Ubuntu | RHEL 8 |
|---|---|---|
| BIND packages / service | `bind9` / `named` | `bind bind-utils` / `named` |
| BIND config | `/etc/bind/named.conf.options` | `/etc/named.conf` |
| Zones written by the agent | `/etc/bind/zones` | `/var/named/bindmanager` |
| Agent include file | `/etc/bind/named.bindmanager.conf` | `/etc/named/bindmanager.conf` |
| MariaDB | distro package | AppStream **module stream ≥ 10.5** — the default 10.3 is too old for Django 4.2 |
| Redis | distro package (7.x) | AppStream module stream `redis:6` (see note in 1.2) |
| Docker | distro/Docker repo | Docker CE repo; remove Podman first |
| Python for the agent | system `python3` | `python3.9` — RHEL 8's default 3.6 is too old |
| Firewall | `ufw` | `firewalld` |

---

## Part 0 — BIND

```bash
dnf install -y bind bind-utils python39 git
```

Edit `/etc/named.conf`. Inside `options { ... }`, change these lines
(leave everything else as shipped):

```
        listen-on port 53 { any; };
        listen-on-v6 port 53 { any; };
        allow-query     { any; };
        recursion no;
        notify no;
```

At the very end of the file add:

```
include "/etc/named/bindmanager.conf";
```

```bash
mkdir -p /var/named/bindmanager
touch /etc/named/bindmanager.conf
restorecon -Rv /var/named/bindmanager /etc/named/bindmanager.conf
named-checkconf && systemctl enable --now named
rndc status | head -1                     # "server is up and running"
firewall-cmd --permanent --add-service=dns
firewall-cmd --reload
```

Why each setting, and why these exact paths satisfy SELinux:
[`INSTALL-NAMESERVER-RHEL8.md` Part 2](INSTALL-NAMESERVER-RHEL8.md#part-2--configure-bind-as-an-authoritative-server).

---

## Part 1 — MariaDB and Redis

Both run as normal host services (not containers), so your data lives
outside Docker's lifecycle.

### 1.1 MariaDB

```bash
dnf module list mariadb                    # see which streams your RHEL 8 minor release offers
dnf module enable -y mariadb:10.5          # or a newer stream if listed — never the 10.3 default
dnf install -y mariadb-server
systemctl enable --now mariadb
```

RHEL's MariaDB already listens on all interfaces, so containers can reach
it via `host.docker.internal`. It stays private because firewalld's
`public` zone doesn't open 3306 — **don't** add 3306 to it. Docker puts its
bridge networks in firewalld's `docker` zone, which accepts traffic from
containers to the host.

### 1.2 Redis

```bash
dnf module enable -y redis:6
dnf install -y redis
sed -i 's/^bind .*/bind 0.0.0.0/' /etc/redis.conf
sed -i "s/^# requirepass .*/requirepass $(openssl rand -hex 24)/" /etc/redis.conf
grep '^requirepass' /etc/redis.conf         # copy this value for .env in Part 2
systemctl enable --now redis
```

If `/etc/redis.conf` doesn't exist, your build keeps it at
`/etc/redis/redis.conf` — use that path in the three commands above. As
with MariaDB, don't open 6379 in firewalld's `public` zone.

> **Redis version:** RHEL 8's AppStream stops at Redis 6, while the README
> lists Redis 7+. Celery only uses basic list and pub/sub commands that
> Redis 6 has, but this combination hasn't been tested with this release —
> if you want to match the README exactly, install Redis 7 from the
> [Remi repository](https://rpms.remirepo.net/) instead; the rest of this
> section is unchanged.

> **PostgreSQL instead of MariaDB?** Pick a stream with
> `dnf module list postgresql` (13 or newer), then follow the PostgreSQL
> note in [`INSTALL-DEBIAN.md` Part 1.2](INSTALL-DEBIAN.md#12-redis) —
> on RHEL the config files live in `/var/lib/pgsql/data/`, and you must
> run `postgresql-setup --initdb` before the first start.

---

## Part 2 — Docker and the BindManager app

### 2.1 Install Docker CE

RHEL 8 ships Podman, which conflicts with Docker CE — remove it first:

```bash
dnf remove -y podman buildah runc
dnf install -y dnf-plugins-core
dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker
docker compose version
```

### 2.2 Get the app and create the database

```bash
git clone https://github.com/WhatTheKel/bindmanager.git /opt/bindmanager
cd /opt/bindmanager
mysql -u root < docker/mysql/init.sql
```

As OS root, `mysql -u root` needs no password. This creates database
`bindmanager` and user `bindmanager@'%'` with password `bindmanager` —
change it now:

```bash
mysql -u root -e "ALTER USER 'bindmanager'@'%' IDENTIFIED BY '<a real password>';"
```

### 2.3 Configure `.env`

```bash
cp .env.example .env
```

Minimum edits:

```ini
DJANGO_SECRET_KEY=<generate: python3.9 -c "import secrets; print(secrets.token_urlsafe(50))">
DJANGO_ALLOWED_HOSTS=localhost,<this-vm-ip-or-hostname>
CSRF_TRUSTED_ORIGINS=http://<this-vm-ip-or-hostname>:81

DB_HOST=host.docker.internal
DB_PASSWORD=<the password from 2.2>

REDIS_URL=redis://:<the requirepass value from 1.2>@host.docker.internal:6379/0
```

### 2.4 Build and start

```bash
docker compose up -d --build
docker compose ps                              # 5 services: web, nginx, worker, beat, bind
docker compose logs web | grep -E 'Error|Listening'   # want "Listening at"
docker compose exec web python manage.py createsuperuser
firewall-cmd --permanent --add-port=81/tcp && firewall-cmd --reload
```

Log in at `http://<this-vm-ip>:81/`.

---

## Part 3 — Turn on the sync schedule (don't skip)

The Celery Beat schedule lives in the database and nothing creates it for
you. Without it the app looks fine but **no zone ever syncs**, with no
error anywhere.

```bash
docker compose exec -T web python manage.py shell -c "
from django_celery_beat.models import IntervalSchedule, PeriodicTask
s,_=IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.MINUTES)
PeriodicTask.objects.get_or_create(name='Sync dirty zones', defaults=dict(
    task='apps.dns_manager.tasks.sync_dirty_zones', interval=s, enabled=True))"
```

Within a minute:

```bash
docker compose logs --since 2m beat | grep sync_dirty_zones   # want "Sending due task"
```

---

## Part 4 — Connect this VM's BIND to the app (the agent)

### 4.1 Register the nameserver

**Manage → Nameservers → + Add Nameserver:**

- `name` — this server's full DNS name, e.g. `ns1.example.com` (this is
  what goes into the zones' NS records)
- `address` — this VM's IP (informational)
- `config_dir` — `/var/named/bindmanager` (informational)
- `is_active` — checked

Save, then copy the **Agent API Key** from the list.

### 4.2 Install and configure the agent

The repo is already at `/opt/bindmanager` from Part 2:

```bash
cd /opt/bindmanager
mkdir -p /opt/bindmanager-agent /etc/bindmanager-agent
\cp -f agents/bindmanager_agent.py /opt/bindmanager-agent/
\cp -f agents/config.example.ini /etc/bindmanager-agent/config.ini
\cp -f agents/systemd/bindmanager-agent.{service,timer} /etc/systemd/system/
sed -i 's|/usr/bin/python3 |/usr/bin/python3.9 |' /etc/systemd/system/bindmanager-agent.service
chmod 600 /etc/bindmanager-agent/config.ini
```

Set **all four** settings. The example file has Debian `/etc/bind/...`
paths, which break on RHEL:

```bash
API_KEY='paste-the-agent-api-key-here'
sed -i \
  -e "s|^api_url.*|api_url = http://localhost:81/api/v1|" \
  -e "s|^api_key.*|api_key = ${API_KEY}|" \
  -e "s|^zones_dir.*|zones_dir = /var/named/bindmanager|" \
  -e "s|^named_conf_include.*|named_conf_include = /etc/named/bindmanager.conf|" \
  /etc/bindmanager-agent/config.ini
grep -n '/etc/bind/' /etc/bindmanager-agent/config.ini && echo "FIX THESE" || echo "paths OK"
```

### 4.3 Dry run, then enable

```bash
python3.9 /opt/bindmanager-agent/bindmanager_agent.py --config /etc/bindmanager-agent/config.ini --dry-run -v
# want: "dry-run: 0 changed, 0 removed, topology_changed=False"
systemctl daemon-reload
systemctl enable --now bindmanager-agent.timer
```

---

## Part 5 — Test zone, end to end

1. **Manage → Zones → Add Zone:** your test domain, tick the nameserver
   from 4.1.
2. Add records:

   | Name | Type | Value |
   |---|---|---|
   | `ns1` | **A** (not NS) | this VM's IP |
   | `www` | A | any valid IP |

   Don't add an NS record — the app generates it from the assigned
   nameserver.
3. Wait ~3 minutes (sync 1 min + agent 2 min), or push it through now:
   ```bash
   systemctl start bindmanager-agent.service
   journalctl -u bindmanager-agent.service -n 5 --no-pager   # "sync complete: 1 changed"
   ```
4. From **another machine**:
   ```bash
   dig @<this-vm-ip> <zone> SOA +norec
   dig @<this-vm-ip> www.<zone> A +norec
   dig @<this-vm-ip> ns1.<zone> A +norec
   ```
   All three should return `NOERROR` with the `aa` flag.

---

## Troubleshooting

The nameserver side (agent, BIND, SELinux, `REFUSED`) is covered in
detail in [`INSTALL-NAMESERVER-RHEL8.md` → Troubleshooting](INSTALL-NAMESERVER-RHEL8.md#troubleshooting-rhel-specific).
App-side problems specific to a RHEL host:

| Symptom | Cause | Fix |
|---|---|---|
| `dnf install docker-ce` fails with conflicts on `runc`/`containers-common` | Podman packages still installed | `dnf remove -y podman buildah runc`, then retry |
| `web` crash-loops; logs mention MariaDB version or unsupported features | MariaDB 10.3 (RHEL 8 default stream) — Django 4.2 needs 10.4+ | Back up, `dnf module reset mariadb`, enable a newer stream, `dnf distro-sync mariadb-server` |
| `web`/`worker` can't reach MariaDB or Redis (`Connection refused` / timed out to `host.docker.internal`) | Redis still bound to `127.0.0.1`, or the service isn't running | `ss -ltn \| grep -E '3306\|6379'` should show `0.0.0.0`; `firewall-cmd --get-active-zones` should list the Docker bridges in zone `docker` |
| Containers log `Permission denied` on `./bind_zones`, `./staticfiles` or `./branding` | Docker's SELinux support was turned on (`"selinux-enabled": true` in `/etc/docker/daemon.json`) so bind mounts need labels | `chcon -Rt container_file_t bind_zones staticfiles branding`, or turn that option back off |
| Zone stays `is_dirty=True` | No sync schedule (Part 3), or a record fails `named-checkzone` | `docker compose logs worker \| grep -A3 checkzone` shows the reason |
| Login page unreachable from your browser | Port 81 closed | Part 2.4 firewall step |

---

## Security checklist

- [ ] Changed the default `bindmanager` DB password (MariaDB **and** `.env`)
- [ ] Generated a real `DJANGO_SECRET_KEY`
- [ ] Redis has a `requirepass`, and `REDIS_URL` includes it
- [ ] Ports 3306 and 6379 are **not** open in firewalld's `public` zone
      (`firewall-cmd --list-all`)
- [ ] `recursion no;` in `/etc/named.conf`
- [ ] SELinux still enforcing (`getenforce`)
- [ ] `chmod 600 /etc/bindmanager-agent/config.ini`
- [ ] TLS in front of Nginx for anything beyond a lab — the app serves
      plain HTTP on :81
- [ ] Rotate a leaked agent key: `/admin/` → **Name servers** → tick the
      row → **Regenerate API key** action, then update `config.ini`

---

## Updating later

See [`UPDATING.md`](UPDATING.md). In short: on this server, `cd /opt/bindmanager &&
git pull && docker compose up -d --build && docker compose restart nginx`.
If the update changed `agents/`, also copy the new
`agents/bindmanager_agent.py` to `/opt/bindmanager-agent/` (UPDATING.md
Part 2.4). Back up the database first if the update includes migrations.

---

## Adding more nameservers later

Nothing here needs redoing. For each new RHEL 8 box, follow
[`INSTALL-NAMESERVER-RHEL8.md`](INSTALL-NAMESERVER-RHEL8.md) with
`api_url` pointing at this VM's IP instead of `localhost`. For
Debian/Ubuntu boxes or container-based nameservers, see
[`INSTALL-DEBIAN.md` → Scaling beyond one server](INSTALL-DEBIAN.md#scaling-beyond-one-server).
