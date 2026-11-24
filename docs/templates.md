# VM templates

Templates are built from each distro's official **cloud image** by
[`packetflow.proxmox.template`](../ansible_collections/packetflow/proxmox/roles/template/README.md):
no installer, no Packer. A build is one command and can be repeated at any
time; the template's **Notes** in Proxmox record what it is, when it was built,
and the exact image (URL and checksum).

## Which templates exist

| ID | Name | Built by | What |
|---|---|---|---|
| 9000 | `tpl-ubuntu2404` | `make template` in a **site repo** (ssh method) | the site template: Ubuntu 24.04 + guest agent + Zabbix agent with the site's PSK. The `vms` role clones every site VM from it |
| 9900 | `tpl-ubuntu2404-test` | `make test-template` in **this repo** | test template: Ubuntu 24.04, login `debian` / `debian` |
| 9901 | `tpl-ubuntu2204-test` | `make test-template DISTRO=ubuntu2204` | idem, Ubuntu 22.04 |
| 9902 | `tpl-debian12-test` | `make test-template DISTRO=debian12` | idem, Debian 12 |
| 9903 | `tpl-debian13-test` | `make test-template DISTRO=debian13` | idem, Debian 13 |
| 9904 | `tpl-ubuntu2604-test` | `make test-template DISTRO=ubuntu2604` | idem, Ubuntu 26.04 |
| 9001 | `tpl-ubuntu2604` | `make template ARGS="-e template_distro=ubuntu2604 -e template_vmid=9001"` in a site repo | site template on Ubuntu 26.04 (guest agent + Zabbix agent). VMs get it through their NetBox platform: `vms_templates: {ubuntu-2604: 9001}` |

Templates are tagged `template` and `<distro>` in Proxmox. 9000 is built from
a site repo, with that site's Zabbix PSK.

Test templates (name ends in `-test`) have a known password. They're for
trying things out, never for production VMs: the role refuses a test login on
any template whose name doesn't end in `-test`.

## Test templates

Two ways to build one; use **ssh** when you have root SSH on the node:

```bash
cd ~/packetflow/proxmox-netbox-ansible
make test-template METHOD=ssh HOST=192.0.2.10              # Ubuntu 24.04 -> 9900, root over SSH
make test-template METHOD=ssh HOST=192.0.2.10 DISTRO=debian12
make test-template METHOD=ssh HOST=192.0.2.10 FORCE=1      # rebuild 9900 from the newest image

make test-template NODE=pve                                # through the API instead (no root SSH needed)
```

