# packetflow.zabbix

The monitoring side of VMs built from NetBox: the agent that is baked into the
template, and the job that follows the NetBox status. Both expect a Zabbix
server (7.x) you already run.

| Role | What |
|---|---|
| [`agent`](roles/agent/README.md) | `zabbix-agent2` with PSK and autoregistration metadata, reporting to `zabbix-proxy` (a proxy or the server). Baked into the VM template (`tasks_from: bake`) and kept in line on running VMs (default tasks) |
| [`host_status`](roles/host_status/README.md) | Disables Zabbix hosts whose NetBox device or VM is offline or decommissioning (and enables them again); module `host_status`. Controller only |
