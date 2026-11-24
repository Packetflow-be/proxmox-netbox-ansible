"""Unit tests for packetflow.proxmox.vm_plan (pytest, no Proxmox or NetBox needed)."""

import copy
import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[4] / "plugins" / "filter" / "vm_plan.py"
_spec = importlib.util.spec_from_file_location("vm_plan", _PATH)
vm_plan_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vm_plan_mod)
vm_plan = vm_plan_mod.vm_plan

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPl+test/key test@laptop"
SETTINGS = {
    "default_node": "pve",
    "template_vmid": 9000,
    "datastore": "local-lvm",
    "bridge": "vmbr0",
    "default_vlan": None,
    "default_gateway": "192.168.10.1",
    "vlan_gateways": {"20": "10.0.20.1"},
    "dns_servers": ["192.168.10.2", "192.168.10.3"],
    "dns_domain": "lab.example",
    "ssh_public_keys": [KEY],
}
TEMPLATE = {"vmid": 9000, "name": "tpl-ubuntu2404", "node": "pve", "template": True, "type": "qemu", "status": "stopped"}


def nb_vm(vid=1, **kw):
    """A NetBox VM as nb_lookup(raw_data=true) returns it."""
    vm = {
        "id": vid,
        "name": f"lab-tst{vid:02d}",
        "status": {"value": "active", "label": "Active"},
        "device": {"id": 1, "name": "pve"},
        "vcpus": 2.0,
        "memory": 2048,
        "disk": 20480,
        "primary_ip4": {"id": 5, "address": "192.168.10.21/24"},
        "custom_fields": {"vlan_id": None},
    }
    vm.update(kw)
    return vm


def pve_vm(vmid=10001, **config):
    """A Proxmox VM (proxmox_vm_info, config=pending) matching nb_vm() exactly."""
    cfg = {
        "name": "lab-tst01",
        "cores": 2,
        "memory": "2048",
        "cpu": "x86-64-v2-AES",
        "ciuser": "ansible",
        "ipconfig0": "ip=192.168.10.21/24,gw=192.168.10.1",
        "nameserver": "192.168.10.2 192.168.10.3",
        "searchdomain": "lab.example",
        "sshkeys": "ssh-ed25519%20AAAAC3NzaC1lZDI1NTE5AAAAIPl%2Btest%2Fkey%20test%40laptop%0A",
        "onboot": 1,
        "protection": 1,
        "agent": "enabled=1",
        "tags": "packetflow",
        "net0": "virtio=BC:24:11:AA:BB:CC,bridge=vmbr0",
        "scsi0": "local-lvm:vm-10001-disk-0,discard=on,iothread=1,size=20G,ssd=1",
    }
    cfg.update(config)
    return {"vmid": vmid, "name": cfg["name"], "node": "pve", "status": "running", "type": "qemu",
            "template": False, "tags": cfg.get("tags"), "config": cfg}


def plan(nb, pve, **settings):
    s = copy.deepcopy(SETTINGS)
    s.update(settings)
    return vm_plan(nb, pve, s)


def only(p):
    assert len(p["vms"]) == 1, p
    return p["vms"][0]


# --- create --------------------------------------------------------------------

def test_create_new_vm():
    p = plan([nb_vm()], [TEMPLATE])
    vm = only(p)
    assert p["errors"] == []
    assert vm["actions"] == ["create", "config", "nic", "disk", "start"]
    assert vm["vmid"] == 10001 and vm["node"] == "pve" and vm["clone_node"] == "pve" and vm["target"] is None
    assert vm["ipconfig0"] == "ip=192.168.10.21/24,gw=192.168.10.1"
    assert vm["disk_size"] == "20G" and vm["cores"] == 2 and vm["memory"] == 2048
    assert vm["tags"] == ["packetflow"]
    assert p["summary"]["create"] == 1


def test_offline_vm_is_created_stopped():
    vm = only(plan([nb_vm(status={"value": "offline"})], [TEMPLATE]))
    assert "start" not in vm["actions"] and vm["state"] == "stopped"


def test_clone_to_other_node_sets_target():
    vm = only(plan([nb_vm(device={"name": "pve2"})], [TEMPLATE]))
    assert vm["clone_node"] == "pve" and vm["target"] == "pve2"


