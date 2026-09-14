#!/bin/bash
set -e

KEY_FILE=/etc/bind/rndc-shared/rndc.key

mkdir -p /etc/bind/rndc-shared /etc/bind/zones-dynamic /var/run/named
chown bind:bind /etc/bind/zones-dynamic /var/run/named 2>/dev/null || true

# Generated once, on whichever container (this one) starts first; shared via
# the ./bind_rndc volume with the app image's worker container so its plain
# `rndc reload <zone>` (writer.py) authenticates without any code change.
# Never baked into the image or committed — see .gitignore.
if [ ! -f "$KEY_FILE" ]; then
    echo "bind: generating shared rndc key at $KEY_FILE"
    rndc-confgen -a -A hmac-sha256 -c "$KEY_FILE"
fi
chmod 644 "$KEY_FILE"

# Local default so `rndc` with no flags (used by zone-watcher.sh and the
# healthcheck below) talks to this instance without extra arguments. Debian's
# bind9 package patches rndc's compiled-in default conf path to
# /etc/bind/rndc.conf (not upstream's /etc/rndc.conf) — get this path wrong
# and bare `rndc` silently falls back to the *different* key Debian's bind9
# postinst auto-generates at /etc/bind/rndc.key, and every call fails with
# "bad auth" against our named.
cat > /etc/bind/rndc.conf <<EOF
options {
    default-server 127.0.0.1;
    default-port 953;
    default-key "rndc-key";
};
include "$KEY_FILE";
EOF

/zone-watcher.sh &

exec named -g -u bind -c /etc/bind/named.conf.template
