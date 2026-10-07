# BindManager — Installation Guide (Debian/Ubuntu, Single-Server, From Scratch)

This is a start-to-finish runbook for the most common real starting point:
**one brand-new Debian/Ubuntu VM, no existing zone data, and you want
BindManager to manage the DNS it serves.** Everything — BIND9, the
BindManager app, and the pull agent that connects them — runs on this one
box. (On RHEL 8, 9 or 10, use [`INSTALL-RHEL.md`](INSTALL-RHEL.md) instead.)

If you later add a second or third physical nameserver, see
[Scaling beyond one server](#scaling-beyond-one-server) at the end — you
don't need to plan for that now, and nothing in this guide has to be redone.

> **Done this before?** [`INSTALL-CHECKLIST.md`](INSTALL-CHECKLIST.md) is a
> short copy-paste checklist of the whole install.

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
                    │   BIND9 (Part 0)            ◄──────── pull agent (systemd
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
you install on this VM's bare OS in Part 0. A small **pull agent** (a
script, not a container) bridges the two: it polls BindManager's REST API
over HTTP and writes/reloads zones on that real BIND9. This
indirection is what lets the same mechanism scale to 2 or 20 nameservers
later without changing how the app itself works.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| A Debian 12 / Ubuntu 22.04+ VM with a static IP | On RHEL 8, 9 or 10 use [`INSTALL-RHEL.md`](INSTALL-RHEL.md) — package names, paths, SELinux and the agent's Python version all differ |
| Docker + Docker Compose | For the BindManager app stack |
| Root or sudo access | To install MariaDB/Redis and manage systemd units |
| No existing zone data to migrate | If you *do* have zones from another system, import them via the Django admin or API after this guide — not covered here |

---

## Part 0 — Install BIND9 as an authoritative nameserver

```bash
apt update && apt install -y bind9 bind9-utils bind9-dnsutils
```

The stock config is a localhost-oriented caching resolver. Replace the
`options { ... }` block in `/etc/bind/named.conf.options` so it answers
authoritatively for everyone and recurses for no one:

```
options {
        directory "/var/cache/bind";
        listen-on { any; };
        listen-on-v6 { any; };
        allow-query { any; };
        recursion no;
        notify no;
};
```

- `recursion no` — an internet-facing open resolver gets abused for
  amplification attacks; an authoritative server shouldn't recurse.
- `notify no` — the agent writes every zone as `type master` on every
  server, each fed by BindManager directly, so there are no secondaries
  to NOTIFY.

Create the zone directory the agent will write into, then check and
restart:

```bash
mkdir -p /etc/bind/zones
named-checkconf && systemctl restart named     # the unit is `bind9` on older releases
systemctl enable named
rndc status | head -1                           # "server is up and running"
ufw allow 53                                    # if ufw is enabled (covers UDP and TCP)
```

`rndc` works out of the box — the package generates `/etc/bind/rndc.key`
at install time. The `include` line that hooks BindManager's zones into
BIND is added in Part 4.2.

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

Or do both in one command instead of clicking through the admin:

```bash
docker compose exec -T web python manage.py shell -c "
from django_celery_beat.models import IntervalSchedule, PeriodicTask
s,_=IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.MINUTES)
PeriodicTask.objects.get_or_create(name='Sync dirty zones', defaults=dict(
    task='apps.dns_manager.tasks.sync_dirty_zones', interval=s, enabled=True))"
```

Within a minute, `docker compose logs beat` should show
`Sending due task Sync dirty zones`.

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

In the app: **Manage → Nameservers → + Add Nameserver**.

- `name` — the nameserver's fully-qualified DNS name (e.g.
  `ns1.example.com`). **This is the only field that ends up in zone
  files:** every zone assigned to this server gets an `NS` record for it,
  and the first assigned nameserver becomes the SOA primary.
- `address` and `config_dir` — informational only (shown in the UI, not
  rendered anywhere, not read by the agent). Use this VM's real IP and
  BIND9's actual zone directory (commonly `/etc/bind/zones`).

