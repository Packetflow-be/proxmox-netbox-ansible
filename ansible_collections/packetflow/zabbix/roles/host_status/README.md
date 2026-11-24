# packetflow.zabbix.host_status

Keeps Zabbix in line with the NetBox status of devices and VMs: a host that
is out of service in NetBox is disabled in Zabbix, so it stops alerting.
Runs on the controller against both APIs (module
[`packetflow.zabbix.host_status`](../../plugins/modules/host_status.py));
site repos run it with `make zabbix-status`.

| NetBox status | Zabbix |
|---|---|
| `offline`, `decommissioning`, `inventory` | host disabled, tag `netbox_status=<status>` |
| `active` | a host with tag `netbox_status` is enabled again, the tag removed |
| anything else (`planned`, `staged`, `failed`, …) | unchanged |

- **Matching:** the Zabbix technical name is the NetBox name, or the name plus
  `.<host_status_domain>` (autoregistered VMs). Otherwise the primary IPv4
  address, only when exactly one NetBox object and one Zabbix host have it
  (hosts with older names).
- A host disabled by hand (no tag) is never enabled. Zabbix hosts without a
  NetBox match are left alone.
- `--check` lists the changes without making them.

| Variable | Default | |
|---|---|---|
| `host_status_netbox_tenant` | `""` | required: the tenant whose devices and VMs count |
| `host_status_domain` | `""` | e.g. `internal.example.com` |
| `host_status_disable_statuses` | `[offline, decommissioning, inventory]` | |
| `host_status_enable_statuses` | `[active]` | |
| `host_status_tag` | `netbox_status` | |
| `host_status_zabbix_url`, `_token`, `_validate_certs` | env `ZABBIX_URL`, `ZABBIX_API_TOKEN`, `ZABBIX_VALIDATE_CERTS` | token needs write access to the hosts |
| `host_status_netbox_url`, `_token`, `_validate_certs` | env `NETBOX_URL`, `NETBOX_TOKEN`, `NETBOX_VALIDATE_CERTS` | read-only is enough |
