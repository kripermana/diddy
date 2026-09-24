"""Autentikasi sesi / HTTP Basic dan endpoint login."""

import hashlib
import hmac
import secrets
import time

from flask import Blueprint, g, jsonify, request, session
from functools import wraps
from werkzeug.security import check_password_hash

from .audit import audit
from .config import C, DRY
from .core.errors import ApiError
from .core.util import body
from .db.connection import q, state_get
from .version import NAME, SLOGAN, VERSION

bp = Blueprint("auth", __name__)


# Cache verifikasi HTTP Basic: hash password (scrypt) sengaja lambat, ~90 ms per cek.
# Kunci cache adalah HMAC(user:password) dengan kunci acak per proses, jadi password tidak
# pernah disimpan. Entri hanya berlaku selama pw_hash di database tidak berubah, sehingga
# ganti password atau hapus user langsung berlaku tanpa menunggu cache kedaluwarsa.
_BASIC_KEY = secrets.token_bytes(32)
_BASIC_CACHE = {}
_BASIC_TTL = 300
_BASIC_MAX = 1000


def _basic_key(username, password):
    return hmac.new(_BASIC_KEY, f"{username}\0{password}".encode(), hashlib.sha256).hexdigest()


def current_user():
    uid = session.get("uid")
    if uid:
        return q("SELECT id,username,role FROM users WHERE id=?", (uid,), one=True)
    a = request.authorization  # HTTP Basic untuk API dan script
    if a and a.username:
        u = q("SELECT * FROM users WHERE username=?", (a.username,), one=True)
        if not u:
            return None
        key = _basic_key(a.username, a.password or "")
        hit = _BASIC_CACHE.get(key)
        if hit and hit[0] == u["pw_hash"] and hit[1] > time.time():
            return {"id": u["id"], "username": u["username"], "role": u["role"]}
        if check_password_hash(u["pw_hash"], a.password or ""):
            if len(_BASIC_CACHE) >= _BASIC_MAX:
                _BASIC_CACHE.clear()
            _BASIC_CACHE[key] = (u["pw_hash"], time.time() + _BASIC_TTL)
            return {"id": u["id"], "username": u["username"], "role": u["role"]}
    return None


def auth_self(f):
    """Seperti @auth, tapi user read-only juga boleh menulis. Hanya untuk data milik user itu sendiri."""
    @wraps(f)
    def w(*a, **k):
        u = current_user()
        if not u:
            return jsonify(error="Unauthorized"), 401
        g.user = u
        return f(*a, **k)
    return w


def auth(f):
    @wraps(f)
    def w(*a, **k):
        u = current_user()
        if not u:
            return jsonify(error="Unauthorized"), 401
        if request.method != "GET" and u["role"] != "admin":
            return jsonify(error="Read-only account: changes are not allowed"), 403
        g.user = u
        return f(*a, **k)
    return w


@bp.post("/api/v1/login")
def api_login():
    d = body()
    u = q("SELECT * FROM users WHERE username=?", ((d.get("username") or "").strip(),), one=True)
    if not u or not check_password_hash(u["pw_hash"], d.get("password") or ""):
        time.sleep(0.5)
        raise ApiError("Wrong username or password", 401)
    session.clear()
    session["uid"] = u["id"]
    g.user = u
    audit("login", u["username"])
    return jsonify(ok=True)


@bp.post("/api/v1/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/api/v1/me")
@auth
def api_me():
    return jsonify(user=g.user, pending=state_get("pending") == "1", name=NAME, version=VERSION, slogan=SLOGAN,
                   dry_run=DRY,
                   defaults={"primary_ns": C["default_ns"], "admin_email": C["default_admin_email"]})