def test_missing_template_is_an_error():
    p = plan([nb_vm()], [])
    assert p["vms"] == [] and "make template" in p["errors"][0]


def test_template_id_used_by_normal_vm_is_an_error():
    p = plan([nb_vm()], [dict(TEMPLATE, template=False)])
    assert any("not a template" in e for e in p["errors"])


# --- mapping -----------------------------------------------------------------------

def test_vcpus_floor_disk_ceil():
    vm = only(plan([nb_vm(vcpus=2.5, disk=20481)], [TEMPLATE]))
    assert vm["cores"] == 2 and vm["disk_size"] == "21G"


def test_default_node_when_no_device():
    vm = only(plan([nb_vm(device=None)], [TEMPLATE], default_node="pve9"))
    assert vm["node"] == "pve9"


def test_vlan_custom_field_and_gateway_map():
    vm = only(plan([nb_vm(primary_ip4={"address": "10.0.20.22/24"}, custom_fields={"vlan_id": 20})], [TEMPLATE]))
    assert vm["vlan"] == 20 and vm["ipconfig0"] == "ip=10.0.20.22/24,gw=10.0.20.1"


def test_default_vlan_applies_without_custom_field():
    vm = only(plan([nb_vm(primary_ip4={"address": "10.0.20.22/24"})], [TEMPLATE], default_vlan=20))
    assert vm["vlan"] == 20 and "gw=10.0.20.1" in vm["ipconfig0"]


def test_no_gateway_at_all():
    vm = only(plan([nb_vm()], [TEMPLATE], default_gateway=None))
    assert vm["ipconfig0"] == "ip=192.168.10.21/24"


def test_decommissioning_vm_is_not_planned():
    p = plan([nb_vm(status={"value": "decommissioning"})], [TEMPLATE])
    assert p["vms"] == [] and p["errors"] == []


def test_plain_string_status_is_accepted():
    vm = only(plan([nb_vm(status="staged")], [TEMPLATE]))
    assert vm["state"] == "running"


# --- validation -----------------------------------------------------------------------

@pytest.mark.parametrize("override, message", [
    ({"primary_ip4": None}, "no primary IPv4"),
    ({"primary_ip4": {"address": "fd00::21/64"}}, "not IPv4"),
    ({"primary_ip4": {"address": "300.1.2.3/24"}}, "not an address with prefix"),
    ({"primary_ip4": {"address": "192.168.10.21"}}, "has no prefix"),
    ({"vcpus": None}, "vCPUs"),
    ({"vcpus": 0.5}, "vCPUs"),
    ({"memory": 256}, "memory"),
    ({"memory": None}, "memory"),
    ({"disk": None}, "disk must be at least 8 GB"),
    ({"disk": 4096}, "disk must be at least 8 GB"),
    ({"custom_fields": {"vlan_id": 4095}}, "outside 1-4094"),
    ({"name": "bad_name"}, "not a valid DNS name"),
    ({"primary_ip4": {"address": "192.168.11.21/24"}}, "gateway 192.168.10.1 is outside 192.168.11.0/24"),
])
def test_validation_errors(override, message):
    p = plan([nb_vm(**override)], [TEMPLATE])
    assert p["vms"] == []
    assert len(p["errors"]) == 1 and message in p["errors"][0], p["errors"]


def test_no_ssh_keys_is_an_error():
    p = plan([nb_vm()], [TEMPLATE], ssh_public_keys=[])
    assert "ssh_public_keys" in p["errors"][0]


def test_invalid_vm_is_not_reported_as_orphan():
    p = plan([nb_vm(primary_ip4=None)], [TEMPLATE, pve_vm()])
    assert p["orphans"] == [] and len(p["errors"]) == 1


# --- existing VMs -----------------------------------------------------------------------

def test_matching_vm_has_no_actions():
    p = plan([nb_vm()], [TEMPLATE, pve_vm()])
    vm = only(p)
    assert vm["actions"] == [], vm["changes"]
    assert p["summary"] == {"create": 0, "update": 0, "unchanged": 1, "orphans": 0, "errors": 0, "unmanaged": 0}


def test_memory_in_pve81_current_format_matches():
    vm = only(plan([nb_vm()], [TEMPLATE, pve_vm(memory="current=2048")]))
    assert vm["actions"] == []


