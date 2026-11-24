# Copyright: Packetflow
# Apache License 2.0 (see LICENSE)
"""vm_plan: compare the VMs NetBox wants with the VMs Proxmox has.

Pure data in, data out, no API calls, so it can be unit-tested and shown
before anything changes (make plan). The vms role runs the modules for what
the plan says.
"""

from __future__ import annotations

import ipaddress
import math
import re
from urllib.parse import unquote

try:
    from ansible.errors import AnsibleFilterError
except ImportError:  # unit tests without ansible-core
    AnsibleFilterError = ValueError

DOCUMENTATION = r"""
name: vm_plan
short_description: Plan Proxmox VM changes from NetBox virtual machines
description:
  - Input is the list of NetBox virtual machines (C(netbox.netbox.nb_lookup) with C(raw_data=true)).
  - Arguments are the C(proxmox_vms) list from C(community.proxmox.proxmox_vm_info) (with C(config=pending))
    and a settings dict (see the vms role defaults).
  - Returns C(vms) (one entry per managed NetBox VM with its C(actions)), C(orphans), C(errors),
    C(warnings), C(template) and C(summary).
positional: proxmox_vms, settings
options:
  _input:
    description: NetBox virtual machines.
    type: list
    elements: dict
    required: true
  proxmox_vms:
    description: Proxmox VMs with configuration.
    type: list
    elements: dict
    required: true
  settings:
    description: Site settings.
    type: dict
    required: true
"""

RETURN = r"""
_value:
  description: The plan.
  type: dict
"""

DEFAULTS = {
    "default_node": None,
    "template_vmid": 9000,
    "datastore": "local-lvm",
    "bridge": "vmbr0",
    "default_vlan": None,
    "default_gateway": None,
    "vlan_gateways": {},
    "dns_servers": [],
    "dns_domain": None,
    "ssh_public_keys": [],
    "ci_user": "ansible",
    "vmid_offset": 10000,
    "managed_tag": "packetflow",
    "pool": "",
    "templates": {},
    "dhcp_tag": "dhcp",
    "netbox_interfaces": [],
    "vnets": {},
    "unmanaged_tag": "unmanaged",
    "name_pattern": "",
    "cpu_type": "x86-64-v2-AES",
    "protection": True,
    "onboot": True,
    "running_statuses": ["active", "planned", "staged"],
    "stopped_statuses": ["offline"],
    "min_memory_mb": 512,
    "min_disk_gb": 8,
}

# Proxmox's dns-name format for VM names.
_NAME_RE = re.compile(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?$")
_TAG_SPLIT_RE = re.compile(r"[;,\s]+")
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMGT]?)$")
_SIZE_FACTOR_MB = {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024, "": 1 / (1024 * 1024)}


def _value(field):
    """NetBox choice fields come as {'value': ..., 'label': ...} or a plain string."""
    if isinstance(field, dict):
        return field.get("value")
    return field


def _name(field):
    if isinstance(field, dict):
        return field.get("name")
    return field


def _kv(text):
    """'a=1,b=2' -> {'a': '1', 'b': '2'}; a bare first item maps to ''."""
    out = {}
    for item in str(text or "").split(","):
        if not item:
            continue
        key, _, val = item.partition("=")
        out[key.strip()] = val.strip()
    return out


def _tags(text):
    return {t for t in _TAG_SPLIT_RE.split(str(text or "")) if t}


def _size_mb(text):
    m = _SIZE_RE.match(str(text or ""))
    if not m:
        return None
    return float(m.group(1)) * _SIZE_FACTOR_MB[m.group(2)]


def _int(value, default=None):
    if value is None or value == "":
        return default
    text = str(value)
    if text.startswith("current="):  # PVE 8.1+ memory format
        text = _kv(text).get("current", "")
    try:
        return int(float(text))
    except ValueError:
        return default


