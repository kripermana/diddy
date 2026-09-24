"""Validasi DHCP range."""

import ipaddress

from ..core.errors import ApiError
from ..core.util import parse_ip
from ..db.connection import q


def validate_range(d, rid=None):
    row = q("SELECT * FROM networks WHERE id=?", (d.get("network_id"),), one=True)
    if not row:
        raise ApiError("Network not found")
    n = ipaddress.ip_network(row["cidr"])
    if n.version != 4:
        raise ApiError("DHCP ranges are supported for IPv4 only")
    s, e = parse_ip(d.get("start_ip"), "start IP", 4), parse_ip(d.get("end_ip"), "end IP", 4)
    if s not in n or e not in n:
        raise ApiError(f"Range must be inside {n}")
    if s > e:
        raise ApiError("Start IP must be lower than or equal to end IP")
    if n.prefixlen < 31 and (s == n.network_address or e == n.broadcast_address):
        raise ApiError("Range cannot include the network or broadcast address")
    if row["gateway"] and s <= ipaddress.ip_address(row["gateway"]) <= e:
        raise ApiError(f"Range cannot include the gateway {row['gateway']}")
    for r in q("SELECT * FROM ranges WHERE network_id=? AND id<>?", (row["id"], rid or 0)):
        if not (e < ipaddress.ip_address(r["start_ip"]) or s > ipaddress.ip_address(r["end_ip"])):
            raise ApiError(f"Overlaps existing range {r['start_ip']} - {r['end_ip']}")
    return {"network_id": row["id"], "start_ip": str(s), "end_ip": str(e),
            "comment": str(d.get("comment") or "")[:200], "_cidr": row["cidr"]}
