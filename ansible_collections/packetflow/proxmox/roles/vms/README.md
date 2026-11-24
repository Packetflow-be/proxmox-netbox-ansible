# packetflow.proxmox.vms

Makes a site's Proxmox VMs match NetBox. For each of the site's clusters it
reads the tenant's VMs in that cluster from NetBox and the VMs in that
cluster's Proxmox, prints a plan, and applies it. Runs on the controller
(`hosts: localhost`) against the Proxmox API.

```
NetBox lab/lab-pve: 1 to create, 1 to change, 3 unchanged, 1 orphaned, 0 errors, 1 unmanaged (skipped)
lab-web02: create from template 9000 on pve
lab-db01: memory: 4096 -> 8192
WARNING lab-db01: CPU, memory and cloud-init changes apply at the next reboot
ORPHAN old-app
```

`vms_plan_only: true` (`make plan` in a site repo) prints the plan and changes nothing.

## Clusters

A site's VMs can live on several Proxmox clusters, e.g. its own hardware and a
shared cluster elsewhere.
`vms_clusters` lists them; each entry overrides the site-wide settings of the
same name for that cluster:

```yaml
vms_clusters:
  - netbox_cluster: acme-pve                # the site's own node(s)
    api_host: pve.acme.example
    default_node: pve
  - netbox_cluster: shared-pve              # a cluster shared with other sites
    api_host: 192.0.2.10
    api_env: PROXMOX_SHARED                 # token: PROXMOX_SHARED_USER / _TOKEN_ID / _TOKEN_SECRET
    validate_certs: false
    default_node: shared-pve1
    pool: acme
    vlan_gateways: {"40": 10.0.40.1}
```

Keys: `netbox_cluster`, `api_host`, `api_port`, `api_env`, `validate_certs`,
`default_node`, `template_vmid`, `datastore`, `bridge`, `default_vlan`,
`default_gateway`, `vlan_gateways`, `pool`, `cpu_type`. Tenant, DNS, SSH keys
and the name pattern are the site's and the same everywhere.

All clusters are planned first; an error in any of them stops the run before
the first change. Then each cluster is applied with its own token. Without
`vms_clusters` there's one cluster: `vms_netbox_cluster` with the `PROXMOX_*`
environment, as before.

## NetBox → Proxmox

| Proxmox | From NetBox |
|---|---|
| exists and running | status `active`, `planned`, `staged` |
| exists, stopped | status `offline` (a running VM is shut down, forced after `vms_timeout`) |
| not wanted | any other status, or not in NetBox: reported as an **orphan**, never deleted |
| VM ID | `vms_vmid_offset` (10000) + NetBox VM ID |
| name | NetBox name; with `vms_name_pattern` set, a name that doesn't match it is a plan error |
| node | the VM's device, else `vms_default_node` |
| cores / memory / disk | `floor(vCPUs)` / memory MB / `ceil(disk MB / 1024)` GB, at least 8 |
| IP | primary IPv4 with prefix, via cloud-init (**required**), unless the VM is tagged `dhcp` (`vms_dhcp_tag`): then `ip=dhcp` and no primary IP is needed. Record the lease in NetBox afterwards (IP status `dhcp`); it is documentation, not configuration |
| template | the VM's NetBox **platform** looked up in `vms_templates` (`{ubuntu-2604: 9001}`), else `vms_template_vmid`; a platform without an entry is a plan error |
| network (net0) | the untagged VLAN of the VM's interface (the first by name that has one). If that VLAN's custom field `pve_vnet` names a Proxmox SDN VNet: the NIC goes on that VNet, untagged, with the VNet's MTU (`mtu=1`, so VXLAN VNets work). Else: `vms_bridge` with the VLAN's tag. Without such an interface: custom field `vlan_id`, else `vms_default_vlan` (null = untagged) |
| gateway | `vms_vlan_gateways[<VLAN ID>]`, else `vms_default_gateway`; must be inside the IP's subnet |
| DNS | `vms_dns_servers`, search domain `vms_dns_domain` |
| user | `vms_ci_user` (`ansible`) with `vms_ssh_public_keys` |

New VMs are full clones of the template (`vms_template_vmid`), with
protection and start-on-boot on and the tag `packetflow`.

## Safety rules

- **NetBox VMs tagged `unmanaged` are skipped** (`vms_unmanaged_tag`): VMs
  built by hand that NetBox only documents. Without the tag the role would
  plan a new clone for them (VM ID 10000 + NetBox ID).
- **Shared clusters: one pool per site** (`vms_pool`, usually the tenant
  slug). New VMs are cloned into it, orphans are only looked for in it, and a
  VM ID that belongs to another pool is refused. With a token that only has
  rights on its pool, other sites' VMs aren't even visible.
- **Only VMs tagged `packetflow` are touched.** If a NetBox VM's ID is taken by
  an untagged VM, the plan fails and nothing changes.
- **Any plan error stops the run before the first change**: a missing IP, too
  little memory, a gateway outside the subnet, a disk smaller than it is now
  (disks only grow), a missing template.
