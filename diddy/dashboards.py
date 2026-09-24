"""Endpoint dashboard: data grafik DNS/DHCP dan layout widget per user."""
import json

from flask import Blueprint, g, jsonify, request

from .auth import auth, auth_self
from .core.errors import ApiError
from .core.util import body
from .db.connection import state_get, state_set, x
from .metrics import dhcp_report, dns_report, RANGES

bp = Blueprint("dashboards", __name__)

WIDGETS = {
    "kpi_overview", "health", "net_util", "activity",
    "dns_kpi", "dns_qps", "dns_hourly", "dns_util", "dns_rcodes", "dns_qtypes", "dns_zones", "dns_answers",
    "dhcp_kpi", "dhcp_leases", "dhcp_pools", "dhcp_packets", "dhcp_util",
}
SIZES = {"S", "M", "L"}
DEFAULT_LAYOUTS = {   # tiap baris 12 kolom: L = 12, M = 6, S = 4
    "overview": [("kpi_overview", "L"), ("dns_qps", "M"), ("dhcp_leases", "M"), ("dns_util", "S"),
                 ("dhcp_util", "S"), ("health", "S"), ("net_util", "M"), ("activity", "M")],
    "dns": [("dns_kpi", "L"), ("dns_qps", "L"), ("dns_hourly", "M"), ("dns_qtypes", "M"), ("dns_util", "S"),
            ("dns_rcodes", "S"), ("dns_answers", "S"), ("dns_zones", "L")],
    "dhcp": [("dhcp_kpi", "L"), ("dhcp_leases", "L"), ("dhcp_pools", "M"), ("dhcp_packets", "M"),
             ("dhcp_util", "S"), ("net_util", "S"), ("health", "S")],
}


def _range():
    r = request.args.get("range", "24h")
    if r not in RANGES:
        raise ApiError(f"range harus salah satu dari: {', '.join(RANGES)}")
    return r


@bp.get("/api/v1/metrics/dns")
@auth
def api_metrics_dns():
    return jsonify(dns_report(_range()))


@bp.get("/api/v1/metrics/dhcp")
@auth
def api_metrics_dhcp():
    return jsonify(dhcp_report(_range()))


def _default(board):
    return [{"id": i, "size": s} for i, s in DEFAULT_LAYOUTS[board]]


@bp.route("/api/v1/dashboard/layout", methods=["GET", "PUT", "DELETE"])
@auth_self
def api_layout():
    board = request.args.get("board", "overview")
    if board not in DEFAULT_LAYOUTS:
        raise ApiError(f"board harus salah satu dari: {', '.join(DEFAULT_LAYOUTS)}")
    key = f"layout:{g.user['id']}:{board}"
    if request.method == "DELETE":
        x("DELETE FROM state WHERE k=?", (key,))
        return jsonify(board=board, widgets=_default(board), default=True)
    if request.method == "PUT":
        ws = body().get("widgets")
        if not isinstance(ws, list) or len(ws) > 30:
            raise ApiError("widgets harus berupa daftar, maksimal 30")
        clean = []
        for w in ws:
            if not isinstance(w, dict) or w.get("id") not in WIDGETS:
                raise ApiError(f"widget tidak dikenal: {w}")
            clean.append({"id": w["id"], "size": w.get("size") if w.get("size") in SIZES else "M"})
        state_set(key, json.dumps(clean))
        return jsonify(board=board, widgets=clean, default=False)
    saved = state_get(key)
    if saved:
        try:
            return jsonify(board=board, widgets=json.loads(saved), default=False)
        except ValueError:
            pass
    return jsonify(board=board, widgets=_default(board), default=True)
