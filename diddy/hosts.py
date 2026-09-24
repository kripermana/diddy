"""Host object: A/AAAA + PTR + reservasi DHCP dalam satu objek."""

import csv
import io
import ipaddress
import re

from flask import Blueprint, jsonify, request

from .audit import changed
from .auth import auth
from .core.errors import ApiError
from .core.util import body, boolv, normalize_mac, parse_ip, parse_net, valid_fqdn
from .db.connection import q, x
from .dns.zones import find_zone, rel
from .ipam.networks import all_nets, next_free, smallest_net

bp = Blueprint("hosts", __name__)


def resolve_ip(v):
    v = (v or "").strip()
    m = re.match(r"^(?:next:|func:nextavailableip:)(.+)$", v, re.I)
    if m:
        t = m.group(1).strip()
        row = q("SELECT * FROM networks WHERE id=?", (int(t),), one=True) if t.isdigit() else \
            q("SELECT * FROM networks WHERE cidr=?", (str(parse_net(t)),), one=True)
        if not row:
            raise ApiError(f"Network '{t}' not found")
        free = next_free(row, 1)
        if not free:
            raise ApiError(f"No free IP left in {row['cidr']}")
        return free[0]
    return str(parse_ip(v))


def validate_host(d, hid=None):
    f = valid_fqdn(d.get("fqdn"), "FQDN")
    ipv = resolve_ip(d.get("ip"))
    a = ipaddress.ip_address(ipv)
    dup = q("SELECT id,fqdn FROM hosts WHERE ip=?", (ipv,), one=True)
    if dup and dup["id"] != hid:
        raise ApiError(f"{ipv} is already assigned to {dup['fqdn']}", 409)
    cdns, cdhcp = boolv(d.get("configure_dns", 1)), boolv(d.get("configure_dhcp", 0))
    macv = normalize_mac(d.get("mac")) if (d.get("mac") or "").strip() else ""
    if cdns:
        z = find_zone(f)
        if not z:
            raise ApiError(f"No DNS zone found for {f}. Create the zone first, or turn off 'Configure DNS'.")
        if q("SELECT 1 FROM records WHERE zone_id=? AND name=? AND type='CNAME'",
             (z["id"], rel(f, z["name"])), one=True):
            raise ApiError(f"{f} already exists as a CNAME record")
    if cdhcp:
        if a.version != 4:
            raise ApiError("DHCP reservations are IPv4 only")
        if not macv:
            raise ApiError("MAC address is required for a DHCP reservation")
        net = smallest_net(ipv, all_nets(), dhcp_only=True)
        if not net:
            raise ApiError(f"{ipv} is not inside a DHCP-enabled network")
        if net["_n"].prefixlen < 31 and a in (net["_n"].network_address, net["_n"].broadcast_address):
            raise ApiError("Cannot reserve the network or broadcast address")
        for h in q("SELECT * FROM hosts WHERE configure_dhcp=1 AND mac=? AND id<>?", (macv, hid or 0)):
            if ipaddress.ip_address(h["ip"]) in net["_n"]:
                raise ApiError(f"MAC {macv} is already reserved in {net['cidr']} by {h['fqdn']}")
    return {"fqdn": f, "ip": ipv, "mac": macv, "configure_dns": cdns, "configure_dhcp": cdhcp,
            "comment": str(d.get("comment") or "")[:200]}


def create_host(d):
    o = validate_host(d)
    hid = x("INSERT INTO hosts(fqdn,ip,mac,configure_dns,configure_dhcp,comment) VALUES(?,?,?,?,?,?)",
            (o["fqdn"], o["ip"], o["mac"], o["configure_dns"], o["configure_dhcp"], o["comment"]))
    changed("create", f"host {o['fqdn']}", o)
    return dict(id=hid, **o)


@bp.route("/api/v1/hosts", methods=["GET", "POST"])
@auth
def api_hosts():
    if request.method == "POST":
        return jsonify(create_host(body())), 201
    rows = q("SELECT * FROM hosts")
    nets = all_nets()
    for h in rows:
        n = smallest_net(h["ip"], nets)
        h["network"] = n["cidr"] if n else ""
    rows.sort(key=lambda h: (ipaddress.ip_address(h["ip"]).version, int(ipaddress.ip_address(h["ip"]))))
    return jsonify(rows)


@bp.route("/api/v1/hosts/<int:hid>", methods=["GET", "PUT", "DELETE"])
@auth
def api_host(hid):
    h = q("SELECT * FROM hosts WHERE id=?", (hid,), one=True)
    if not h:
        raise ApiError("Host not found", 404)
    if request.method == "DELETE":
        x("DELETE FROM hosts WHERE id=?", (hid,))
        changed("delete", f"host {h['fqdn']}", h)
        return jsonify(ok=True)
    if request.method == "PUT":
        o = validate_host(body(), hid)
        x("UPDATE hosts SET fqdn=?,ip=?,mac=?,configure_dns=?,configure_dhcp=?,comment=? WHERE id=?",
          (o["fqdn"], o["ip"], o["mac"], o["configure_dns"], o["configure_dhcp"], o["comment"], hid))
        changed("update", f"host {o['fqdn']}", o)
        return jsonify(id=hid, **o)
    return jsonify(h)


@bp.post("/api/v1/import/hosts")
@auth
def api_import_hosts():
    text = request.get_data(as_text=True)
    if request.is_json:
        text = body().get("csv", "")
    ok, errors = 0, []
    for i, row in enumerate(csv.DictReader(io.StringIO(text.strip())), start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        try:
            create_host({"fqdn": row.get("fqdn"), "ip": row.get("ip"), "mac": row.get("mac", ""),
                         "configure_dns": row.get("configure_dns", "1") or "1",
                         "configure_dhcp": row.get("configure_dhcp", "0") or "0",
                         "comment": row.get("comment", "")})
            ok += 1
        except ApiError as e:
            errors.append(f"line {i}: {e}")
    return jsonify(imported=ok, errors=errors)
