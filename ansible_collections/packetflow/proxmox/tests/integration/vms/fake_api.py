#!/usr/bin/env python3
"""Fake Proxmox VE + NetBox APIs for the vms role integration test.

Stateful enough for the role: clone creates a VM from the template, config
PUTs stick, resize/start/stop update the VM. Every Proxmox call is appended to
calls.jsonl so the test can assert exact request parameters.

  fake_api.py STATE_DIR   (serves Proxmox on :18006 over TLS, NetBox on :18000)

A second Proxmox ("hq", node hq1, state pve_hq.json) listens on :18007 for the
multi-cluster case; calls.jsonl records which one ("pve" / "hq") was called.
"""

import json
import pathlib
import ssl
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = pathlib.Path(sys.argv[1])
LOCK = threading.Lock()


def load(name):
    return json.loads((STATE / name).read_text())


def save(name, data):
    (STATE / name).write_text(json.dumps(data, indent=1, sort_keys=True))


def upid(node, kind, vmid):
    return f"UPID:{node}:00001234:00005678:66F00000:{kind}:{vmid}:root@pam:"


class Proxmox(BaseHTTPRequestHandler):
    NAME, NODE, STATE_FILE = "pve", "pve", "pve.json"

    def log_message(self, *args):
        pass

    def _params(self):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length).decode()
            if self.headers.get("Content-Type", "").startswith("application/json"):
                query.update(json.loads(body))
            else:
                query.update(dict(urllib.parse.parse_qsl(body, keep_blank_values=True)))
        return query

    def _reply(self, data, code=200):
        body = json.dumps({"data": data}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        path = urllib.parse.urlsplit(self.path).path.removeprefix("/api2/json").rstrip("/")
        params = self._params()
        if not self.headers.get("Authorization", "").startswith("PVEAPIToken="):
            return self._reply(None, 401)
        with LOCK:
            vms = {int(k): v for k, v in load(self.STATE_FILE).items()}
            with (STATE / "calls.jsonl").open("a") as log:
                log.write(json.dumps({"pve": self.NAME, "method": method, "path": path, "params": params}) + "\n")
            parts = path.strip("/").split("/")
            result = self._route(method, parts, params, vms)
            save(self.STATE_FILE, {str(k): v for k, v in vms.items()})
        if isinstance(result, tuple):
            return self._reply(result[1], result[0])
        return self._reply(result)

    def _route(self, method, p, params, vms):
        def resource(vmid, vm):
            return {"id": f"qemu/{vmid}", "vmid": vmid, "type": "qemu", "node": vm["node"], "name": vm["config"].get("name"),
                    "status": vm["status"], "template": vm["template"], "tags": vm["config"].get("tags", ""),
                    **({"pool": vm["pool"]} if vm.get("pool") else {})}

        if p == ["version"]:
            return {"version": "8.2.4", "release": "8.2", "repoid": "fake"}
        if p == ["cluster", "resources"]:
            return [resource(k, v) for k, v in sorted(vms.items())]
        if p == ["nodes"]:
            return [{"node": self.NODE, "status": "online", "type": "node"}]
        if len(p) >= 3 and p[0] == "nodes" and p[2] == "tasks":
            return {"status": "stopped", "exitstatus": "OK"} if p[-1] == "status" else []
        if p[:3] == ["nodes", self.NODE, "qemu"] and len(p) == 3:
            return [dict(resource(k, v), template=v["template"]) for k, v in sorted(vms.items()) if v["node"] == self.NODE]
        if p[:3] != ["nodes", self.NODE, "qemu"] or len(p) < 5:
            return (501, f"fake: unhandled {method} {'/'.join(p)}")
        vmid, rest = int(p[3]), p[4:]
        vm = vms.get(vmid)
        if vm is None:
            return (500, f"VM {vmid} does not exist")
        if rest == ["config"] and method == "GET":
            return dict(vm["config"], digest="fake")
        if rest == ["config"] and method in ("PUT", "POST"):
            for key in params.pop("delete", "").split(","):
                vm["config"].pop(key, None)
            for key, value in params.items():
                if key not in ("digest", "skiplock"):
                    vm["config"][key] = value
            return None
        if rest == ["clone"] and method == "POST":
            newid = int(params["newid"])
            if newid in vms:
                return (500, f"VM {newid} already exists")
            cfg = json.loads(json.dumps(vm["config"]))
            cfg["name"] = params["name"]
            cfg["scsi0"] = cfg["scsi0"].replace(f"base-{vmid}-disk-0", f"vm-{newid}-disk-0")
            cfg["net0"] = "virtio=BC:24:11:%02X:%02X:%02X,bridge=vmbr0" % (newid >> 16 & 255, newid >> 8 & 255, newid & 255)
            vms[newid] = {"node": params.get("target") or self.NODE, "status": "stopped", "template": 0, "config": cfg,
                          **({"pool": params["pool"]} if params.get("pool") else {})}
            return upid("pve", "qmclone", vmid)
        if rest == ["resize"] and method == "PUT":
            disk = vm["config"][params["disk"]]
            fields = [f for f in disk.split(",") if not f.startswith("size=")]
            vm["config"][params["disk"]] = ",".join([*fields, "size=" + params["size"]])
            return upid("pve", "resize", vmid)
        if rest == ["status", "current"]:
            return {"status": vm["status"], "vmid": vmid, "name": vm["config"].get("name")}
        if rest[:1] == ["status"] and method == "POST":
            vm["status"] = "running" if rest[1] == "start" else "stopped"
            return upid("pve", "qm" + rest[1], vmid)
        return (501, f"fake: unhandled {method} {'/'.join(p)}")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")


class ProxmoxHQ(Proxmox):
    NAME, NODE, STATE_FILE = "hq", "hq1", "pve_hq.json"


class NetBox(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        ids = {int(i) for i in urllib.parse.parse_qs(url.query).get("id", [])}
        if url.path.rstrip("/") == "/api/virtualization/interfaces":
            items = [i for i in load("netbox_interfaces.json") if query.get("cluster") in (None, i["cluster"])]
            body = {"count": len(items), "next": None, "previous": None, "results": items}
        elif url.path.rstrip("/").startswith("/api/ipam/vlans/"):  # nb_lookup uses .get() for a single id
            vid = int(url.path.rstrip("/").rsplit("/", 1)[1])
            body = next((v for v in load("netbox_vlans.json") if v["id"] == vid), {"detail": "Not found."})
        elif url.path.rstrip("/") == "/api/ipam/vlans":
            items = [v for v in load("netbox_vlans.json") if not ids or v["id"] in ids]
            body = {"count": len(items), "next": None, "previous": None, "results": items}
        elif url.path.rstrip("/") == "/api/virtualization/virtual-machines":
            vms = [vm for vm in load("netbox.json")
                   if query.get("tenant") in (None, vm["tenant"]["slug"])
                   and query.get("cluster") in (None, vm["cluster"]["name"])]
            body = {"count": len(vms), "next": None, "previous": None, "results": vms}
        elif url.path.rstrip("/") in ("/api", "/api/status"):
            body = {"netbox-version": "4.4.0"}
        elif url.path.startswith("/api/"):  # anything else (e.g. the inventory plugin): empty
            body = {"count": 0, "next": None, "previous": None, "results": []}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("API-Version", "4.4")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    pve = ThreadingHTTPServer(("127.0.0.1", 18006), Proxmox)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(STATE / "cert.pem", STATE / "key.pem")
    pve.socket = ctx.wrap_socket(pve.socket, server_side=True)
    hq = ThreadingHTTPServer(("127.0.0.1", 18007), ProxmoxHQ)
    hq.socket = ctx.wrap_socket(hq.socket, server_side=True)
    threading.Thread(target=hq.serve_forever, daemon=True).start()
    netbox = ThreadingHTTPServer(("127.0.0.1", 18000), NetBox)
    threading.Thread(target=netbox.serve_forever, daemon=True).start()
    pve.serve_forever()


if __name__ == "__main__":
    main()
