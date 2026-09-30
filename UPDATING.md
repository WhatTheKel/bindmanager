# Updating BindManager

How to move an existing install to the latest code on `main`. There are two
halves, updated separately:

| Where | What you run | How often |
|---|---|---|
| **App host** (the Docker server) | `git pull` + rebuild the containers | Every update |
| **Each nameserver** (the pull agent) | Copy one file, only if it changed | Rarely — most updates don't touch the agent |

Your own settings and data are never in git, so an update leaves them alone:
`.env`, `.env.agent`, `.env.node`, `bind_zones/`, `staticfiles/`,
`branding/` logos, `/etc/bindmanager-agent/config.ini`, and the database
contents.

Paths below use `/opt/bindmanager` for the app checkout (as in
[`INSTALL-RHEL8.md`](INSTALL-RHEL8.md)) and `/root/bindmanager` for a
nameserver's checkout (as in
[`INSTALL-NAMESERVER-RHEL8.md`](INSTALL-NAMESERVER-RHEL8.md)). Use wherever
you actually cloned it.

---

## Part 0 — Before you update (app host)

### 0.1 See what's coming

```bash
cd /opt/bindmanager
git fetch
git log --oneline HEAD..origin/main              # the new commits (empty = already up to date)
git diff --stat HEAD origin/main                 # which files change
git diff --stat HEAD origin/main -- '*/migrations/*' .env.example agents/
```

The last line tells you what needs extra attention:

| If it lists… | It means |
|---|---|
| `…/migrations/…` | The database structure changes. It's applied automatically when `web` starts, but **take the backup in 0.3** — you can't undo it without one |
| `.env.example` | A setting was added or changed. See step 1.2 |
| `agents/…` | The nameserver agent changed. Do Part 2 on every nameserver after the app is updated |

### 0.2 Check for local edits

```bash
git status --short
```

Empty output is what you want. If it lists files you changed yourself
(say `nginx/nginx.conf` or `docker-compose.yml`), `git pull` may refuse to
overwrite them. Set them aside, pull, then re-apply:

```bash
git stash          # before the pull
git stash pop      # after the pull — fix any conflicts it reports
```

### 0.3 Back up the database

Always worth doing; essential if 0.1 listed migrations. On the host that
runs MariaDB (the app host, in every install guide here):

```bash
mkdir -p /root/backups
mysqldump -u root --single-transaction --routines bindmanager \
  | gzip > /root/backups/bindmanager-$(date +%Y%m%d-%H%M%S).sql.gz
ls -lh /root/backups | tail -1        # should not be a few bytes
```

(On Debian/Ubuntu, prefix `sudo`. As OS root, `mysql`/`mysqldump -u root`
needs no password.)

---

## Part 1 — Update the app host

### 1.1 Pull the new code

```bash
cd /opt/bindmanager
git pull
```

### 1.2 Add any new settings to `.env`

Only needed if 0.1 showed `.env.example` changed. List setting names that
are in the example but missing from your `.env`:

```bash
comm -23 <(grep -o '^[A-Z_]*=' .env.example | sort) <(grep -o '^[A-Z_]*=' .env | sort)
```

Empty output = nothing to add. Otherwise copy each listed line from
`.env.example` into `.env` and set a real value. Read the comment above it in
`.env.example` — a setting may be optional.

### 1.3 Rebuild and restart

```bash
docker compose up -d --build
docker compose restart nginx
```

- `--build` rebuilds the image with the new code; containers are only
  recreated if something changed. The app is unavailable for a few seconds
  while `web` restarts.
- On start, `web` runs `migrate` (database changes) and `collectstatic`
  (CSS/JS) by itself — nothing to run by hand.
- **Always restart nginx afterwards.** nginx looks up the `web` container's
  address once, when it starts. A rebuild can give `web` a new address, and
  nginx then answers every page with **502 Bad Gateway** until restarted.

### 1.4 Check it

```bash
docker compose ps                                  # all running, none restarting
docker compose logs --since 5m web | grep -iE 'error|traceback|migrat'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:81/accounts/login/   # 200
docker compose logs --since 5m worker | grep -iE 'error|traceback'
```

Then log in and open a zone. Within a couple of minutes of an edit, the zone
should stop showing as unsynced — that confirms the worker and sync schedule
survived the update.

---

## Part 2 — Update each nameserver's agent

**Skip this entirely if 0.1 listed nothing under `agents/`.** The agent talks
to a stable API, so app-only updates never require touching nameservers.

Pick the section that matches how that nameserver was installed.

### 2.1 Agent installed on the host (systemd timer — the install guides' default)

```bash
cd /root/bindmanager
git pull
cmp agents/bindmanager_agent.py /opt/bindmanager-agent/bindmanager_agent.py \
  && echo "agent already current" \
  || \cp -f agents/bindmanager_agent.py /opt/bindmanager-agent/
```

