# packetflow.services.powerdns

Internal DNS on one host, with the zone content in NetBox DNS
([netbox-plugin-dns](https://github.com/peteeckel/netbox-plugin-dns), installed
in your NetBox). The plugin can create A and PTR records from the DNS names of
IP addresses, so a VM added in NetBox gets its records without extra work:

| Part | What |
|---|---|
| `pdns-recursor` on `:53` | what clients ask. `powerdns_zones` go to the local authoritative server, the rest to `powerdns_recursor_forwarders` (or the root). DNSSEC validation, negative trust anchors for the local zones |
| `pdns` (authoritative) | `127.0.0.1:5300`, SQLite, API on `127.0.0.1:8081`. Nothing else talks to it |
| `octodns-sync.timer` | every 5 minutes: octoDNS reads `powerdns_zones` from NetBox DNS and writes the difference to the authoritative server, then wipes those zones from the recursor's cache |

NetBox down: the sync fails (journal), the last copy keeps being served.
octoDNS refuses to update or delete more than 30 % of a zone with 10+ records
in one run; look at `sudo -u octodns /opt/octodns/bin/octodns-sync --config-file
/etc/octodns/config.yaml` (no `--doit` = dry run), then add `--doit --force`
yourself. SOA records are PowerDNS's own (`powerdns_default_soa_content`, serial
raised on every change); NetBox DNS's SOA isn't copied.

Tested on Ubuntu 26.04 (pdns 5.0, recursor 5.3: `recursor.yml`, YAML settings).

## Variables

| Variable | Default | |
|---|---|---|
| `powerdns_zones` | — | **required**: zone names, each must exist (active) in NetBox DNS |
| `powerdns_netbox_url` | — | **required** |
| `powerdns_netbox_token` | — | **required**, encrypted: a token that can read `netbox_dns` objects |
| `powerdns_api_key` | — | **required**, encrypted, 32+ chars (`openssl rand -hex 32`) |
| `powerdns_netbox_validate_certs` | `true` | |
| `powerdns_recursor_listen` | default IPv4 (+ IPv6), `127.0.0.1` | not `0.0.0.0`: systemd-resolved holds `127.0.0.53:53` |
| `powerdns_recursor_allow_from` | RFC 1918, ULA, loopback | |
| `powerdns_recursor_forwarders` | `[]` | `[]` = resolve from the root |
| `powerdns_recursor_dnssec` | `validate` | |
| `powerdns_sync_schedule` | `*:0/5` | systemd `OnCalendar` |
| `powerdns_octodns_packages` | octodns 1.22, octodns-netbox-dns 0.3.19, octodns-powerdns 1.2 | pinned |

## Example

```yaml
- name: DNS
  hosts: powerdns        # NetBox tag `powerdns`
  become: true
  roles:
    - packetflow.services.powerdns
```

```yaml
# host_vars/<host>/main.yml
powerdns_zones: [internal.example.com, 30.0.10.in-addr.arpa]
powerdns_recursor_forwarders: [9.9.9.9, 149.112.112.112]
powerdns_netbox_url: https://netbox.internal.example.com
```

## Adding a zone

Create it in NetBox DNS (nameserver, status active), add it to
`powerdns_zones`, run the role: the recursor learns the zone and the sync
creates it.

## Checks

```bash
systemctl status octodns-sync.timer; journalctl -u octodns-sync -n 30
sudo pdnsutil list-all-zones
dig @127.0.0.1 -p 5300 <zone> SOA      # authoritative
dig @<host> <name>                     # through the recursor
```
