#!/bin/bash
set -u

echo "zone-watcher: waiting for named..."
until rndc status >/dev/null 2>&1; do
    sleep 1
done
echo "zone-watcher: named is up, polling /etc/bind/zones every 15s"

# atomic_write() in writer.py always finishes writing the .zone file before
# tasks.py calls reload_zone() — so by the time a *known* zone's reload
# fires, its content is already on disk here too. A brand-new zone can still
# race this loop once (reload fails until the next poll picks it up); the
# Celery task's own retry (max_retries=3, 30s backoff) absorbs that.
while true; do
    for f in /etc/bind/zones/*.zone; do
        [ -e "$f" ] || continue
        name=$(basename "$f" .zone)
        if ! rndc showzone "$name" >/dev/null 2>&1; then
            if rndc addzone "$name" "{ type master; file \"$f\"; };" >/dev/null 2>&1; then
                echo "$(date -Iseconds) zone-watcher: added $name"
            else
                echo "$(date -Iseconds) zone-watcher: failed to add $name" >&2
            fi
        fi
    done
    sleep 15
done
