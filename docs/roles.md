# Roles, defaults and why

Every role has its own README with the full variable list. This page is the
overview: what each role is for, the defaults that matter, and the reasoning
behind them.

## Principles

The same few rules show up in every role:

| Principle | What it means in practice |
|---|---|
| **NetBox decides, the roles follow** | VMs, IPs, VLANs and monitoring status are changed in NetBox. The roles read it; nobody edits Proxmox or Zabbix by hand |
| **Plan first** | `make plan` (vms), `CHECK=1` (Zabbix status), Ansible's `--check` everywhere. A plan error stops the run before the first change |
| **Nothing is deleted automatically** | Orphaned VMs are reported, not removed. Zabbix hosts without a NetBox match are left alone |
| **No secrets in defaults** | Tokens, PSKs and passwords default to `""`. The role refuses to run without them; sites keep them in sops-encrypted files |
| **Least privilege** | Each token can do its job and nothing more: a read-only NetBox token for the VM plan, a Proxmox user with four ACLs |
| **Nothing is rebooted for you** | `make vms` never reboots a VM: CPU, memory and cloud-init changes wait for the next reboot, and the plan says so |

## packetflow.proxmox

### vms: NetBox → Proxmox

Makes the site's Proxmox VMs match NetBox: reads both, prints a plan, applies
it. [README](../ansible_collections/packetflow/proxmox/roles/vms/README.md)

| Default | Why |
|---|---|
| `vms_vmid_offset: 10000` | Proxmox VM ID = 10000 + NetBox VM ID. The mapping is fixed, so a VM can always be found from either side, and the IDs stay clear of templates (9000–9904) and hand-built guests (100 and up) |
| `vms_managed_tag: packetflow` | Every VM the role creates gets this Proxmox tag, and it never touches a VM without it. The API user needs rights on all of `/vms` (VM IDs come from NetBox), so the tag is what keeps it away from other VMs |
| `vms_unmanaged_tag: unmanaged` | VMs built by hand can still be documented in NetBox. Without the tag the role would plan a fresh clone for them |
| `vms_protection: true` | Proxmox refuses to delete a protected VM, also by mistake in the GUI. Orphans have to be unprotected and removed by hand, on purpose |
| `vms_onboot: true` | VMs come back after a node reboot |
| `vms_cpu_type: x86-64-v2-AES` | Proxmox's own default CPU model: it works on any recent x86 host, so VMs can move between different nodes, and still has AES-NI |
| `vms_name_pattern: ""` | Off by default. Set it to your naming convention and a typo in NetBox is a plan error, not a VM with a wrong name |
| `vms_dhcp_tag: dhcp` | Some VMs take their address from DHCP. The tag says so explicitly, so a missing IP is never silently turned into DHCP |
| `vms_ci_user: ansible` | The cloud-init user that the next playbook (`make ansible`) logs in as |
| `vms_plan_only: false` | `make plan` sets it to true: same code path, nothing written |
| `vms_pool: ""` | Only needed on shared clusters: then each site gets its own pool and the role only looks for orphans inside it |

Also built in, without a switch: disks only grow (a smaller disk in NetBox is
a plan error), CPU and memory changes on a running VM are applied at its next
reboot (the role warns and never reboots), and a VM on another node than
NetBox says is reported, not migrated.

### template: the image every VM is cloned from

Builds VM templates from the distro's cloud image with `virt-customize`,
offline on the node. No installer, no Packer, and no VM boots during the
build, so a template takes minutes and comes out generalized (no SSH host
keys, empty machine-id). [README](../ansible_collections/packetflow/proxmox/roles/template/README.md) · [docs/templates.md](templates.md)

| Default | Why |
|---|---|
| `template_method: ssh` | Customizing the image needs root on the node. The `api` method gets by with an API token, but only with the vendor image as is: fine for test templates |
| `template_vmid: 9000`, `template_name: tpl-<distro>` | One well-known ID per site template. Test templates use 9900+ and names that end in `-test` |
| `template_distros` | Ubuntu 24.04 and 26.04 (with the Zabbix agent), Ubuntu 22.04 and Debian 12/13 for tests. Every image is checked against the vendor's checksum file. `current`/`latest` follow the vendor's rebuilds; point `image_url` at a dated directory to pin one |
| `template_zabbix_agent: true` | The agent is in the image, so a new VM is monitored from its first boot, before any playbook runs |
| `template_force: false` | Rebuilding replaces only something that *is* a template with that name. Any other VM at that ID is refused |
| `template_test_user: ""` | A console password for test templates only: refused unless the name ends in `-test`, so it can't end up in a site template |

## packetflow.zabbix

### agent

`zabbix-agent2`, baked into the template and kept in line by `make ansible`.
Both paths produce byte-identical config (tested).

| Default | Why |
|---|---|
| `zabbix_agent_server: zabbix-proxy` | A short name resolved through the site's DNS search domain: the same template works for every site, and each site's VMs find their own proxy (or the server, when the record points there) |
| no `Hostname`, `HostnameItem=system.hostname[fqdn]` | Zabbix host names are unique per server; the FQDN keeps `web01` at two sites apart |
| `zabbix_agent_host_metadata: packetflow-linux` | The autoregistration action matches it: a new VM appears in Zabbix with the right templates, no clicking |
| PSK, same for every site | Zabbix has one autoregistration PSK per server, so it can't be per site. It lives in the site's sops secrets |
| `zabbix_agent_version: "7.0"` | LTS; 7.0 agents work with every 7.x proxy and server |

### host_status

Disables Zabbix hosts that NetBox says are out of service. Run it on a
schedule, e.g. every 15 minutes from a systemd timer (`make zabbix-status`, [README](../ansible_collections/packetflow/zabbix/roles/host_status/README.md)).

| Default | Why |
|---|---|
| disable on `offline`, `decommissioning`, `inventory` | A VM that's switched off on purpose stops alerting |
| enable on `active` | Back in service = monitored again |
| `host_status_tag: netbox_status` | The role tags what it disabled, and only re-enables hosts with that tag. A host disabled by hand stays disabled |
| other statuses (`planned`, `failed`, …) unchanged | `failed` is exactly when you want the alerts |
