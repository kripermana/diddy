"""Dashboard, health, system information, pencarian, export, dan audit log."""

import csv
import io
import ipaddress
import os
import platform
import re
import shutil
import socket
import time

from flask import Blueprint, Response, g, jsonify, request

from .audit import audit_count, audit_entry, audit_facets, audit_list
from .auth import auth
from .config import C, CONF_PATH, DB, DRY, FILE, KEA_MYSQL, MYSQL, PFX, describe_conf
from .core.errors import ApiError
from .core.runtime import LAST_DDNS, STARTED
from .core.util import clean, human_time, now, run, svc_state
from .db.connection import mysql_connect, q, state_get
from .deploy.drift import drift_report
from .deploy.services import services_status
from .dns.errlog import source_status as errlog_status
from .dhcp.leases import read_leases
from .dns.resolver import dns_settings, proxy_target
from .ipam.networks import all_nets, usage_sets, utilization
from .metrics import STATUS
from .version import AUTHOR, NAME, SLOGAN, VERSION

bp = Blueprint("system", __name__)


@bp.get("/api/v1/dashboard")
@auth
def api_dashboard():
    nets = all_nets()
    sets = usage_sets()
    top = []
    for n in nets:
        used, total, pct = utilization(n, sets)
        top.append({"id": n["id"], "cidr": n["cidr"], "comment": n["comment"], "used": used, "total": total,
                    "utilization": pct})
    top.sort(key=lambda r: -r["utilization"])
    counts = {
        "networks": len(nets),
        "hosts": q("SELECT COUNT(*) c FROM hosts", one=True)["c"],
        "zones": q("SELECT COUNT(*) c FROM zones", one=True)["c"],
        "records": q("SELECT COUNT(*) c FROM records", one=True)["c"],
        "dhcp_networks": sum(1 for n in nets if n["dhcp_enabled"]),
        "leases": len(sets[2]),
    }
    db_label = f"MySQL {C['mysql_host']}/{C['mysql_database']}" if MYSQL else "SQLite"
    if PFX:
        db_label += f", prefix {PFX}"
    services = [{"name": f"DNS ({C['dns_service']})", "state": svc_state(C["dns_service"])},
                {"name": f"DHCP ({C['dhcp_service']})", "state": svc_state(C["dhcp_service"])},
                {"name": f"Diddy database ({db_label})", "state": "active"}]
    if KEA_MYSQL:
        try:
            mysql_connect(C["kea_db_host"], C["kea_db_port"], C["kea_db_user"], C["kea_db_password"],
                          C["kea_db_name"], timeout=3).close()
            st = "active"
        except Exception:
            st = "unreachable"
        services.append({"name": f"Kea lease DB (MySQL {C['kea_db_host']}/{C['kea_db_name']})", "state": st})
    return jsonify(counts=counts, top_networks=top[:8], services=services,
                   recent=q("SELECT * FROM audit ORDER BY id DESC LIMIT 10"),
                   pending=state_get("pending") == "1", last_deploy=q(
                       "SELECT ts,username,detail FROM audit WHERE action='deploy' ORDER BY id DESC LIMIT 1", one=True))


