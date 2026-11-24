# packetflow.proxmox

Proxmox VE VMs from NetBox: the template they are cloned from, and the VMs
themselves.

## Roles

| Role | Runs on | What |
|---|---|---|
| [`template`](roles/template/README.md) | the node, as root over SSH (or the controller, Proxmox API) | Builds templates from a distro's cloud image: the site template 9000 (Ubuntu 24.04, guest agent and Zabbix agent baked in with `virt-customize`) and test templates (Ubuntu, Debian). No Packer, no installer |
| [`vms`](roles/vms/README.md) | the controller (Proxmox API) | Makes the site's VMs match NetBox: plan, then clone, configure, resize, start or stop. Only touches VMs tagged `packetflow`; reports orphans, never deletes |

The `template` role includes `packetflow.zabbix.agent`, so install that
collection too (a site repo's `requirements.yml` does).

## Plugins

| Plugin | What |
|---|---|
| `packetflow.proxmox.vm_plan` (filter) | NetBox VMs + Proxmox VMs + site settings → plan (create / config / nic / disk / start / stop, orphans, errors). No API calls; the `vms` role applies it |

## Playbooks

| Playbook | Roles |
|---|---|
| `packetflow.proxmox.template` | `template`, on every host in the inventory (`make test-template` in this repo uses it) |

Site repos have their own playbooks (`00-template.yml`, `05-vms.yml`, …), see
[`site-template/`](../../../site-template).

## Dependencies

`community.proxmox` (Proxmox API modules, needs `proxmoxer` and `requests` on
the controller) and `netbox.netbox` (`nb_lookup`, needs `pynetbox`). The dev
setup (`make setup`) installs all of them.

## Tests

```bash
pytest tests/unit                      # vm_plan
tests/integration/vms/run.sh           # vms role against fake Proxmox + NetBox APIs
tests/integration/template/run.sh      # template role (ssh) with fake qm and virt-customize
```

CI runs all three (`.github/workflows/ansible.yml`).
