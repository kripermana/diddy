"""Zona DNS, record, dan render zone file BIND."""

import datetime
import hashlib
import ipaddress
import os
import re

from ..config import C
from ..core.errors import ApiError
from ..core.util import LABEL_RE, now, parse_ip, valid_fqdn
from ..db.connection import q, state_get


REC_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "PTR", "SRV"]


DYN_BEGIN = "; --- BEGIN DHCP dynamic records (managed by Diddy, do not edit) ---"


DYN_END = "; --- END DHCP dynamic records ---"
# Zone file dari LiteDDI (<= 1.9) memakai penanda awal dengan nama lama.
LEGACY_DYN_BEGIN = "; --- BEGIN DHCP dynamic records (managed by LiteDDI, do not edit) ---"


def is_reverse(name):
    return name.endswith(".in-addr.arpa") or name.endswith(".ip6.arpa") or name in ("in-addr.arpa", "ip6.arpa")


def rel(name, zone):
    return "@" if name == zone else name[: -(len(zone) + 1)]


def find_zone(name, reverse=False, zones=None):
    best = None
    for z in zones if zones is not None else q("SELECT * FROM zones"):
        if is_reverse(z["name"]) != reverse:
            continue
        if name == z["name"] or name.endswith("." + z["name"]):
            if best is None or len(z["name"]) > len(best["name"]):
                best = z
    return best


def validate_zone(d, existing=None):
    name = existing["name"] if existing else valid_fqdn(d.get("name"), "zone name")
    ns = valid_fqdn(d.get("primary_ns") or C["default_ns"], "primary name server")
    email = (d.get("admin_email") or C["default_admin_email"]).strip().lower()
    if not re.match(r"^[a-z0-9._+-]+@[a-z0-9.-]+\.[a-z]{2,}$|^[a-z0-9._+-]+@[a-z0-9-]+$", email):
        raise ApiError(f"Invalid admin email: '{email}'")
    try:
        ttl = int(d.get("ttl") or 3600)
    except (TypeError, ValueError):
        raise ApiError("TTL must be a number")
    if ttl < 60:
        raise ApiError("Zone TTL must be at least 60 seconds")
    return {"name": name, "primary_ns": ns, "admin_email": email, "ttl": ttl,
            "comment": str(d.get("comment") or "")[:200]}


def get_zone(zid):
    z = q("SELECT * FROM zones WHERE id=?", (zid,), one=True)
    if not z:
        raise ApiError("Zone not found", 404)
    return z


def derived_records(z, zs, hs):
    """Records generated from host objects (A/AAAA in forward zone, PTR in reverse zone)."""
    rev = is_reverse(z["name"])
    for h in hs:
        if rev:
            ptr = ipaddress.ip_address(h["ip"]).reverse_pointer
            b = find_zone(ptr, True, zs)
            if b and b["id"] == z["id"]:
                yield {"id": f"host-{h['id']}", "zone_id": z["id"], "name": rel(ptr, z["name"]), "type": "PTR",
                       "value": h["fqdn"], "ttl": None, "comment": h["comment"], "source": "host", "host_id": h["id"]}
        else:
            b = find_zone(h["fqdn"], False, zs)
            if b and b["id"] == z["id"]:
                t = "A" if ipaddress.ip_address(h["ip"]).version == 4 else "AAAA"
                yield {"id": f"host-{h['id']}", "zone_id": z["id"], "name": rel(h["fqdn"], z["name"]), "type": t,
                       "value": h["ip"], "ttl": None, "comment": h["comment"], "source": "host", "host_id": h["id"]}


def zone_records(z):
    recs = q("SELECT * FROM records WHERE zone_id=?", (z["id"],))
    for r in recs:
        r["source"] = "manual"
    recs += list(derived_records(z, q("SELECT * FROM zones"), q("SELECT * FROM hosts WHERE configure_dns=1")))
    order = {t: i for i, t in enumerate(["NS", "MX", "A", "AAAA", "CNAME", "PTR", "SRV", "TXT"])}

    def key(r):
        nm = r["name"]
        if r["type"] == "PTR" and all(p.isdigit() for p in nm.split(".")):
            nm = ".".join(p.zfill(3) for p in reversed(nm.split(".")))
        return (nm != "@", nm, order.get(r["type"], 99), r["value"])
    return sorted(recs, key=key)


