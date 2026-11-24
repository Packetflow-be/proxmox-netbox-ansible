#!/usr/bin/env python3
"""Seed NetBox with the objects the automation expects.

Creates what is missing and never modifies or deletes existing objects.
Existing objects that differ from what the automation needs are reported as
warnings. Dry-run by default; pass --apply to write.

Always:
  - cluster type   proxmox
  - device role    proxmox_node
  - VM roles       generic, webserver, monitoring   (device roles with vm_role)
  - custom field   vlan_id (integer 1-4094, optional) on virtualization.virtualmachine

With --site SLUG (e.g. --site lab --node pve):
  - tenant         SLUG
  - site           SLUG, unless the node device already exists: then its site
  - cluster        --cluster NAME, default SLUG-pve (type proxmox, tenant SLUG)
  - device         NODE (role proxmox_node, in the cluster, tenant SLUG, status active)
  - with --node-ip: interface vmbr0 + that IP as the device's primary IPv4

Role and tenant slugs use underscores, never hyphens: they become Ansible
group names (nb_inventory group_by: role, group_names_raw: true).

Env: NETBOX_URL (e.g. https://netbox.example), NETBOX_TOKEN,
NETBOX_VALIDATE_CERTS (default true; false for a self-signed certificate).
Needs NetBox >= 4.0 and pynetbox.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from typing import Any

try:
    import pynetbox
    import requests
except ImportError:  # pragma: no cover - exercised by users, not tests
    sys.exit("netbox-seed: pynetbox is not installed (pip install pynetbox)")

SLUG_RE = re.compile(r"^[a-z0-9_]+$")

CLUSTER_TYPE = {"slug": "proxmox", "name": "Proxmox VE"}
NODE_ROLE = {"slug": "proxmox_node", "name": "Proxmox node", "color": "e65100", "vm_role": False}
VM_ROLES = [
    {"slug": "generic", "name": "Generic", "color": "9e9e9e", "vm_role": True},
    {"slug": "webserver", "name": "Webserver", "color": "2196f3", "vm_role": True},
    {"slug": "monitoring", "name": "Monitoring", "color": "4caf50", "vm_role": True},
]
VLAN_FIELD = {
    "name": "vlan_id",
    "label": "VLAN ID",
    "type": "integer",
    "object_types": ["virtualization.virtualmachine"],
    "required": False,
    "validation_minimum": 1,
    "validation_maximum": 4094,
    "description": "VLAN tag for the VM's NIC (packetflow.proxmox.vms). Empty = site default.",
}
MANUFACTURER = {"slug": "generic", "name": "Generic"}
DEVICE_TYPE = {"slug": "generic_server", "model": "Generic server"}
NODE_INTERFACE = {"name": "vmbr0", "type": "bridge"}


class Seeder:
    """Get-or-create against a pynetbox API, honouring dry-run."""

    def __init__(self, nb: Any, apply: bool, out=None) -> None:
        self.nb = nb
        self.apply = apply
        self.out = out if out is not None else sys.stdout
        self.created = 0
        self.pending = 0
        self.warnings = 0

    # -- output ---------------------------------------------------------------

    def _say(self, status: str, label: str, extra: str = "") -> None:
        print(f"  {status:<8} {label}{('  ' + extra) if extra else ''}", file=self.out)

    def warn(self, label: str, msg: str) -> None:
        self.warnings += 1
        self._say("WARN", label, msg)

    # -- core -----------------------------------------------------------------

    def ensure(self, endpoint: Any, label: str, lookup: dict, create: dict,
               expect: dict | None = None) -> Any:
        """Return the existing object, create it (apply), or None (dry-run)."""
        obj = endpoint.get(**lookup)
        if obj is not None:
            self._check(obj, label, expect or {})
            self._say("ok", label)
            return obj
        if not self.apply:
            self.pending += 1
            self._say("create", label, "(dry-run)")
            return None
        obj = endpoint.create(**create)
        self.created += 1
        self._say("created", label)
        return obj

    def _check(self, obj: Any, label: str, expect: dict) -> None:
        for key, want in expect.items():
            have = _plain(getattr(obj, key, None))
            if isinstance(want, list):
                if not set(want).issubset(set(have or [])):
                    self.warn(label, f"{key} is {have!r}, needs to include {want!r}")
            elif have != want:
                self.warn(label, f"{key} is {have!r}, expected {want!r}")

    # -- seed steps -----------------------------------------------------------

    def base(self) -> dict:
        nb = self.nb
        refs = {}
        refs["cluster_type"] = self.ensure(
            nb.virtualization.cluster_types, f"cluster type {CLUSTER_TYPE['slug']}",
            {"slug": CLUSTER_TYPE["slug"]}, dict(CLUSTER_TYPE))
        refs["node_role"] = self.ensure(
            nb.dcim.device_roles, f"device role {NODE_ROLE['slug']}",
            {"slug": NODE_ROLE["slug"]}, dict(NODE_ROLE))
        for role in VM_ROLES:
            self.ensure(nb.dcim.device_roles, f"VM role {role['slug']}",
                        {"slug": role["slug"]}, dict(role), expect={"vm_role": True})
        self.ensure(
            nb.extras.custom_fields, f"custom field {VLAN_FIELD['name']}",
            {"name": VLAN_FIELD["name"]}, dict(VLAN_FIELD),
            expect={"type": "integer", "object_types": VLAN_FIELD["object_types"]})
        return refs

    def site(self, slug: str, node: str, node_ip: str | None, refs: dict,
             cluster_name: str | None = None) -> None:
        nb = self.nb
        tenant = self.ensure(nb.tenancy.tenants, f"tenant {slug}",
                             {"slug": slug}, {"name": slug, "slug": slug})
        # An existing node keeps its site (a node that is already documented).
        existing_node = nb.dcim.devices.get(name=node)
        if existing_node is not None and getattr(existing_node, "site", None) is not None:
            site = existing_node.site
            self._say("ok", f"site {getattr(site, 'slug', None) or getattr(site, 'name', '?')}", f"(node {node}'s site)")
        else:
            site = self.ensure(nb.dcim.sites, f"site {slug}", {"slug": slug},
                               {"name": slug, "slug": slug, "status": "active",
                                **_ref("tenant", tenant)})
        manufacturer = self.ensure(nb.dcim.manufacturers, f"manufacturer {MANUFACTURER['slug']}",
                                   {"slug": MANUFACTURER["slug"]}, dict(MANUFACTURER))
        device_type = self.ensure(
            nb.dcim.device_types, f"device type {DEVICE_TYPE['slug']}",
            {"slug": DEVICE_TYPE["slug"]},
            {**DEVICE_TYPE, **_ref("manufacturer", manufacturer)})

        cluster_name = cluster_name or f"{slug}-pve"
        cluster = self.ensure(
            nb.virtualization.clusters, f"cluster {cluster_name}", {"name": cluster_name},
            {"name": cluster_name, "status": "active",
             **_ref("type", refs["cluster_type"]), **_ref("tenant", tenant)})
        # No tenant check on an existing cluster: a shared one belongs to no
        # single tenant.

        device_lookup = {"name": node, **({"site_id": site.id} if site is not None else {})}
        device = self.ensure(
            nb.dcim.devices, f"device {node}", device_lookup,
            {"name": node, "status": "active",
             **_ref("role", refs["node_role"]), **_ref("device_type", device_type),
             **_ref("site", site), **_ref("tenant", tenant), **_ref("cluster", cluster)},
            expect={"role": NODE_ROLE["slug"], "tenant": slug, "cluster": cluster_name,
                    "status": "active"})

        if node_ip:
            self.node_ip(device, node, node_ip, tenant)

    def node_ip(self, device: Any, node: str, address: str, tenant: Any) -> None:
        nb = self.nb
        iface = None
        if device is not None:
            iface = self.ensure(
                nb.dcim.interfaces, f"interface {node}/{NODE_INTERFACE['name']}",
                {"device_id": device.id, "name": NODE_INTERFACE["name"]},
                {**NODE_INTERFACE, "device": device.id})
        else:
            self.pending += 1
            self._say("create", f"interface {node}/{NODE_INTERFACE['name']}", "(dry-run)")

        ip = nb.ipam.ip_addresses.get(address=address) if nb is not None else None
        label = f"IP {address}"
        if ip is None:
            if not self.apply:
                self.pending += 1
                self._say("create", label, "(dry-run)")
            else:
                ip = nb.ipam.ip_addresses.create(
                    address=address, status="active",
                    assigned_object_type="dcim.interface", assigned_object_id=iface.id,
                    **_ref("tenant", tenant))
                self.created += 1
                self._say("created", label)
        else:
            if iface is None or getattr(ip, "assigned_object_id", None) != iface.id:
                self.warn(label, "exists but is not assigned to "
                          f"{node}/{NODE_INTERFACE['name']}; not touching it")
                return
            self._say("ok", label)

        if device is None or ip is None:
            if not self.apply or device is None:
                self._say("set", f"device {node} primary IPv4 -> {address}", "(dry-run)")
            return
        current = getattr(device, "primary_ip4", None)
        if current is None:
            device.primary_ip4 = ip.id
            device.save()
            self.created += 1
            self._say("set", f"device {node} primary IPv4 -> {address}")
        elif getattr(current, "id", None) != ip.id:
            self.warn(f"device {node}", f"primary IPv4 is {current}, not {address}; not touching it")
        else:
            self._say("ok", f"device {node} primary IPv4 {address}")


def _ref(key: str, obj: Any) -> dict:
    """{key: obj.id}, or {} while an object only exists in a dry-run."""
    return {key: obj.id} if obj is not None else {}


def _plain(value: Any) -> Any:
    """Reduce pynetbox records/choices to comparable plain values."""
    if isinstance(value, list):
        return [_plain(v) for v in value]
    for attr in ("slug", "value"):
        if hasattr(value, attr) and not isinstance(value, (str, int, bool)):
            return getattr(value, attr)
    if hasattr(value, "name") and not isinstance(value, (str, int, bool)):
        return value.name
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="netbox-seed.py",
        description="Create the NetBox objects the automation expects. "
                    "Dry-run unless --apply.",
        epilog="Env: NETBOX_URL, NETBOX_TOKEN.")
    p.add_argument("--apply", action="store_true", help="write to NetBox (default: dry-run)")
    p.add_argument("--site", metavar="SLUG",
                   help="also create tenant/site SLUG, the cluster and the node device")
    p.add_argument("--cluster", metavar="NAME",
                   help="NetBox cluster of the node (default: SLUG-pve)")
    p.add_argument("--node", metavar="NAME",
                   help="Proxmox node name (required with --site; must match the real node name)")
    p.add_argument("--node-ip", metavar="CIDR",
                   help="node management IPv4 with prefix, e.g. 192.168.10.5/24 (set as primary IP)")
    args = p.parse_args(argv)

    if args.site:
        if not SLUG_RE.match(args.site):
            p.error(f"--site {args.site!r}: use lowercase letters, digits and underscores "
                    "(it becomes an Ansible group name)")
        if not args.node:
            p.error("--site needs --node NAME (the Proxmox node's name)")
        if args.cluster is not None and (not args.cluster or " " in args.cluster):
            p.error("--cluster needs a name without spaces")
    elif args.node or args.node_ip or args.cluster:
        p.error("--node/--node-ip/--cluster only make sense with --site")
    if args.node_ip:
        try:
            iface = ipaddress.ip_interface(args.node_ip)
        except ValueError:
            p.error(f"--node-ip {args.node_ip!r} is not an address with prefix length")
        if iface.version != 4 or "/" not in args.node_ip:
            p.error("--node-ip must be IPv4 with prefix length, e.g. 192.168.10.5/24")
    return args


def connect() -> Any:
    url = os.environ.get("NETBOX_URL")
    token = os.environ.get("NETBOX_TOKEN")
    missing = [n for n, v in (("NETBOX_URL", url), ("NETBOX_TOKEN", token)) if not v]
    if missing:
        sys.exit(f"netbox-seed: set {' and '.join(missing)}")
    nb = pynetbox.api(url, token=token)
    if os.environ.get("NETBOX_VALIDATE_CERTS", "true").lower() in ("false", "0", "no"):
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        nb.http_session.verify = False
    try:
        version = nb.version
    except (pynetbox.RequestError, requests.RequestException) as exc:  # connection, TLS, auth
        sys.exit(f"netbox-seed: cannot reach NetBox at {url}: {exc}")
    major = int(str(version).split(".")[0] or 0)
    if major < 4:
        sys.exit(f"netbox-seed: NetBox {version} is too old; need >= 4.0")
    print(f"NetBox {version} at {url}")
    return nb


def main(argv: list[str] | None = None, nb: Any = None) -> int:
    args = parse_args(argv)
    nb = nb if nb is not None else connect()
    seeder = Seeder(nb, apply=args.apply)

    print("Mode:", "APPLY" if args.apply else "dry-run (use --apply to write)")
    refs = seeder.base()
    if args.site:
        print(f"Site {args.site}:")
        seeder.site(args.site, args.node, args.node_ip, refs, args.cluster)

    if args.apply:
        print(f"\nDone: {seeder.created} change(s), {seeder.warnings} warning(s).")
    else:
        print(f"\nDry-run: {seeder.pending} object(s) would be created, "
              f"{seeder.warnings} warning(s). Re-run with --apply to write.")
    return 1 if seeder.warnings else 0


if __name__ == "__main__":
    sys.exit(main())