Save it, then copy the **Agent API Key** shown on the row — you'll need it
next. It is shown in full only once, on the list right after saving — copy it then (lost it? **Regenerate** on the row makes a new one).

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
2. **Manage → Zones → your zone → Add Record.** Add:
   - an **A** record for the nameserver's own name if it's inside this
     zone — e.g. `ns1` → this VM's IP when the NameServer row is
     `ns1.<your zone>`. Without this "glue" record `named-checkzone`
     rejects the zone (`has no address records`) and it never syncs.
   - at least one more record to test with, e.g. `www` **A** → an IP.

   Don't add an NS record yourself — BindManager generates it from the
   zone's assigned nameservers. And never create `ns1` as an **NS** record
   with an IP as its value (a common slip when you mean "ns1's address is
   …"): that makes BIND treat `ns1` as a delegated sub-zone, so the name
   never resolves. The app rejects that combination when you save.
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
| Zone stays `is_dirty=True`, worker logs `named-checkzone failed for <zone>` | A record in the zone makes it invalid | The lines after that message give `named-checkzone`'s reason; fix that record in the UI and the next sync picks it up |
| `docker compose exec worker rndc status` fails | Hidden `bind` container unhealthy | `docker compose logs bind`; `docker compose ps` should show it `healthy` |
| `web`/`worker` can't reach MariaDB/Redis | Service still bound to `127.0.0.1` only | Re-check Part 1.1 / 1.2's `bind-address` edits; `systemctl restart mariadb redis-server` after changing |
| Zone `"has no address records"` in `docker compose logs bind` | An in-bailiwick NS record (e.g. `ns1.<zone>`) has no matching A/AAAA record in the same zone | Add the missing glue record |
| Agent's `--dry-run` shows an empty zone list forever | Zone hasn't passed its first sync yet (Celery not running, or a record fails `named-checkzone`), or the NameServer row has no zones assigned, or `is_active=False` | Check Manage → Zones assignment; confirm Part 3's checkpoint passed |
| Agent gets HTTP 401 | Wrong API key in `config.ini`, or it was regenerated since | Regenerate the key in Manage → Nameservers and put the new one in `config.ini` |
| Agent gets HTTP 404 on a specific zone | Zone isn't assigned to this NameServer, or has never passed a sync | Check assignment / wait for sync |
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
- [ ] Rotate the NameServer's API key if it ever leaks: **Manage → Nameservers** → **Regenerate** on the row, then update `config.ini` on that server (the old key stops working immediately)
- [ ] `.env` and `/etc/bindmanager-agent/config.ini` are not committed to version control

---

## Updating later

See [`UPDATING.md`](UPDATING.md). In short: on this server, `cd <your checkout> &&
git pull && docker compose up -d --build && docker compose restart nginx`.
If **Manage → Nameservers** then marks this server's agent *Outdated*
(or *Unknown*), run `./agents/update-agent.sh` as root in the same
checkout (UPDATING.md Part 2.4). Back up the database first if the update includes migrations. Afterwards
the page footer shows the new version (compare with `cat VERSION`).

---

## Scaling beyond one server

Everything above generalizes without any rework: create another
**NameServer** row in Manage → Nameservers for the new box, copy its API
key, and repeat Part 4 on that host instead (pointing `api_url` at this
VM's real address, not `localhost`). Three ways to do that, depending on
what the new box looks like:

- **Bare metal with BIND9 already there** — same as Part 4 above.
- **RHEL 8, 9 or 10 box** — [`INSTALL-NAMESERVER-RHEL.md`](INSTALL-NAMESERVER-RHEL.md) (BIND, SELinux, firewalld, the agent's Python on RHEL 8).
- **Fresh box, nothing installed yet** — [`docker/bind9-node/README.md`](docker/bind9-node/README.md) bakes BIND9 + the agent into one container.
- **BIND9 already there, but you want the agent containerized anyway** — [`docker/bind9-agent/README.md`](docker/bind9-agent/README.md).

The BindManager app itself (Part 2) is deployed exactly once, regardless
of how many nameservers you add. See [`agents/README.md`](agents/README.md)
for the full agent reference.
