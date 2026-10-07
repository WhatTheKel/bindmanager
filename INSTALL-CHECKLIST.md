# BindManager — Quick Install Checklist

The short version, for when you've done this before. Every step is a
command to paste or a field to fill in. For the *why* behind each step
and full troubleshooting, see [`INSTALL-RHEL.md`](INSTALL-RHEL.md) or
[`INSTALL-DEBIAN.md`](INSTALL-DEBIAN.md) (the app, from scratch) and
[`INSTALL-NAMESERVER-RHEL.md`](INSTALL-NAMESERVER-RHEL.md) (a RHEL 8, 9 or 10 nameserver).

Fill these in once, then substitute them below:

| Placeholder | Meaning | Example |
|---|---|---|
| `APP_IP` | Host running the BindManager app (Docker) | `192.0.2.10` |
| `NS_IP` | The RHEL 8, 9 or 10 VM that will answer DNS | `192.0.2.20` |
| `NS_NAME` | That VM's DNS name | `ns1.example.com` |
| `ZONE` | A domain to test with | `example.com` |

---

## A. App host — once

Assumes MariaDB, Redis and Docker are installed and `.env` is filled in
(Parts 1–2 of `INSTALL-RHEL.md` or `INSTALL-DEBIAN.md`).

```bash
cd bindmanager
mysql -u root < docker/mysql/init.sql          # first time only
docker compose up -d --build
docker compose logs web | grep -E 'Error|Listening'   # want "Listening at"
docker compose exec web python manage.py createsuperuser
```

**Create the sync schedule** — without it, nothing ever syncs and there's
no error to tell you:

```bash
docker compose exec -T web python manage.py shell -c "
from django_celery_beat.models import IntervalSchedule, PeriodicTask
s,_=IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.MINUTES)
PeriodicTask.objects.get_or_create(name='Sync dirty zones', defaults=dict(
    task='apps.dns_manager.tasks.sync_dirty_zones', interval=s, enabled=True))"
docker compose logs --since 2m beat | grep sync_dirty_zones   # want "Sending due task"
```

☐ Login works at `http://APP_IP:81/` ☐ beat shows `Sending due task`

---

## B. RHEL nameserver (8, 9 or 10) — per server

### B1. BIND

```bash
dnf install -y bind bind-utils git
dnf install -y python39                  # RHEL 8 only — skip on RHEL 9 and 10
```

In `/etc/named.conf`, inside `options { }`, set:

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
rndc status | head -1                    # "server is up and running"
firewall-cmd --permanent --add-service=dns && firewall-cmd --reload
```

### B2. Register it in the app

**Manage → Nameservers → + Add:** name `NS_NAME` (full DNS name — this is
what goes into the zones' NS records), address `NS_IP`, config dir
`/var/named/bindmanager`, active ✔. Save, copy the **Agent API Key**
from the list (shown only once — **Regenerate** makes a new one). Don't assign zones yet.

### B3. Agent

```bash
git clone https://github.com/WhatTheKel/bindmanager.git /root/bindmanager
cd /root/bindmanager
mkdir -p /opt/bindmanager-agent /etc/bindmanager-agent
\cp -f agents/bindmanager_agent.py /opt/bindmanager-agent/
\cp -f agents/config.example.ini /etc/bindmanager-agent/config.ini
\cp -f agents/systemd/bindmanager-agent.{service,timer} /etc/systemd/system/
sed -i 's|/usr/bin/python3 |/usr/bin/python3.9 |' /etc/systemd/system/bindmanager-agent.service   # RHEL 8 ONLY — skip on 9/10
chmod 600 /etc/bindmanager-agent/config.ini
```

Configure — **all four lines**; the example file has Debian `/etc/bind`
paths that break on RHEL:

```bash
APP_URL=http://APP_IP:81
API_KEY='paste-key-here'
sed -i \
  -e "s|^api_url.*|api_url = ${APP_URL}/api/v1|" \
  -e "s|^api_key.*|api_key = ${API_KEY}|" \
  -e "s|^zones_dir.*|zones_dir = /var/named/bindmanager|" \
  -e "s|^named_conf_include.*|named_conf_include = /etc/named/bindmanager.conf|" \
  /etc/bindmanager-agent/config.ini
