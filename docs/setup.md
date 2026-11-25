# Setup: fresh machine → first VM

This page covers the whole path once, in order. Component details live in each
component's README; the links below point to them.

| Where | What ends up there |
|---|---|
| Your machine (WSL or Ubuntu) | `~/packetflow/proxmox-netbox-ansible` (this repo), `~/packetflow/site-<slug>` (one per site), the tools |
| Proxmox node | template `tpl-ubuntu2404` (ID 9000), site VMs (ID 10000 + NetBox VM ID) |
| NetBox | what should exist: tenant, cluster, node, VMs, IPs |
| Zabbix | VMs register themselves through `zabbix-proxy` (a proxy, or the server) |
| GitHub | site repos install the collections from this repo at a pinned tag |

What you need before you start: a Proxmox VE node (8 or 9), a NetBox (4.x)
and a Zabbix server (7.x). This repo doesn't install those.

A **site** is one NetBox tenant with one Proxmox cluster: a location, a
department, or simply "everything" when you have one cluster.

Everything runs through Ansible from your machine. Steps 1–2 happen once.
Step 3 happens once per Proxmox node, and steps 4–5 once per site. Commands
use the lab (`lab`, node `pve`) as the example.

## 1. Your machine: tools (~10 min)

Needs Ubuntu 24.04: a Linux machine or VM, or WSL2 on Windows (PowerShell:
`wsl --install -d Ubuntu-24.04`). On WSL, work in the Linux home (`~`), never under `/mnt/c`: permissions break and
Ansible ignores a world-writable `ansible.cfg`.

```bash
sudo apt update && sudo apt install -y git make gh
git config --global user.name  "Your Name"
git config --global user.email you@example.com
gh auth login --hostname github.com --git-protocol https --web
gh repo clone Packetflow-be/proxmox-netbox-ansible ~/packetflow/proxmox-netbox-ansible
cd ~/packetflow/proxmox-netbox-ansible
make setup      # tools, venv, collections; see scripts/README.md. Re-run any time to update
exec bash       # reload PATH and the `pfa` alias
make check      # optional: CI's lint and tests, locally
```

`make` on its own lists the targets.

## 2. Keys and accounts (once)

**age key.** One key encrypts every site's secrets. Losing it means losing
access to them, so back up `keys.txt` in your password manager.

```bash
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
age-keygen -y ~/.config/sops/age/keys.txt    # public key (age1…): Copier asks for it in step 4
```

**SSH key**, if you don't have one: `ssh-keygen -t ed25519`. The public key
goes onto every VM (Copier asks for it), and onto each node for root in step 3.

**NetBox API token:** user menu → API Tokens → add a token with write
enabled. On NetBox ≥ 4.5 copy the full `nbt_<key>.<token>` value. `make seed`
needs write access; everything else only reads.

**Zabbix autoregistration PSK.** Zabbix has one per server, so every site
uses the same pair:

```bash
openssl rand -hex 32     # the PSK; pick an identity, e.g. packetflow-autoreg
```

