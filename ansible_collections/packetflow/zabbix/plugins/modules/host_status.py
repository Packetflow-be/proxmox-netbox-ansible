#!/usr/bin/python
# -*- coding: utf-8 -*-
# Apache License 2.0 (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: host_status
short_description: Disable Zabbix hosts whose NetBox device or VM is out of service
description:
  - Reads the devices and virtual machines of NetBox (narrowed by
    C(netbox_filter)) and the hosts of Zabbix, and brings each matched Zabbix
    host's status in line with the NetBox status.
  - A Zabbix host matches a NetBox object by technical name (the NetBox name,
    or the name followed by C(.domain)). Without a name match, by primary IPv4
    address, but only when exactly one NetBox object and exactly one Zabbix
    host have that address.
  - A NetBox status in C(disable_statuses) disables the host and tags it
    C(tag)=<status>. A status in C(enable_statuses) enables a host again, but
    only one that carries C(tag) (disabled by this module); a host disabled
    by hand stays disabled.
  - Zabbix hosts without a NetBox match are left alone.
  - Supports check mode (C(changes) lists what would happen).
version_added: "0.4.0"
author: Packetflow (@Packetflow-be)
options:
  zabbix_url:
    description: Zabbix frontend URL (with or without C(/api_jsonrpc.php)).
    type: str
    required: true
  zabbix_token:
    description: Zabbix API token with write access to the hosts.
    type: str
    required: true
  zabbix_validate_certs:
    type: bool
    default: true
  netbox_url:
    type: str
    required: true
  netbox_token:
    description: NetBox token (read-only is enough).
    type: str
    required: true
  netbox_validate_certs:
    type: bool
    default: true
  netbox_filter:
    description: Query parameters for both NetBox lists, e.g. C({tenant: acme}).
    type: dict
    default: {}
  domain:
    description: DNS domain that Zabbix host names may carry after the NetBox name.
    type: str
    default: ""
  disable_statuses:
    description: NetBox statuses that disable the Zabbix host.
    type: list
    elements: str
    default: [offline, decommissioning, inventory]
  enable_statuses:
    description: NetBox statuses that enable a host this module disabled.
    type: list
    elements: str
    default: [active]
  tag:
    description: Zabbix host tag that marks hosts this module disabled.
    type: str
    default: netbox_status
"""

EXAMPLES = r"""
- name: Zabbix host status from NetBox
  packetflow.zabbix.host_status:
    zabbix_url: "{{ lookup('env', 'ZABBIX_URL') }}"
    zabbix_token: "{{ lookup('env', 'ZABBIX_API_TOKEN') }}"
    netbox_url: "{{ lookup('env', 'NETBOX_URL') }}"
    netbox_token: "{{ lookup('env', 'NETBOX_TOKEN') }}"
    netbox_filter: {tenant: acme}
    domain: internal.example.com
  no_log: true
"""

RETURN = r"""
changes:
  description: What was (or, in check mode, would be) changed.
  type: list
  elements: str
  returned: always
