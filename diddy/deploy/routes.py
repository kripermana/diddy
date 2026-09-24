"""Endpoint REST deploy dan drift."""

from flask import Blueprint, jsonify

from ..auth import auth
from ..config import C, DRY
from ..core.runtime import LAST_DRIFT
from ..db.connection import state_get
from .drift import drift_repair, drift_report
from .pipeline import build, run_deploy

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
