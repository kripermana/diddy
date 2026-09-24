"""Endpoint REST DHCP."""

import ipaddress

from flask import Blueprint, jsonify, request

from ..audit import changed
from ..auth import auth
from ..core.errors import ApiError
from ..core.util import body, clean
from ..db.connection import q, x
from .leases import read_leases
from .ranges import validate_range
from ..ipam.networks import all_nets, smallest_net

bp = Blueprint("dhcp_routes", __name__)


@bp.route("/api/v1/ranges", methods=["GET", "POST"])
@auth
def api_ranges():
    if request.method == "POST":
        o = validate_range(body())
        rid = x("INSERT INTO ranges(network_id,start_ip,end_ip,comment) VALUES(?,?,?,?)",
                (o["network_id"], o["start_ip"], o["end_ip"], o["comment"]))
        changed("create", f"range {o['start_ip']}-{o['end_ip']}", o["_cidr"])
        return jsonify(id=rid, **clean(o)), 201
    nid = request.args.get("network_id", type=int)
    sql = "SELECT r.*, n.cidr FROM ranges r JOIN networks n ON n.id=r.network_id"
    rows = q(sql + " WHERE r.network_id=?", (nid,)) if nid else q(sql)
    for r in rows:
        r["size"] = int(ipaddress.ip_address(r["end_ip"])) - int(ipaddress.ip_address(r["start_ip"])) + 1
    rows.sort(key=lambda r: int(ipaddress.ip_address(r["start_ip"])))
    return jsonify(rows)


@bp.route("/api/v1/ranges/<int:rid>", methods=["PUT", "DELETE"])
@auth
def api_range(rid):
    r = q("SELECT * FROM ranges WHERE id=?", (rid,), one=True)
    if not r:
        raise ApiError("Range not found", 404)
    if request.method == "DELETE":
        x("DELETE FROM ranges WHERE id=?", (rid,))
        changed("delete", f"range {r['start_ip']}-{r['end_ip']}")
        return jsonify(ok=True)
    d = body()
    d["network_id"] = r["network_id"]
    o = validate_range(d, rid)
    x("UPDATE ranges SET start_ip=?,end_ip=?,comment=? WHERE id=?", (o["start_ip"], o["end_ip"], o["comment"], rid))
    changed("update", f"range {o['start_ip']}-{o['end_ip']}")
    return jsonify(ok=True)


@bp.get("/api/v1/leases")
@auth
def api_leases():
    nets = all_nets()
    out = read_leases()
    for lease in out:
        n = smallest_net(lease["ip"], nets)
        lease["network"] = n["cidr"] if n else ""
    out.sort(key=lambda lease: int(ipaddress.ip_address(lease["ip"])))
    return jsonify(out)
