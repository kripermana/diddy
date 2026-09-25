"""Endpoint REST deploy dan drift."""

from flask import Blueprint, g, jsonify
from werkzeug.security import check_password_hash

from ..audit import audit
from ..auth import auth
from ..core.errors import ApiError
from ..core.util import body
from ..db.connection import q
from ..config import C, DRY
from ..core.runtime import LAST_DRIFT
from ..db.connection import state_get
from .drift import drift_repair, drift_report
from .pipeline import build, run_deploy
from .services import control, services_status

bp = Blueprint("deploy_routes", __name__)


@bp.get("/api/v1/drift")
@auth
def api_drift():
    items = drift_report()
    return jsonify(items=items, drifted=sum(1 for i in items if i["state"] in ("missing", "modified")),
                   checked_at=LAST_DRIFT["ts"], auto_repair=C.getboolean("drift_auto_repair"),
                   interval=int(C["drift_check_interval"]))


@bp.post("/api/v1/drift/repair")
@auth
def api_drift_repair():
    return jsonify(repaired=drift_repair())


@bp.get("/api/v1/deploy/preview")
@auth
def api_deploy_preview():
    zs, rendered, named, kea, options, dd = build()
    return jsonify(named_conf=named, zones={z["name"]: t for z, t, _, _ in rendered}, kea=kea,
                   named_options=options, dnsdist=dd, pending=state_get("pending") == "1", dry_run=DRY)


@bp.post("/api/v1/deploy")
@auth
def api_deploy():
    result, status = run_deploy()
    return jsonify(result), status


@bp.get("/api/v1/services")
@auth
def api_services():
    return jsonify(services_status())


@bp.post("/api/v1/services/<action>")
@auth
def api_services_control(action):
    """reload, restart, stop, start semua service DNS/DHCP (Diddy tidak ikut). stop butuh password admin."""
    if action == "stop":
        u = q("SELECT pw_hash FROM users WHERE id=?", (g.user["id"],), one=True)
        if not u or not check_password_hash(u["pw_hash"], body().get("password") or ""):
            audit("services-stop-denied", "services", "wrong password")
            raise ApiError("Wrong password. Services were not shut down.", 403)
    return jsonify(control(action, g.user["username"]))
