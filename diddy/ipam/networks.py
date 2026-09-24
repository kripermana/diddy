"""Logika IPAM: network, utilisasi, IP bebas, reverse zone otomatis."""

import ipaddress
import re

from ..audit import changed
from ..config import C
from ..core.errors import ApiError
from ..core.util import boolv, parse_ip, parse_net, run, valid_fqdn
from ..db.connection import q, x
from ..dhcp.leases import read_leases
from ..dns.zones import find_zone


def all_nets():
    rows = q("SELECT * FROM networks")
    for r in rows:
        r["_n"] = ipaddress.ip_network(r["cidr"])
    rows.sort(key=lambda r: (r["_n"].version, int(r["_n"].network_address), r["_n"].prefixlen))
    return rows


def smallest_net(ipstr, nets, dhcp_only=False):
    a = ipaddress.ip_address(ipstr)
    best = None
    for r in nets:
        if dhcp_only and not r["dhcp_enabled"]:
            continue
        if r["_n"].version == a.version and a in r["_n"]:
            if best is None or r["_n"].prefixlen > best["_n"].prefixlen:
                best = r
    return best


def usage_sets():
    hosts = {r["ip"]: r for r in q("SELECT * FROM hosts")}
    arecs = {}
    for r in q("SELECT r.name,r.value,z.name AS zone FROM records r JOIN zones z ON z.id=r.zone_id "
               "WHERE r.type IN ('A','AAAA')"):
        arecs.setdefault(r["value"], []).append(r["zone"] if r["name"] == "@" else f"{r['name']}.{r['zone']}")
    leases = {lease["ip"]: lease for lease in read_leases()}
    disc = {r["ip"]: r["last_seen"] for r in q("SELECT * FROM discovered")}
    return hosts, arecs, leases, disc


def net_ranges(nid):
    return [(int(ipaddress.ip_address(r["start_ip"])), int(ipaddress.ip_address(r["end_ip"])))
            for r in q("SELECT * FROM ranges WHERE network_id=?", (nid,))]


def usable(n):
    return n.num_addresses - (2 if n.version == 4 and n.prefixlen < 31 else 0)


def utilization(row, sets):
    n = row["_n"]
    used = set()
    for src in sets:
        for ipstr in src:
            try:
                if ipaddress.ip_address(ipstr) in n:
                    used.add(ipstr)
            except ValueError:
                pass
    if row["gateway"]:
        used.add(row["gateway"])
    total = usable(n)
    return len(used), total, (round(len(used) * 100 / total, 1) if total else 0)


def next_free(row, count=1):
    n = ipaddress.ip_network(row["cidr"])
    hosts, arecs, leases, disc = usage_sets()
    used = set(hosts) | set(arecs) | set(leases) | set(disc) | {row["gateway"]}
    rng = net_ranges(row["id"])
    out = []
    for a in n.hosts():
        s = str(a)
        if s in used or any(lo <= int(a) <= hi for lo, hi in rng):
            continue
        out.append(s)
        if len(out) >= count:
            break
    return out


def reverse_zone_name(n):
    if n.version == 4:
        k = max(1, min(3, n.prefixlen // 8))
        return ".".join(reversed(str(n.network_address).split(".")[:k])) + ".in-addr.arpa"
    k = max(1, min(32, n.prefixlen // 4))
    return ".".join(reversed(n.network_address.exploded.replace(":", "")[:k])) + ".ip6.arpa"


def validate_network(d, existing=None):
    n = ipaddress.ip_network(existing["cidr"]) if existing else parse_net(d.get("cidr"))
    out = {"cidr": str(n)}
    gw = (d.get("gateway") or "").strip()
    if gw:
        a = parse_ip(gw, "gateway")
        if a not in n or (n.version == 4 and n.prefixlen < 31 and a in (n.network_address, n.broadcast_address)):
            raise ApiError("Gateway must be a usable address inside the network")
        gw = str(a)
    out["gateway"] = gw
    dns = [str(parse_ip(v, "DNS server")) for v in re.split(r"[,\s]+", (d.get("dns_servers") or "").strip()) if v]
    out["dns_servers"] = ", ".join(dns)
    dn = (d.get("domain_name") or "").strip()
    out["domain_name"] = valid_fqdn(dn, "domain name") if dn else ""
    try:
        lt = int(d.get("lease_time") or 86400)
    except (TypeError, ValueError):
        raise ApiError("Lease time must be a number (seconds)")
    if lt < 60:
        raise ApiError("Lease time must be at least 60 seconds")
    out["lease_time"] = lt
    out["dhcp_enabled"] = boolv(d.get("dhcp_enabled"))
    out["ddns_enabled"] = boolv(d.get("ddns_enabled"))
    dd = (d.get("ddns_domain") or "").strip()
    out["ddns_domain"] = valid_fqdn(dd, "DDNS domain") if dd else ""
    if out["ddns_enabled"]:
        if not out["dhcp_enabled"]:
            raise ApiError("Turn on DHCP first: DDNS registers the clients that this server leases addresses to")
        dom = out["ddns_domain"] or out["domain_name"]
        if not dom:
            raise ApiError("Set a DHCP domain name (or a DDNS domain) so client names can be built")
        if not find_zone(dom):
            raise ApiError(f"No DNS zone covers '{dom}'. Create that zone first.")
    if out["dhcp_enabled"]:
        if n.version != 4:
            raise ApiError("DHCP is supported for IPv4 networks only")
        for r in all_nets():
            if r["dhcp_enabled"] and (not existing or r["id"] != existing["id"]) \
                    and r["_n"].version == 4 and r["_n"].overlaps(n):
                raise ApiError(f"DHCP-enabled networks cannot overlap: {r['cidr']} already has DHCP")
    for k in ("comment", "vlan", "site"):
        out[k] = str(d.get(k) or "").strip()[:200]
    return n, out


def ensure_reverse_zone(n):
    name = reverse_zone_name(n)
    if not q("SELECT 1 FROM zones WHERE name=?", (name,), one=True):
        x("INSERT INTO zones(name,ttl,primary_ns,admin_email,comment) VALUES(?,?,?,?,?)",
          (name, 3600, C["default_ns"], C["default_admin_email"], f"Reverse zone for {n}"))
        changed("create", f"zone {name}", "auto reverse zone")
    return name


def get_net(nid):
    r = q("SELECT * FROM networks WHERE id=?", (nid,), one=True)
    if not r:
        raise ApiError("Network not found", 404)
    r["_n"] = ipaddress.ip_network(r["cidr"])
    return r


def ping(ipstr):
    ok, _ = run(["ping", "-c", "1", "-W", "1", ipstr], timeout=4)
    return ok
