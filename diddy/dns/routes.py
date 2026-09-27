"""Endpoint REST DNS."""

import json
import shutil

from flask import Blueprint, Response, jsonify, request

from ..audit import changed
from ..auth import auth
from ..config import DRY
from ..core.errors import ApiError
from ..core.util import body, boolv, clean, parse_ip
from ..db.connection import q, state_set, x
from ..metrics import RANGES, cache_report
from .cache import CACHE_DEFAULTS, LOOKUP_TYPES, cache_flush, cache_lookup, cache_stats, validate_cache_settings
from .ddns import ddns_refresh, dynamic_records
from .zone_io import apply_import, export_all, export_zone, plan_import, preview
from .resolver import dns_settings, recursion_acl, validate_dns_settings, validate_forwarder
from .zones import derived_records, get_zone, is_reverse, rel, validate_record, validate_zone, zone_records

bp = Blueprint("dns_routes", __name__)


@bp.route("/api/v1/zones", methods=["GET", "POST"])
@auth
def api_zones():
    if request.method == "POST":
        d = body()
        o = validate_zone(d)
        if q("SELECT 1 FROM zones WHERE name=?", (o["name"],), one=True):
            raise ApiError(f"Zone {o['name']} already exists", 409)
        zid = x("INSERT INTO zones(name,ttl,primary_ns,admin_email,comment) VALUES(?,?,?,?,?)",
                (o["name"], o["ttl"], o["primary_ns"], o["admin_email"], o["comment"]))
        changed("create", f"zone {o['name']}", o)
        ns_ip = (d.get("ns_ip") or "").strip()
        if ns_ip and o["primary_ns"].endswith("." + o["name"]):
            a = parse_ip(ns_ip, "name server IP")
            x("INSERT INTO records(zone_id,name,type,value) VALUES(?,?,?,?)",
              (zid, rel(o["primary_ns"], o["name"]), "A" if a.version == 4 else "AAAA", str(a)))
        return jsonify(id=zid, **o), 201
    zs = q("SELECT * FROM zones ORDER BY name")
    counts = {r["zone_id"]: r["c"] for r in q("SELECT zone_id, COUNT(*) c FROM records GROUP BY zone_id")}
    hs = q("SELECT * FROM hosts WHERE configure_dns=1")
    for z in zs:
        z["reverse"] = is_reverse(z["name"])
        z["record_count"] = counts.get(z["id"], 0) + sum(1 for r in derived_records(z, zs, hs))
    dynmap = dynamic_records(zs)
    for z in zs:
        z["dynamic_count"] = len(dynmap.get(z["id"], []))
        z["record_count"] += z["dynamic_count"]
    return jsonify(zs)


@bp.route("/api/v1/zones/<int:zid>", methods=["GET", "PUT", "DELETE"])
@auth
def api_zone(zid):
    z = get_zone(zid)
    if request.method == "DELETE":
        x("DELETE FROM zones WHERE id=?", (zid,))
        changed("delete", f"zone {z['name']}")
        return jsonify(ok=True)
    if request.method == "PUT":
        o = validate_zone(body(), z)
        x("UPDATE zones SET ttl=?,primary_ns=?,admin_email=?,comment=? WHERE id=?",
          (o["ttl"], o["primary_ns"], o["admin_email"], o["comment"], zid))
        changed("update", f"zone {z['name']}", o)
        z = get_zone(zid)
    z["reverse"] = is_reverse(z["name"])
    return jsonify(z)


def _import_plan():
    d = body()
    return plan_import(d.get("text") or "", d.get("format") or None, (d.get("zone") or "").strip() or None,
                       d.get("mode") or "merge", d.get("zone_id") or None, d.get("filename") or "")


@bp.post("/api/v1/zones/import/preview")
@auth
def api_zone_import_preview():
    """Rencana import tanpa mengubah apa pun: record yang ditambah, dilewati (dengan alasan), dan dihapus."""
    return jsonify(preview(_import_plan()))


@bp.post("/api/v1/zones/import")
@auth
def api_zone_import():
    plan = _import_plan()
    bad = [p for p in plan["zones"] if p["error"]]
    if bad and len(bad) == len(plan["zones"]):
        raise ApiError("; ".join(f"{p['zone'] or '?'}: {p['error']}" for p in bad))
    done = apply_import(plan)
    for z in done:
        changed("import", f"zone {z['zone']}", dict(z, format=plan["format"], mode=plan["mode"]))
    return jsonify(ok=True, zones=done, errors=[{"zone": p["zone"], "error": p["error"]} for p in bad])


