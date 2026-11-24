# Day-to-day

Recurring tasks after the [setup](setup.md). NetBox is the source of truth:
change it there first, then let the tools follow.

## Names

Pick a naming convention and put it in `vms_name_pattern` (a regex): the `vms`
role then refuses other names before it changes anything. Empty, any valid DNS
name goes.

## VMs built from the template (site repos)

| Task | In NetBox | Then |
|---|---|---|
| New VM | Add the VM: name, status **Active**, tenant, cluster, device (node), role, vCPUs, memory, a virtual disk, an interface (Access, untagged VLAN) with its IP as **Primary IPv4** | `make plan`, then `make vms`, then `make ansible` |
| Other VLAN | Change the interface's untagged VLAN | `make vms` moves net0 (keeps the MAC) |
| More CPU / memory | Change vCPUs / memory | `make vms`; applies at the VM's next reboot (the plan warns) |
| Bigger disk | Change the virtual disk size | `make vms` (disks only grow) |
| Stop a VM | Status **Offline** | `make vms` shuts it down |
| Retire a VM | Status **Decommissioning** (or delete it) | `make vms` reports it as an **orphan** and fails on purpose; delete it in Proxmox by hand (Options → Protection off, then Remove) |
| Rename | Change the name | `make vms` renames it in Proxmox; the hostname inside changes only when cloud-init runs again, so rename it in the OS too |

`make plan` never changes anything: run it whenever you're unsure.

## VMs built by hand

VMs that didn't come from the template (appliances, containers, the
automation host itself) are still documented in NetBox, with the tag
**`unmanaged`**, so the `vms` role skips them (`… N unmanaged (skipped)` in the
plan). Record them like any other VM: tenant, site, cluster, device, role,
vCPUs, memory, virtual disks, interface with MAC and VLAN, IPs with DNS names,
and the Proxmox ID in the description (`Proxmox VM 104 on pve`).

