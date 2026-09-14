#!/bin/sh
set -e

# Volume mounts are owned by root when Docker creates them at runtime.
# Fix ownership so the django user can write to them.
chown -R django:django /app/staticfiles /etc/bind/zones 2>/dev/null || true

# Only the worker container mounts the `bind` service's shared rndc key
# (docker-compose.yml) — when present, point the bare `rndc` command
# writer.py's reload_zone() runs at the hidden-primary `bind` service
# instead of (nonexistent) localhost. Key is generated at runtime by
# docker/bind9/entrypoint.sh, never baked into an image or committed.
#
# Path matters: Debian's bind9-utils package patches rndc's compiled-in
# default conf path to /etc/bind/rndc.conf, not upstream's /etc/rndc.conf —
# writing to the wrong one means bare `rndc` silently ignores it.
RNDC_SHARED_KEY=/etc/bind/rndc-shared/rndc.key
if [ -f "$RNDC_SHARED_KEY" ]; then
  mkdir -p /etc/bind
  cat > /etc/bind/rndc.conf <<EOF
options {
    default-server bind;
    default-port 953;
    default-key "rndc-key";
};
include "$RNDC_SHARED_KEY";
EOF
  chmod 644 /etc/bind/rndc.conf
fi

# Drop from root to the django user and exec the container command.
exec gosu django "$@"
