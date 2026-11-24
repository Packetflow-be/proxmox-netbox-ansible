"""Unit tests for packetflow.zabbix.host_status (matching and planning)."""

import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[4] / "plugins" / "modules" / "host_status.py"
_spec = importlib.util.spec_from_file_location("host_status", _PATH)
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)

DIS = ["offline", "decommissioning", "inventory"]
ENA = ["active"]
TAG = "netbox_status"


def obj(name, status, ip=""):
    return {"name": name, "status": status, "ip": ip, "kind": "device"}


def host(hostid, name, status="0", ips=(), tags=()):
    return {"hostid": hostid, "host": name, "status": status, "ips": list(ips), "tags": list(tags)}


def run(objects, hosts, domain="internal.example"):
    pairs, warnings = hs.match(objects, hosts, domain)
    return hs.plan(pairs, DIS, ENA, TAG), warnings


def test_name_and_fqdn_match():
    pairs, _ = hs.match([obj("a01", "active"), obj("b01", "active")],
                        [host("1", "a01"), host("2", "B01.internal.example")], "internal.example")
    assert [(o["name"], h["hostid"]) for o, h in pairs] == [("a01", "1"), ("b01", "2")]


def test_ip_match_only_when_unique():
    objects = [obj("new", "decommissioning", "10.0.0.5"), obj("other", "active", "10.0.0.6"),
               obj("x", "active", "10.0.0.6")]
    hosts = [host("1", "legacy-name", ips=["10.0.0.5"]), host("2", "h6", ips=["10.0.0.6"])]
    pairs, _ = hs.match(objects, hosts, "")
    assert [(o["name"], h["hostid"]) for o, h in pairs] == [("new", "1")]


def test_name_beats_ip():
    objects = [obj("a01", "active", "10.0.0.1"), obj("b01", "offline", "10.0.0.9")]
    hosts = [host("1", "a01", ips=["10.0.0.9"]), host("2", "zz", ips=["10.0.0.9"])]
    pairs, _ = hs.match(objects, hosts, "")
    assert [(o["name"], h["hostid"]) for o, h in pairs] == [("a01", "1")]  # 10.0.0.9 is on two hosts


def test_host_matched_twice_is_skipped():
    objects = [obj("a01", "active", "10.0.0.1"), obj("b01", "offline", "10.0.0.1")]
    pairs, warnings = hs.match(objects, [host("1", "a01", ips=["10.0.0.1"])], "")
    assert len(pairs) == 1  # b01's IP is shared with a01 in NetBox: no IP match
    pairs, warnings = hs.match([obj("a01", "active"), obj("A01", "offline")], [host("1", "a01")], "")
    assert pairs == [] and warnings


def test_disable_keeps_other_tags():
    updates, _ = run([obj("a01", "decommissioning")], [host("1", "a01", tags=[{"tag": "site", "value": "x"}])])
    assert updates == [("1", {"status": "1", "tags": [{"tag": "site", "value": "x"},
                                                      {"tag": TAG, "value": "decommissioning"}]},
                        "disable a01 (NetBox a01, decommissioning)")]


def test_disabled_by_hand_is_left_alone():
    assert run([obj("a01", "offline")], [host("1", "a01", status="1")])[0] == []
    assert run([obj("a01", "active")], [host("1", "a01", status="1")])[0] == []


def test_enable_only_what_we_disabled():
    updates, _ = run([obj("a01", "active")],
                     [host("1", "a01", status="1", tags=[{"tag": TAG, "value": "offline"}, {"tag": "s", "value": ""}])])
    assert updates == [("1", {"tags": [{"tag": "s", "value": ""}], "status": "0"}, "enable a01 (NetBox a01, active)")]


def test_retag_and_idempotent():
    hosts = [host("1", "a01", status="1", tags=[{"tag": TAG, "value": "offline"}])]
    assert run([obj("a01", "decommissioning")], hosts)[0][0][1] == {"tags": [{"tag": TAG, "value": "decommissioning"}]}
    assert run([obj("a01", "offline")], hosts)[0] == []


def test_other_statuses_do_nothing():
    for status in ("planned", "staged", "failed"):
        assert run([obj("a01", status)], [host("1", "a01"), host("2", "b", status="1")])[0] == []
