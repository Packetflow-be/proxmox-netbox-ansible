# packetflow.proxmox.template

Builds VM templates from a distro's cloud image: the site template every
site VM is cloned from (9000, Ubuntu 24.04 with the Zabbix agent), and test
templates for other distros. There's no installer and no Packer, and no VM
boots during the build. How-to and conventions: [docs/templates.md](../../../../../docs/templates.md).

Two build methods (`template_method`):

| | `ssh` (default) | `api` |
|---|---|---|
| runs | on the node, as root over SSH | on the controller, Proxmox API token |
| image | customized offline: guest agent, Zabbix agent, test login | the vendor's image as is |
| used for | site templates (`make template` in a site repo) | test templates without root SSH (`make test-template` in this repo) |

## ssh

1. Download the distro's cloud image (`template_distros[template_distro]`) and
   check it against the checksum file next to it. The image is cached in
   `/var/tmp/packetflow-template/cache`.
2. Customize a copy **offline** with `virt-customize` (installed on the node
   from `libguestfs-tools`):
   - install `qemu-guest-agent`;
   - install and configure `zabbix-agent2` (`packetflow.zabbix.agent`,
     `tasks_from: bake`: repo, PSK, proxy, autoregistration metadata);
   - test templates only: allow SSH password login;
   - the distro's clean-up (`apt-get clean`), empty `/etc/machine-id`.
3. `qm create` the template with the image imported as `scsi0`, a cloud-init
   drive, the serial console cloud images expect, and the guest agent enabled;
   for a test template, the cloud-init user, password and SSH keys. Then
   `qm template`. The Notes say what it is, when it was built, and the image's
   URL and checksum.
4. Delete the build directory, which holds the PSK, even when the build fails.

Takes a few minutes on the node. The result is still generalized: cloud-init
hasn't run, and there are no SSH host keys or machine-id.

## api

Proxmox downloads the image into `template_import_storage` (content type
`import`; the checksum from the vendor's file is verified by Proxmox), and the
template's disk is imported from it. The file name carries the checksum, so a
rebuild reuses an unchanged image and downloads a new one after the vendor
refreshes it. The image isn't changed: no guest agent (so `agent` is off), no
Zabbix agent, and SSH password login stays off on cloud images; use SSH keys
(`template_test_ssh_keys`) or the password on the console.

## Run

From a site repo: `make template`. It runs `playbooks/00-template.yml`
against the `proxmox_node` group as **root over SSH**; `virt-customize` and
`qm` need the node's shell, not the API.

| Variable | Default | |
|---|---|---|
| `template_distro` | `ubuntu2404` | an entry of `template_distros`: `ubuntu2404`, `ubuntu2604`, `ubuntu2204`, `debian12`, `debian13` |
| `template_vmid` / `template_name` | `9000` / `tpl-<distro>` | |
| `template_method` | `ssh` | or `api` (see above; needs `template_node`) |
| `template_storage` | `local-lvm` | disk and cloud-init drive |
| `template_bridge` | `vmbr0` | clones get their NIC from the vms role |
| `template_image_url` | from `template_distros` | `current`/`latest` follow the vendor's rebuilds; point it at a dated directory to pin one |
| `template_force` | `false` | `true` (`make template FORCE=1`) rebuilds an existing template |
| `template_zabbix_agent` | `true` | bake in the Zabbix agent (ssh; Ubuntu 24.04 and 26.04); needs `zabbix_agent_psk_identity` and `zabbix_agent_psk` |
| `template_test_user` / `template_test_password` | `""` | test login through cloud-init; refused unless `template_name` ends in `-test` |
| `template_test_ssh_keys` / `…_file` | `""` | SSH public keys for the test user |
| `template_api_*` | `PROXMOX_*` environment | host, port, user, token ID and secret for `api` |

**Existing ID:** with nothing at `template_vmid`, the role builds. If the
template is already there, it does nothing unless `template_force` is set. Anything
else at that ID (a normal VM, or a template with another name) is refused,
and nothing changes. A rebuild deletes the old template first; existing VMs
are full clones and aren't affected.

The node needs internet access (the image, the distro's archive, `repo.zabbix.com`).

## Tests

`tests/integration/template/run.sh`: the ssh method on the test machine with a
fake `qm` and `virt-customize` and a local image server: the commands it runs,
the test-login guard, idempotency and refusals. Both methods were also run
against a real node (Proxmox VE 9.1.7, `make test-template`), and clones of the
results booted and accepted the test login.

## Check a fresh template

```bash
qm clone 9000 9100 --name tpl-test --full
qm set 9100 --ciuser ansible --sshkeys ~/.ssh/id_ed25519.pub --ipconfig0 ip=dhcp
qm start 9100
qm guest cmd 9100 network-get-interfaces     # guest agent answers
ssh ansible@<ip> systemctl is-active zabbix-agent2
qm stop 9100 && qm destroy 9100
```
