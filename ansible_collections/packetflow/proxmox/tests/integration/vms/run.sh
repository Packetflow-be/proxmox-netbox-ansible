#!/usr/bin/env bash
# Integration test for packetflow.proxmox.vms against fake Proxmox and NetBox
# APIs (fake_api.py): the real community.proxmox and netbox.netbox modules run
# unchanged. Needs: ansible-core, proxmoxer, pynetbox, requests, openssl, and
# community.proxmox + netbox.netbox on ANSIBLE_COLLECTIONS_PATH next to this repo.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
# KEEP=1 keeps the work dir (logs, calls, plans) for debugging.
trap 'kill "${API_PID:-0}" 2>/dev/null || true; if [[ -n "${KEEP:-}" ]]; then echo "kept $WORK"; else rm -rf "$WORK"; fi' EXIT

openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 \
  -keyout "$WORK/key.pem" -out "$WORK/cert.pem" 2>/dev/null

cat > "$WORK/pve.json" <<'EOF'
{
 "9000": {"node": "pve", "status": "stopped", "template": 1, "config": {
   "name": "tpl-ubuntu2404", "cores": 2, "memory": "2048", "cpu": "x86-64-v2-AES", "agent": "enabled=1",
   "scsihw": "virtio-scsi-single", "boot": "order=scsi0", "ide2": "local-lvm:vm-9000-cloudinit,media=cdrom",
   "scsi0": "local-lvm:base-9000-disk-0,discard=on,iothread=1,size=3584M,ssd=1",
   "net0": "virtio=BC:24:11:00:90:00,bridge=vmbr0", "template": 1}},
 "100": {"node": "pve", "status": "running", "template": 0, "config": {
   "name": "router", "cores": 1, "memory": "1024", "scsi0": "local-lvm:vm-100-disk-0,size=8G",
   "net0": "virtio=BC:24:11:00:01:00,bridge=vmbr0"}},
 "10077": {"node": "pve", "status": "running", "template": 0, "pool": "exa", "config": {
   "name": "exa-rds01", "cores": 4, "memory": "8192", "tags": "packetflow",
   "scsi0": "local-lvm:vm-10077-disk-0,size=80G", "net0": "virtio=BC:24:11:01:00:77,bridge=exa"}},
 "10099": {"node": "pve", "status": "running", "template": 0, "pool": "lab", "config": {
   "name": "old-app", "cores": 1, "memory": "1024", "tags": "packetflow",
   "scsi0": "local-lvm:vm-10099-disk-0,size=8G", "net0": "virtio=BC:24:11:01:00:99,bridge=vmbr0"}}
}
EOF

# Second cluster (hq): only the template, on node hq1.
cat > "$WORK/pve_hq.json" <<'JSON'
{
 "9000": {"node": "hq1", "status": "stopped", "template": 1, "config": {
   "name": "tpl-ubuntu2404", "cores": 2, "memory": "2048", "cpu": "x86-64-v2-AES", "agent": "enabled=1",
   "scsihw": "virtio-scsi-single", "boot": "order=scsi0", "ide2": "local-lvm:vm-9000-cloudinit,media=cdrom",
   "scsi0": "local-lvm:base-9000-disk-0,discard=on,iothread=1,size=3584M,ssd=1",
   "net0": "virtio=BC:24:11:00:90:00,bridge=vmbr0", "template": 1}}
}
JSON

# NetBox: the hosted VM's interface is on VLAN 2042, which the VNet "lab" carries.
cat > "$WORK/netbox_interfaces.json" <<'JSON'
[{"id": 60, "name": "eth0", "virtual_machine": {"id": 6, "name": "lab-rds01"}, "cluster": "hq-pve",
  "mode": {"value": "access", "label": "Access"}, "untagged_vlan": {"id": 90, "vid": 2042, "name": "LAB_HOSTED"}}]
JSON
cat > "$WORK/netbox_vlans.json" <<'JSON'
[{"id": 90, "vid": 2042, "name": "LAB_HOSTED", "custom_fields": {"pve_vnet": "lab"}},
 {"id": 91, "vid": 2012, "name": "OTHER", "custom_fields": {"pve_vnet": null}}]
JSON

vm() { # id name tenant status device vcpus memory disk ip vlan
  local device=null vlan=null
  [[ "$5" != - ]] && device="{\"id\": 1, \"name\": \"$5\"}"
  [[ "${10}" != - ]] && vlan="${10}"
  cat <<EOF
{"id": $1, "name": "$2", "tenant": {"id": 1, "slug": "$3"}, "cluster": {"id": 1, "name": "$3-pve"},
 "status": {"value": "$4", "label": "$4"}, "device": $device, "vcpus": $6, "memory": $7, "disk": $8,
 "primary_ip4": {"id": $1, "address": "$9"}, "custom_fields": {"vlan_id": $vlan}}
EOF
}
netbox() { # web_memory
  printf '[%s,%s,%s,%s,%s,%s]\n' \
    "$(vm 1 lab-web01 lab active pve 2.0 "$1" 20480 192.168.10.21/24 -)" \
    "$(vm 2 lab-db01 lab offline - 4.0 8192 40960 10.0.20.22/24 20)" \
    "$(vm 3 oth-vm01 other active pve 2.0 2048 20480 192.168.10.99/24 -)" \
    "$(vm 4 lab-old01 lab decommissioning pve 1.0 1024 10240 192.168.10.44/24 -)" \
    "$(vm 5 hand-built01 lab active pve 4.0 0 0 10.50.30.10/24 - | sed 's/}$/, "tags": [{"slug": "unmanaged", "name": "unmanaged"}]}/')" \
    "$(vm 6 lab-rds01 lab active hq1 4.0 8192 81920 10.50.200.10/24 - | sed 's/"lab-pve"/"hq-pve"/')" \
    > "$WORK/netbox.json"
}
netbox 2048
: > "$WORK/calls.jsonl"

python3 "$HERE/fake_api.py" "$WORK" & API_PID=$!
for _ in $(seq 50); do (echo > /dev/tcp/127.0.0.1/18006) 2>/dev/null && break; sleep 0.1; done

# No PROXMOX_HOST: each cluster in test.yml brings its own api_host/port, and
# hq its own token (PROXMOX_SHARED_*), so the per-cluster connection is exercised.
unset PROXMOX_HOST PROXMOX_PORT
export PROXMOX_USER=ansible@pve PROXMOX_TOKEN_ID=lab PROXMOX_TOKEN_SECRET=secret \
       PROXMOX_SHARED_USER=lab-hosting@pve PROXMOX_SHARED_TOKEN_ID=hq PROXMOX_SHARED_TOKEN_SECRET=secret-hq \
       NETBOX_URL=http://127.0.0.1:18000 NETBOX_TOKEN=nbt_fake.token
run() { # name plan_only
  : > "$WORK/calls.jsonl"
  ansible-playbook "$HERE/test.yml" -e "out=$WORK/$1.json" -e "vms_plan_only=$2" > "$WORK/$1.log" 2>&1 \
    || { cat "$WORK/$1.log"; exit 1; }
  cp "$WORK/calls.jsonl" "$WORK/$1.calls.jsonl"
}

run apply1 false
run plan2 true
netbox 4096
run apply3 false
run plan4 true

python3 "$HERE/check.py" "$WORK"
