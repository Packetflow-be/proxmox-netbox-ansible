"""Assertions for run.sh: the plan per round and the exact Proxmox write calls."""

import json
import pathlib
import sys
from urllib.parse import unquote

WORK = pathlib.Path(sys.argv[1])
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPl+test/key test@laptop"


def result(name):
    return json.loads((WORK / f"{name}.json").read_text())


def writes(name, pve="pve"):
    """Write calls of one round to one Proxmox ("pve" = lab-pve, "hq" = hq-pve, None = both)."""
    calls = [json.loads(line) for line in (WORK / f"{name}.calls.jsonl").read_text().splitlines()]
    return [(c["method"], c["path"], c["params"]) for c in calls
            if c["method"] != "GET" and pve in (None, c["pve"])]


def check(cond, msg):
    if not cond:
        sys.exit(f"FAIL: {msg}")
    print(f"ok   {msg}")


# --- round 1: create -------------------------------------------------------------
r, w = result("apply1"), writes("apply1")
check(r["plan"]["summary"] == {"create": 2, "update": 0, "unchanged": 0, "orphans": 1, "errors": 0, "unmanaged": 1},
      f"apply1 plan summary {r['plan']['summary']}")
clones = {p["newid"]: p for m, path, p in w if path == "/nodes/pve/qemu/9000/clone"}
check(set(clones) == {"10001", "10002"}, "clones 10001 and 10002 from template 9000")
check(all(p["full"] == "1" and p["storage"] == "local-lvm" for p in clones.values()), "full clones to local-lvm")
check(all(p.get("pool") == "lab" for p in clones.values()), "clones go into the site's pool")
check(clones["10001"]["name"] == "lab-web01" and clones["10002"]["name"] == "lab-db01", "clone names from NetBox")

cfg = {path.split("/")[4]: p for m, path, p in w if path.endswith("/config") and "name" in p}
web, db = cfg["10001"], cfg["10002"]
check(web["ipconfig0"] == "ip=192.168.10.21/24,gw=192.168.10.1", "web ipconfig0 with default gateway")
check(db["ipconfig0"] == "ip=10.0.20.22/24,gw=10.0.20.1", "db ipconfig0 with VLAN 20 gateway")
check((web["cores"], web["memory"], db["cores"], db["memory"]) == ("2", "2048", "4", "8192"), "cores/memory from NetBox")
check(web["nameserver"] == "192.168.10.2 192.168.10.3" and web["searchdomain"] == "lab.example", "DNS")
check(unquote(web["sshkeys"]).strip() == KEY, "ssh key (urlencoded by the module)")
check(web["ciuser"] == "ansible" and web["tags"] == "packetflow", "ci user and managed tag")
check((web["protection"], web["onboot"], web["agent"]) == ("1", "1", "enabled=1"), "protection, onboot, agent")

nics = {path.split("/")[4]: p["net0"] for m, path, p in w if path.endswith("/config") and "net0" in p}
check("10001" not in nics, "web net0 already right after the clone: no NIC call")
check(nics.get("10002", "").endswith(",bridge=vmbr0,tag=20") and nics["10002"].startswith("virtio=BC:24:11:"),
      "db net0 gets tag 20 and keeps its MAC")

resizes = {path.split("/")[4]: p["size"] for m, path, p in w if path.endswith("/resize")}
check(resizes == {"10001": "20G", "10002": "40G"}, "disks grown to NetBox size")
starts = [path for m, path, p in w if path.endswith("/status/start")]
check(starts == ["/nodes/pve/qemu/10001/status/start"], "only the active VM is started (db is offline)")
check(not [path for m, path, p in w if "/qemu/100/" in path or "/qemu/10099/" in path or "/qemu/10077/" in path],
      "unmanaged VM 100, orphan 10099 and another pool's VM 10077 are not touched")
check("10077" not in r["failed"], "a tagged VM in another pool is not an orphan")
check("10099 old-app" in r["failed"] and "Nothing was deleted" in r["failed"], "orphan reported, run fails")

# --- round 1, second cluster (hq-pve on its own Proxmox, own token) ----------------
hq, wh = r["plans"][1], writes("apply1", "hq")
check(len(r["plans"]) == 2 and hq["summary"]["create"] == 1 and hq["summary"]["orphans"] == 0,
      f"hq plan: 1 to create {hq['summary']}")
check(not [c for c in w if "10006" in c[1]], "the hosted VM isn't created on the first cluster's Proxmox")
hq_clone = [p for m, path, p in wh if path == "/nodes/hq1/qemu/9000/clone"]
check(len(hq_clone) == 1 and hq_clone[0]["newid"] == "10006" and hq_clone[0]["name"] == "lab-rds01",
      "hosted VM cloned from hq's template on hq1")
check(hq_clone[0].get("pool") == "lab", "hosted VM in the site's pool")
hq_cfg = {path.split("/")[4]: p for m, path, p in wh if path.endswith("/config") and "name" in p}["10006"]
check(hq_cfg["ipconfig0"] == "ip=10.50.200.10/24,gw=10.50.200.1", "hosted VM: gateway of its VLAN (2042) on that cluster")
hq_nic = [p["net0"] for m, path, p in wh if path.endswith("/config") and "net0" in p]
check(len(hq_nic) == 1 and hq_nic[0].endswith(",bridge=lab,mtu=1") and "tag=" not in hq_nic[0],
      "hosted VM on VNet lab (from the VLAN's pve_vnet), no tag, MTU from the VNet")
check(any(path == "/nodes/hq1/qemu/10006/status/start" for m, path, p in wh), "hosted VM started")

# --- round 2: plan, nothing to do ------------------------------------------------
r = result("plan2")
check(writes("plan2", None) == [], "plan mode makes no write calls")
check(r["plan"]["summary"] == {"create": 0, "update": 0, "unchanged": 2, "orphans": 1, "errors": 0, "unmanaged": 1},
      "second run: both VMs unchanged")
check(r["failed"] == "", "plan mode doesn't fail on orphans")
check(r["plans"][1]["summary"]["unchanged"] == 1, "hosted VM unchanged on the second run")

# --- round 3: memory changed in NetBox -------------------------------------------
w = writes("apply3")
check(len(w) == 1 and w[0][1] == "/nodes/pve/qemu/10001/config" and w[0][2]["memory"] == "4096",
      "memory change: exactly one config call")
check(writes("apply3", "hq") == [], "nothing changes on the other cluster")
log = (WORK / "apply3.log").read_text()
check("NetBox lab/lab-pve: 0 to create, 1 to change, 1 unchanged, 1 orphaned, 0 errors" in log
      and "lab-web01: memory: 2048 -> 4096" in log, "plan output lists the change")

# --- round 4: converged -----------------------------------------------------------
check(writes("plan4", None) == [] and result("plan4")["plan"]["summary"]["unchanged"] == 2, "converged after the change")
print("all checks passed")