| | `METHOD=ssh` | `METHOD=api` (default) |
|---|---|---|
| access | root SSH with your key on the node (`HOST` = its address) | a Proxmox API token (`NODE` = the node's name) |
| guest agent | installed: Proxmox shows the clone's IP | no: find the IP in DHCP leases or ARP |
| SSH login | password `debian` and your keys | keys only (cloud images turn password login off); the password works on the console and for `sudo` |
| image cache | `/var/tmp/packetflow-template/cache` on the node | `local:import/` (file name contains the checksum) |

- **Login:** user `debian`, password `debian` (change with `TEST_USER=`,
  `TEST_PASSWORD=`), plus the keys in `~/.ssh/authorized_keys` of the machine
  you run it on (override with `TEST_SSH_KEYS=<file>`).
- **Service account** (`METHOD=ssh`): `automation`, baked into
  the image with the key `~/.ssh/id_ed25519.pub` of the machine you run it on
  (e.g. the automation host's key), key login only, passwordless
  `sudo`. Change with `SERVICE_USER=` / `SERVICE_KEYS=`, `SERVICE_USER=` (empty)
  to leave it out. Role variables: `template_service_user`,
  `template_service_ssh_keys(_file)`; they work for site templates too.
- **API credentials:** `PROXMOX_*` from the environment, else from `./.env`.
  The token needs the rights below; the `vms` token doesn't have them on
  purpose.
- A second run does nothing while the template exists; `FORCE=1` rebuilds it.
  The cached image is reused until the vendor publishes a new one.

Using one:

```bash
# Proxmox UI: right-click the template -> Clone (Full), then on the clone:
#   Hardware -> Network Device: VLAN tag if you need one, Hard Disk -> Resize (the image is ~3.5 GB)
#   Cloud-Init -> IP Config: DHCP (or a static IP), then Start
ssh debian@<ip>                                      # password debian, or one of your keys
```

Tested on Proxmox VE 9.1.7: both methods built 9900. A clone of the
ssh-built template got a DHCP address, the guest agent reported it,
cloud-init finished, and SSH with the password and `sudo` worked. With the
api-built template, SSH with a key and `sudo` worked.

### Token rights for the api method

Tested with a `root@pam` token. The least-privilege set below follows
Proxmox's permission checks but **hasn't been tested yet**; if a step fails
with 403 the message names the missing privilege.

| Path | Privileges | For |
|---|---|---|
| `/storage/local` | `Datastore.AllocateTemplate`, `Datastore.Audit` | download the image into `local:import/`, list it, import from it |
| `/nodes/<node>` | `Sys.Audit`, `Sys.Modify` | download-url runs as a node task |
| `/storage/local-lvm` | `Datastore.AllocateSpace` | the template's disk and cloud-init drive |
| `/vms` | `VM.Allocate`, `VM.Config.*`, `VM.Audit` | create the VM, set cloud-init, convert to template |
| `/sdn/zones/localnetwork/vmbr0` | `SDN.Use` | the template's NIC |

Storage `local` must allow content type `import` (Datacenter → Storage →
local → Content).

## Site templates

Built per site because the Zabbix agent's PSK is baked in:

```bash
cd ~/packetflow/site-<slug>
make template           # 9000 on the site's default node, root over SSH
make template FORCE=1   # rebuild (existing VMs are full clones, unaffected)
```

This uses the ssh method: `virt-customize` on the node installs the guest
agent and the Zabbix agent into the image before it becomes the template. It
needs root SSH with your key on the node (`ssh-copy-id root@<node>`); see
[setup.md](setup.md#3-proxmox-node-once-per-node).

## Versioning

- **The build code** is versioned with this repo: site repos pin a tag
  (`collections_ref`), so a site builds its template with the role version it pins.
- **The image**: `current` (Ubuntu) and `latest` (Debian) follow the vendor's
  rebuilds, which carry the security updates. Each template's Notes record the
  checksum it was built from, so you can always tell which image it is.
- **Pinning** one image: set `image_url` to a dated directory in
  `template_distros` (in the role defaults, or in a site's `group_vars`), for
  example `https://cloud-images.ubuntu.com/noble/release-20260915/noble-server-cloudimg-amd64.img`
  or `https://cloud.debian.org/images/cloud/bookworm/20260901-1234/debian-12-genericcloud-amd64.qcow2`.
  (the dates are examples: pick a real one from the vendor's directory
  listing). The checksum file is taken from the same directory.
- **Refresh** about once a month (`FORCE=1`), and after a vendor security
  advisory. Existing VMs are full clones and don't change.

## Another distro

Add an entry to `template_distros` in
[`roles/template/defaults/main.yml`](../ansible_collections/packetflow/proxmox/roles/template/defaults/main.yml)
(or override it in a site's `group_vars`), give it a test ID in the
`TEST_VMIDS` list in the root `Makefile`, and add it to the table above.

```yaml
template_distros:
  debian12:
    description: Debian 12 (bookworm)                 # shown in the template's Notes
    image_url: https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2
    checksum_file: SHA512SUMS                         # next to the image
    checksum_algorithm: sha512
    zabbix: false                                     # the Zabbix bake supports Ubuntu 24.04 only
    # cleanup: dnf clean all                          # ssh method; default apt-get clean
```

What an image needs:

- a **cloud image with cloud-init** in **qcow2** format ("genericcloud",
  "cloudimg", "GenericCloud"). The api method stores it as `.qcow2`, so a raw
  image doesn't work there;
- a checksum file in the usual `<hash>  <file>` (or `<hash> *<file>`) format,
  `sha256` or `sha512`. Rocky, Alma and Fedora publish BSD-style
  `CHECKSUM` files (`SHA256 (file) = hash`), which the role doesn't read yet:
  that needs a small parser change in `tasks/api.yml` (and `get_url` in the ssh
  method doesn't read them either);
- for the **ssh** method: a package manager `virt-customize --install` knows
  (apt, dnf, zypper) for `qemu-guest-agent`, and a `cleanup` command;
- for the **Zabbix agent**: the bake (`packetflow.zabbix.agent`, `tasks_from:
  bake`) writes an apt source for Ubuntu `noble`. Other distros need their own
  repo file in that role first; until then build them with
  `template_zabbix_agent: false`.

Then `make test-template NODE=pve DISTRO=<name>` and try a clone before
using it anywhere else.
