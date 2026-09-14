FROM debian:bookworm-slim

# bind9utils only — rndc, named-checkzone, named-checkconf. No bind9 (named
# daemon) package: this container never runs named itself, it only talks
# to the named already running on the host.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bind9utils \
    python3 \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
# Same file as agents/bindmanager_agent.py in the repo root — kept in sync
# manually (stdlib-only, no build step, easy to diff). See README.md.
COPY bindmanager_agent.py /opt/bindmanager-agent/bindmanager_agent.py
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
