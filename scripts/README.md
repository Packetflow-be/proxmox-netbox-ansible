# scripts

| Script | What |
|---|---|
| `release.sh X.Y.Z` | Set the repo's version in every `galaxy.yml`, commit, print the tag command (see root README) |
| `netbox-seed.py` | Create the NetBox objects the automation expects |
| `bootstrap-wsl.sh` | Set up / update the WSL dev environment (`make setup`) |

CI: `.github/workflows/scripts.yml` runs shellcheck, ruff and `pytest scripts/tests`.

## netbox-seed.py

Creates what's missing, idempotently. It **never modifies or deletes**
existing objects. If an existing object differs from what the automation needs
(e.g. a VM role without "VM role" ticked), it prints `WARN` and exits 1, so you
fix it by hand.

Always:

| Object | Value |
|---|---|
| Cluster type | `proxmox` |
| Device role | `proxmox_node` |
| VM roles | `generic`, `webserver`, `monitoring` (device roles with *VM role* set) |
| Custom field | `vlan_id`: integer 1–4094, optional, on Virtual Machine |

With `--site lab --node pve [--node-ip 192.168.10.5/24]`:

| Object | Value |
|---|---|
| Tenant, site | `lab` |
| Manufacturer / device type | `generic` / `generic_server` (NetBox requires a type for devices) |
| Cluster | `lab-pve`, type `proxmox`, tenant `lab` |
| Device | `pve`, role `proxmox_node`, in `lab-pve`, tenant `lab`, status active |
| `--node-ip` | interface `vmbr0` with that IP, set as the device's primary IPv4, so the Ansible inventory can reach the node |

`--node` must be the node's real Proxmox name: `packetflow.proxmox.vms` uses
the VM's NetBox device as the Proxmox node.

Slugs use underscores, never hyphens. Roles become Ansible group names via
`nb_inventory` (`group_by: [role]`, `group_names_raw: true`), and a hyphen
isn't a valid group name.

### Run

```bash
pip install pynetbox            # the dev venv from scripts/bootstrap-wsl.sh has it
export NETBOX_URL=https://netbox.example
export NETBOX_TOKEN=nbt_xxxxxxxxxxxx.yyyyyyyy…   # write-enabled token

scripts/netbox-seed.py                                   # dry-run: shows what it would create
scripts/netbox-seed.py --apply                           # base objects only
scripts/netbox-seed.py --apply --site lab --node pve --node-ip 192.168.10.5/24
```

| Env var | |
|---|---|
| `NETBOX_URL` | NetBox base URL |
| `NETBOX_TOKEN` | API token with write permission. On NetBox ≥ 4.5 use the full v2 value `nbt_<key>.<token>`; pynetbox sends it as `Bearer`. Legacy v1 tokens still work |
| `NETBOX_VALIDATE_CERTS` | `false` to accept a self-signed certificate (default `true`) |

Requires NetBox ≥ 4.0 (custom fields use `object_types`). Tested against
NetBox 4.7.1: dry-run, apply, a second apply with 0 changes, and a drift
warning.

Exit codes: `0` ok, `1` warnings (existing objects need a manual fix),
`2` bad arguments.

## bootstrap-wsl.sh

Sets up the dev environment on WSL (Ubuntu, amd64). Idempotent: re-run it any
time to update. It refuses to run under `/mnt/*` (the Windows filesystem).

```bash
# first time (fresh WSL): see docs/setup.md step 1
sudo apt update && sudo apt install -y git make gh
gh auth login --hostname github.com --git-protocol https --web
gh repo clone Packetflow-be/proxmox-netbox-ansible ~/packetflow/proxmox-netbox-ansible
cd ~/packetflow/proxmox-netbox-ansible && make setup
# afterwards, to update
cd ~/packetflow/proxmox-netbox-ansible && make setup
```

Ubuntu's `gh` only bootstraps the clone; the script then installs the pinned
`gh` into `~/.local/bin`, which takes precedence. Both use the same login.

What it does:

| Step | Result |
|---|---|
| apt | `git curl make python3-venv openssh-client age …` |
| git | stops if `user.name`/`user.email` aren't set (commits need them) |
| tools → `~/.local/bin` | `sops` 3.13.3, `gh` 2.101.0: pinned versions, SHA-256 checked against the value in the script before install |
| GitHub | requires `gh auth login` (the script stops and tells you if not), then `gh auth setup-git` so git over HTTPS (clones, `ansible-galaxy` git collections) uses gh's token |
| this repo | single clone at `~/packetflow/proxmox-netbox-ansible`; fast-forwarded when clean, left alone with local changes |
| venv `~/packetflow/.venv` | `.github/requirements-ci.txt` (same ansible-core, ansible-lint, molecule, pytest, proxmoxer, pynetbox as CI) + pytz (NetBox inventory), copier 9.18.2, and CI's ruff/shellcheck pins (`make check`) |
| `~/packetflow/ansible.cfg` | `collections_path = ~/packetflow/proxmox-netbox-ansible:~/packetflow/.deps`, so `packetflow.*` load live from the checkout; exported as `ANSIBLE_CONFIG` by the venv |
| galaxy | each collection's `requirements.yml` → `~/packetflow/.deps` |
| `~/.bashrc` | `~/.local/bin` on PATH; alias `pfa` = activate venv + cd to the checkout |
| verify | tool versions, and that every `packetflow.*` resolves to the checkout |

Options: `PF_ROOT=~/elsewhere` (default `~/packetflow`). No env vars or secrets
needed. To bump a tool, change its version and SHA-256 together (from the
release's checksums file).