def _download(text, filename, mimetype):
    return Response(text, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.get("/api/v1/zones/export")
@auth
def api_zones_export():
    """Semua zona dan record manualnya dalam satu file JSON (backup; bisa diimport kembali)."""
    return _download(export_all(), "diddy-zones.json", "application/json")


@bp.get("/api/v1/zones/<int:zid>/export")
@auth
def api_zone_export(zid):
    text, filename, mimetype = export_zone(zid, request.args.get("format", "bind"),
                                           request.args.get("dynamic") in ("1", "true", "yes"))
    return _download(text, filename, mimetype)


@bp.get("/api/v1/zones/<int:zid>/records")
@auth
def api_zone_records(zid):
    z = get_zone(zid)
    return jsonify(zone_records(z) + dynamic_records().get(z["id"], []))


@bp.post("/api/v1/records")
@auth
def api_record_create():
    o = validate_record(body())
    rid = x("INSERT INTO records(zone_id,name,type,value,ttl,comment) VALUES(?,?,?,?,?,?)",
            (o["zone_id"], o["name"], o["type"], o["value"], o["ttl"], o["comment"]))
    changed("create", f"record {o['type']} {o['_fq']}", clean(o))
    return jsonify(id=rid, **clean(o)), 201


@bp.route("/api/v1/records/<int:rid>", methods=["PUT", "DELETE"])
@auth
def api_record(rid):
    r = q("SELECT * FROM records WHERE id=?", (rid,), one=True)
    if not r:
        raise ApiError("Record not found", 404)
    if request.method == "DELETE":
        x("DELETE FROM records WHERE id=?", (rid,))
        changed("delete", f"record {r['type']} {r['name']}", r)
        return jsonify(ok=True)
    d = body()
    d["zone_id"] = r["zone_id"]
    o = validate_record(d, rid)
    x("UPDATE records SET name=?,type=?,value=?,ttl=?,comment=? WHERE id=?",
      (o["name"], o["type"], o["value"], o["ttl"], o["comment"], rid))
    changed("update", f"record {o['type']} {o['_fq']}", clean(o))
    return jsonify(ok=True)


@bp.route("/api/v1/dns-settings", methods=["GET", "PUT"])
@auth
def api_dns_settings():
    if request.method == "PUT":
        cfg = validate_dns_settings(body())
        state_set("dns_settings", json.dumps(cfg))
        changed("update", "dns settings", cfg)
        return jsonify(cfg)
    cfg = dns_settings()
    cfg["effective_acl"] = recursion_acl(cfg)
    return jsonify(cfg)


@bp.route("/api/v1/forwarders", methods=["GET", "POST"])
@auth
def api_forwarders():
    if request.method == "POST":
        o = validate_forwarder(body())
        fid = x("INSERT INTO forwarders(domain,servers,policy,comment) VALUES(?,?,?,?)",
                (o["domain"], o["servers"], o["policy"], o["comment"]))
        changed("create", f"forwarder {o['domain']}", o)
        return jsonify(id=fid, **o), 201
    return jsonify(q("SELECT * FROM forwarders ORDER BY domain"))


@bp.route("/api/v1/forwarders/<int:fid>", methods=["PUT", "DELETE"])
@auth
def api_forwarder(fid):
    f = q("SELECT * FROM forwarders WHERE id=?", (fid,), one=True)
    if not f:
        raise ApiError("Forwarder tidak ditemukan", 404)
    if request.method == "DELETE":
        x("DELETE FROM forwarders WHERE id=?", (fid,))
        changed("delete", f"forwarder {f['domain']}")
        return jsonify(ok=True)
    o = validate_forwarder(body(), fid)
    x("UPDATE forwarders SET domain=?,servers=?,policy=?,comment=? WHERE id=?",
      (o["domain"], o["servers"], o["policy"], o["comment"], fid))
    changed("update", f"forwarder {o['domain']}", o)
    return jsonify(id=fid, **o)


@bp.get("/api/v1/dns-cache")
@auth
def api_dns_cache():
    rng = request.args.get("range", "24h")
    if rng not in RANGES:
        raise ApiError(f"range harus salah satu dari: {', '.join(RANGES)}")
    cfg = dns_settings()
    return jsonify(recursion=cfg["recursion"], settings={k: cfg[k] for k in CACHE_DEFAULTS},
                   stats=cache_stats(), history=cache_report(rng), lookup_types=LOOKUP_TYPES,
                   tools={"rndc": bool(shutil.which("rndc")), "dig": bool(shutil.which("dig"))}, dry_run=DRY)


@bp.put("/api/v1/dns-cache/settings")
@auth
def api_dns_cache_settings():
    cfg = dns_settings()
    new = validate_cache_settings(body(), cfg)
    cfg.update(new)
    state_set("dns_settings", json.dumps(cfg))
    changed("update", "dns cache settings", new)
    return jsonify(new)


@bp.post("/api/v1/dns-cache/flush")
@auth
def api_dns_cache_flush():
    d = body()
    return jsonify(cache_flush(d.get("name"), boolv(d.get("tree"))))


@bp.get("/api/v1/dns-cache/lookup")
@auth
def api_dns_cache_lookup():
    return jsonify(cache_lookup(request.args.get("name"), request.args.get("type", "A")))


@bp.post("/api/v1/ddns/refresh")
@auth
def api_ddns_refresh():
    return jsonify(changed=ddns_refresh())
