# BindManager — Installation Guide (Single-Server, From Scratch)

This is a start-to-finish runbook for the most common real starting point:
**one brand-new Linux VM that already has BIND9 installed, no existing zone
data, and you want BindManager to take over managing it.** Everything —
the BindManager app and the nameserver it manages — runs on this one box.

If you later add a second or third physical nameserver, see
[Scaling beyond one server](#scaling-beyond-one-server) at the end — you
don't need to plan for that now, and nothing in this guide has to be redone.

## Architecture (single server)

```
                    You (browser / admin)
                            │  http://<vm>:81
                            ▼
                    ┌──────────────────────────────┐
                    │           This VM             │
                    │                                │
                    │  Docker Compose:               │
                    │   nginx · web · worker · beat  │
                    │   + a hidden "bind" container  │──┐
                    │     (never answers DNS —       │  │ rndc reload
                    │      see note below)           │◄─┘ (Docker-internal only)
                    │                                │
                    │  Native OS packages:           │
                    │   MariaDB · Redis               │
                    │                                │
                    │  Native OS services:            │
                    │   BIND9 (already installed) ◄──────── pull agent (systemd
                    │   → answers real DNS on :53     │      timer, polls the
                    │                                │      app over HTTP)
                    └──────────────────────────────┘
                            ▲
                            │ DNS queries :53
                        resolvers
```

**Why are there two "binds"?** The Docker Compose stack includes its own
tiny `bind` container. It answers **no** DNS queries and isn't published on
any port — it exists purely so the `worker` container's `rndc reload` (part
of validating every zone change) has a real `named` to talk to. The BIND9
you actually care about — the one resolvers query on port 53 — is the one
already installed on this VM's bare OS. A small **pull agent** (a script,
not a container) bridges the two: it polls BindManager's REST API over
HTTP and writes/reloads zones on your real, already-installed BIND9. This
indirection is what lets the same mechanism scale to 2 or 20 nameservers
later without changing how the app itself works.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| A Linux VM (Debian/Ubuntu assumed below; adjust package manager for others) | |
| BIND9 already installed and running | `bind9`, `bind9-utils`, `bind9-dnsutils` packages; out of scope here — same as setting up any authoritative nameserver |
| Docker + Docker Compose | For the BindManager app stack |
| Root or sudo access | To install MariaDB/Redis and manage systemd units |
| No existing zone data to migrate | If you *do* have zones from another system, import them via the Django admin or API after this guide — not covered here |

---

## Part 1 — Install the database and cache on this VM

BindManager expects MySQL/MariaDB (or PostgreSQL) and Redis as regular
services, not containers — this keeps your data outside Docker's
lifecycle. We'll install both directly on the VM.

### 1.1 MariaDB

```bash
apt update && apt install -y mariadb-server
systemctl enable --now mariadb
```

**Make it reachable from Docker containers.** By default MariaDB only
listens on `127.0.0.1`, which Docker containers can't reach even via
`host.docker.internal`. Edit the bind address:

```bash
# Debian/Ubuntu path — find yours with: mysql --help | grep 'Default options' -A1
sed -i "s/^bind-address.*/bind-address = 0.0.0.0/" /etc/mysql/mariadb.conf.d/50-server.cnf
systemctl restart mariadb
```

Then restrict who can actually reach that port — Docker containers only,
not the whole internet:

```bash
ufw allow from 172.16.0.0/12 to any port 3306 proto tcp   # covers Docker's default bridge ranges
ufw deny 3306
```

### 1.2 Redis

```bash
apt install -y redis-server
```

Same reachability problem, plus Redis ships with no password by default —
set one:

```bash
sed -i "s/^bind .*/bind 0.0.0.0/" /etc/redis/redis.conf
sed -i "s/^# requirepass.*/requirepass $(openssl rand -hex 24)/" /etc/redis/redis.conf
grep '^requirepass' /etc/redis/redis.conf   # copy this value for .env in Part 2
systemctl restart redis-server

ufw allow from 172.16.0.0/12 to any port 6379 proto tcp
ufw deny 6379
```

> **Using PostgreSQL instead?** `apt install postgresql`, then set
> `DB_ENGINE=postgresql` in `.env` (Part 2.2). Initialize it as the
> `postgres` OS user, not root — Postgres uses peer auth locally, so plain
> `psql -U postgres` fails with `Peer authentication failed`:
> ```bash
> sudo -u postgres psql -f docker/postgres/init.sql
> ```
> Postgres also needs the same Docker-reachability treatment as MariaDB
> above: set `listen_addresses = '*'` in `postgresql.conf`, add a line to
> `pg_hba.conf` allowing `md5`/`scram-sha-256` auth from Docker's bridge
> ranges (`172.16.0.0/12`), then `systemctl restart postgresql` and repeat
> the `ufw` rule from 1.1 for port 5432 instead of 3306.

---

## Part 2 — Deploy the BindManager app

### 2.1 Clone and initialize the database

