# bind9-node — containerized alternative to INSTALL-DEBIAN.md's agent setup

BIND9 + `bindmanager_agent.py` in one container, instead of installing both
directly on the nameserver host. Deploy `docker-compose.node.yml` (repo
root) on each answering nameserver — same idea as `docker-compose.yml` on
the app host, but for `ns1`/`ns2`/`ns3` instead.

**This is an optional path, not the recommended default.** Read this before
choosing it over the bare-metal + agent-script path in `INSTALL-DEBIAN.md` Part 4.

## When this is a good fit

- Greenfield nameservers with nothing else running on the box
- A team that's already standardized ops tooling (deploys, monitoring,
  log shipping) around Docker and wants DNS nodes to fit the same pattern
- Low-to-moderate query volume, or fronted by something else that already
  handles scale (anycast, a DNS load balancer)

## When it's probably not

- **You already have a production nameserver running here.** This image
  owns `/etc/bind/named.conf.local` and everything under `zones/` — it's
  not designed to coexist with an existing hand-managed BIND config. The
  bare-metal agent script (`agents/bindmanager_agent.py`) was specifically
  built to be non-invasive instead: one `include` line, nothing else
  touched. That's almost certainly the better fit if the box predates this
  app — or, if you still want the agent itself containerized, see
  [`docker/bind9-agent/`](../bind9-agent/README.md), which leaves BIND9 on
  the bare host and containerizes only the agent.
- **High query volume, or you need real client IPs.** DNS over UDP:53
  through Docker's published ports adds a NAT/userland-proxy hop, which
  costs throughput/latency at scale — and when the userland proxy handles
  the traffic, `named` sees the Docker gateway as the client instead of
  the real resolver, which breaks per-client ACLs and makes query logs
  useless. Set `network_mode: host` in `docker-compose.node.yml` (and drop
  the `ports:` block) if either matters for your deployment.
- **Docker's own uptime becomes a dependency of your DNS uptime.** On bare
  metal, BIND runs whether or not anything else on the box is healthy. In
  a container, a Docker daemon issue takes your nameserver down with it.
  For anything beyond 1 of your 3 servers, think about whether that's a
  dependency you want.

## Usage

On the nameserver host (not the BindManager app host):

```bash
cp .env.node.example .env.node
# edit .env.node: BINDMANAGER_API_URL, BINDMANAGER_API_KEY (from Manage >
# Nameservers on the app for the NameServer row representing this box)

docker compose -f docker-compose.node.yml up --build -d
docker compose -f docker-compose.node.yml logs -f
```

The container starts `named`, waits for `rndc status` to succeed, then
loops the agent every `BINDMANAGER_POLL_INTERVAL` seconds (default 120) —
same script, same behavior as the systemd-timer deployment in
`INSTALL-DEBIAN.md` Part 4, just running as a background loop instead.

## How the rndc auth differs from the app host's `bind` service

The app host's hidden-primary `bind` service (`docker/bind9/`) needs a
*shared* rndc key because a **different container** (`worker`) has to
authenticate to it across the Docker network. This node doesn't have that
problem — `named` and the agent run in the *same* container, so there's
nothing to share: Debian's `bind9` package auto-generates
`/etc/bind/rndc.key` at install time and both sides use it implicitly, with
no `controls{}` block and no port ever exposed past `127.0.0.1`.

## Keeping `bindmanager_agent.py` in sync

This directory has its own copy of the agent script (Docker can't `COPY`
from outside the build context) rather than a symlink. If you edit
`agents/bindmanager_agent.py`, copy the change here too:

```bash
cp agents/bindmanager_agent.py docker/bind9-node/bindmanager_agent.py
```