def normalize_record(zone_name, d):
    """Validasi nama, tipe, nilai, dan TTL satu record terhadap nama zona, tanpa cek bentrok di database."""
    name = (d.get("name") or "@").strip().lower().rstrip(".")
    if name in ("", "@", zone_name):
        name = "@"
    elif name.endswith("." + zone_name):
        name = name[: -(len(zone_name) + 1)]
    if name != "@" and not LABEL_RE.match(name):
        raise ApiError(f"Invalid record name: '{name}'")
    t = (d.get("type") or "").upper()
    if t not in REC_TYPES:
        raise ApiError(f"Unsupported record type: {t}. Supported: {', '.join(REC_TYPES)}")
    v = (d.get("value") or "").strip()
    if t == "A":
        v = str(parse_ip(v, "IPv4 address", 4))
    elif t == "AAAA":
        v = str(parse_ip(v, "IPv6 address", 6))
    elif t in ("CNAME", "NS", "PTR"):
        v = valid_fqdn(v, "target host")
    elif t == "MX":
        p = v.split()
        if len(p) != 2 or not p[0].isdigit() or int(p[0]) > 65535:
            raise ApiError("MX format: '<preference> <mail host>', e.g. '10 mail.example.com'")
        v = f"{int(p[0])} {valid_fqdn(p[1], 'mail host')}"
    elif t == "SRV":
        p = v.split()
        if len(p) != 4 or not all(s.isdigit() and int(s) <= 65535 for s in p[:3]):
            raise ApiError("SRV format: '<priority> <weight> <port> <target>', e.g. '10 5 5060 sip.example.com'")
        v = f"{int(p[0])} {int(p[1])} {int(p[2])} {valid_fqdn(p[3], 'target')}"
    elif t == "TXT":
        if not v or len(v) > 4000:
            raise ApiError("TXT value must be 1-4000 characters")
    if t == "CNAME" and name == "@":
        raise ApiError("CNAME is not allowed at the zone apex (@)")
    ttl = d.get("ttl")
    if ttl in (None, ""):
        ttl = None
    else:
        try:
            ttl = int(ttl)
        except (TypeError, ValueError):
            raise ApiError("TTL must be a number")
        if not 0 <= ttl <= 2147483647:
            raise ApiError("TTL out of range")
    fq = zone_name if name == "@" else f"{name}.{zone_name}"
    return {"name": name, "type": t, "value": v, "ttl": ttl, "comment": str(d.get("comment") or "")[:200], "_fq": fq}


def validate_record(d, rid=None):
    z = get_zone(d.get("zone_id"))
    o = normalize_record(z["name"], d)
    name, t, v, fq = o["name"], o["type"], o["value"], o["_fq"]
    others = q("SELECT type FROM records WHERE zone_id=? AND name=? AND id<>?", (z["id"], name, rid or 0))
    host_same = q("SELECT 1 FROM hosts WHERE fqdn=? AND configure_dns=1", (fq,), one=True)
    if t == "CNAME" and (others or host_same):
        raise ApiError(f"'{fq}' already has other records; a CNAME must be the only record for a name")
    if any(r["type"] == "CNAME" for r in others):
        raise ApiError(f"'{fq}' is a CNAME; no other records can share that name")
    if q("SELECT 1 FROM records WHERE zone_id=? AND name=? AND type=? AND value=? AND id<>?",
         (z["id"], name, t, v, rid or 0), one=True):
        raise ApiError("An identical record already exists", 409)
    return dict(o, zone_id=z["id"])


def dot(v):
    return v if v.endswith(".") else v + "."


