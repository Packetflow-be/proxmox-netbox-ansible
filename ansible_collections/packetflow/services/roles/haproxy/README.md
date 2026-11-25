# packetflow.services.haproxy

HAProxy from the distro as a TLS-terminating reverse proxy. The whole
configuration comes from one YAML list, `haproxy_services`: each service is
one or more host names and the backend servers behind them. Requests on :443
are routed by their `Host` header (through `/etc/haproxy/hosts.map`); :80
redirects to https; unknown names get a 404.

Together with [`certbot`](../certbot/README.md) this gives internal services
one publicly trusted wildcard certificate (`*.internal.example.com`), while
the backends keep their self-signed certificates.

Tested on Ubuntu 26.04 (HAProxy 3.2).

## Variables

| Variable | Default | |
|---|---|---|
| `haproxy_services` | `[]` | the services, see below |
| `haproxy_certificates` | `[/etc/haproxy/certs/<fqdn>.pem]` | combined PEM files (fullchain + key); first = default, others by SNI. Missing ones get a self-signed placeholder |
| `haproxy_bind` | `*` | listen address for :80 and :443 |
| `haproxy_hsts` | `false` | `Strict-Transport-Security` on every response |
| `haproxy_stats_bind` | `127.0.0.1:8404` | stats page with backend health; `""` = off |
| `haproxy_timeout_connect` / `_client` / `_server` / `_tunnel` | `5s` / `60s` / `60s` / `1h` | tunnel = WebSockets after the upgrade |
| `haproxy_global_extra` / `haproxy_defaults_extra` | `[]` | extra lines for those sections |

A service:

| Key | Default | |
|---|---|---|
| `name` | — | backend name, `[a-z0-9_-]` |
| `hosts` | — | host names; each belongs to one service only |
| `servers` | — | `host:port`, round robin over several |
| `ssl` | `false` | HTTPS to the backend |
| `verify` | `false` | check the backend's certificate against the system CAs (`false` for self-signed backends) |
| `check` | `true` | health checks: TCP connect, or HTTP with `check_path` |
| `check_path` | — | HTTP GET with the first host name, expects 2xx/3xx |
| `server_options` | — | appended to every `server` line (`sni str(name)`, `maxconn 50`, ...) |
| `options` | `[]` | extra backend lines (`timeout server 5m`, `http-request ...`) |

The configuration is checked with `haproxy -c` before it replaces the old
one, and applied with a seamless reload.

## Example

```yaml
haproxy_certificates:
  - /etc/haproxy/certs/internal.example.com.pem
haproxy_services:
  - name: netbox
    hosts: [netbox.internal.example.com]
    servers: ["10.0.30.13:443"]
    ssl: true
    check_path: /login/
  - name: proxmox
    hosts: [proxmox.internal.example.com]
    servers: ["10.0.100.11:8006"]
    ssl: true
```

```yaml
- name: Reverse proxy
  hosts: haproxy
  become: true
  roles:
    - packetflow.services.certbot   # first: the real certificate before HAProxy starts
    - packetflow.services.haproxy
```

Each backend sees the client in `X-Forwarded-For`, plus `X-Forwarded-Proto:
https` and the original `Host`. Applications that build absolute URLs or
check CSRF origins must accept the new name (NetBox: `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS`).

## Checks

```bash
curl -s http://127.0.0.1:8404/ | grep -o 'netbox[^<]*'      # stats page (or open it via ssh -L 8404:127.0.0.1:8404)
echo "show servers state" | socat stdio /run/haproxy/admin.sock
openssl s_client -connect 127.0.0.1:443 -servername netbox.internal.example.com </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -enddate
```
