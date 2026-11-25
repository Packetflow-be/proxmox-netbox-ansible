# packetflow.services

Service roles for VMs built from NetBox. A site repo runs them from a play
that targets a NetBox role or tag (`playbooks/30-services.yml`).

## Roles

| Role | What |
|---|---|
| [`powerdns`](roles/powerdns/README.md) | Internal DNS: PowerDNS recursor + authoritative, zones synced from NetBox DNS with octoDNS every 5 minutes |
| [`haproxy`](roles/haproxy/README.md) | HAProxy reverse proxy with TLS termination; services (host name → backends) from one YAML list |
| [`certbot`](roles/certbot/README.md) | Let's Encrypt certificates (DNS-01 via Cloudflare, also for internal-only names), renewal timer, deploy hooks |

## Adding a role

```bash
cd ansible_collections/packetflow/services
ansible-galaxy role init --init-path roles <role_name>
```

Role names use underscores (hyphens are invalid in collection role names).