No restart needed: the timer starts the script fresh every run, so the next
run (within 2 minutes) uses the new version. Your `config.ini` is untouched.

Check the next run:

```bash
python3.9 /opt/bindmanager-agent/bindmanager_agent.py \
  --config /etc/bindmanager-agent/config.ini --dry-run -v   # Debian: python3
journalctl -u bindmanager-agent --since '-5 min'
```

**Only if `agents/systemd/` changed too** (0.1 shows it), re-install the unit
files. On RHEL 8, re-apply the Python 3.9 edit — copying the unit resets it:

```bash
\cp -f agents/systemd/bindmanager-agent.{service,timer} /etc/systemd/system/
sed -i 's|/usr/bin/python3 |/usr/bin/python3.9 |' /etc/systemd/system/bindmanager-agent.service   # RHEL 8 only
systemctl daemon-reload
```

**If `agents/config.example.ini` changed,** compare it with your
`/etc/bindmanager-agent/config.ini` and add any new settings by hand. Never
copy the example over your config — it would wipe your API key and paths.

**No git on the nameserver?** Re-download the file instead of `git pull`:

```bash
curl -fsSL https://raw.githubusercontent.com/WhatTheKel/bindmanager/main/agents/bindmanager_agent.py \
  -o /opt/bindmanager-agent/bindmanager_agent.py
```

(Or `scp` it from the app host's checkout.)

### 2.2 Agent in Docker, BIND on the host (`docker-compose.agent.yml`)

```bash
cd /root/bindmanager
git pull
docker compose -f docker-compose.agent.yml up -d --build
```

### 2.3 BIND and agent together in Docker (`docker-compose.node.yml`)

```bash
cd /root/bindmanager
git pull
docker compose -f docker-compose.node.yml up -d --build
```

This restarts the container, so this nameserver stops answering for a few
seconds. With more than one nameserver, update them one at a time.

### 2.4 Single-server installs

If the app and BIND are on the same machine
([`INSTALL-RHEL8.md`](INSTALL-RHEL8.md) or
[`INSTALL-DEBIAN.md`](INSTALL-DEBIAN.md)), the agent was installed from the
same checkout. Part 1's `git pull` already fetched the new agent; just run the
`cmp … || \cp -f …` line from 2.1 in `/opt/bindmanager` (no second pull).

---

## Rolling back

If an update misbehaves, go back to the commit you were on.

```bash
cd /opt/bindmanager
git log --oneline -10               # find the commit before the update
git checkout <old-commit>
```

**If the update had no migrations** (0.1 listed none), just rebuild:

```bash
docker compose up -d --build && docker compose restart nginx
```

**If it had migrations,** the database has already moved to the new
structure, and the old code may fail against it. Restore the backup from 0.3
first:

```bash
docker compose stop web worker beat
gunzip -c /root/backups/bindmanager-<timestamp>.sql.gz | mysql -u root bindmanager
docker compose up -d --build && docker compose restart nginx
```

Anything changed in the app after that backup (zones, records, users) is lost
by the restore, so keep the gap short.

To return to the latest code later: `git checkout main && git pull`, then
Part 1 again.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `git pull` says *Your local changes would be overwritten* | You edited tracked files. Use `git stash` / `git stash pop` as in 0.2 |
| Build fails at `apt-get … exit code: 100` | Usually a temporary failure downloading Debian packages. Re-run `docker compose up -d --build`; the old containers keep running until a build succeeds |
| Every page is **502 Bad Gateway** after the rebuild | nginx still points at the old `web` container. `docker compose restart nginx` |
| `web` keeps restarting; logs show a migration error | Read the error in `docker compose logs web`. Don't loop restarts — roll back (restore the backup, check out the old commit) and report the error |
| Zones stay unsynced after the update | `docker compose logs worker` for errors; check the "Sync dirty zones" periodic task is still enabled in `/admin/` → **Periodic tasks** |
| Nameserver agent logs `401` after an update | The app didn't change keys on update — check `api_key` in `config.ini` still matches the nameserver's key in **Manage > Nameservers** |

---

## Quick reference

```bash
# App host
cd /opt/bindmanager
mkdir -p /root/backups
git fetch && git diff --stat HEAD origin/main -- '*/migrations/*' .env.example agents/
mysqldump -u root --single-transaction --routines bindmanager | gzip > /root/backups/bindmanager-$(date +%Y%m%d-%H%M%S).sql.gz
git pull
docker compose up -d --build && docker compose restart nginx

# Each nameserver — only if agents/ changed
cd /root/bindmanager && git pull
cmp agents/bindmanager_agent.py /opt/bindmanager-agent/bindmanager_agent.py || \cp -f agents/bindmanager_agent.py /opt/bindmanager-agent/
```