def fmt_rr(name, ttl, t, v):
    if t in ("CNAME", "NS", "PTR"):
        v = dot(v)
    elif t == "MX":
        p, h = v.split(None, 1)
        v = f"{p} {dot(h)}"
    elif t == "SRV":
        a, b, c, h = v.split()
        v = f"{a} {b} {c} {dot(h)}"
    elif t == "TXT":
        chunks = [v[i:i + 255] for i in range(0, len(v), 255)]
        v = " ".join('"' + c.replace("\\", "\\\\").replace('"', '\\"') + '"' for c in chunks)
    return f"{name:<30} {'' if ttl is None else ttl:<7} IN {t:<6} {v}"


def render_zone(z, dyn=None):
    lines = [fmt_rr("@", None, "NS", z["primary_ns"])]
    lines += [fmt_rr(r["name"], r["ttl"], r["type"], r["value"]) for r in zone_records(z)]
    dyn_block = "\n".join(fmt_rr(r["name"], r["ttl"], r["type"], r["value"]) for r in (dyn or []))
    local, _, domain = z["admin_email"].partition("@")
    rname = local.replace(".", "\\.") + "." + domain   # titik di bagian lokal email wajib di-escape (RFC 1035)
    sig = "\n".join(lines) + "\n" + dyn_block + f"|{z['ttl']}|{z['primary_ns']}|{rname}"
    h = hashlib.sha256(sig.encode()).hexdigest()
    serial = z["serial"] or 0
    if h != state_get("zonehash:" + z["name"]):
        serial = max(serial + 1, int(datetime.date.today().strftime("%Y%m%d")) * 100 + 1)
    text = (f"; Zone {z['name']} - generated by Diddy on {now()}\n"
            f"; DO NOT EDIT: this file is overwritten on every deploy\n"
            f"$ORIGIN {z['name']}.\n$TTL {z['ttl']}\n"
            f"@ IN SOA {dot(z['primary_ns'])} {dot(rname)} (\n"
            f"    {serial} ; serial\n    3600       ; refresh\n    900        ; retry\n"
            f"    1209600    ; expire\n    300 )      ; negative cache TTL\n" +
            "\n".join(lines) + "\n" +
            DYN_BEGIN + "\n" + (dyn_block + "\n" if dyn_block else "") + DYN_END + "\n")
    return text, serial, h


def render_named_conf(zs, log_file=None):
    """Isi named.conf.diddy. `log_file` = tulis blok logging untuk halaman DNS errors (lihat dns/errlog.py)."""
    zdir = os.path.join(C["bind_dir"], "zones")
    out = ["// Generated by Diddy - DO NOT EDIT. Include this file from named.conf.local"]
    if log_file:
        out.append(f"// log error DNS untuk halaman DNS errors Diddy (bind_log_manage = false untuk mematikan)\n"
                   f"logging {{\n"
                   f'    channel diddy_errors {{ file "{log_file}" versions 3 size 20m; severity debug 2;\n'
                   f"        print-time yes; print-category yes; print-severity yes; }};\n"
                   f"    category query-errors {{ diddy_errors; }};\n"
                   f"    category lame-servers {{ diddy_errors; }};\n"
                   f"    category security {{ diddy_errors; default_syslog; }};\n"
                   f"}};")
    if C.getboolean("bind_stats_manage"):
        from urllib.parse import urlparse
        u = urlparse(C["bind_stats_url"])
        host, port = u.hostname or "127.0.0.1", u.port or 8053
        out.append(f"// statistik query untuk dashboard Diddy (bind_stats_manage = false untuk mematikan)\n"
                   f"statistics-channels {{ inet {host} port {port} allow {{ 127.0.0.1; ::1; }}; }};")
    for z in zs:
        out.append(f'zone "{z["name"]}" {{\n    type master;\n    file "{zdir}/db.{z["name"]}";\n'
                   f'    allow-update {{ none; }};\n}};')
    fws = q("SELECT * FROM forwarders ORDER BY domain")
    if fws:
        out.append("\n// Conditional forwarders")
    for f in fws:
        servers = " ".join(v.strip() + ";" for v in f["servers"].split(",") if v.strip())
        out.append(f'zone "{f["domain"]}" {{\n    type forward;\n    forward {f["policy"]};\n'
                   f'    forwarders {{ {servers} }};\n}};')
    return "\n".join(out) + "\n"