```bash
git clone <repo-url> bindmanager
cd bindmanager
sudo mysql -u root < docker/mysql/init.sql
```

Run as root/sudo, with no `-p` — a fresh MariaDB install authenticates the
local `root` account by matching OS user over the Unix socket, not by
password, so `mysql -u root -p` (prompting for one) fails with `Access
denied`. This creates database `bindmanager` and user `bindmanager@'%'`
(password `bindmanager` — you'll change it next).

### 2.2 Configure `.env`

```bash
cp .env.example .env
```

Minimum required edits:

```ini
DJANGO_SECRET_KEY=<generate one: python3 -c "import secrets; print(secrets.token_urlsafe(50))">
DJANGO_ALLOWED_HOSTS=localhost,<this-vm-ip-or-hostname>
CSRF_TRUSTED_ORIGINS=http://<this-vm-ip-or-hostname>:81

DB_HOST=host.docker.internal
DB_PASSWORD=<a real password — also update it in MariaDB: ALTER USER 'bindmanager'@'%' IDENTIFIED BY '...'>

REDIS_URL=redis://:<the requirepass value from Part 1.2>@host.docker.internal:6379/0
```

### 2.3 Build and start

```bash
docker compose up --build -d
docker compose ps
```

You should see **5** services: `web`, `nginx`, `worker`, `beat`, `bind`.

```bash
docker compose logs -f web    # confirm migrate + collectstatic ran clean
docker compose exec worker rndc status   # should print "server is up and running" — confirms the hidden `bind` container is reachable
```

### 2.4 Create a superuser

```bash
docker compose exec web python manage.py createsuperuser
```

Log in at `http://<this-vm-ip>:81/` with it.

---

## Part 3 — Turn on the sync engine (do not skip this — verify it)

Celery Beat's schedule lives in the database, not in code, so **nothing
syncs until you create it by hand.** This is the single most common way
this app ends up silently doing nothing — skipping it doesn't error, it
just leaves every future zone/record change sitting unsynced forever.

1. Go to `/admin/django_celery_beat/intervalschedule/add/` — add an
   interval, e.g. **every 1** minute.
2. Go to `/admin/django_celery_beat/periodictask/add/` — name it anything
   (e.g. "Sync dirty zones"), set **Task** to
   `apps.dns_manager.tasks.sync_dirty_zones`, **Interval Schedule** to the
   one you just created, **Enabled** checked. Save.

**Verify it actually works before moving on** — don't just trust that you
clicked the right things:

```bash
docker compose exec web python manage.py shell -c "
from apps.dns_manager.models import Zone
print(Zone.objects.count(), 'zones,', Zone.objects.filter(is_dirty=True).count(), 'dirty')
"
```

With zero zones so far this will print `0 zones, 0 dirty` — that's fine
for now. The real check comes after you create your first zone in Part 5:
if it's still showing `is_dirty=True` a couple of minutes later, the
schedule above isn't actually running — check `docker compose logs beat`
for a `Sending due task ... sync_dirty_zones` line before continuing.

---

## Part 4 — Point BindManager at this VM's own BIND9

This is the pull-agent setup — the same mechanism used for 2 or 20
nameservers, just pointed at `localhost` since everything is one box here.

### 4.1 Create the NameServer row

In the app: **Manage → Nameservers → + Add Nameserver**. `address` and
`config_dir` are informational (rendered into NS/SOA records) — use this
VM's real address and BIND9's actual zone directory (commonly
`/etc/bind/zones` or `/var/lib/bind`, depending on your distro's layout).
Save it, then copy the **Agent API Key** shown on the row — you'll need it
next.

### 4.2 Install the agent

```bash
mkdir -p /opt/bindmanager-agent /etc/bindmanager-agent
cp agents/bindmanager_agent.py /opt/bindmanager-agent/
cp agents/config.example.ini /etc/bindmanager-agent/config.ini
cp agents/systemd/bindmanager-agent.{service,timer} /etc/systemd/system/
```

Edit `/etc/bindmanager-agent/config.ini`:

```ini
[agent]
api_url = http://localhost:81/api/v1
api_key = <the Agent API Key from 4.1>
zones_dir = /etc/bind/zones            # match your BIND9's real zone directory
named_conf_include = /etc/bind/named.bindmanager.conf
rndc_bin = rndc
checkzone_bin = named-checkzone
checkconf_bin = named-checkconf
```

```bash
chmod 600 /etc/bindmanager-agent/config.ini   # it's a bearer credential
```

Add one line to this VM's `named.conf` (once):

```
include "/etc/bind/named.bindmanager.conf";
```

An empty file at that path is fine — the agent creates it on first run.

### 4.3 Dry-run, then enable

```bash
python3 /opt/bindmanager-agent/bindmanager_agent.py \
  --config /etc/bindmanager-agent/config.ini --dry-run -v
```

With no zones assigned yet, this should report an empty list and touch
nothing. Then:

```bash
systemctl daemon-reload
systemctl enable --now bindmanager-agent.timer
systemctl list-timers bindmanager-agent.timer   # confirm it's scheduled (every 2 min)
```

> **Prefer the agent containerized instead of a systemd timer?** See
> [`docker/bind9-agent/README.md`](docker/bind9-agent/README.md) — same
> setup, packaged as a container that reaches this VM's BIND9 via
> `network_mode: host`. Functionally identical; pick whichever fits your
> ops workflow.

---

## Part 5 — Create your first zone and confirm it's live end-to-end

1. **Manage → Zones → Add Zone.** Enter a domain you control (or a
   throwaway one for testing), assign it to the NameServer row from 4.1,
   save.
2. **Manage → Zones → your zone → Add Record.** Add at least an `A`
   record (e.g. `www` → an IP).
3. Wait for the Beat interval (Part 3), then check:
   ```bash
   docker compose logs -f worker
   ```
   You should see the zone rendered, validated with `named-checkzone`,
   and reloaded — and its dirty flag clear (re-run the shell check from
   Part 3).
4. Wait for the agent's timer interval (~2 min), then on this VM:
   ```bash
   journalctl -u bindmanager-agent.service -n 20
   ls -la /etc/bind/zones/            # your zone's .zone file should appear
   ```
5. Confirm it's actually answering:
   ```bash
   dig @localhost yourzone.example.com SOA
   dig @localhost www.yourzone.example.com A
   ```

If all of that resolves, the full pipeline — UI → database → Celery sync
→ validation → agent pull → BIND9 → real DNS answer — is working.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Zone stays `is_dirty=True` indefinitely | No Celery Beat periodic task created (Part 3), or it's disabled | Check `/admin/django_celery_beat/periodictask/`; check `docker compose logs beat` for the "Sending due task" line |
| `docker compose exec worker rndc status` fails | Hidden `bind` container unhealthy | `docker compose logs bind`; `docker compose ps` should show it `healthy` |
| `web`/`worker` can't reach MariaDB/Redis | Service still bound to `127.0.0.1` only | Re-check Part 1.1 / 1.2's `bind-address` edits; `systemctl restart mariadb redis-server` after changing |
| Zone `"has no address records"` in `docker compose logs bind` | An in-bailiwick NS record (e.g. `ns1.<zone>`) has no matching A/AAAA record in the same zone | Add the missing glue record |
| Agent's `--dry-run` shows an empty zone list forever | Zone not yet `is_dirty=False` (Celery hasn't synced it), or the NameServer row has no zones assigned, or `is_active=False` | Check Manage → Zones assignment; confirm Part 3's checkpoint passed |
| Agent gets HTTP 401 | Wrong API key in `config.ini`, or it was regenerated since | Re-copy the key from Manage → Nameservers |
| Agent gets HTTP 404 on a specific zone | Zone isn't assigned to this NameServer, or still `is_dirty=True` | Check assignment / wait for sync |
| `systemctl status bindmanager-agent.timer` shows inactive | Timer not enabled | `systemctl enable --now bindmanager-agent.timer` |
| `dig @localhost` returns nothing | `named.conf`'s `include` line missing, or BIND9 needs a reload | Confirm the include from 4.2; `rndc reload` manually to see the real error |

---

## Security checklist before going further

- [ ] Changed the default `bindmanager` DB password (MariaDB **and** `.env`)
- [ ] Generated a real `DJANGO_SECRET_KEY`
- [ ] Set a Redis password and confirmed `REDIS_URL` includes it
- [ ] Firewalled MariaDB (3306) and Redis (6379) to Docker's subnet only — not `0.0.0.0/0`
- [ ] `chmod 600` on `/etc/bindmanager-agent/config.ini` (it's an API key)
- [ ] TLS terminated in front of Nginx — this repo runs plain HTTP on :81 by default; put a reverse proxy or load balancer with a real cert in front for anything beyond a lab
- [ ] Rotate the NameServer's API key if it ever leaks: Manage → Nameservers → select row → **Regenerate API key**, then update `config.ini` and restart the timer
- [ ] `.env` and `/etc/bindmanager-agent/config.ini` are not committed to version control

---

## Scaling beyond one server

Everything above generalizes without any rework: create another
**NameServer** row in Manage → Nameservers for the new box, copy its API
key, and repeat Part 4 on that host instead (pointing `api_url` at this
VM's real address, not `localhost`). Three ways to do that, depending on
what the new box looks like:

- **Bare metal with BIND9 already there** — same as Part 4 above.
- **Fresh box, nothing installed yet** — [`docker/bind9-node/README.md`](docker/bind9-node/README.md) bakes BIND9 + the agent into one container.
- **BIND9 already there, but you want the agent containerized anyway** — [`docker/bind9-agent/README.md`](docker/bind9-agent/README.md).

The BindManager app itself (Part 2) is deployed exactly once, regardless
of how many nameservers you add. See [`agents/README.md`](agents/README.md)
for the full agent reference.
