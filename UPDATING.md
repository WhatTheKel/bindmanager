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
cat VERSION; git show origin/main:VERSION         # your version, then the latest
git log --oneline HEAD..origin/main              # the new commits (empty = already up to date)
git diff --stat HEAD origin/main                 # which files change
git diff --stat HEAD origin/main -- '*/migrations/*' .env.example agents/ docker/bind9-* docker-compose.*.yml
```

The last line tells you what needs extra attention:

| If it lists… | It means |
|---|---|
| `…/migrations/…` | The database structure changes. It's applied automatically when `web` starts, but **take the backup in 0.3** — you can't undo it without one |
| `.env.example` | A setting was added or changed. See step 1.2 |
| `agents/…` | The nameserver agent changed. Do Part 2 on every nameserver after the app is updated |
| `docker/bind9-…` or `docker-compose.agent.yml` / `.node.yml` | Only matters for Docker-based nameservers (Part 2.2 / 2.3) |

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

Commented-out settings in `.env.example` (e.g. `# TRUSTED_PROXY_COUNT=1`)
are optional with safe defaults, so the command above doesn't list them.
Skim the diff of `.env.example` from 0.1 for any you might need.

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

Then log in: the page footer shows the version (`vX.Y.Z`), which should now
match `cat VERSION`. Open a zone. Within a couple of minutes of an edit, the zone
should stop showing as unsynced — that confirms the worker and sync schedule
survived the update.

---

## Part 2 — Update each nameserver's agent

**Skip this entirely if 0.1 listed nothing under `agents/`** (or, for Docker
nameservers, `docker/bind9-…` and their compose file). The agent talks to a
stable API, so app-only updates never require touching nameservers.

**Which agents need it:** after updating the app, open **Manage →
Nameservers**. The *Agent* column shows the version each nameserver's agent
last reported (it checks in every 2 minutes), and the subtitle shows the
latest agent version the app ships. Anything marked **Outdated** or
**Unknown** (agents from before 0.2.4 don't report a version)
should be updated below. **Stale** means the agent stopped checking in —
look at its timer and journal.

Pick the section that matches how that nameserver was installed.

### 2.1 Agent installed on the host (systemd timer — the install guides' default)

Run the update script as root:

```bash
cd /root/bindmanager
git pull                          # fetches the latest update-agent.sh too
./agents/update-agent.sh          # add --check to only see whether an update is due
```

It finds the installed agent, its Python (e.g. `python3.9` on RHEL 8) and
`config.ini` from the systemd unit, checks the new agent runs, backs up the
old one, installs the new one, dry-runs it against the app — **putting the
old one back if that fails** — and runs it once so **Manage → Nameservers**
shows the new version straight away. It never changes `config.ini` or the
systemd units (it tells you if the units changed; see below).

**No clone on the nameserver?** Download the script and let it download the agent:

```bash
curl -fsSLO https://raw.githubusercontent.com/WhatTheKel/bindmanager/main/agents/update-agent.sh
bash update-agent.sh              # --ref v0.2.4 installs a specific release
```

No internet on the nameserver? Copy `bindmanager_agent.py` over (e.g. `scp`
from the app host) and run `bash update-agent.sh --source ./bindmanager_agent.py`.

<details><summary>Doing it by hand instead</summary>

```bash
cd /root/bindmanager && git pull
cmp agents/bindmanager_agent.py /opt/bindmanager-agent/bindmanager_agent.py \
  && echo "agent already current" \
  || \cp -f agents/bindmanager_agent.py /opt/bindmanager-agent/
```

No restart needed: the timer starts the script fresh every run, so the next
run (within 2 minutes) uses the new version.

</details>

Check it by hand if you like:

```bash
python3.9 /opt/bindmanager-agent/bindmanager_agent.py --version  # Debian: python3
python3.9 /opt/bindmanager-agent/bindmanager_agent.py \
  --config /etc/bindmanager-agent/config.ini --dry-run -v
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
same checkout. Part 1's `git pull` already fetched the new agent; just run
`./agents/update-agent.sh` in `/opt/bindmanager` (it pulls again, harmlessly).

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
| Login page says *Too many failed login attempts* | 10 failed logins from that IP in 5 minutes; it clears 5 minutes after the last one. To clear it now: `docker compose exec web python manage.py shell -c "from django.core.cache import cache; cache.delete('login_fail:<client-ip>')"` (the IP is in the audit log's failed-login entries) |
| Manage → Nameservers shows an agent as *Stale* | That nameserver's agent stopped checking in: `systemctl status bindmanager-agent.timer` and `journalctl -u bindmanager-agent -n 50` on it |
| A script gets `401` with `Authorization: Token bmt_…` | The token was revoked, has expired, or its user was deactivated — the response's `detail` says which. Create a new one under user menu → **API tokens** |
| Nameserver agent logs `401` after an update | The app didn't change keys on update — check `api_key` in `config.ini` still matches the nameserver's key in **Manage > Nameservers** |

---

## Releasing a new version (maintainers)

The version shown in the page footer comes from the `VERSION` file at the
top of the repo. When pushing changes that users should pick up:

1. Bump `VERSION` — `MAJOR.MINOR.PATCH`: patch for fixes, minor for new
   features, major for changes that need manual steps (new required `.env`
   settings, agent changes that must be rolled out together with the app).
2. **If `agents/bindmanager_agent.py` changed** in this release, set its
   `AGENT_VERSION` to the same number and copy the file to
   `docker/bind9-agent/` and `docker/bind9-node/` (the three must stay
   identical). That's what marks older agents as *Outdated* in Manage →
   Nameservers. Leave it alone if the agent didn't change.
3. Commit it with the changes, then tag and push the tag:

   ```bash
   git tag -a v0.2.0 -m "v0.2.0"
   git push && git push --tags
   ```

Users can then see what they run in the footer and compare with
`git show origin/main:VERSION`. Nothing breaks if you forget to bump it — the
footer just shows the old number.

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

# Each nameserver — only if Manage → Nameservers shows its agent as Outdated/Unknown
cd /root/bindmanager && git pull && ./agents/update-agent.sh
```
