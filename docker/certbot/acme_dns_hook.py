#!/usr/bin/env python3
"""
certbot manual auth/cleanup hook that drives BindManager's REST API
to satisfy DNS-01 challenges for zones BindManager manages.

Auth mode  (default, no args):
  - resolves CERTBOT_DOMAIN to a BindManager zone
  - creates a TXT record _acme-challenge.<name> = CERTBOT_VALIDATION
  - waits for the record to actually resolve before returning
  - prints the created record's id on stdout (certbot passes this back
    as CERTBOT_AUTH_OUTPUT to the matching cleanup call)

Cleanup mode ("cleanup" as argv[1]):
  - deletes the record whose id is in CERTBOT_AUTH_OUTPUT

Required env vars:
  ACME_API_BASE      e.g. http://web:8000/api
  ACME_API_USERNAME  BindManager service account (must have is_staff=True)
  ACME_API_PASSWORD

Optional env vars:
  ACME_DNS_TTL                 TXT record TTL in seconds (default 60)
  ACME_DNS_NAMESERVER          IP to query directly instead of the system resolver
  ACME_DNS_PROPAGATION_TIMEOUT seconds to wait for the record to resolve (default 180)
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        die(f'missing required env var {name}')
    return val


def die(msg):
    print(f'acme_dns_hook: {msg}', file=sys.stderr)
    sys.exit(1)


def api_request(method, path, token=None, body=None):
    base = env('ACME_API_BASE', required=True).rstrip('/')
    req = urllib.request.Request(f'{base}{path}', method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        die(f'{method} {path} -> HTTP {e.code}: {e.read().decode(errors="replace")}')


def get_token():
    resp = api_request('POST', '/token/', body={
        'username': env('ACME_API_USERNAME', required=True),
        'password': env('ACME_API_PASSWORD', required=True),
    })
    return resp['access']


def domain_candidates(domain):
    labels = domain.split('.')
    return ['.'.join(labels[i:]) for i in range(len(labels) - 1)]


def find_zone(domain, token):
    for candidate in domain_candidates(domain):
        resp = api_request('GET', f'/v1/zones/?search={urllib.parse.quote(candidate)}', token=token)
        for zone in resp.get('results', resp if isinstance(resp, list) else []):
            if zone['name'] == candidate:
                return zone
    die(f'no BindManager zone matches {domain!r}')


def split_record_name(domain, zone_name):
    relative = '' if domain == zone_name else domain[: -(len(zone_name) + 1)]
    return '_acme-challenge' + (f'.{relative}' if relative else '')


def create_txt_record(zone_id, record_name, value, token):
    return api_request('POST', '/v1/records/', token=token, body={
        'zone': zone_id,
        'name': record_name,
        'record_type': 'TXT',
        'value': value,
        'ttl': int(env('ACME_DNS_TTL', '60')),
    })


def delete_record(record_id, token):
    api_request('DELETE', f'/v1/records/{record_id}/', token=token)


def wait_for_propagation(fqdn, expected_value):
    timeout = int(env('ACME_DNS_PROPAGATION_TIMEOUT', '180'))
    nameserver = env('ACME_DNS_NAMESERVER')
    cmd = ['dig', '+short', 'TXT', fqdn]
    if nameserver:
        cmd.insert(1, f'@{nameserver}')

    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if f'"{expected_value}"' in result.stdout:
            return
        time.sleep(5)
    die(f'{fqdn} did not propagate with the expected TXT value within {timeout}s')


def do_auth():
    domain = env('CERTBOT_DOMAIN', required=True)
    validation = env('CERTBOT_VALIDATION', required=True)
    base_domain = domain[2:] if domain.startswith('*.') else domain

    token = get_token()
    zone = find_zone(base_domain, token)
    record_name = split_record_name(base_domain, zone['name'])

    record = create_txt_record(zone['id'], record_name, validation, token)
    wait_for_propagation(f"{record_name}.{zone['name']}", validation)

    print(record['id'])


def do_cleanup():
    record_id = env('CERTBOT_AUTH_OUTPUT', '').strip()
    if not record_id:
        print('acme_dns_hook: no CERTBOT_AUTH_OUTPUT, nothing to clean up', file=sys.stderr)
        return
    token = get_token()
    delete_record(record_id, token)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'auth'
    if mode == 'cleanup':
        do_cleanup()
    else:
        do_auth()
