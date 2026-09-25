"""Manajemen user."""

import re

from flask import Blueprint, g, jsonify, request
from werkzeug.security import generate_password_hash

from .audit import audit, last_logins
from .auth import auth
from .core.errors import ApiError
from .core.util import body, now
from .db.connection import q, x

bp = Blueprint("users", __name__)


@bp.route("/api/v1/users", methods=["GET", "POST"])
@auth
def api_users():
    if request.method == "POST":
        d = body()
        u = (d.get("username") or "").strip()
        if not re.match(r"^[A-Za-z0-9._-]{2,32}$", u):
            raise ApiError("Username: 2-32 characters, letters/digits/._-")
        if len(d.get("password") or "") < 8:
            raise ApiError("Password must be at least 8 characters")
        role = d.get("role") if d.get("role") in ("admin", "readonly") else "readonly"
        if q("SELECT 1 FROM users WHERE username=?", (u,), one=True):
            raise ApiError("Username already exists", 409)
        uid = x("INSERT INTO users(username,pw_hash,role,created) VALUES(?,?,?,?)",
                (u, generate_password_hash(d["password"]), role, now()))
        audit("create", f"user {u}", role)
        return jsonify(id=uid), 201
    rows = q("SELECT id,username,role,created FROM users " +
             ("ORDER BY username" if g.user["role"] == "admin" else "WHERE id=?"),
             () if g.user["role"] == "admin" else (g.user["id"],))
    seen = last_logins()
    for r in rows:
        r["last_login"] = seen.get(r["username"])
    return jsonify(rows)


@bp.route("/api/v1/users/<int:uid>", methods=["PUT", "DELETE"])
@auth
def api_user(uid):
    u = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        raise ApiError("User not found", 404)
    if request.method == "DELETE":
        if uid == g.user["id"]:
            raise ApiError("You cannot delete your own account")
        x("DELETE FROM users WHERE id=?", (uid,))
        audit("delete", f"user {u['username']}")
        return jsonify(ok=True)
    d = body()
    if d.get("password"):
        if len(d["password"]) < 8:
            raise ApiError("Password must be at least 8 characters")
        x("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(d["password"]), uid))
    if d.get("role") in ("admin", "readonly") and uid != g.user["id"]:
        x("UPDATE users SET role=? WHERE id=?", (d["role"], uid))
    audit("update", f"user {u['username']}")
    return jsonify(ok=True)
