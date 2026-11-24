"""Unit tests for scripts/netbox-seed.py against an in-memory fake of pynetbox."""

import importlib.util
import io
import pathlib
import sys
import types

import pytest

# pynetbox is only needed for real runs; the tests inject a fake API.
sys.modules.setdefault("pynetbox", types.ModuleType("pynetbox"))
sys.modules.setdefault("requests", types.ModuleType("requests"))

_spec = importlib.util.spec_from_file_location(
    "netbox_seed", pathlib.Path(__file__).resolve().parents[1] / "netbox-seed.py")
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)


class Record(types.SimpleNamespace):
    def save(self):
        return True


class Endpoint:
    def __init__(self, api, name):
        self.api, self.name, self.items = api, name, []

    def get(self, **kw):
        hits = [r for r in self.items if all(getattr(r, k, None) == v for k, v in kw.items())]
        if len(hits) > 1:
            raise ValueError("get() returned more than one result")
        return hits[0] if hits else None

    def create(self, **kw):
        self.api.writes.append((self.name, kw))
        # Like NetBox, return foreign keys as nested records (id, slug/name).
        nested = {k: self.api.lookup(k, v) for k, v in kw.items() if k in FK and isinstance(v, int)}
        rec = Record(id=len(self.items) + 1, **{**kw, **nested})
        for key, value in nested.items():  # server-side filters like site_id=
            setattr(rec, f"{key}_id", value.id)
        self.items.append(rec)
        return rec


FK = {"tenant": ("tenancy", "tenants"), "site": ("dcim", "sites"),
      "role": ("dcim", "device_roles"), "cluster": ("virtualization", "clusters"),
      "type": ("virtualization", "cluster_types"), "device_type": ("dcim", "device_types"),
      "manufacturer": ("dcim", "manufacturers"), "device": ("dcim", "devices")}


class FakeAPI:
    def lookup(self, key, obj_id):
        app, ep = FK[key]
        return next(r for r in getattr(getattr(self, app), ep).items if r.id == obj_id)

    def __init__(self):
        self.writes = []
        mk = lambda *names: types.SimpleNamespace(**{n: Endpoint(self, n) for n in names})
        self.virtualization = mk("cluster_types", "clusters")
        self.dcim = mk("device_roles", "sites", "manufacturers", "device_types", "devices", "interfaces")
        self.extras = mk("custom_fields")
        self.tenancy = mk("tenants")
        self.ipam = mk("ip_addresses")


def run(argv, nb):
    out = io.StringIO()
    old, sys.stdout = sys.stdout, out
    try:
        rc = seed.main(argv, nb=nb)
    finally:
        sys.stdout = old
    return rc, out.getvalue()


def test_dry_run_writes_nothing():
    nb = FakeAPI()
    rc, out = run(["--site", "lab", "--node", "pve", "--node-ip", "192.168.10.5/24"], nb)
    assert rc == 0
    assert nb.writes == []
    assert "would be created" in out
    for thing in ("cluster type proxmox", "device role proxmox_node", "VM role generic",
                  "VM role webserver", "VM role monitoring", "custom field vlan_id",
                  "tenant lab", "cluster lab-pve", "device pve", "IP 192.168.10.5/24"):
        assert thing in out