@bp.get("/api/v1/health")
@auth
def api_health():
    items = []

    def add(name, state, detail=""):
        items.append({"name": name, "state": state, "detail": detail})

    add("Diddy service", "ok", f"v{VERSION}, uptime {human_time(time.time() - STARTED)}")
    ss = services_status()
    if ss["stopped"]:
        add("Service control", "warn", f"DNS and DHCP services were shut down from Diddy by {ss['stopped_by']} at "
            f"{ss['stopped_at']}. Use Services > Start services to bring them back.")
    try:
        q("SELECT 1 FROM users LIMIT 1")
        add("Diddy database", "ok",
            f"MySQL {C['mysql_host']}/{C['mysql_database']}" + (f" prefix {PFX}" if PFX else "") if MYSQL else "SQLite")
    except Exception as e:
        add("Diddy database", "fail", str(e)[:200])
    for label, svc in (("DNS service", C["dns_service"]), ("DHCP service", C["dhcp_service"])):
        st = svc_state(svc)
        add(label, "ok" if st == "active" else "unknown" if st == "unknown" else "fail", f"{svc}: {st}")
    if KEA_MYSQL:
        try:
            mysql_connect(C["kea_db_host"], C["kea_db_port"], C["kea_db_user"], C["kea_db_password"],
                          C["kea_db_name"], timeout=3).close()
            add("Kea lease database", "ok", f"MySQL {C['kea_db_host']}/{C['kea_db_name']}")
        except Exception as e:
            add("Kea lease database", "fail", str(e)[:200])
    else:
        add("Kea lease storage", "ok", f"memfile {C['kea_lease_file']}")
    cfg = dns_settings()
    if cfg["upstream_mode"] == "encrypted":
        st = svc_state(C["dnsdist_service"])
        add("Encrypted upstream (dnsdist)", "ok" if st == "active" else "unknown" if st == "unknown" else "fail",
            f"{C['dnsdist_service']}: {st}, listen {proxy_target()}, upstream " +
            ", ".join(f"{u['protocol'].upper()} {u['hostname']}" for u in cfg["encrypted_upstreams"]))
    if shutil.which("named-checkconf"):
        ok, out = run(["named-checkconf"])
        add("BIND configuration", "ok" if ok else "fail", out[:300] or "named-checkconf: ok")
    if int(C["metrics_interval"]) > 0:
        st = STATUS
        if st["ts"] is None:
            add("Dashboard statistics", "unknown", "Belum ada sampel; collector jalan tiap "
                f"{C['metrics_interval']} detik")
        elif st["dns"]:
            add("Dashboard statistics", "ok", f"Statistik BIND terbaca dari {C['bind_stats_url']}; "
                + ("statistik Kea terbaca" if st["kea"] else "statistik paket Kea tidak tersedia"))
        else:
            add("Dashboard statistics", "warn", f"Statistik BIND tidak terbaca dari {C['bind_stats_url']}: "
                f"{st['dns_error']}. Deploy sekali agar statistics-channel aktif.")
    el = errlog_status()
    if el["managed"]:
        if el["conflict"]:
            add("DNS error log", "warn", "named.conf already has its own logging block; add channel diddy_errors "
                "manually to see SERVFAIL/REFUSED details")
        elif not el["exists"]:
            add("DNS error log", "unknown", f"{el['log_file']} not written yet. Deploy once to enable it.")
        else:
            add("DNS error log", "ok" if not el["error"] else "warn", el["error"] or f"reading {el['log_file']}")
    items_drift = drift_report()
    nbad = sum(1 for i in items_drift if i["state"] in ("missing", "modified"))
    if items_drift:
        add("Configuration drift", "ok" if not nbad else "warn",
            "Semua file service sama dengan config terakhir yang di-deploy" if not nbad else
            f"{nbad} file diubah di luar Diddy: " +
            ", ".join(i["path"] for i in items_drift if i["state"] != "ok"))
    pending = state_get("pending") == "1"
    last = q("SELECT ts,username FROM audit WHERE action='deploy' ORDER BY id DESC LIMIT 1", one=True)
    add("Deployment", "warn" if pending else "ok",
        ("Changes pending since the last deploy. " if pending else "Services match the database. ") +
        (f"Last deploy {last['ts']} by {last['username']}" if last else "Never deployed"))
    ddns_nets = [n["cidr"] for n in all_nets() if n["ddns_enabled"]]
    if ddns_nets:
        if LAST_DDNS.get("skipped"):
            add("DDNS refresh", "warn", "Dilewati untuk zona " + ", ".join(LAST_DDNS["skipped"]) +
                " karena file-nya drift. Perbaiki drift di halaman Deploy.")
        else:
            add("DDNS refresh", "ok" if LAST_DDNS["ts"] else "unknown",
                (f"Last run {LAST_DDNS['ts']}" if LAST_DDNS["ts"] else "No run yet") +
                f", every {C['ddns_refresh_interval']}s, networks: {', '.join(ddns_nets)}")
    try:
        du = shutil.disk_usage(C["bind_dir"] if os.path.isdir(C["bind_dir"]) else "/")
        free_pct = round(du.free * 100 / du.total, 1)
        add("Disk space", "ok" if free_pct > 10 else "warn",
            f"{round(du.free / 2**30, 1)} GB free of {round(du.total / 2**30, 1)} GB ({free_pct}%)")
    except Exception:
        pass
    worst = "fail" if any(i["state"] == "fail" for i in items) else \
            "warn" if any(i["state"] == "warn" for i in items) else "ok"
    return jsonify(overall=worst, items=items)