"""

import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url
from ansible.module_utils.six.moves.urllib.parse import urlencode

DISABLED, ENABLED = "1", "0"


class ApiError(Exception):
    pass


def netbox_objects(url, token, validate_certs, query):
    """Devices and VMs as [{name, status, ip, kind}]."""
    out = []
    for kind, path in (("device", "dcim/devices"), ("vm", "virtualization/virtual-machines")):
        params = dict(query, limit=200)
        nxt = "%s/api/%s/?%s" % (url.rstrip("/"), path, urlencode(params))
        while nxt:
            resp = open_url(nxt, headers={"Authorization": "Token " + token, "Accept": "application/json"},
                            validate_certs=validate_certs, timeout=30)
            page = json.loads(resp.read())
            for o in page["results"]:
                ip = (o.get("primary_ip4") or {}).get("address") or ""
                out.append({"name": o["name"] or "", "status": o["status"]["value"],
                            "ip": ip.split("/")[0], "kind": kind})
            nxt = page.get("next")
    return out


class Zabbix:
    def __init__(self, url, token, validate_certs):
        url = url.rstrip("/")
        self.url = url if url.endswith("api_jsonrpc.php") else url + "/api_jsonrpc.php"
        self.token, self.validate_certs = token, validate_certs

    def call(self, method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        resp = open_url(self.url, data=body, method="POST", timeout=30, validate_certs=self.validate_certs,
                        headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        answer = json.loads(resp.read())
        if "error" in answer:
            raise ApiError("%s: %s" % (method, answer["error"].get("data") or answer["error"]))
        return answer["result"]

    def hosts(self):
        return [{"hostid": h["hostid"], "host": h["host"], "status": h["status"],
                 "ips": sorted({i["ip"] for i in h["interfaces"] if i.get("ip")}),
                 # automatic tags come from discovery and can't be set
                 "tags": [{"tag": t["tag"], "value": t["value"]} for t in h["tags"]
                          if str(t.get("automatic", "0")) == "0"]}
                for h in self.call("host.get", {"output": ["hostid", "host", "status"],
                                                "selectInterfaces": ["ip"], "selectTags": "extend"})]


def match(objects, hosts, domain):
    """[(object, host)] and warnings. Names first, then unique IPs."""
    by_name = {h["host"].lower(): h for h in hosts}
    hosts_by_ip, objects_by_ip = {}, {}
    for h in hosts:
        for ip in h["ips"]:
            hosts_by_ip.setdefault(ip, []).append(h)
    for o in objects:
        if o["ip"]:
            objects_by_ip.setdefault(o["ip"], []).append(o)

    pairs, taken, warnings = [], {}, []
    for o in objects:
        names = [o["name"].lower()] + (["%s.%s" % (o["name"].lower(), domain.lower())] if domain else [])
        h = next((by_name[n] for n in names if n in by_name), None)
        if h is None and o["ip"] and len(objects_by_ip[o["ip"]]) == 1 and len(hosts_by_ip.get(o["ip"], [])) == 1:
            h = hosts_by_ip[o["ip"]][0]
        if h is None:
            continue
        if h["hostid"] in taken:
            warnings.append("Zabbix host %s matches both %s and %s: skipped" % (h["host"], taken[h["hostid"]], o["name"]))
            pairs = [p for p in pairs if p[1]["hostid"] != h["hostid"]]
            continue
        taken[h["hostid"]] = o["name"]
        pairs.append((o, h))
    return pairs, warnings


def plan(pairs, disable_statuses, enable_statuses, tag):
    """[(hostid, params for host.update, description)]."""
    updates = []
    for o, h in pairs:
        ours = [t for t in h["tags"] if t["tag"] == tag]
        others = [t for t in h["tags"] if t["tag"] != tag]
        label = "%s (NetBox %s, %s)" % (h["host"], o["name"], o["status"])
        if o["status"] in disable_statuses:
            if h["status"] == ENABLED:
                updates.append((h["hostid"], {"status": DISABLED, "tags": others + [{"tag": tag, "value": o["status"]}]},
                                "disable " + label))
            elif ours and ours != [{"tag": tag, "value": o["status"]}]:
                updates.append((h["hostid"], {"tags": others + [{"tag": tag, "value": o["status"]}]},
                                "retag " + label))
        elif o["status"] in enable_statuses and ours:
            params = {"tags": others}
            if h["status"] == DISABLED:
                params["status"] = ENABLED
            updates.append((h["hostid"], params, ("enable " if "status" in params else "untag ") + label))
    return updates


def main():
    module = AnsibleModule(
        argument_spec=dict(
            zabbix_url=dict(type="str", required=True),
            zabbix_token=dict(type="str", required=True, no_log=True),
            zabbix_validate_certs=dict(type="bool", default=True),
            netbox_url=dict(type="str", required=True),
            netbox_token=dict(type="str", required=True, no_log=True),
            netbox_validate_certs=dict(type="bool", default=True),
            netbox_filter=dict(type="dict", default={}),
            domain=dict(type="str", default=""),
            disable_statuses=dict(type="list", elements="str", default=["offline", "decommissioning", "inventory"]),
            enable_statuses=dict(type="list", elements="str", default=["active"]),
            tag=dict(type="str", default="netbox_status"),
        ),
        supports_check_mode=True,
    )
    p = module.params
    overlap = set(p["disable_statuses"]) & set(p["enable_statuses"])
    if overlap:
        module.fail_json(msg="statuses both disable and enable: %s" % ", ".join(sorted(overlap)), changes=[])
    try:
        objects = netbox_objects(p["netbox_url"], p["netbox_token"], p["netbox_validate_certs"], p["netbox_filter"])
        zabbix = Zabbix(p["zabbix_url"], p["zabbix_token"], p["zabbix_validate_certs"])
        pairs, warnings = match(objects, zabbix.hosts(), p["domain"])
        updates = plan(pairs, p["disable_statuses"], p["enable_statuses"], p["tag"])
        for hostid, params, _ in updates:
            if not module.check_mode:
                zabbix.call("host.update", dict(params, hostid=hostid))
    except Exception as e:  # API or HTTP error: report it without the arguments
        module.fail_json(msg="%s: %s" % (type(e).__name__, e), changes=[])
    for w in warnings:
        module.warn(w)
    module.exit_json(changed=bool(updates), changes=[d for _, _, d in updates])


if __name__ == "__main__":
    main()
