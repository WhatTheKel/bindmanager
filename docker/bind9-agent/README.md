# bind9-agent — containerized agent only, for hosts that already run BIND9

Deploys just `bindmanager_agent.py` in a container; BIND9 itself keeps
running on the bare host exactly as it does today. This is a third path
alongside `INSTALL-DEBIAN.md`'s two other options (Part 4, and the "Scaling
beyond one server" section):

| | BIND9 runs | Agent runs | Touches host's existing BIND config |
|---|---|---|---|
| Option A — bare metal + agent script | on the host | on the host (systemd timer) | one `include` line |
| Option B — `docker-compose.node.yml` | in a container | in the same container | nothing — container owns its own `/etc/bind` |
| Option C — this directory | on the host | in a container | one `include` line + 3 bind-mounts |

## When this is a good fit

- The host already has a working BIND9 install you don't want to touch or
  containerize (the case `bind9-node`'s own README says it's *not* a good
  fit for), but you'd still rather manage the agent as a container —
  isolated dependencies, `restart: unless-stopped`, fits a Docker-based ops
  workflow.
- You want that without taking on Option B's "Docker becomes a DNS-uptime
  dependency" tradeoff: BIND9 stays a normal host service here, unaffected
  by the Docker daemon's own health.

## When it's probably not

- No BIND9 running here yet → Option B is simpler: one container instead
  of coordinating three mounts against a host BIND9 that doesn't exist.
- Want zero Docker involvement in DNS → Option A (bare metal + systemd
  timer) has one less moving part.

## How it reaches the host's BIND9

Unlike `docker-compose.node.yml` (where `named` and the agent share a
container, so nothing needs to cross the container boundary), here the
agent has to reach a `named` process running outside its container:

- **`network_mode: host`** — `rndc` talks to `127.0.0.1:953` by default;
  the container needs the host's network namespace to reach that, instead
  of requiring you to open a `controls{}` block on the host's BIND9 to a
  Docker bridge IP.
- **`/etc/bind/rndc.key` bind-mounted read-only** — Debian/Ubuntu's `bind9`
  package auto-generates this at install time; the container reuses the
  host's key rather than generating or storing its own.
- **`zones_dir` and `named_conf_include` bind-mounted** to the same paths
  the host's `named.conf` already expects (or will, once you add the one
  `include` line below) — the agent writes zone files directly where the
  host's `named` reads them.

The image itself only has `bind9utils` (`rndc`, `named-checkzone`,
`named-checkconf`) — no `bind9` daemon package, no `named` process ever
starts inside this container.

## Setup

On the nameserver host (BIND9 already installed and running):

1. In BindManager: **Manage > Nameservers**, create (or find) the row for
   this server, copy its **Agent API Key** (shown in full only once, right
   after saving; **Regenerate** on the row makes a new one).
2. If using the generated config include, add one line to this host's
   `named.conf` (once):
   ```
   include "/etc/bind/named.bindmanager.conf";
   ```
   Same as Option A — an empty file is fine at first, the agent seeds it
   on its first run. Adjust the path if it differs from
   `BIND_NAMED_CONF_INCLUDE` below.
3. From the repo root, on the nameserver host:
   ```bash
   cp .env.agent.example .env.agent
   # edit .env.agent: BINDMANAGER_API_URL, BINDMANAGER_API_KEY
   # (and BIND_ZONES_DIR / BIND_RNDC_KEY / BIND_NAMED_CONF_INCLUDE only if
   # this host's paths differ from stock Debian/Ubuntu defaults)
   docker compose -f docker-compose.agent.yml up --build -d
   docker compose -f docker-compose.agent.yml logs -f
   ```
4. Confirm: logs should show a completed sync (zero zones is fine if none
   are assigned yet). An `rndc` connection error in the entrypoint's
   startup check usually means `network_mode: host` didn't take effect, or
   `/etc/bind/rndc.key` doesn't exist at the mounted path on the host.

Repeat on each nameserver — only `.env.agent`'s `BINDMANAGER_API_KEY` (and
possibly the `BIND_*` paths, if hosts differ) changes.

## RHEL / SELinux hosts

The defaults above are Debian/Ubuntu paths. On RHEL the equivalents are
`BIND_ZONES_DIR=/var/named/bindmanager`, `BIND_RNDC_KEY=/etc/rndc.key` and
`BIND_NAMED_CONF_INCLUDE=/etc/named/bindmanager.conf` — but with SELinux
enforcing, a container writing into `/var/named` is denied unless you
relabel those paths for container access, which in turn stops `named`
from reading them. On RHEL, run the agent on bare metal instead
([`INSTALL-NAMESERVER-RHEL8.md`](../../INSTALL-NAMESERVER-RHEL8.md)) — it needs nothing extra.

## Updating

Only needed when a BindManager update changes `agents/` or this
directory. **Manage → Nameservers** in the app marks this server's agent
**Outdated** (or **Unknown**) when the app ships a newer one; to
check from here instead:

```bash
git fetch && git diff --stat HEAD origin/main -- agents/ docker/bind9-agent/ docker-compose.agent.yml
```

If it lists anything:

```bash
git pull
docker compose -f docker-compose.agent.yml up -d --build
```

`.env.agent` isn't in git, so it's kept. BIND on the host keeps answering
throughout. See [`UPDATING.md`](../../UPDATING.md) Part 2.

## Keeping `bindmanager_agent.py` in sync

Same caveat as `docker/bind9-node/`: this directory keeps its own copy of
the agent script rather than a symlink (Docker can't `COPY` from outside
the build context). If you edit `agents/bindmanager_agent.py`, copy the
change here too:

```bash
cp agents/bindmanager_agent.py docker/bind9-agent/bindmanager_agent.py
```