def test_resize_cores_memory():
    vm = only(plan([nb_vm(vcpus=4, memory=4096)], [TEMPLATE, pve_vm()]))
    assert vm["actions"] == ["config"]
    assert "cores: 2 -> 4" in vm["changes"] and "memory: 2048 -> 4096" in vm["changes"]
    assert any("next reboot" in w for w in vm["warnings"])


def test_ip_change_updates_ipconfig():
    vm = only(plan([nb_vm(primary_ip4={"address": "192.168.10.99/24"})], [TEMPLATE, pve_vm()]))
    assert vm["actions"] == ["config"] and vm["changes"][0].startswith("ipconfig0")


def test_ssh_key_change_detected():
    vm = only(plan([nb_vm()], [TEMPLATE, pve_vm()], ssh_public_keys=[KEY, "ssh-ed25519 AAAAother x"]))
    assert "sshkeys: changed" in vm["changes"]


def test_vlan_change_is_a_nic_change():
    vm = only(plan([nb_vm(primary_ip4={"address": "10.0.20.21/24"}, custom_fields={"vlan_id": 20})],
                   [TEMPLATE, pve_vm(ipconfig0="ip=10.0.20.21/24,gw=10.0.20.1")]))
    assert vm["actions"] == ["nic"] and vm["vlan"] == 20


def test_disk_grow():
    vm = only(plan([nb_vm(disk=40960)], [TEMPLATE, pve_vm()]))
    assert vm["actions"] == ["disk"] and "scsi0: 20G -> 40G" in vm["changes"]


def test_disk_grow_from_megabytes():
    vm = only(plan([nb_vm()], [TEMPLATE, pve_vm(scsi0="local-lvm:vm-10001-disk-0,size=3584M")]))
    assert vm["actions"] == ["disk"]


def test_disk_shrink_is_an_error():
    p = plan([nb_vm(disk=10240)], [TEMPLATE, pve_vm()])
    assert p["vms"] == [] and "can't shrink" in p["errors"][0]


def test_offline_stops_running_vm():
    vm = only(plan([nb_vm(status={"value": "offline"})], [TEMPLATE, pve_vm()]))
    assert vm["actions"] == ["stop"]


def test_stopped_vm_is_started():
    p = plan([nb_vm()], [TEMPLATE, dict(pve_vm(), status="stopped")])
    assert only(p)["actions"] == ["start"]


def test_extra_proxmox_tags_are_kept():
    vm = only(plan([nb_vm()], [TEMPLATE, pve_vm(tags="backup;packetflow")]))
    assert vm["actions"] == [] and vm["tags"] == ["backup", "packetflow"]


def test_node_mismatch_is_a_warning_not_a_move():
    p = plan([nb_vm(device={"name": "pve2"})], [TEMPLATE, pve_vm()])
    vm = only(p)
    assert vm["node"] == "pve" and any("migrate it by hand" in w for w in p["warnings"])


def test_unmanaged_vm_with_same_id_is_never_touched():
    p = plan([nb_vm()], [TEMPLATE, pve_vm(tags="")])
    assert p["vms"] == [] and "Refusing to touch it" in p["errors"][0]


# --- orphans -------------------------------------------------------------------------

def test_managed_vm_not_in_netbox_is_an_orphan():
    p = plan([], [TEMPLATE, pve_vm(vmid=10007, name="old")])
    assert p["orphans"] == [{"vmid": 10007, "name": "old", "node": "pve", "status": "running"}]


def test_decommissioned_vm_that_exists_is_an_orphan():
    p = plan([nb_vm(status={"value": "decommissioning"})], [TEMPLATE, pve_vm()])
    assert [o["vmid"] for o in p["orphans"]] == [10001]


def test_unmanaged_vms_are_ignored():
    p = plan([], [TEMPLATE, pve_vm(vmid=100, tags="")])
    assert p["orphans"] == []


# --- naming and hand-built VMs --------------------------------------------------------

PATTERN = r"^[a-z]{3,4}-[a-z]+[0-9]{2}$"


@pytest.mark.parametrize("name", ["lab-tst01", "exa-dc01", "acme-web01"])
def test_names_matching_the_pattern(name):
    p = plan([nb_vm(name=name)], [TEMPLATE], name_pattern=PATTERN)
    assert p["errors"] == [] and only(p)["name"] == name


