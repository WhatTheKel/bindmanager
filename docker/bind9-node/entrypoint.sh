#!/bin/bash
set -e

: "${BINDMANAGER_API_URL:?BINDMANAGER_API_URL is required (e.g. https://your-app-host/api/v1)}"
: "${BINDMANAGER_API_KEY:?BINDMANAGER_API_KEY is required — from Manage > Nameservers for this node}"
POLL_INTERVAL="${BINDMANAGER_POLL_INTERVAL:-120}"

mkdir -p /etc/bind/zones /var/lib/bindmanager-agent /etc/bindmanager-agent
chown -R bind:bind /etc/bind/zones /var/cache/bind

# named.conf.local's `include` fails BIND's startup if the target doesn't
# exist yet — seed an empty one, the agent's first run replaces it.
[ -f /etc/bind/named.bindmanager.conf ] || : > /etc/bind/named.bindmanager.conf
chown bind:bind /etc/bind/named.bindmanager.conf

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

named -u bind -c /etc/bind/named.conf -f &
NAMED_PID=$!

echo "entrypoint: waiting for named..."
until rndc status >/dev/null 2>&1; do
    sleep 1
done
echo "entrypoint: named is up"

# Same script Part 4 of INSTALL.md runs via a systemd timer on a bare host —
# here the poll loop replaces the timer, everything else is identical.
( while true; do
    python3 /opt/bindmanager-agent/bindmanager_agent.py --config /etc/bindmanager-agent/config.ini -v || true
    sleep "$POLL_INTERVAL"
done ) &
AGENT_PID=$!

trap 'kill $NAMED_PID $AGENT_PID 2>/dev/null' TERM INT
wait -n $NAMED_PID $AGENT_PID
