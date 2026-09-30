#!/bin/bash
set -e

: "${BINDMANAGER_API_URL:?BINDMANAGER_API_URL is required (e.g. https://your-app-host/api/v1)}"
: "${BINDMANAGER_API_KEY:?BINDMANAGER_API_KEY is required — from Manage > Nameservers for this node}"
POLL_INTERVAL="${BINDMANAGER_POLL_INTERVAL:-120}"

mkdir -p /var/lib/bindmanager-agent /etc/bindmanager-agent

# zones_dir and named_conf_include below are container paths — the host's
# actual BIND directories/files are bind-mounted onto them by
# docker-compose.agent.yml. This container never edits anything on the
# host outside of those two mount points.
cat > /etc/bindmanager-agent/config.ini <<EOF
[agent]
api_url = ${BINDMANAGER_API_URL}
api_key = ${BINDMANAGER_API_KEY}
zones_dir = /etc/bind/zones
named_conf_include = /etc/bind/named.bindmanager.conf
rndc_bin = rndc
checkzone_bin = named-checkzone
checkconf_bin = named-checkconf
lock_file = /var/lock/bindmanager_agent.lock
manifest_file = /var/lib/bindmanager-agent/managed_zones.json
timeout = ${BINDMANAGER_TIMEOUT:-15}
EOF
chmod 600 /etc/bindmanager-agent/config.ini

echo "entrypoint: checking rndc reaches the host's named..."
if ! rndc status >/dev/null 2>&1; then
    echo "entrypoint: WARNING - 'rndc status' failed. Confirm this container" \
         "has network_mode: host and that the host's /etc/bind/rndc.key is" \
         "bind-mounted in (see docker/bind9-agent/README.md)." >&2
fi

# Same script INSTALL-DEBIAN.md Part 4's Option A runs via a systemd timer on a bare
# host — here a poll loop replaces the timer. Unlike docker-compose.node.yml
# this container never starts named itself; the host's own BIND9 install
# stays authoritative and untouched beyond the bind-mounted paths.
while true; do
    python3 /opt/bindmanager-agent/bindmanager_agent.py --config /etc/bindmanager-agent/config.ini -v || true
    sleep "$POLL_INTERVAL"
done
