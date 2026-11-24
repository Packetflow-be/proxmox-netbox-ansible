#!/usr/bin/env bash
# Integration test for packetflow.proxmox.template, template_method ssh, on
# this machine: fake qm and virt-customize (they log their arguments), and a
# local HTTP server for the "cloud image" and its checksum file. Checks the
# commands the role runs, the test login guard and idempotency.
# Needs: ansible-core, python3.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
# KEEP=1 keeps the work dir for debugging.
trap 'kill "${WEB_PID:-0}" 2>/dev/null || true; if [[ -n "${KEEP:-}" ]]; then echo "kept $WORK"; else rm -rf "$WORK"; fi' EXIT

mkdir -p "$WORK/www/noble" "$WORK/bin" "$WORK/qm"
head -c 65536 /dev/urandom > "$WORK/www/noble/noble-server-cloudimg-amd64.img"
(cd "$WORK/www/noble" && sha256sum noble-server-cloudimg-amd64.img | sed 's/  / */' > SHA256SUMS)
echo "0000  other.img" >> "$WORK/www/noble/SHA256SUMS"

# qm: state per VM ID in $WORK/qm/<id>; every call is logged.
cat > "$WORK/bin/qm" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "qm $*" >> "$FAKE_LOG"
cmd=$1 id=$2; shift 2
f="$FAKE_QM/$id"
case $cmd in
  config)   [[ -f $f ]] || { echo "Configuration file 'nodes/pve/qemu-server/$id.conf' does not exist" >&2; exit 2; }; cat "$f" ;;
  create)   [[ ! -f $f ]] || exit 1; while (($#)); do [[ $1 == --name ]] && echo "name: $2" > "$f"; shift; done ;;
  set)      [[ -f $f ]] ;;
  template) echo "template: 1" >> "$f" ;;
  destroy)  rm -f "$f" ;;
esac
SH
cat > "$WORK/bin/virt-customize" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "virt-customize $*" >> "$FAKE_LOG"
SH
chmod +x "$WORK/bin/"*

(cd "$WORK/www" && exec python3 -m http.server 18080 --bind 127.0.0.1 >/dev/null 2>&1) & WEB_PID=$!
for _ in $(seq 50); do (echo > /dev/tcp/127.0.0.1/18080) 2>/dev/null && break; sleep 0.1; done

export PATH="$WORK/bin:$PATH" FAKE_QM="$WORK/qm"
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPl+test/key test@laptop" > "$WORK/keys.pub"
COMMON=(-e template_work_dir="$WORK/node" -e template_install_tools=false -e template_zabbix_agent=false
        -e template_image_url=http://127.0.0.1:18080/noble/noble-server-cloudimg-amd64.img)
run() { # name, extra args...
  local name=$1; shift
  export FAKE_LOG="$WORK/$name.calls"; : > "$FAKE_LOG"
  ansible-playbook -i localhost, -c local "$HERE/test.yml" "${COMMON[@]}" "$@" > "$WORK/$name.log" 2>&1
}
fail=0
check() { if eval "$1"; then echo "ok   $2"; else echo "FAIL $2"; fail=1; fi; }

run build1 -e template_vmid=9900 -e template_name=tpl-ubuntu2404-test \
  -e template_test_user=debian -e template_test_password=debian -e template_test_ssh_keys_file="$WORK/keys.pub" \
  -e template_service_user=automation -e template_service_ssh_keys_file="$WORK/keys.pub" \
  || { cat "$WORK/build1.log"; exit 1; }
C="$WORK/build1.calls"
check "grep -q '^virt-customize -a $WORK/node/build/image.img --install qemu-guest-agent' $C" "guest agent installed into the working copy"
check "grep -q 'sshd_config.d/01-packetflow-test-login.conf:PasswordAuthentication yes' $C" "test login: SSH password drop-in"
check "! grep -q zabbix $C" "no Zabbix bake when disabled"
check "grep -q -- '--truncate /etc/machine-id' $C" "machine-id emptied"
check "grep -q -- '--run-command useradd --create-home --shell /bin/bash automation --ssh-inject automation:file:$WORK/node/build/service_keys.pub' $C" "service account created with its key"
check "grep -q -- '--write /etc/sudoers.d/90-automation:automation ALL=(ALL) NOPASSWD:ALL --chmod 0440:/etc/sudoers.d/90-automation' $C" "service account: passwordless sudo"
check "grep -q '^qm create 9900 --name tpl-ubuntu2404-test' $C" "template created with its name"
check "grep -q 'import-from=$WORK/node/build/image.img' $C" "disk imported from the working copy"
check "grep -q -- '--tags template;ubuntu2404' $C" "tags"
check "grep -qE -- '--description Ubuntu 24.04 LTS .*TEST LOGIN debian.*sha256 [0-9a-f]{64}' $C" "description records the test login and checksum"
check "grep -q '^qm set 9900 --ciuser debian --cipassword debian --sshkeys $WORK/node/build/test_keys.pub' $C" "cloud-init user, password and keys"
check "tail -1 $C | grep -q '^qm template 9900'" "converted to template last"
check "[[ ! -e $WORK/node/build ]]" "build directory removed"
check "[[ -f $WORK/node/cache/noble-server-cloudimg-amd64.img ]]" "image cached"

run again -e template_vmid=9900 -e template_name=tpl-ubuntu2404-test -e template_test_user=debian -e template_test_password=debian \
  || { cat "$WORK/again.log"; exit 1; }
check "! grep -qE '^qm (create|destroy|set|template)' $WORK/again.calls && ! grep -q virt-customize $WORK/again.calls" "second run changes nothing"

run force -e template_vmid=9900 -e template_name=tpl-ubuntu2404-test -e template_force=true || { cat "$WORK/force.log"; exit 1; }
check "grep -q '^qm destroy 9900' $WORK/force.calls && grep -q '^qm create 9900' $WORK/force.calls" "force rebuilds"
check "! grep -q 'qm set' $WORK/force.calls && ! grep -q PasswordAuthentication $WORK/force.calls" "no test login unless asked"
check "! grep -q useradd $WORK/force.calls" "no service account unless asked"

if run guard -e template_vmid=9000 -e template_name=tpl-ubuntu2404 -e template_test_user=debian -e template_test_password=debian; then
  check false "test login refused on a non -test template"
else
  check "grep -q 'ending in \"-test\"' $WORK/guard.log && [[ ! -f $WORK/qm/9000 ]]" "test login refused on a non -test template"
fi

if run nozabbix -e template_vmid=9901 -e template_distro=debian12 -e template_zabbix_agent=true; then
  check false "Zabbix bake refused for a distro without support"
else
  check "grep -q 'only supports' $WORK/nozabbix.log && [[ ! -f $WORK/qm/9901 ]]" "Zabbix bake refused for a distro without support"
fi

echo "name: some-vm" > "$WORK/qm/9500"
if run taken -e template_vmid=9500 -e template_name=tpl-x; then
  check false "an ID used by something else is refused"
else
  check "grep -q 'is not template tpl-x' $WORK/taken.log && grep -q 'some-vm' $WORK/qm/9500" "an ID used by something else is refused"
fi

((fail == 0)) && echo "all checks passed"
exit $fail
