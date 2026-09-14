FROM certbot/certbot:latest

RUN apk add --no-cache bind-tools

COPY acme_dns_hook.py /opt/acme_dns_hook.py
RUN chmod +x /opt/acme_dns_hook.py
