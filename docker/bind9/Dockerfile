FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bind9 \
    bind9utils \
    && rm -rf /var/lib/apt/lists/*

COPY named.conf.template /etc/bind/named.conf.template
COPY entrypoint.sh /entrypoint.sh
COPY zone-watcher.sh /zone-watcher.sh
RUN chmod +x /entrypoint.sh /zone-watcher.sh

EXPOSE 953

ENTRYPOINT ["/entrypoint.sh"]
