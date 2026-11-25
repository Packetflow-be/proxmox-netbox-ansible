# packetflow.services.certbot

Publicly trusted Let's Encrypt certificates, also for names that only exist
internally (`*.internal.example.com`). certbot proves control of the name
with a **DNS-01** challenge: it creates `_acme-challenge.<name>` as a TXT
record in the public zone through the Cloudflare API, so nothing has to be
reachable from the internet and the internal zone stays internal.

Renewal is certbot's own `certbot.timer` (twice a day, renews 30 days before
expiry), so it doesn't depend on Ansible runs. After every issuance and
renewal a **deploy hook** per certificate can write a combined PEM (fullchain
+ key, what HAProxy wants) and reload services.

Tested on Ubuntu 26.04 (certbot 4.0).

## Variables

| Variable | Default | |
|---|---|---|
| `certbot_email` | — | **required**: Let's Encrypt account e-mail |
| `certbot_cloudflare_api_token` | — | **required**, from the site's encrypted vars: Cloudflare token with *Zone → DNS → Edit* on the zone only |
| `certbot_certificates` | `[]` | `name`, `domains`; optional `pem` (+ `pem_owner`/`pem_group`/`pem_mode`) and `reload` (systemd units) |
| `certbot_staging` | `false` | Let's Encrypt staging (untrusted, for testing); back to `false` reissues from production |
| `certbot_propagation_seconds` | `30` | wait between creating the TXT record and the check |
| `certbot_key_type` | `ecdsa` | or `rsa` |

The certificate is (re)issued when it's missing, its `domains` changed, or it
comes from the other environment (staging ↔ production). Otherwise the role
leaves it to the timer. A changed deploy hook runs once for the existing
certificate, so a new `pem` path or `reload` list applies immediately.

## Example

```yaml
certbot_email: admin@example.com
certbot_certificates:
  - name: internal.example.com
    domains: [internal.example.com, "*.internal.example.com"]
    pem: /etc/haproxy/certs/internal.example.com.pem
    reload: [haproxy]
```

## Cloudflare token

Cloudflare → My Profile → API Tokens → Create Token → *Edit zone DNS*
template, Zone Resources: *Include → Specific zone → example.com*. Optionally
restrict *Client IP Address Filtering* to the office's public IP. The token
ends up in `/etc/letsencrypt/cloudflare.ini` (root, 0600) on the host.

## Checks

```bash
certbot certificates                   # names, expiry, TEST_CERT for staging
certbot renew --dry-run                # full renewal against staging, nothing replaced
systemctl list-timers certbot.timer
```

Let's Encrypt rate limits: 5 certificates per identical set of names per
week. Test new setups with `certbot_staging: true` first.