- **Orphans are reported, never deleted.** An apply run ends with a failure
  that lists them, after all other changes are done. Delete them in Proxmox by
  hand (Options → Protection off, then Remove), or set them back to
  active/offline in NetBox.
- **Changes to running VMs:** CPU, memory and cloud-init changes are stored
  but only apply at the next reboot. The plan warns; the role never reboots.
  Proxmox derives the cloud-init instance-id from the VM config, so after any
  change the next boot is a first boot for cloud-init: it regenerates the SSH
  host keys (unless a cloud-init drop-in sets `ssh_deletekeys: false`) and runs the
  template's `package_upgrade`, a full apt upgrade. Plan the reboot for it.
- **Node moves aren't done:** if a VM runs on another node than NetBox says,
  the plan warns and leaves it.
- `community.proxmox.proxmox_kvm` rewrites the whole config and always reports
  a change, so the role only calls it when the plan found a difference.

A multi-node cluster clones from the template's node to another node only if
the template is on shared storage (Proxmox's rule).

## Variables

See [`defaults/main.yml`](defaults/main.yml). A site repo sets them in
`inventory/group_vars/all/main.yml`, generated by Copier.

## Credentials (environment)

| Variable | |
|---|---|
| `NETBOX_URL`, `NETBOX_TOKEN` | NetBox (read-only is enough) |
| `NETBOX_VALIDATE_CERTS` | `false` for a self-signed NetBox certificate (`vms_netbox_validate_certs`, default `true`) |
| `PROXMOX_HOST` (`PROXMOX_PORT`, default 8006) | node or cluster address, no `https://` |
| `PROXMOX_USER`, `PROXMOX_TOKEN_ID`, `PROXMOX_TOKEN_SECRET` | the API token below: user `ansible@pve`, token ID `lab`. A cluster in `vms_clusters` with `api_env: PROXMOX_SHARED` reads `PROXMOX_SHARED_USER`, `…_TOKEN_ID`, `…_TOKEN_SECRET` instead, and takes its host from `api_host` |
| `PROXMOX_VALIDATE_CERTS` | `false` for a self-signed node certificate |

## Least-privilege API user (`ansible@pve`)

| Path | Role | Why |
|---|---|---|
| `/vms` | `PacketflowVM` | read all VMs (plan, orphans), clone the template, configure, resize, start, stop. VM IDs come from NetBox, so the ACL can't be narrowed to single IDs |
| `/nodes/<node>` | `PacketflowNode` | `Sys.Audit`: the modules check that the node exists |
| `/storage/local-lvm` | `PVEDatastoreUser` (built-in) | disks of new clones, cloud-init drives |
| `/sdn/zones/localnetwork/vmbr0` | `PVESDNUser` (built-in) | attach NICs to the bridge |

On the node, as root (replace `lab` with the site slug and `pve` with the node):

```bash
pveum role add PacketflowVM -privs "VM.Allocate VM.Audit VM.Clone VM.Config.CDROM VM.Config.Cloudinit VM.Config.CPU VM.Config.Disk VM.Config.HWType VM.Config.Memory VM.Config.Network VM.Config.Options VM.PowerMgmt"
pveum role add PacketflowNode -privs "Sys.Audit"

pveum user add ansible@pve --comment "VMs from NetBox (packetflow.proxmox.vms)"
# privsep 0: the token has exactly the user's permissions. Prints the secret ONCE.
pveum user token add ansible@pve lab --privsep 0 --comment "site-lab"

pveum acl modify /vms                          --users ansible@pve --roles PacketflowVM
pveum acl modify /nodes/pve                    --users ansible@pve --roles PacketflowNode
pveum acl modify /storage/local-lvm            --users ansible@pve --roles PVEDatastoreUser
pveum acl modify /sdn/zones/localnetwork/vmbr0 --users ansible@pve --roles PVESDNUser
```

`/vms` lets this user change any VM on the node. The `packetflow` tag rule
above is what keeps the role away from other VMs; `protection` blocks deletes.

Keep the user **out of groups** that have roles higher up (`/`): with
`privsep 0` the token gets everything its user has, including group roles.
Check with `pveum user permissions <user>`: only the four paths above should
show up.

**Tested against a real node** (Proxmox VE 9.1): with exactly these
ACLs the role built VMs (clone, configure, start), and plan-only
runs read the node, the VMs and their configs. Resize and stop are covered by
the integration test, not yet by a real node. The list comes from PVE's permission checks
for the calls the role makes. If a task fails with a 403, the message names
the missing privilege; add it with
`pveum role modify PacketflowVM -append 1 -privs <Priv>`.

## Tests

- `tests/unit/plugins/filter/test_vm_plan.py`: the plan logic (pytest).
- `tests/integration/vms/run.sh`: the role against fake Proxmox and NetBox
  APIs, with the real `community.proxmox` and `netbox.netbox` modules. It
  creates two VMs, checks every write call, re-runs to prove nothing changes,
  changes memory in NetBox and checks it converges again.