@bp.get("/api/v1/system")
@auth
def api_system():
    def tool_version(cmd, args, pattern):
        if not shutil.which(cmd):
            return "not installed"
        ok, out = run([cmd] + args, timeout=10)
        m = re.search(pattern, out)
        return m.group(1) if m else (out.splitlines() or ["unknown"])[0][:60]

    return jsonify({
        "name": NAME,
        "version": VERSION,
        "diddy": VERSION,
        "slogan": SLOGAN,
        "author": AUTHOR,
        "copyright": f"(c) 2026 {AUTHOR}, MIT License",
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "distribution": (open("/etc/os-release").read().split('PRETTY_NAME="')[1].split('"')[0]
                         if os.path.exists("/etc/os-release") and 'PRETTY_NAME="' in open("/etc/os-release").read()
                         else platform.platform()),
        "python": platform.python_version(),
        "bind": tool_version("named", ["-v"], r"BIND ([\d.]+\S*)"),
        "kea": tool_version("kea-dhcp4", ["-V"], r"^([\d.]+)"),
        "database": (f"MySQL {C['mysql_host']}:{C['mysql_port']}/{C['mysql_database']}" if MYSQL else f"SQLite {DB}"),
        "table_prefix": PFX or "(none)",
        "kea_lease_backend": C["kea_lease_backend"],
        "dnsdist": (shutil.which("dnsdist") and (run(["dnsdist", "--version"], timeout=10)[1].split()[1]
                                                 if run(["dnsdist", "--version"], timeout=10)[0] else "installed")) or
        "not installed",
        "upstream_mode": dns_settings()["upstream_mode"],
        "dhcp_interfaces": C["dhcp_interfaces"],
        "bind_dir": C["bind_dir"],
        "kea_conf": C["kea_conf"],
        "config_file": CONF_PATH,
        "dry_run": DRY,
        "ddns_refresh_interval": int(C["ddns_refresh_interval"]),
        "drift_check_interval": int(C["drift_check_interval"]),
        "drift_auto_repair": C.getboolean("drift_auto_repair"),
        "uptime": human_time(time.time() - STARTED),
        "server_time": now(),
    })


@bp.get("/api/v1/search")
@auth
def api_search():
    s = (request.args.get("q") or "").strip().lower()
    if not s:
        return jsonify(networks=[], hosts=[], records=[], leases=[])
    like = f"%{s}%"
    nets = [clean(n) for n in all_nets() if s in n["cidr"] or s in n["comment"].lower() or
            s in n["site"].lower() or s == n["vlan"].lower()]
    try:
        a = ipaddress.ip_address(s)
        ids = {n["id"] for n in nets}
        nets += [clean(n) for n in all_nets() if n["_n"].version == a.version and a in n["_n"] and n["id"] not in ids]
    except ValueError:
        pass
    hs = q("SELECT * FROM hosts WHERE lower(fqdn) LIKE ? OR ip LIKE ? OR mac LIKE ? OR lower(comment) LIKE ?",
           (like, like, like, like))
    recs = q("SELECT r.*, z.name zone FROM records r JOIN zones z ON z.id=r.zone_id "
             "WHERE lower(r.name) LIKE ? OR lower(r.value) LIKE ? OR lower(z.name) LIKE ?", (like, like, like))
    ls = [lease for lease in read_leases() if s in lease["ip"] or s in lease["mac"] or s in lease["hostname"].lower()]
    return jsonify(networks=nets, hosts=hs, records=recs, leases=ls)


@bp.get("/api/v1/export/<kind>.csv")
@auth
def api_export(kind):
    if kind == "networks":
        rows = [clean(n) for n in all_nets()]
    elif kind == "hosts":
        rows = q("SELECT fqdn,ip,mac,configure_dns,configure_dhcp,comment FROM hosts ORDER BY ip")
    elif kind == "records":
        rows = q("SELECT z.name zone, r.name, r.type, r.value, r.ttl, r.comment FROM records r "
                 "JOIN zones z ON z.id=r.zone_id ORDER BY z.name, r.name")
    elif kind == "leases":
        rows = read_leases()
    elif kind == "audit":
        rows = audit_list(request.args, 100000)
    else:
        raise ApiError("Unknown export type", 404)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=diddy-{kind}.csv"})


@bp.get("/api/v1/audit")
@auth
def api_audit():
    """Audit log terbaru. Filter opsional: q, user, action, since, until; halaman lewat limit dan offset."""
    lim = max(1, min(2000, request.args.get("limit", 300, type=int)))
    off = max(0, request.args.get("offset", 0, type=int))
    return jsonify(audit_list(request.args, lim, off))


@bp.get("/api/v1/audit/count")
@auth
def api_audit_count():
    return jsonify(total=audit_count(request.args))


@bp.get("/api/v1/audit/facets")
@auth
def api_audit_facets():
    return jsonify(audit_facets())


@bp.get("/api/v1/audit/<int:aid>")
@auth
def api_audit_entry(aid):
    return jsonify(audit_entry(aid))


@bp.get("/api/v1/system/config")
@auth
def api_system_config():
    """Variabel config Diddy (read-only). Hanya admin; password tidak pernah dikirim."""
    if g.user["role"] != "admin":
        raise ApiError("Only administrators can view the system configuration", 403)
    return jsonify(config_file=CONF_PATH, config_exists=os.path.exists(CONF_PATH), section=FILE["section"],
                   legacy_section=FILE["section"] == "liteddi", unknown=FILE["unknown"], items=describe_conf(),
                   derived={"database": (f"MySQL {C['mysql_host']}:{C['mysql_port']}/{C['mysql_database']}"
                                         if MYSQL else f"SQLite {DB}"),
                            "table_prefix": PFX or "(none)", "dry_run": DRY,
                            "kea_lease_storage": "MySQL" if KEA_MYSQL else "memfile"})