def _flag(value):
    """Proxmox booleans: 1/0, '1', 'enabled=1', '1,fstrim_cloned_disks=1'."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    kv = _kv(value)
    if "enabled" in kv:
        return kv["enabled"] == "1"
    return str(value).split(",")[0].strip() == "1"


def _cpu_type(value):
    kv = _kv(value)
    if "cputype" in kv:
        return kv["cputype"]
    return str(value or "").split(",")[0] or "kvm64"


def _ssh_keys(value):
    return {line.strip() for line in unquote(str(value or "")).splitlines() if line.strip()}


def _net(value):
    kv = _kv(value)
    tag, mtu = kv.get("tag"), kv.get("mtu")
    return {"bridge": kv.get("bridge"), "tag": int(tag) if tag else None, "mtu": int(mtu) if mtu else None}


def _network(vm, s):
    """(vlan id, vnet, interface name) for net0.

    From the VM's NetBox interface with an untagged VLAN (the first by name):
    its VLAN, and the Proxmox VNet mapped to that VLAN (custom field pve_vnet),
    if any. Without such an interface: custom field vlan_id, else default_vlan.
    """
    vid = vm.get("id")
    ifaces = sorted(
        (i for i in s["netbox_interfaces"] or []
         if isinstance(i.get("virtual_machine"), dict) and i["virtual_machine"].get("id") == vid
         and isinstance(i.get("untagged_vlan"), dict)),
        key=lambda i: str(i.get("name")),
    )
    if ifaces:
        vlan = ifaces[0]["untagged_vlan"]
        vnet = (s["vnets"] or {}).get(str(vlan.get("id"))) or None
        return _int(vlan.get("vid")), vnet, ifaces[0].get("name")
    custom = vm.get("custom_fields") or {}
    vlan = custom.get("vlan_id")
    return (_int(vlan) if vlan not in (None, "") else s["default_vlan"]), None, None


def _desired(vm, s, errors):
    """NetBox VM -> desired Proxmox spec, or None (errors appended)."""
    vid = vm.get("id")
    name = vm.get("name") or ""
    label = "{} (NetBox VM {})".format(name or "?", vid)
    problems = []

    if not _NAME_RE.match(name):
        problems.append(f"name {name!r} is not a valid DNS name, which Proxmox requires")
    elif s["name_pattern"] and not re.match(s["name_pattern"], name):
        problems.append(
            f"name {name!r} doesn't match vms_name_pattern {s['name_pattern']!r}"
        )

    status = _value(vm.get("status"))
    running = status in s["running_statuses"]

    node = _name(vm.get("device")) or s["default_node"]
    if not node:
        problems.append("no device (Proxmox node) set and no default_node")

    vcpus = vm.get("vcpus")
    cores = math.floor(float(vcpus)) if vcpus not in (None, "") else 0
    if cores < 1:
        problems.append(f"vCPUs must be at least 1 (got {vcpus!r})")

    memory = _int(vm.get("memory"), 0)
    if memory < s["min_memory_mb"]:
        problems.append("memory must be at least {} MB (got {!r})".format(s["min_memory_mb"], vm.get("memory")))

    disk_mb = _int(vm.get("disk"), 0)
    disk_gb = math.ceil(disk_mb / 1024.0) if disk_mb else 0
    if disk_gb < s["min_disk_gb"]:
        problems.append(
            "disk must be at least {} GB, the template size (NetBox disk = {!r} MB; add a virtual disk)".format(s["min_disk_gb"], vm.get("disk"))
        )

    # Template: the NetBox platform's entry in `templates`, else the site default.
    platform = vm.get("platform") if isinstance(vm.get("platform"), dict) else {}
    platform_slug = platform.get("slug")
    template_vmid = int(s["template_vmid"])
    if platform_slug:
        if platform_slug in (s["templates"] or {}):
            template_vmid = int(s["templates"][platform_slug])
        else:
            problems.append(f"platform {platform_slug!r} has no template in vms_templates")

    # DHCP: the VM carries the dhcp tag; no static address, no gateway.
    dhcp = bool(s["dhcp_tag"]) and s["dhcp_tag"] in _nb_tags(vm)
    ip = None
    primary = vm.get("primary_ip4") or {}
    address = primary.get("address") if isinstance(primary, dict) else None
    if dhcp:
        pass  # a primary IP, if recorded, documents the lease; it isn't configured
    elif not address:
        problems.append(f"no primary IPv4: assign an IP (with prefix) to an interface and set it as primary, or tag the VM {s['dhcp_tag']!r}")
    else:
        try:
            ip = ipaddress.ip_interface(address)
        except ValueError:
            problems.append(f"primary IPv4 {address!r} is not an address with prefix")
        else:
            if ip.version != 4:
                problems.append(f"primary IPv4 {address!r} is not IPv4")
                ip = None
            elif ip.network.prefixlen == 32 and "/" not in address:
                problems.append(f"primary IPv4 {address!r} has no prefix")
                ip = None

    vlan, vnet, iface = _network(vm, s)
    if vlan is not None and not 1 <= int(vlan) <= 4094:
        problems.append(f"VLAN {vlan!r} is outside 1-4094")

    gateway = None
    if vlan is not None:
        gateway = (s["vlan_gateways"] or {}).get(str(vlan)) or (s["vlan_gateways"] or {}).get(int(vlan))
    gateway = None if dhcp else (gateway or s["default_gateway"])
    if gateway and ip is not None:
        try:
            gw = ipaddress.ip_address(gateway)
        except ValueError:
            problems.append(f"gateway {gateway!r} is not an IP address")
        else:
            if gw not in ip.network:
                fix = f"set vlan_gateways[{vlan}] for this VLAN" if vlan is not None else "check default_gateway"
                problems.append(f"gateway {gw} is outside {ip.network}: {fix}")

    if not s["ssh_public_keys"]:
        problems.append("no ssh_public_keys configured for the site")

    if problems:
        errors.extend(f"{label}: {p}" for p in problems)
        return None

    if dhcp:
        ipconfig = "ip=dhcp"
    else:
        ipconfig = f"ip={ip.with_prefixlen}"
        if gateway:
            ipconfig += f",gw={gateway}"
    return {
        "name": name,
        "netbox_id": vid,
        "vmid": s["vmid_offset"] + int(vid),
        "node": node,
        "state": "running" if running else "stopped",
        "status": status,
        "cores": cores,
        "memory": memory,
        "cpu": s["cpu_type"],
        "disk_gb": disk_gb,
        "disk_size": f"{disk_gb}G",
        # A VNet carries the VLAN itself (VLAN, QinQ or VXLAN zone): no tag on the NIC.
        "bridge": vnet or s["bridge"],
        "vlan": int(vlan) if vlan is not None and not vnet else None,
        "vnet": vnet,
        # mtu=1: the NIC takes its bridge's MTU, so a VXLAN VNet (1450) just works.
        "mtu": 1 if vnet else None,
        "netbox_interface": iface,
        "ipconfig0": ipconfig,
        "dhcp": dhcp,
        "template_vmid": template_vmid,
        "nameservers": list(s["dns_servers"]),
        "searchdomains": [s["dns_domain"]] if s["dns_domain"] else [],
        "ciuser": s["ci_user"],
        "sshkeys": "\n".join(s["ssh_public_keys"]),
        "onboot": bool(s["onboot"]),
        "protection": bool(s["protection"]),
        "agent": "enabled=1",
        "description": f"Managed by packetflow from NetBox VM {vid}. Change it in NetBox.",
        "pool": s["pool"] or None,
    }


def _config_changes(want, have, managed_tag):
    """Changes proxmox_kvm update has to make; returns (changes, merged tags)."""
    cfg = have.get("config") or {}
    changes = []

    def diff(field, old, new):
        if old != new:
            changes.append(f"{field}: {old} -> {new}")

    diff("name", have.get("name"), want["name"])
    diff("cores", _int(cfg.get("cores"), 1), want["cores"])
    diff("memory", _int(cfg.get("memory"), 512), want["memory"])
    diff("cpu", _cpu_type(cfg.get("cpu")), want["cpu"])
    diff("ciuser", cfg.get("ciuser"), want["ciuser"])
    diff("ipconfig0", _kv(cfg.get("ipconfig0")), _kv(want["ipconfig0"]))
    diff("nameserver", str(cfg.get("nameserver") or "").split(), want["nameservers"])
    diff("searchdomain", str(cfg.get("searchdomain") or "").split(), want["searchdomains"])
    if _ssh_keys(cfg.get("sshkeys")) != _ssh_keys(want["sshkeys"]):
        changes.append("sshkeys: changed")
    diff("onboot", _flag(cfg.get("onboot")), want["onboot"])
    diff("protection", _flag(cfg.get("protection")), want["protection"])
    diff("agent", _flag(cfg.get("agent")), True)

    tags = _tags(cfg.get("tags") or have.get("tags"))
    if managed_tag not in tags:
        changes.append(f"tags: add {managed_tag}")
    return changes, sorted(tags | {managed_tag})


def _nb_tags(vm):
    """NetBox tags (nb_lookup raw_data: list of {'slug': ..., 'name': ...}) -> set of slugs and names."""
    out = set()
    for tag in vm.get("tags") or []:
        if isinstance(tag, dict):
            out.update(v for v in (tag.get("slug"), tag.get("name")) if v)
        else:
            out.add(str(tag))
    return out


def vm_plan(netbox_vms, proxmox_vms, settings):
    if not isinstance(netbox_vms, list) or not isinstance(proxmox_vms, list) or not isinstance(settings, dict):
        raise AnsibleFilterError("vm_plan expects (list netbox_vms) | vm_plan(list proxmox_vms, dict settings)")
    s = dict(DEFAULTS)
    s.update({k: v for k, v in settings.items() if v is not None or k in ("default_vlan", "default_gateway")})

    errors, warnings, entries, orphans = [], [], [], []
    managed_statuses = set(s["running_statuses"]) | set(s["stopped_statuses"])
    by_vmid = {int(vm["vmid"]): vm for vm in proxmox_vms if vm.get("type", "qemu") == "qemu"}

    template = by_vmid.get(int(s["template_vmid"]))
    if template is not None and not template.get("template"):
        errors.append("VM {} exists but is not a template".format(s["template_vmid"]))
        template = None

    wanted_vmids = set()
    unmanaged = 0
    for vm in netbox_vms:
        if s["unmanaged_tag"] and s["unmanaged_tag"] in _nb_tags(vm):
            unmanaged += 1  # built by hand (not from the template): documented in NetBox, never touched
            continue
        status = _value(vm.get("status"))
        if status not in managed_statuses:
            continue  # decommissioning etc.: not wanted; reported as orphan if it exists
        want = _desired(vm, s, errors)
        if want is None:
            # Keep an invalid VM out of the orphan report: it is still wanted.
            wanted_vmids.add(s["vmid_offset"] + int(vm.get("id", 0)))
            continue
        wanted_vmids.add(want["vmid"])
        have = by_vmid.get(want["vmid"])
        entry = dict(want, exists=have is not None, actions=[], changes=[], warnings=[])

        if have is None:
            template = by_vmid.get(want["template_vmid"])
            if template is not None and not template.get("template"):
                errors.append("{}: VM {} exists but is not a template".format(want["name"], want["template_vmid"]))
                continue
            if template is None:
                errors.append("{}: template {} is missing: run `make template`".format(want["name"], want["template_vmid"]))
                continue
            entry["clone_node"] = template.get("node")
            entry["target"] = want["node"] if want["node"] != template.get("node") else None
            entry["tags"] = [s["managed_tag"]]
            entry["actions"] = ["create", "config", "nic", "disk"] + (["start"] if want["state"] == "running" else [])
            entry["changes"] = ["create from template {} on {}".format(want["template_vmid"], want["node"])]
            entries.append(entry)
            continue

        have_tags = _tags((have.get("config") or {}).get("tags") or have.get("tags"))
        if s["pool"] and not have.get("template") and (have.get("pool") or "") != s["pool"]:
            errors.append(
                "{}: VM ID {} is used by {!r} in pool {!r}, not {!r} (another site's VM?). "
                "Refusing to touch it.".format(want["name"], want["vmid"], have.get("name"), have.get("pool") or "", s["pool"])
            )
            continue
        if have.get("template") or s["managed_tag"] not in have_tags:
            errors.append(
                "{}: VM ID {} is already used by {!r}, which is not managed (no {!r} tag). "
                "Refusing to touch it.".format(want["name"], want["vmid"], have.get("name"), s["managed_tag"])
            )
            continue

        if have.get("node") and have["node"] != want["node"]:
            entry["warnings"].append(
                "runs on {}, NetBox says {}: migrate it by hand".format(have["node"], want["node"])
            )
            entry["node"] = have["node"]

        cfg = have.get("config") or {}
        changes, tags = _config_changes(want, have, s["managed_tag"])
        entry["tags"] = tags
        if changes:
            entry["actions"].append("config")
            entry["changes"].extend(changes)

        net = _net(cfg.get("net0"))
        if net != {"bridge": want["bridge"], "tag": want["vlan"], "mtu": want["mtu"]}:
            entry["actions"].append("nic")
            entry["changes"].append(
                "net0: {}/{}{} -> {}/{}{}".format(
                    net["bridge"], net["tag"] or "untagged", " mtu=" + str(net["mtu"]) if net["mtu"] else "",
                    want["bridge"], want["vlan"] or "untagged", " mtu=" + str(want["mtu"]) if want["mtu"] else "")
            )

        current_mb = _size_mb(_kv(cfg.get("scsi0")).get("size"))
        want_mb = want["disk_gb"] * 1024
        if current_mb is None:
            errors.append("{}: can't read the size of scsi0 ({!r})".format(want["name"], cfg.get("scsi0")))
            continue
        if want_mb < current_mb:
            errors.append(
                "{}: NetBox disk {} GB is smaller than the current {:.0f} GB; disks can't shrink".format(want["name"], want["disk_gb"], current_mb / 1024)
            )
            continue
        if want_mb > current_mb:
            entry["actions"].append("disk")
            entry["changes"].append("scsi0: {:.0f}G -> {}".format(current_mb / 1024, want["disk_size"]))

        running = have.get("status") == "running"
        if want["state"] == "running" and not running:
            entry["actions"].append("start")
            entry["changes"].append("start")
        elif want["state"] == "stopped" and running:
            entry["actions"].append("stop")
            entry["changes"].append("stop (NetBox status {})".format(want["status"]))
        if running and want["state"] == "running" and ("config" in entry["actions"] or "nic" in entry["actions"]):
            entry["warnings"].append("CPU, memory and cloud-init changes apply at the next reboot")
        entries.append(entry)

    for vmid, have in sorted(by_vmid.items()):
        if have.get("template") or vmid in wanted_vmids:
            continue
        if s["managed_tag"] not in _tags((have.get("config") or {}).get("tags") or have.get("tags")):
            continue
        if s["pool"] and (have.get("pool") or "") != s["pool"]:
            continue  # another pool (site) on a shared cluster: not ours to report
        orphans.append({"vmid": vmid, "name": have.get("name"), "node": have.get("node"), "status": have.get("status")})

    for entry in entries:
        warnings.extend("{}: {}".format(entry["name"], w) for w in entry["warnings"])

    summary = {
        "create": sum(1 for e in entries if "create" in e["actions"]),
        "update": sum(1 for e in entries if e["actions"] and "create" not in e["actions"]),
        "unchanged": sum(1 for e in entries if not e["actions"]),
        "orphans": len(orphans),
        "errors": len(errors),
        "unmanaged": unmanaged,
    }
    return {
        "vms": entries,
        "orphans": orphans,
        "errors": errors,
        "warnings": warnings,
        "template": {"vmid": int(s["template_vmid"]), "node": template.get("node")} if template else None,
        "summary": summary,
    }


class FilterModule:
    def filters(self):
        return {"vm_plan": vm_plan}
