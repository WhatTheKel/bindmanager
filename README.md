# BindManager

A modern, Django-based web application for managing BIND DNS — a web-based BIND DNS management interface with a REST API, light/dark UI, and a background zone-file sync engine.

---

## Documentation — which file to read

BindManager has two halves: the **app** (web UI + API + sync engine, run
once with Docker) and one or more **nameservers** (plain BIND plus a small
pull agent that fetches zones from the app). Pick the guide for the half
you're setting up:

| I want to… | Read |
|---|---|
| Install everything from scratch on one **RHEL 8** server (BIND + app + agent) | [`INSTALL-RHEL8.md`](INSTALL-RHEL8.md) |
| Install everything from scratch on one **Debian/Ubuntu** server (BIND + app + agent) | [`INSTALL-DEBIAN.md`](INSTALL-DEBIAN.md) — also the fullest explanation of how the pieces fit |
| Add a **RHEL 8** server as an extra nameserver for an app that's already running | [`INSTALL-NAMESERVER-RHEL8.md`](INSTALL-NAMESERVER-RHEL8.md) — BIND, SELinux, firewalld, agent, troubleshooting |
| Repeat an install I've done before, quickly | [`INSTALL-CHECKLIST.md`](INSTALL-CHECKLIST.md) — copy-paste steps only (app + RHEL 8 nameserver + test zone) |
| Understand or configure the pull agent itself | [`agents/README.md`](agents/README.md) |
| Run a nameserver as a Docker container (BIND + agent together) | [`docker/bind9-node/README.md`](docker/bind9-node/README.md) |
| Keep BIND on the host but run only the agent in Docker | [`docker/bind9-agent/README.md`](docker/bind9-agent/README.md) |
| Update an existing install to the latest code | [`UPDATING.md`](UPDATING.md) — app host rebuild, nameserver agents, rollback |
| Look up app features, REST API, config, branding, SSO | This README (below) |