def test_apply_creates_everything_with_expected_fields():
    nb = FakeAPI()
    rc, _ = run(["--apply", "--site", "lab", "--node", "pve", "--node-ip", "192.168.10.5/24"], nb)
    assert rc == 0
    created = {name: [kw for n, kw in nb.writes if n == name] for name, _ in nb.writes}

    assert created["cluster_types"] == [{"slug": "proxmox", "name": "Proxmox VE"}]
    roles = {r["slug"]: r for r in created["device_roles"]}
    assert set(roles) == {"proxmox_node", "generic", "webserver", "monitoring"}
    assert roles["proxmox_node"]["vm_role"] is False
    assert all(roles[s]["vm_role"] for s in ("generic", "webserver", "monitoring"))
    assert all("-" not in s for s in roles)  # slugs become Ansible group names

    (cf,) = created["custom_fields"]
    assert cf["name"] == "vlan_id" and cf["type"] == "integer" and cf["required"] is False
    assert cf["object_types"] == ["virtualization.virtualmachine"]

    (cluster,) = created["clusters"]
    assert cluster["name"] == "lab-pve" and cluster["type"] == nb.virtualization.cluster_types.items[0].id
    (device,) = created["devices"]
    assert device["name"] == "pve" and device["status"] == "active"
    assert device["cluster"] == nb.virtualization.clusters.items[0].id
    assert device["tenant"] == nb.tenancy.tenants.items[0].id

    (ip,) = created["ip_addresses"]
    assert ip["address"] == "192.168.10.5/24"
    assert ip["assigned_object_type"] == "dcim.interface"
    assert nb.dcim.devices.items[0].primary_ip4 == nb.ipam.ip_addresses.items[0].id


def test_apply_is_idempotent():
    nb = FakeAPI()
    argv = ["--apply", "--site", "lab", "--node", "pve", "--node-ip", "192.168.10.5/24"]
    run(argv, nb)
    first = len(nb.writes)
    # second run: objects now exist (fake primary_ip4 is an id, mimic a record)
    dev = nb.dcim.devices.items[0]
    dev.primary_ip4 = types.SimpleNamespace(id=dev.primary_ip4)
    nb.ipam.ip_addresses.items[0].assigned_object_id = nb.dcim.interfaces.items[0].id
    rc, out = run(argv, nb)
    assert rc == 0, out
    assert len(nb.writes) == first
    assert "created" not in out


def test_existing_node_keeps_its_site_and_cluster_comes_from_the_argument():
    nb = FakeAPI()
    run(["--apply"], nb)  # base objects
    hq = nb.dcim.sites.create(name="ACME - HQ", slug="acme-hq")
    nb.tenancy.tenants.create(name="acme", slug="acme")
    nb.virtualization.clusters.create(name="shared-pve", tenant="acme", status="active")
    nb.dcim.devices.create(name="pve01", site=hq.id, tenant="acme", cluster="shared-pve",
                           role="proxmox_node", status="active")
    nb.writes.clear()
    rc, out = run(["--site", "acme", "--node", "pve01", "--cluster", "shared-pve"], nb)
    assert "create   site" not in out and "create   cluster" not in out, out
    assert "site acme-hq" in out and "(node pve01's site)" in out
    assert rc == 0 and nb.writes == []


def test_new_cluster_gets_the_given_name():
    nb = FakeAPI()
    run(["--apply", "--site", "lab", "--node", "pve", "--cluster", "lab-cluster"], nb)
    assert [c.name for c in nb.virtualization.clusters.items] == ["lab-cluster"]


def test_existing_role_without_vm_role_warns_and_is_not_changed():
    nb = FakeAPI()
    nb.dcim.device_roles.items.append(Record(id=99, slug="generic", name="Generic", vm_role=False))
    rc, out = run(["--apply"], nb)
    assert rc == 1
    assert "WARN" in out and "vm_role" in out
    assert not any(n == "device_roles" and kw["slug"] == "generic" for n, kw in nb.writes)


@pytest.mark.parametrize("argv, msg", [
    (["--site", "lab-1", "--node", "pve"], "underscores"),
    (["--site", "lab"], "--node"),
    (["--node", "pve"], "only make sense"),
    (["--site", "lab", "--node", "pve", "--cluster", "a b"], "without spaces"),
    (["--cluster", "x"], "only make sense"),
    (["--site", "lab", "--node", "pve", "--node-ip", "192.168.10.5"], "prefix"),
    (["--site", "lab", "--node", "pve", "--node-ip", "fd00::5/64"], "IPv4"),
])
def test_argument_validation(argv, msg, capsys):
    with pytest.raises(SystemExit):
        seed.parse_args(argv)
    assert msg in capsys.readouterr().err
