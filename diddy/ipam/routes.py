"""Endpoint REST IPAM."""

import shutil

from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, jsonify, request

from ..audit import audit, changed
from ..auth import auth
from ..core.errors import ApiError
from ..core.util import body, boolv, clean, now
from ..db.connection import db, q, sql, x
from .networks import (all_nets, ensure_reverse_zone, get_net, net_ranges, next_free, ping, reverse_zone_name,
                       usage_sets, utilization, validate_network)

bp = Blueprint("ipam_routes", __name__)


@bp.route("/api/v1/networks", methods=["GET", "POST"])
@auth
def api_networks():
    if request.method == "POST":
        d = body()
        n, o = validate_network(d)
        if q("SELECT 1 FROM networks WHERE cidr=?", (o["cidr"],), one=True):
            raise ApiError(f"Network {o['cidr']} already exists", 409)
        nid = x("INSERT INTO networks(cidr,comment,gateway,dhcp_enabled,dns_servers,domain_name,lease_time,vlan,site,"
                "ddns_enabled,ddns_domain) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (o["cidr"], o["comment"], o["gateway"], o["dhcp_enabled"], o["dns_servers"], o["domain_name"],
                 o["lease_time"], o["vlan"], o["site"], o["ddns_enabled"], o["ddns_domain"]))
        changed("create", f"network {o['cidr']}", o)
        if boolv(d.get("auto_reverse")):
            ensure_reverse_zone(n)
        return jsonify(id=nid, **o), 201
    nets = all_nets()
    sets = usage_sets()
    stack, out = [], []
    for r in nets:
        while stack and not (r["_n"].version == stack[-1]["_n"].version and r["_n"].subnet_of(stack[-1]["_n"])):
            stack.pop()
        used, total, pct = utilization(r, sets)
        item = clean(r)
        item.update(depth=len(stack), parent_id=stack[-1]["id"] if stack else None,
                    used=used, total=total, utilization=pct)
        out.append(item)
        stack.append(r)
    return jsonify(out)


@bp.route("/api/v1/networks/<int:nid>", methods=["GET", "PUT", "DELETE"])
@auth
def api_network(nid):
    r = get_net(nid)
    if request.method == "DELETE":
        x("DELETE FROM networks WHERE id=?", (nid,))
        changed("delete", f"network {r['cidr']}")
        return jsonify(ok=True)
    if request.method == "PUT":
        _, o = validate_network(body(), existing=r)
        x("UPDATE networks SET comment=?,gateway=?,dhcp_enabled=?,dns_servers=?,domain_name=?,lease_time=?,vlan=?,"
          "site=?,ddns_enabled=?,ddns_domain=? WHERE id=?",
          (o["comment"], o["gateway"], o["dhcp_enabled"], o["dns_servers"], o["domain_name"], o["lease_time"],
           o["vlan"], o["site"], o["ddns_enabled"], o["ddns_domain"], nid))
        changed("update", f"network {r['cidr']}", o)
        r = get_net(nid)
    used, total, pct = utilization(r, usage_sets())
    out = clean(r)
    out.update(used=used, total=total, utilization=pct, reverse_zone=reverse_zone_name(r["_n"]))
    return jsonify(out)


@bp.get("/api/v1/networks/<int:nid>/ipmap")
@auth
def api_ipmap(nid):
    row = get_net(nid)
    n = row["_n"]
    if n.version != 4 or n.num_addresses > 4096:
        raise ApiError("IP map is available for IPv4 networks of /20 or smaller")
    hosts, arecs, leases, disc = usage_sets()
    rng = net_ranges(nid)
    cells, counts = [], {}
    for a in n:
        s, ai = str(a), int(a)
        inr = any(lo <= ai <= hi for lo, hi in rng)
        c = {"ip": s, "status": "free", "in_range": inr, "alive": s in disc}
        h, lease = hosts.get(s), leases.get(s)
        if n.prefixlen < 31 and a == n.network_address:
            c["status"] = "network"
        elif n.prefixlen < 31 and a == n.broadcast_address:
            c["status"] = "broadcast"
        elif s == row["gateway"]:
            c["status"] = "gateway"
        elif h:
            c.update(status="host", name=h["fqdn"], mac=h["mac"], host_id=h["id"], dhcp=bool(h["configure_dhcp"]))
        elif lease:
            c.update(status="lease", name=lease["hostname"], mac=lease["mac"], expires=lease["expires"])
        elif s in arecs:
            c.update(status="dns", name=", ".join(arecs[s]))
        elif s in disc:
            c.update(status="unmanaged", last_seen=disc[s])
        elif inr:
            c["status"] = "range"
        if s in arecs and "name" not in c:
            c["name"] = ", ".join(arecs[s])
        conf = []
        if h and inr and h["configure_dhcp"]:
            conf.append("Fixed address inside a DHCP range")
        if h and lease and h["mac"] and lease["mac"] and h["mac"] != lease["mac"]:
            conf.append(f"Leased to {lease['mac']} but host MAC is {h['mac']}")
        if lease and not inr and not h:
            conf.append("Active lease outside any DHCP range")
        if s == row["gateway"] and (h or lease):
            conf.append("Gateway address is also assigned")
        if conf:
            c["conflict"] = "; ".join(conf)
        counts[c["status"]] = counts.get(c["status"], 0) + 1
        cells.append(c)
    return jsonify(cidr=row["cidr"], cells=cells, counts=counts,
                   conflicts=sum(1 for c in cells if "conflict" in c))


@bp.get("/api/v1/networks/<int:nid>/next_available")
@auth
def api_next_available(nid):
    cnt = max(1, min(256, request.args.get("num", 1, type=int)))
    return jsonify(ips=next_free(get_net(nid), cnt))


@bp.post("/api/v1/networks/<int:nid>/discover")
@auth
def api_discover(nid):
    row = get_net(nid)
    n = row["_n"]
    if n.version != 4 or n.num_addresses > 1024:
        raise ApiError("Discovery supports IPv4 networks of /22 or smaller")
    if not shutil.which("ping"):
        raise ApiError("'ping' is not installed on this server")
    ips = [str(a) for a in (n.hosts() if n.prefixlen < 31 else n)]
    with ThreadPoolExecutor(max_workers=64) as ex:
        alive = [ip for ip, ok in zip(ips, ex.map(ping, ips)) if ok]
    cur = db().cursor()
    cur.executemany(sql("DELETE FROM discovered WHERE ip=?"), [(ip,) for ip in ips])
    ts = now()
    cur.executemany(sql("INSERT OR REPLACE INTO discovered(ip,last_seen) VALUES(?,?)"), [(ip, ts) for ip in alive])
    db().commit()
    cur.close()
    audit("discover", f"network {row['cidr']}", f"{len(alive)} alive of {len(ips)}")
    return jsonify(scanned=len(ips), alive=alive)
