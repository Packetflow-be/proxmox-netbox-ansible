# packetflow.zabbix.agent

`zabbix-agent2` on the VMs, reporting to the site's Zabbix proxy (or straight
to the server).
The same settings come from two places:

- **Template bake** (`tasks_from: bake`, used by `packetflow.proxmox.template`):
  the agent is in the image, so a new VM is monitored from its first boot with
  no playbook.
- **Day-2** (default tasks, in a site's `20-baseline.yml`): keeps running VMs in
  line, e.g. after a PSK rotation. On a VM cloned from the template every task
  reports ok. This is tested: the baked config and the day-2 result are
  byte-identical.

## What it configures

In `/etc/zabbix/zabbix_agent2.conf` (the package's file, edited in place):

| Setting | Value |
|---|---|
| `Server`, `ServerActive` | `zabbix-proxy`: resolved via the site's DNS search domain, so the VMs of every site find their own proxy. Without proxies, point the record at the Zabbix server |
| `Hostname` | unset; `HostnameItem=system.hostname[fqdn]`. Zabbix host names are unique server-wide, and the FQDN keeps `web01` at two sites apart |
| `HostMetadata` | `packetflow-linux`, for the autoregistration action |
| `TLSConnect`, `TLSAccept` | `psk` |
| `TLSPSKIdentity`, `TLSPSKFile` | from `zabbix_agent_psk_identity` / `zabbix_agent_psk` |

Repository: `repo.zabbix.com`, agent version `zabbix_agent_version` (`7.0` LTS
by default; 7.0 agents work with 7.x proxies and servers).

## Variables

| Variable | Default | |
|---|---|---|
| `zabbix_agent_psk_identity` | | **required**, in the site's `group_vars/all/secrets.sops.yml` |
| `zabbix_agent_psk` | | **required**, ≥ 32 hex characters: `openssl rand -hex 32` |
| `zabbix_agent_server` | `zabbix-proxy` | |
| `zabbix_agent_host_metadata` | `packetflow-linux` | |
| `zabbix_agent_version` | `7.0` | |

## Zabbix side (once)

1. **Administration → General → Autoregistration:** allow PSK and set the same
   identity and PSK. Zabbix has one autoregistration PSK per server, so every
   site uses the same pair.
2. **Alerts → Actions → Autoregistration actions:** one action (one per site
   when each has its own proxy), with these conditions:
   - *Host metadata contains `packetflow-linux`*
   - *Proxy is `<the site's proxy>`* (leave out without proxies)

   Its operations: add the host, add it to a host group, link
   *Linux by Zabbix agent active*, and set host inventory mode to automatic.
3. The site's DNS needs a `zabbix-proxy` record in the domain the VMs
   search (the site's `dns_domain`).

Because the PSK is shared, anyone with root on one managed VM can read it and
register a fake host at any proxy they can reach. Moving to certificates
later means a CA plus per-host certificates in this role.