grep -n '/etc/bind/' /etc/bindmanager-agent/config.ini && echo "FIX THESE" || echo "paths OK"
```

Test, then enable:

```bash
python3 /opt/bindmanager-agent/bindmanager_agent.py --config /etc/bindmanager-agent/config.ini --dry-run -v   # RHEL 8: python3.9
# want: "dry-run: 0 changed, 0 removed"
systemctl daemon-reload && systemctl enable --now bindmanager-agent.timer
```

☐ `rndc status` up ☐ config says `paths OK` ☐ dry run prints the line above

---

## C. Test zone — prove it end to end

**Manage → Zones → Add Zone:** name `ZONE`, tick nameserver `NS_NAME`
(the first ticked nameserver becomes the SOA primary). Then add records:

| Name | Type | Value |
|---|---|---|
| `ns1` | **A** (not NS!) | `NS_IP` |
| `www` | A | any valid IP |

Don't add an NS record — the app generates it from the assigned nameserver.

Wait ~3 minutes (app sync 1 min + agent 2 min), or on the VM:

```bash
systemctl start bindmanager-agent.service
journalctl -u bindmanager-agent.service -n 5 --no-pager   # want "sync complete: 1 changed"
```

From **another machine**:

```bash
dig @NS_IP ZONE SOA +norec
dig @NS_IP www.ZONE A +norec
dig @NS_IP ns1.ZONE A +norec
```

☐ Manage → Nameservers shows this server's agent version, "checked in just now"
☐ all three `NOERROR` with the `aa` flag → done. Only now add the server
to public NS records / the registrar.

---

## D. Updating later

Full version with rollback: [`UPDATING.md`](UPDATING.md).

```bash
# App host (your checkout from section A)
cd bindmanager
mkdir -p /root/backups
git fetch && cat VERSION && git show origin/main:VERSION   # current → latest
git diff --stat HEAD origin/main -- '*/migrations/*' .env.example agents/
mysqldump -u root --single-transaction --routines bindmanager | gzip > /root/backups/bindmanager-$(date +%Y%m%d-%H%M%S).sql.gz
git pull
docker compose up -d --build && docker compose restart nginx

# Each nameserver Manage → Nameservers marks Outdated/Unknown (as root)
cd /root/bindmanager && git pull && ./agents/update-agent.sh
```

☐ login page loads (no 502) ☐ footer shows the new version ☐ an edited zone syncs within ~2 min
☐ Manage → Nameservers: every agent shows the latest version, no notice

---

## If it doesn't work

| You see | Check |
|---|---|
| `dig` → `REFUSED`, named log says `query (cache) ... denied` | Zone not loaded → `journalctl -u bindmanager-agent.service` |
| Agent: `FileNotFoundError ... /etc/bind/...` | A Debian path left in `config.ini` → rerun the `sed` in B3, then `systemctl start bindmanager-agent.service` |
| Agent: HTTP 401 | Wrong API key → **Regenerate** it in Manage → Nameservers and put the new one in `config.ini` |
| Agent: `URLError` / timeout | VM can't reach `APP_IP:81` → `curl http://APP_IP:81/` |
| Zone never reaches the agent | App side not syncing → `docker compose logs worker \| grep -A3 checkzone` shows why; is the sync schedule (A) there? |
| `ns1.ZONE` no answer, no `aa` | `ns1` is an NS record → make it an A record |
| Remote `dig` times out, local works | `firewall-cmd --list-services` includes `dns`? `listen-on { any; }`? |

Full table: [`INSTALL-NAMESERVER-RHEL.md` → Troubleshooting](INSTALL-NAMESERVER-RHEL.md#troubleshooting-rhel-specific).