@pytest.mark.parametrize("name", ["lab-test1", "laboratory-tst01", "lab-tst001", "labtst01"])
def test_names_not_matching_the_pattern(name):
    p = plan([nb_vm(name=name)], [TEMPLATE], name_pattern=PATTERN)
    assert p["vms"] == [] and "doesn't match vms_name_pattern" in p["errors"][0], p["errors"]


def test_no_name_check_by_default():
    p = plan([nb_vm(name="anything-goes")], [TEMPLATE])
    assert p["errors"] == [] and only(p)["name"] == "anything-goes"


def test_vm_tagged_unmanaged_in_netbox_is_skipped():
    hand_built = nb_vm(vid=2, name="hand-built01", memory=None, primary_ip4=None,
                       tags=[{"id": 1, "name": "unmanaged", "slug": "unmanaged"}])
    p = plan([nb_vm(), hand_built], [TEMPLATE])
    assert p["errors"] == [] and only(p)["netbox_id"] == 1
    assert p["summary"]["unmanaged"] == 1 and p["summary"]["create"] == 1


# --- pools (shared clusters) -----------------------------------------------------------

def test_clone_goes_into_the_pool():
    vm = only(plan([nb_vm()], [TEMPLATE], pool="lab"))
    assert vm["pool"] == "lab" and "create" in vm["actions"]


def test_no_pool_by_default():
    assert only(plan([nb_vm()], [TEMPLATE]))["pool"] is None


def test_vm_id_in_another_pool_is_refused():
    p = plan([nb_vm()], [TEMPLATE, dict(pve_vm(), pool="exa")], pool="lab")
    assert p["vms"] == [] and "pool 'exa', not 'lab'" in p["errors"][0]


def test_own_pool_vm_is_managed():
    p = plan([nb_vm()], [TEMPLATE, dict(pve_vm(), pool="lab")], pool="lab")
    assert p["errors"] == [] and only(p)["actions"] == []


def test_orphans_only_in_own_pool():
    other = dict(pve_vm(vmid=10077, name="exa-rds01"), pool="exa")
    ours = dict(pve_vm(vmid=10099, name="old"), pool="lab")
    p = plan([], [TEMPLATE, other, ours], pool="lab")
    assert [o["vmid"] for o in p["orphans"]] == [10099]


def test_without_pool_every_tagged_vm_counts():
    other = dict(pve_vm(vmid=10077, name="exa-rds01"), pool="exa")
    assert [o["vmid"] for o in plan([], [TEMPLATE, other])["orphans"]] == [10077]


# --- network from NetBox interfaces and VNets --------------------------------------------

def iface(vm_id=1, name="eth0", vlan_id=7, vid=20):
    return {"id": 100 + vm_id, "name": name, "virtual_machine": {"id": vm_id},
            "untagged_vlan": {"id": vlan_id, "vid": vid, "name": f"V{vid}"} if vlan_id else None}


def test_interface_vlan_is_the_tag():
    vm = only(plan([nb_vm(primary_ip4={"address": "10.0.20.21/24"})], [TEMPLATE], netbox_interfaces=[iface()]))
    assert (vm["bridge"], vm["vlan"], vm["vnet"]) == ("vmbr0", 20, None)
    assert vm["ipconfig0"] == "ip=10.0.20.21/24,gw=10.0.20.1" and vm["netbox_interface"] == "eth0"


def test_interface_vlan_on_a_vnet_is_untagged():
    vm = only(plan([nb_vm(primary_ip4={"address": "10.0.20.21/24"})], [TEMPLATE],
                   netbox_interfaces=[iface()], vnets={"7": "exa"}))
    assert (vm["bridge"], vm["vlan"], vm["vnet"], vm["mtu"]) == ("exa", None, "exa", 1)
    assert vm["ipconfig0"].endswith("gw=10.0.20.1"), "the VLAN's gateway still applies"


def test_interface_wins_over_custom_field():
    vm = only(plan([nb_vm(primary_ip4={"address": "10.0.20.21/24"}, custom_fields={"vlan_id": 30})], [TEMPLATE],
                   netbox_interfaces=[iface()]))
    assert vm["vlan"] == 20


