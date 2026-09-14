FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bind9 \
    bind9utils \
    python3 \
    && rm -rf /var/lib/apt/lists/*

COPY named.conf.options /etc/bind/named.conf.options
COPY named.conf.local /etc/bind/named.conf.local
COPY entrypoint.sh /entrypoint.sh
# Same file as agents/bindmanager_agent.py in the repo root — kept in sync
# manually (stdlib-only, no build step, easy to diff). See
# docker/bind9-node/README.md.
COPY bindmanager_agent.py /opt/bindmanager-agent/bindmanager_agent.py
RUN chmod +x /entrypoint.sh

EXPOSE 53/udp 53/tcp

ENTRYPOINT ["/entrypoint.sh"]