**New here?** Start with `INSTALL-RHEL8.md` or `INSTALL-DEBIAN.md`
(whichever matches your OS) — you end with one working server. Add more
nameservers later with `INSTALL-NAMESERVER-RHEL8.md` (RHEL) or the
"Scaling beyond one server" section of `INSTALL-DEBIAN.md`. Once you've
done it once, `INSTALL-CHECKLIST.md` is all you need next time. To pick up
new versions later, follow `UPDATING.md`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django 4.2 LTS + Django REST Framework |
| Auth | JWT (SimpleJWT) + Django session auth + SSO (Okta / Authentik) |
| Database | MySQL 8.0 / MariaDB 10.11 **or** PostgreSQL 16 (selectable via `DB_ENGINE` in `.env`) |
| DB driver | `mysqlclient` (MySQL/MariaDB) or `psycopg2-binary` (PostgreSQL) |
| Cache / Broker | Redis 7+ (external, not containerised) |
| Background Tasks | Celery + Celery Beat (schedule stored in the DB via `django-celery-beat`, not hardcoded — see [DNS Zone Sync](#dns-zone-sync)) |
| Frontend | Custom CSS (CSS variables, light/dark tokens) + vanilla JS + GSAP (vendored, no CDN) |
| Web Server | Nginx (port 81) → Gunicorn |
| Containers | Docker + Docker Compose |
| Tests | pytest + pytest-django (SQLite in-memory) |

---

## Prerequisites

- Docker and Docker Compose installed on the VM
- A running **MySQL 8.0** / **MariaDB 10.11** or **PostgreSQL 16** instance reachable from the VM
- A running **Redis 7+** instance reachable from the VM
- BIND9 installed on the target nameserver(s) with `rndc` access

---

## First-Time Setup

### 1. Clone the Repository

```bash
git clone <repo-url> bindmanager
cd bindmanager
```

### 2. Prepare the Database

`DB_ENGINE` controls which database backend is used. It defaults to `mysql` — even if the variable is not present in `.env` at all. Only set it when using PostgreSQL.

---

**Option A — MySQL (default)**

No `DB_ENGINE` change needed. Run the init script once:

```bash
sudo mysql -u root < docker/mysql/init.sql
```

Run as root/sudo, no `-p` — a default MariaDB install authenticates local
`root` by OS user over the Unix socket, not by password.

Creates: database `bindmanager` (utf8mb4) + user `bindmanager@'%'` with full privileges.

---

**Option B — PostgreSQL**

Add to `.env`:

```ini
DB_ENGINE=postgresql
# DB_PORT=5432  (only needed if using a non-standard port)
```

Then run the init script once:

```bash
sudo -u postgres psql -f docker/postgres/init.sql
```

Run as the `postgres` OS user — Postgres uses local peer auth, so
`psql -U postgres` run as any other user fails with `Peer authentication
failed`.

Creates: role `bindmanager` + database `bindmanager` (UTF8) owned by that role.

---

> **Security note:** Both scripts use `bindmanager` as the default password. Change it in the init script **and** in `.env` (`DB_PASSWORD`) before deploying to any non-local environment.

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` — minimum required changes:

```ini
# Generate a strong secret key, e.g.:
#   python3 -c "import secrets; print(secrets.token_urlsafe(50))"
DJANGO_SECRET_KEY=change-me

# Your VM's IP or hostname (used by Django and Nginx)
DJANGO_ALLOWED_HOSTS=localhost,<your-server-ip>
CSRF_TRUSTED_ORIGINS=http://<your-server-ip>:81

# MySQL — use host.docker.internal to reach the VM host from inside Docker,
# or replace with the actual IP if MySQL is on a separate machine
DB_HOST=host.docker.internal
DB_PASSWORD=<your-mysql-password>

# Redis — include password in the URL if Redis is password-protected
REDIS_URL=redis://:<redis-password>@host.docker.internal:6379/0
```

See `.env.example` for the full list of options including SSO and branding configuration.

Optional security overrides:

```ini
# Session lifetime (seconds). Default is 8 hours.
SESSION_COOKIE_AGE=28800
```

### 4. Build and Start

```bash
docker compose up --build -d
```

On first start the `web` container automatically runs:
- `python manage.py migrate` — creates all database tables
- `python manage.py collectstatic` — gathers static assets for Nginx

Check it started cleanly:

```bash
docker compose logs -f web
```

### 5. Create a Superuser

```bash
docker compose exec web python manage.py createsuperuser
```

### 6. Access the App

| URL | Description |
|---|---|
| `http://<host>:81/` | Zone list (login required) |
| `http://<host>:81/dashboard/` | Staff dashboard |
| `http://<host>:81/manage/zones/` | Zone & record management |
| `http://<host>:81/manage/audit/` | Audit log |
| `http://<host>:81/manage/users/` | User management (`is_superuser` only) |
| `http://<host>:81/api/v1/` | Interactive API reference (`is_superuser` only) |
| `http://<host>:81/api/token/` | Obtain JWT token (POST) |
| `http://<host>:81/admin/` | Django admin panel (`is_superuser` only) |

The footer of every page after login shows the running version (e.g.
`v0.2.0`), read from the `VERSION` file — see [Versioning](#versioning).

---

## Project Structure

```
bindmanager/
├── apps/
│   ├── accounts/               # Auth: SmartLoginView, login/logout signals, SSO backends
│   │   ├── backends.py         # AuthentikOpenIdConnect — OIDC backend for Authentik
│   │   ├── context_processors.py  # Injects SSO flags, branding vars (logo, favicon, app name) and APP_VERSION into all templates
│   │   ├── pipeline.py         # SSO pipeline: ignore_session_user (no linking to a logged-in account), set_staff_flag
│   │   ├── signals.py          # Writes AuditLog on login / logout / failed login; superuser ⇒ staff
│   │   └── views.py            # SmartLoginView — rate-limited login, redirects staff to /dashboard/
│   ├── api/
│   │   ├── urls.py             # Mounts v1 at /api/v1/, JWT endpoints at /api/token/
│   │   └── v1/
│   │       ├── views.py        # ZoneViewSet, RecordViewSet, NameServerViewSet, AuditLogViewSet
│   │       ├── serializers.py
│   │       ├── agent_auth.py   # NameServerKeyAuthentication + IsNameServerAgent — API-key auth for pull agents
│   │       ├── agent_views.py  # AgentZoneListView / AgentZoneDetailView — what agents/bindmanager_agent.py polls
│   │       └── urls.py         # Custom API docs at /api/v1/; DRF router for REST endpoints
│   └── dns_manager/
│       ├── admin.py            # NameServerAdmin, ZoneAdmin (+ Record inline, mark_dirty action), RecordAdmin, AuditLogAdmin
│       ├── forms.py            # NameServerForm, ZoneForm, RecordForm, UserCreateForm, UserEditForm
│       ├── manage_views.py     # Staff CRUD views
│       ├── management/commands/
│       │   └── sync_zones.py   # CLI: python manage.py sync_zones
│       ├── migrations/
│       │   ├── 0001_initial.py
│       │   ├── 0002_alter_auditlog_action_maxlength.py
│       │   └── 0003_nameserver_api_key.py
│       ├── models.py           # Zone, Record, NameServer, AuditLog
│       ├── tasks.py            # Celery: sync_dirty_zones dispatcher + per-zone sync_zone task
│       ├── templatetags/
│       │   └── dns_tags.py     # rtype_class filter + url_replace tag
│       ├── urls.py             # All URL patterns
│       ├── validators.py       # Record value checks + same-name conflict/duplicate checks (UI, admin, API)
│       ├── views.py            # Public read-only zone/record views
│       └── zone_engine/
│           ├── generator.py    # Builds BIND zone file content via Jinja2
│           └── writer.py       # Atomic file write, named-checkzone + rndc reload
├── config/
│   ├── __init__.py             # celery_app export + optional PyMySQL shim (no-op — mysqlclient is the driver)
│   ├── settings/
│   │   ├── base.py             # Core settings (used by all environments); reads VERSION into APP_VERSION
│   │   ├── development.py
│   │   ├── production.py       # HSTS, secure cookies
│   │   └── test.py             # SQLite :memory: for pytest
│   ├── celery.py
│   └── urls.py
├── branding/                   # Volume-mounted branding assets (logo + favicon)
│   ├── logo.webp               # Replace with your own logo — .svg/.png/.jpg also accepted
│   └── favicon.ico             # Replace with your own favicon — .png also accepted
├── frontend/
│   ├── static/
│   │   ├── css/theme.css       # All styles — CSS variable tokens for light/dark
│   │   ├── js/vendor/gsap.min.js  # Vendored GSAP 3.12 (no CDN) — animation engine
│   │   ├── js/app.js           # Delete modal, toasts, live search, user menu, progress bar; GSAP-driven motion
│   │   └── js/theme.js         # Theme toggle, persists to localStorage; GSAP icon crossfade
│   └── templates/
│       ├── base.html           # App shell: topbar, nav, footer (with version), toasts, delete modal
│       ├── dashboard.html      # Staff dashboard (standalone, no sidebar)
│       ├── registration/
│       │   └── login.html      # Custom split-panel login page
│       ├── api/
│       │   └── docs.html       # Custom API reference page
│       ├── dns_manager/
│       │   ├── zone_list.html    # Public read-only zone list
│       │   └── zone_detail.html  # Public read-only zone detail
│       └── manage/
│           ├── audit_log.html
│           ├── base.html       # Manage sidebar layout
│           ├── confirm_delete.html  # Shared confirm page for zone/record/nameserver/user deletes
│           ├── dashboard.html  # Unused — manage_views.dashboard renders top-level dashboard.html instead
│           ├── nameserver_form.html / nameserver_list.html
│           ├── record_form.html
│           ├── user_form.html  # User create / edit form
│           ├── user_list.html  # User manager list (superuser only)
│           └── zone_detail.html / zone_form.html / zone_list.html
├── tests/
│   ├── conftest.py             # Shared fixtures (staff_user, regular_user, zone, nameserver)
│   ├── test_generator.py       # Zone engine: _bump_serial, _quote_txt, build_zone (31 tests)
│   ├── test_permissions.py     # IsStaffOrReadOnly (21 tests)
│   ├── test_template_tags.py   # rtype_class, url_replace (21 tests)
│   ├── test_models.py          # Zone, Record, AuditLog, NameServer (12 tests)
│   ├── test_agent_api.py       # NameServer API key + pull-agent endpoints + agent rate limit (14 tests)
│   ├── test_validators.py      # Record value, CNAME-conflict and duplicate validation (form + API) + named-checkzone error text (49 tests)
│   ├── test_sso_pipeline.py    # SSO never links to the session user or by email (4 tests)
│   ├── test_superuser_staff.py # Superuser always saved as staff (4 tests)
│   └── test_version.py         # VERSION file → APP_VERSION → template context (2 tests)
├── pytest.ini
├── nginx/nginx.conf
├── docker/mysql/init.sql       # One-time MySQL database + user creation
├── bind_zones/                 # Generated BIND zone files (host-mounted volume)
├── staticfiles/                # collectstatic output (host-mounted volume, shared by web + nginx)
├── VERSION                     # App version shown in the footer (MAJOR.MINOR.PATCH)
├── UPDATING.md                 # How to update an install + how to release a version
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── requirements.txt
```

---

## Docker Services

| Service | Role |
|---|---|
| `bind` | Hidden BIND primary — answers no DNS queries; exists only so the `worker`'s `rndc reload` has a real `named` to talk to (see [DNS Zone Sync](#dns-zone-sync)) |
| `web` | Gunicorn app server — runs migrations and collectstatic on startup |
| `worker` | Celery worker — processes the `zone_sync` queue |
| `beat` | Celery Beat — schedules periodic zone sync |
| `nginx` | Reverse proxy on port 81, serves `/static/` and `/branding/` |
| `certbot` | Not started by `up` (`profiles: ["acme"]`) — one-shot DNS-01 cert issuance, see [Let's Encrypt](#lets-encrypt-dns-01-for-managed-zones) |

> **Important:** Source code is baked into the Docker image at build time — it is **not** volume-mounted. After any code or template change you must rebuild:
> ```bash
> docker compose build && docker compose up -d
> ```

---

## DNS Zone Sync

Zone files are written to `./bind_zones/` on the host, mounted into containers at `/etc/bind/zones`.

> **Required one-time setup:** Celery Beat's schedule is stored in the database (`django-celery-beat`), not hardcoded — nothing seeds it automatically. Before `sync_dirty_zones` will ever run, create a Periodic Task in `/admin/django_celery_beat/periodictask/` pointing at `apps.dns_manager.tasks.sync_dirty_zones` on whatever interval you want (e.g. every minute) — or run the one-liner in [`INSTALL-DEBIAN.md` Part 3](INSTALL-DEBIAN.md#part-3--turn-on-the-sync-engine-do-not-skip-this--verify-it). Without this step the `beat` container runs but never dispatches syncs, and nothing reports an error.

Any record or zone change automatically sets `is_dirty = True`. The Celery Beat scheduler triggers `sync_dirty_zones` on the configured schedule, which dispatches an independent `sync_zone` task for each dirty zone. Each per-zone task:

1. Renders the zone file from a Jinja2 template
2. Validates it with `named-checkzone`
3. Atomically replaces the old file
4. Calls `rndc reload <zone>` on the nameserver
5. Clears the dirty flag and writes an audit log entry
6. Retries up to 3 times (30-second delay) if anything fails — the worker log and the audit log show `named-checkzone`'s actual reason

**Record validation on save:** the Manage UI, Django admin and REST API all reject record values that can never be valid, before they reach the sync pipeline — `A`/`AAAA` values must be real IPv4/IPv6 addresses, and `NS`/`CNAME`/`PTR`/`MX` values must be hostnames, not IPs (an IP there is almost always a mistyped `A` record). They also reject a CNAME at a name that has other records (or at the zone apex), any record at a name that is already a CNAME, and exact duplicates of an existing record. Several `A`/`AAAA` records with the same name are fine — that's how a name returns multiple IPs (BIND rotates the order between answers). Inactive records are ignored by these checks, since they aren't written to the zone file. Everything else is still validated by `named-checkzone` at sync time.

**Zone contents you don't enter yourself:** each zone's `NS` records come from its assigned nameservers (their `name` field), and the SOA primary is the first assigned nameserver. If that name is inside the zone (e.g. `ns1.example.com` for `example.com`), add an `A` record for it — `named-checkzone` rejects the zone without that glue.

**Manual sync via CLI:**

```bash
# Sync all dirty zones
docker compose exec web python manage.py sync_zones

# Force sync a specific zone (ignores dirty flag)
docker compose exec web python manage.py sync_zones --zone example.com

# Force sync all zones (ignores dirty flag)
docker compose exec web python manage.py sync_zones --all
```

---

## Let's Encrypt (DNS-01) for managed zones

BindManager doesn't run an ACME client itself — it's a DNS manager, not a
certificate manager. But `docker/certbot/` wires up
[certbot](https://certbot.eff.org/)'s manual DNS plugin to BindManager's own
REST API, so you can issue certs (including wildcards) for any zone
BindManager manages, via DNS-01.

**One-time setup:**

1. Create a dedicated BindManager user with `is_staff=True` (writes require
   staff) — don't reuse a personal admin login.
2. Set `ACME_API_USERNAME` / `ACME_API_PASSWORD` in `.env` to that account.

**Issue a cert:**

```bash
docker compose run --rm certbot certonly \
  --manual --preferred-challenges dns \
  --manual-auth-hook /opt/acme_dns_hook.py \
  --manual-cleanup-hook "/opt/acme_dns_hook.py cleanup" \
  -d example.com -d '*.example.com'
```

The hook script (`docker/certbot/acme_dns_hook.py`):

1. Logs into `/api/token/` as the service account.
2. Matches the domain being validated to a zone by name (most specific zone
   wins if you manage both `example.com` and a subdomain zone).
3. Creates a `_acme-challenge` TXT record via `/api/v1/records/`.
4. Polls DNS until the record actually resolves before letting certbot
   proceed — this covers the gap between the record existing in the database
   and the next `sync_dirty_zones` run actually writing the zone file and
   `rndc reload`-ing it (see [DNS Zone Sync](#dns-zone-sync)). By default it
   queries the system resolver; set `ACME_DNS_NAMESERVER` to query your
   authoritative nameserver directly instead.
5. On cleanup, deletes the TXT record it created.

Certs land in `./certbot/conf/live/<domain>/`. Renewal isn't automated by
this service (`docker compose run` is a one-shot job) — either re-run the
command above via cron/your own scheduler, or call
`docker compose run --rm certbot renew` on a schedule once your first cert
is issued.

---

## REST API

Full interactive documentation is available at `/api/v1/` in the app.

Authentication uses JWT. Obtain a token first, then pass it as a Bearer header:

```bash
# Get token
curl -X POST http://<host>:81/api/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "yourpassword"}'

# Use token
curl http://<host>:81/api/v1/zones/ \
  -H "Authorization: Bearer <access_token>"
```

**Permissions:** All endpoints require authentication. Write operations (POST / PUT / PATCH / DELETE) additionally require `is_staff=True`. Read operations are available to any authenticated user. The `/api/v1/agent/` endpoints authenticate with a nameserver's Agent API key instead and only return zones assigned to that nameserver that have already synced cleanly.

**Rate limiting:** Anonymous requests 20/min, authenticated requests 300/min, pull-agent requests 300/min per nameserver key — applied to every endpoint.

**Endpoints:**

| Method | Endpoint | Description |
|---|---|---|
| GET / POST | `/api/v1/zones/` | List or create zones |
| GET / PATCH / DELETE | `/api/v1/zones/{id}/` | Retrieve, update, or delete a zone |
| GET | `/api/v1/zones/{id}/records/` | List records for a specific zone |
| GET / POST | `/api/v1/records/` | List or create records |
| GET / PATCH / DELETE | `/api/v1/records/{id}/` | Retrieve, update, or delete a record |
| GET / POST | `/api/v1/nameservers/` | List or create nameservers |
| GET / PATCH / DELETE | `/api/v1/nameservers/{id}/` | Retrieve, update, or delete a nameserver |
| GET | `/api/v1/audit/` | Audit log (read-only) |
| GET | `/api/v1/agent/zones/` | Pull agent: zones assigned to this nameserver (`Authorization: ApiKey <key>`, not JWT) |
| GET | `/api/v1/agent/zones/{name}/` | Pull agent: rendered zone file for one assigned zone |
| POST | `/api/token/` | Obtain JWT access + refresh tokens |
| POST | `/api/token/refresh/` | Refresh JWT access token |

**Token lifetimes:** access token 15 minutes, refresh token 1 day (rotation enabled).

---

## Running Tests

```bash
# All tests (158 total)
pytest

# One module
pytest tests/test_generator.py

# One test
pytest tests/test_generator.py::TestBuildZone::test_txt_record_is_quoted

# Stop on first failure
pytest -x
```

Tests use SQLite `:memory:` — no database server required. Test settings live in `config/settings/test.py` and are picked up automatically via `pytest.ini`.

---

## Security

The following controls are active out of the box:

| Control | Detail |
|---|---|
| HTTP security headers | CSP, `Referrer-Policy`, `Permissions-Policy`, `X-Content-Type-Options`, `X-Frame-Options` set by Nginx on every response |
| Session timeout | 8 hours (configurable via `SESSION_COOKIE_AGE` in `.env`) |
| Login rate limiting | IP-based; HTTP 429 returned on the 10th failed attempt within 5 minutes |
| API rate limiting | DRF throttling — 20/min anonymous, 300/min authenticated, 300/min per pull-agent key, applied to every REST endpoint |
| Audit log integrity | Append-only from application code; add/change/delete disabled in the Django admin |
| API docs | `/api/v1/` requires `is_superuser=True` — staff-only users are redirected to login |
| Dependency pinning | All packages pinned to exact versions in `requirements.txt` |
| Least privilege | Container drops to a non-root `django` user at runtime via `gosu` |

Production-only (enabled in `config/settings/production.py`):
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`
- `SECURE_HSTS_SECONDS = 31536000` with subdomains and preload

Known gaps (require additional tooling or infrastructure decisions):
- No MFA support
- No audit log archival/retention policy
- TLS termination is the deployer's responsibility

---

## White-Label Branding

BindManager is designed to be sold and deployed under a customer's own brand. The logo, favicon, and application name are all configurable without modifying or rebuilding the image — each customer deployment is fully white-labelled through their `.env` file and a `branding/` folder.

### What changes

| Variable / File | Where it appears |
|---|---|
| `BRANDING_APP_NAME` | Browser tab title, login page heading, login page subtitle, footer (topbar always shows "DNS Manager"; the footer also shows the version from `VERSION`) |
| Logo file / `BRANDING_LOGO_URL` | Topbar (next to app name), login page |
| Favicon file / `BRANDING_FAVICON_URL` | Browser tab icon |

### Option A — File drop (recommended)

The `./branding/` directory already exists and contains the default logo and favicon. Replace them with the customer's files — Nginx serves them directly with no Django involvement and no restart needed.

```
branding/
  logo.webp       ← replace with customer logo (.svg / .png / .webp / .jpg accepted)
  favicon.ico     ← replace with customer favicon (.ico or .png accepted)
```

The app picks up the new files automatically on the next page load. Delete the old file if switching formats (e.g. replacing `logo.webp` with `logo.png`).

### Option B — Environment variables

Set these in the customer's `.env` to use externally hosted assets (e.g. from a CDN) or to override the app name:

```ini
# Customer's application name
BRANDING_APP_NAME=Acme DNS Manager

# Externally hosted assets (optional — use Option A instead if self-hosting)
BRANDING_LOGO_URL=https://cdn.acme.com/logo.png
BRANDING_FAVICON_URL=https://cdn.acme.com/favicon.ico
```

### Priority chain

```
BRANDING_*_URL env var  →  ./branding/ file  →  built-in default
```

Env-var URLs take priority over volume files. The `branding/` folder ships with default files — replace them with the customer's assets and the defaults never show. Customers who prefer CDN delivery can set the env-var URLs and ignore the folder entirely.

---

## SSO (Optional)

SSO users are automatically created with `is_staff=True` (granting access to `/manage/` and `/dashboard/`). `is_superuser=True` can be granted via the User Manager page (`/manage/users/`) by an existing superuser. `/admin/` requires `is_superuser=True`. A superuser is always saved as staff too.

Each SSO identity gets its own account. It is never linked to an existing local account, whether by matching email or because someone was already logged in when the SSO sign-in completed. To give an SSO user more rights, edit their own account in **Manage → Users**.

SSO is disabled by default. When enabled, a branded **"Continue with Okta / Authentik"** button appears on the login page. To enable, set the relevant vars in `.env`:

**Okta:**
```ini
OKTA_ENABLED=true
OKTA_BASE_URL=https://company.okta.com
OKTA_CLIENT_ID=<client-id>
OKTA_CLIENT_SECRET=<client-secret>
```

**Authentik:**
```ini
AUTHENTIK_ENABLED=true
AUTHENTIK_OIDC_ENDPOINT=https://auth.example.com/application/o/bindmanager/
AUTHENTIK_CLIENT_ID=<client-id>
AUTHENTIK_CLIENT_SECRET=<client-secret>
```

Then rebuild:
```bash
docker compose build && docker compose up -d
```

### Authentik Provider Setup

When creating the OAuth2/OpenID provider in Authentik:

1. **Client type:** Confidential
2. **Redirect URI:** `http(s)://<your-host>:<port>/auth/complete/authentik/`
   - Example (dev): `http://192.0.2.10:81/auth/complete/authentik/`
   - Example (prod): `https://dns.example.com/auth/complete/authentik/`
   - The redirect URI field is a tag input — type the URI then press **Enter** to confirm it as a chip before saving, otherwise Authentik silently discards it
3. **Signing Key:** Under *Advanced protocol settings*, assign a signing key (e.g. `authentik Self-signed Certificate`). Without this the JWKS endpoint returns `{}` and login fails with a `KeyError: 'keys'` error.

---

## Updating

Full steps, including backups, nameserver agents and rollback, are in
[`UPDATING.md`](UPDATING.md). The short version, on the app host:

```bash
git pull
docker compose up -d --build
docker compose restart nginx     # otherwise nginx may serve 502s after the rebuild
```

Database migrations and `collectstatic` run automatically when `web`
starts. Nameservers only need updating when a release changes `agents/`.
Afterwards, the page footer should show the new version.

### Versioning

The app version lives in the one-line `VERSION` file at the top of the repo
(`MAJOR.MINOR.PATCH`). Django reads it at startup into `APP_VERSION`, and
every page's footer shows it (after login — not on the login page). Each
release is also a git tag (`v0.2.0`, …).

- **Which version am I running?** Look at the footer, or `cat VERSION` in
  the checkout. `git show origin/main:VERSION` (after `git fetch`) shows the
  latest.
- **Releasing:** bump `VERSION` (patch = fixes, minor = features, major =
  needs manual steps), commit, then `git tag -a vX.Y.Z -m "vX.Y.Z"` and
  `git push && git push --tags`. Details in
  [`UPDATING.md`](UPDATING.md#releasing-a-new-version-maintainers).
- The version covers the app only. The nameserver agent has no version of
  its own; UPDATING.md checks whether it changed with `cmp`.

---

## Common Commands

```bash
# View logs
docker compose logs -f
docker compose logs -f web

# Run a Django management command
docker compose exec web python manage.py <command>

# Open a Django shell
docker compose exec web python manage.py shell

# Apply migrations after a model change
docker compose exec web python manage.py migrate

# Stop all containers
docker compose down

# Full reset — with no Docker-managed volumes left, this is now equivalent to `docker compose down`.
# ./staticfiles/, ./bind_zones/, and ./branding/ are host folders and survive regardless; delete
# ./staticfiles/ manually and re-run collectstatic if you want to force-regenerate it.
docker compose down -v
```

---

## Networking Notes

Containers use `host.docker.internal` (mapped via `extra_hosts: host-gateway`) to reach services on the VM host. If MySQL or Redis runs on a **separate machine**, replace `host.docker.internal` in `.env` with that machine's IP.

To confirm MySQL grants allow Docker connections:

```sql
SELECT user, host FROM mysql.user WHERE user = 'bindmanager';
-- 'host' should show '%' or the Docker subnet (172.x.x.x)
```