def test_without_interface_vlan_custom_field_still_works():
    vm = only(plan([nb_vm(primary_ip4={"address": "10.0.20.21/24"}, custom_fields={"vlan_id": 20})], [TEMPLATE],
                   netbox_interfaces=[iface(vlan_id=None)]))
    assert (vm["bridge"], vm["vlan"], vm["netbox_interface"]) == ("vmbr0", 20, None)


def test_first_interface_by_name_and_only_this_vms():
    ifaces = [iface(vm_id=2, vid=99, vlan_id=9), iface(name="eth1", vid=30, vlan_id=8), iface(name="eth0")]
    vm = only(plan([nb_vm(primary_ip4={"address": "10.0.20.21/24"})], [TEMPLATE], netbox_interfaces=ifaces))
    assert (vm["vlan"], vm["netbox_interface"]) == (20, "eth0")


def test_existing_vm_moves_to_the_vnet():
    p = plan([nb_vm(primary_ip4={"address": "10.0.20.21/24"})],
             [TEMPLATE, pve_vm(ipconfig0="ip=10.0.20.21/24,gw=10.0.20.1")],
             netbox_interfaces=[iface()], vnets={"7": "exa"})
    vm = only(p)
    assert vm["actions"] == ["nic"] and vm["changes"] == ["net0: vmbr0/untagged -> exa/untagged mtu=1"]


def test_vnet_nic_with_inherited_mtu_is_unchanged():
    p = plan([nb_vm(primary_ip4={"address": "10.0.20.21/24"})],
             [TEMPLATE, pve_vm(ipconfig0="ip=10.0.20.21/24,gw=10.0.20.1", net0="virtio=BC:24:11:AA:BB:CC,bridge=exa,mtu=1")],
             netbox_interfaces=[iface()], vnets={"7": "exa"})
    assert only(p)["actions"] == []


# --- template per platform, DHCP ----------------------------------------------------

TPL2604 = dict(TEMPLATE, vmid=9001, name="tpl-ubuntu2604")


def test_platform_picks_its_template():
    vm = only(plan([nb_vm(platform={"id": 3, "slug": "ubuntu-2604"})], [TEMPLATE, TPL2604],
                   templates={"ubuntu-2604": 9001}))
    assert vm["template_vmid"] == 9001 and vm["changes"] == ["create from template 9001 on pve"]


def test_no_platform_uses_the_default_template():
    assert only(plan([nb_vm()], [TEMPLATE], templates={"ubuntu-2604": 9001}))["template_vmid"] == 9000


def test_unmapped_platform_is_an_error():
    p = plan([nb_vm(platform={"slug": "debian-13"})], [TEMPLATE], templates={"ubuntu-2604": 9001})
    assert p["vms"] == [] and "platform 'debian-13' has no template" in p["errors"][0]


def test_missing_platform_template_is_an_error():
    p = plan([nb_vm(platform={"slug": "ubuntu-2604"})], [TEMPLATE], templates={"ubuntu-2604": 9001})
    assert p["vms"] == [] and "template 9001 is missing" in p["errors"][0]


def test_dhcp_tag_needs_no_primary_ip():
    vm = only(plan([nb_vm(primary_ip4=None, tags=[{"slug": "dhcp", "name": "dhcp"}])], [TEMPLATE]))
    assert vm["ipconfig0"] == "ip=dhcp" and vm["dhcp"] is True


def test_dhcp_tag_ignores_a_recorded_lease():
    vm = only(plan([nb_vm(primary_ip4={"address": "10.50.30.77/24"}, tags=[{"slug": "dhcp"}])], [TEMPLATE]))
    assert vm["ipconfig0"] == "ip=dhcp"


def test_existing_dhcp_vm_is_unchanged():
    p = plan([nb_vm(tags=[{"slug": "dhcp"}])], [TEMPLATE, pve_vm(ipconfig0="ip=dhcp")])
    assert only(p)["actions"] == []


def test_without_ip_or_dhcp_tag_still_an_error():
    p = plan([nb_vm(primary_ip4=None)], [TEMPLATE])
    assert "no primary IPv4" in p["errors"][0] and "'dhcp'" in p["errors"][0]


def test_bad_input_raises():
    with pytest.raises((ValueError, Exception)):
        vm_plan({}, [], {})
