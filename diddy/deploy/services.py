"""Kendali service DNS/DHCP dari Diddy: reload, restart, stop, dan start.

Diddy sendiri tidak pernah ikut dimatikan. Status "dihentikan" disimpan di database supaya
proses lain (deploy, perbaikan drift, refresh DDNS) tidak menyalakan service lagi diam-diam.
"""

from ..audit import audit
from ..config import C, DRY
from ..core.errors import ApiError
from ..core.util import now, run, svc_state
from ..db.connection import state_get, state_set
from ..dhcp.kea import kea_command
from ..dns.resolver import dns_settings

STOPPED = "services_stopped"
ACTIONS = ("reload", "restart", "stop", "start")


def services_stopped():
    return state_get(STOPPED) == "1"


def managed():
    """Service yang dikelola Diddy, urut seperti saat start: dnsdist (bila dipakai), BIND, lalu Kea."""
    out = []
    if dns_settings()["upstream_mode"] == "encrypted" or state_get("dnsdist_enabled") == "1":
        out.append({"key": "dnsdist", "name": "DNS proxy (dnsdist)", "unit": C["dnsdist_service"]})
    out.append({"key": "dns", "name": "DNS (BIND)", "unit": C["dns_service"]})
    out.append({"key": "dhcp", "name": "DHCP (Kea)", "unit": C["dhcp_service"]})
    return out


def services_status():
    return {"stopped": services_stopped(), "stopped_at": state_get("services_stopped_at"),
            "stopped_by": state_get("services_stopped_by"), "dry_run": DRY,
            "services": [dict(s, state=svc_state(s["unit"])) for s in managed()]}


def _reload(s):
    if s["key"] == "dns":
        return run(C["dns_reload_cmd"])
    if s["key"] == "dhcp":
        try:
            r = kea_command("config-reload")
            if r.get("result") != 0:
                raise RuntimeError(r.get("text"))
            return True, r.get("text", "") or "ok"
        except Exception as e:  # socket tidak ada / Kea tidak jalan
            ok, out = run(C["kea_reload_fallback_cmd"])
            return ok, f"control socket failed ({e}); fallback: {out or 'ok'}"
    return run(["systemctl", "reload-or-restart", s["unit"]], timeout=30)   # dnsdist tidak punya reload


def control(action, username="admin"):
    """Jalankan satu aksi ke semua service yang dikelola. Mengembalikan {ok, action, report}."""
    if action not in ACTIONS:
        raise ApiError(f"action harus salah satu dari: {', '.join(ACTIONS)}")
    stopped = services_stopped()
    if stopped and action in ("reload", "restart"):
        raise ApiError("Services are stopped. Start them first.", 409)
    svcs = managed()
    if action == "stop":
        svcs = svcs[::-1]   # hentikan DHCP dulu, proxy DNS terakhir
    report = []
    for s in svcs:
        if DRY:
            ok, out = True, f"dry_run = true: {action} {s['unit']} not executed"
        elif action == "reload":
            ok, out = _reload(s)
        else:
            ok, out = run(["systemctl", action, s["unit"]], timeout=60)
        report.append({"step": f"{action} {s['name']}", "ok": ok, "output": out or "ok"})
    all_ok = all(r["ok"] for r in report)
    if action == "stop":
        state_set(STOPPED, "1")
        state_set("services_stopped_at", now())
        state_set("services_stopped_by", username)
    elif action == "start":
        state_set(STOPPED, "0")
    audit("services-" + action, ", ".join(s["unit"] for s in svcs),
          "; ".join(f"{r['step']}: {'ok' if r['ok'] else 'FAILED ' + r['output'][:200]}" for r in report))
    return {"ok": all_ok, "action": action, "report": report}