Enter both in Zabbix under Administration → General → Autoregistration. Also
add an autoregistration action: host metadata contains `packetflow-linux`
(and, with proxies, proxy is the site's proxy), then add the host, put it in a
host group and link *Linux by Zabbix agent active*. See
[`packetflow.zabbix.agent`](../ansible_collections/packetflow/zabbix/roles/agent/README.md#zabbix-side-once).
Without that action the agent connects, the PSK is accepted, and the proxy keeps
answering `host [...] not found`: Zabbix accepts the registration and creates
nothing.

## 3. Proxmox node (once per node)

1. **API user `ansible@pve`** with its token: run the `pveum` commands from
   [`packetflow.proxmox.vms`, least-privilege API user](../ansible_collections/packetflow/proxmox/roles/vms/README.md#least-privilege-api-user-ansiblepve)
   on the node as root. The token secret is printed **once**; it goes into the
   site's secrets in step 4.
2. **Root SSH with your key** (builds the template):
   `ssh-copy-id root@<node>`, then check that `ssh root@<node>` works without a password.
3. VM ID 9000 is free: `qm status 9000` should fail with "does not exist".
4. The node can reach the internet (Ubuntu's cloud image and archive, `repo.zabbix.com`).
   `make template` installs `libguestfs-tools` on the node if it's missing.
   On a node without a subscription, switch apt to the `pve-no-subscription`
   repository first (Node → Updates → Repositories): a fresh install only has
   the enterprise repository, and `apt-get install` fails on it.
5. The site's DNS resolves `zabbix-proxy.<its domain>` to the Zabbix proxy
   (or to the Zabbix server, when you don't use proxies). VMs use that DNS and
   domain, which you enter in step 4.

Tested on Proxmox VE 9.1: the API token with these ACLs, and the template
build over root SSH. Proxmox VE 8 needs nothing different.

## 4. New site repo (per site)

```bash
cd ~/packetflow/proxmox-netbox-ansible
make site NAME=lab    # Copier asks the questions, then runs git init and makes the first commit
```

Or answer from a file, which is repeatable and needs no typing:

```bash
make site NAME=lab ANSWERS=~/packetflow/answers/lab.yml
```

The file has one line per question (example:
[`tests/site-template/answers.yml`](../tests/site-template/answers.yml)),
with `site_slug` equal to `NAME`. Nothing in it is secret, so keep it; a
missing or invalid answer stops Copier with the question's name. The site
repo keeps the answers in `.copier-answers.yml` afterwards.

The answers that matter most:

| Question | Answer |
|---|---|
| `pve_host` | the node's name or IP (no `https://`, no port) |
| `pve_validate_certs` | `false` while the node has Proxmox's self-signed certificate |
| `default_node` | the node's name **exactly as Proxmox shows it** (`hostname` on the node); the template is built there |
| `dns_servers`, `dns_domain` | **the site's** DNS servers and domain. `zabbix-proxy.<domain>` must resolve through them |
| `ssh_public_keys` | your key, e.g. `["ssh-ed25519 AAAA… you@laptop"]`; Ansible connects to the VMs as `ansible` with it |
| `age_recipients` | the `age1…` public key from step 2 |
| `collections_ref` | the tag of this repo to pin, e.g. `v0.5.12` (the default) |

All questions, with their defaults: [site-template.md](site-template.md#questions).

`make site` runs Copier on this repo (`copier.yml` + `site-template/`) at its
latest tag. To render the checkout you're in: `make site NAME=lab
TEMPLATE=$PWD REF=HEAD`.

```bash
cd ~/packetflow/site-lab
make secrets    # 1st editor: ansible@pve token secret, NetBox token, Zabbix API token. 2nd: Zabbix PSK identity + PSK
git add -A && git commit -m "Add encrypted secrets"
make publish    # optional: private GitHub repo <your account>/site-lab (REPO=owner/name for another)
```

The `*.sops.*` files are encrypted, so committing them is intended. Plaintext
never touches the disk: `make` decrypts into the command's environment only.

## 5. NetBox, template, first VM (per site)

All commands run in `~/packetflow/site-lab`.

```bash
make deps                                    # Ansible collections -> .collections/
make seed                                    # dry-run: lists what it would create
make seed APPLY=1 NODE_IP=192.168.10.5/24    # what's missing: tenant, site, cluster, device pve with its IP
make template                                # root over SSH on the node, a few minutes
```

`make template` builds template 9000 from Ubuntu's cloud image with the guest
agent and the Zabbix agent (proxy, PSK, metadata) baked in.

Then create the VM in NetBox (Virtualization → Virtual Machines → Add):

- **Name** `lab-tst01`, **status** Active, **tenant** `lab`, **cluster**
  `lab-pve`, **device** `pve`, **role** `generic`.
- **vCPUs** 2, **memory** 2048 MB.
- **Virtual disk** (VM → Virtual Disks → Add): size **20480**. NetBox stores
  it in MB, and it's rounded up to whole GB (minimum 8 GB).
- **Interface** `eth0` with IP `192.168.10.21/24`. Edit the VM and set that IP
  as its **Primary IPv4**.
- Optional: on the interface, 802.1Q mode **Access** with an **untagged VLAN**:
  the VM's NIC gets that VLAN's tag (or its Proxmox SDN VNet, see the
  [`vms` README](../ansible_collections/packetflow/proxmox/roles/vms/README.md)). Without it the VM gets the site's `default_vlan`.

```bash
make plan       # expect: 1 to create (lab-tst01 as VM 10000 + its NetBox ID)
make vms        # clone, configure, grow the disk, start
make ansible    # OS baseline + Zabbix agent check (make check = dry-run)
```

The inventory only lists VMs with status **Active**. A *Planned* VM is built
and started by `make vms`, and `make ansible` configures it once you set it
to Active in NetBox.

`make site` does `vms` and `ansible` in one run. Within a minute or two of
booting, the VM shows up in Zabbix as `lab-tst01.<domain>` through the
autoregistration action.

To keep Zabbix in line with NetBox afterwards (a VM set to offline stops
alerting), run `make zabbix-status` on a schedule:
[the `host_status` role](../ansible_collections/packetflow/zabbix/roles/host_status/README.md).

The site repo's `README.md` documents the NetBox → Proxmox mapping, including
status handling, VLANs and gateways.

## Day to day

More in [operations.md](operations.md) (NetBox conventions, hand-built VMs)
and [templates.md](templates.md) (test templates, other distros).

| Task | How |
|---|---|
| Add, resize or retire a VM | change it in NetBox, then `make plan` and `make vms` in the site repo |
| Remove a retired VM | `make vms` reports it as an orphan; delete it in Proxmox by hand (Options → Protection off, then Remove) |
| Update the tools | `make setup` in this repo: tools, venv, and a fast-forward of the checkout |
| Point a site at a newer tag of this repo | `make update DATA=collections_ref=vX.Y.Z` in the site repo, then `make deps` |
| Pull site template changes | `make update` in the site repo (commit first; answers are kept) |
| Rebuild the template (new Ubuntu image, new PSK) | `make template FORCE=1` (**deletes VM 9000 first**; existing VMs are full clones and aren't affected) |
| Rotate the Zabbix PSK | change it in Zabbix and in `inventory/group_vars/all/secrets.sops.yml`, then `make ansible` and `make template FORCE=1` |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `make setup`: `gh is not logged in` | `gh auth login --hostname github.com --git-protocol https --web` |
| `make setup`: `git has no identity` | the two `git config --global` lines in step 1 |
| sops: `no identity matched any of the recipients` | the age key isn't at `~/.config/sops/age/keys.txt`, or its public key isn't in the site's `.sops.yaml` |
| `make plan` lists `ERROR` lines | a NetBox VM is incomplete (no primary IP, too little memory or disk, a gateway outside its subnet) or its name doesn't match `vms_name_pattern`. Nothing was changed; fix it in NetBox |
| `make plan` wants to create a VM that already exists | it was built by hand, not from the template: tag it `unmanaged` in NetBox ([operations.md](operations.md#vms-built-by-hand)) |
| `make plan`: `VM ID … is already used by '…', which is not managed (no 'packetflow' tag)` | an earlier `make vms` was interrupted during the clone, before the tag was set. Delete the half-built VM in Proxmox and run again |
| `make vms` ends with "no longer wanted by NetBox" | orphans: tagged VMs NetBox doesn't list (any more). Everything else was applied; delete them by hand or fix their status in NetBox |
| `CERTIFICATE_VERIFY_FAILED` from Proxmox | self-signed node certificate: `make update DATA=pve_validate_certs=false` |
| Proxmox `403 … Permission check failed` | the named privilege is missing from the user's role; the `vms` README shows how to add it |
| The `vms` token can do far more than its ACLs | its user is in a group with a role on `/` (privsep 0 passes that on): remove the user from the group |
| `make test-template`: `403` on `/storage/local` | the token lacks the api method's rights ([templates.md](templates.md#token-rights-for-the-api-method)), or `.env` overrode the token you meant: variables already in the environment win over `.env` only when `PROXMOX_TOKEN_SECRET` is set |
| `make template`: `UNREACHABLE` | root SSH to the node's NetBox primary IP doesn't work: `ssh-copy-id root@<node>` |
| `make template`: VM 9000 exists but is not template … | something else uses the ID; free it or set `vms_template_vmid` |
| VM missing in Zabbix | `zabbix-proxy` doesn't resolve on the VM (DNS/domain answers), the PSK differs from Zabbix's autoregistration settings, or no action matches its proxy. `journalctl -u zabbix-agent2` on the VM says which |
| Ansible `UNREACHABLE` on a VM | your machine can't reach the VM's IP, or your key isn't in the site's `ssh_public_keys` |
