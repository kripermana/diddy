"""Audit log dan penanda perubahan tertunda."""

import json
import re

from flask import g

from .core.errors import ApiError
from .core.util import now
from .db.connection import q, state_set, x


def audit(action, obj, detail=""):
    user = g.get("user") or {}
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    x("INSERT INTO audit(ts,username,action,object,detail) VALUES(?,?,?,?,?)",
      (now(), user.get("username", "system"), action, obj[:500], detail[:60000]))


def changed(action, obj, detail=""):
    audit(action, obj, detail)
    state_set("pending", "1")


def audit_filters(args):
    """Klausa WHERE dari parameter filter audit: q (teks), user, action, since/until (YYYY-MM-DD)."""
    where, params = [], []
    text = (args.get("q") or "").strip().lower()
    if text:
        like = f"%{text}%"
        where.append("(lower(username) LIKE ? OR lower(action) LIKE ? OR lower(object) LIKE ? OR lower(detail) LIKE ?)")
        params += [like] * 4
    for col in ("user", "action"):
        v = (args.get(col) or "").strip()
        if v:
            where.append(("username" if col == "user" else "action") + "=?")
            params.append(v)
    for k, op, suffix in (("since", ">=", " 00:00:00"), ("until", "<=", " 23:59:59")):
        v = (args.get(k) or "").strip()
        if v:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise ApiError(f"{k} harus berformat YYYY-MM-DD")
            where.append(f"ts {op} ?")
            params.append(v + suffix)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def audit_list(args, limit, offset=0):
    sql, params = audit_filters(args)
    return q(f"SELECT * FROM audit{sql} ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset])


def audit_count(args):
    sql, params = audit_filters(args)
    return q(f"SELECT COUNT(*) c FROM audit{sql}", params, one=True)["c"]


def audit_entry(aid):
    """Satu entri audit lengkap. Detail berformat JSON ikut dikirim sebagai objek (`detail_json`)."""
    e = q("SELECT * FROM audit WHERE id=?", (aid,), one=True)
    if not e:
        raise ApiError("Audit entry not found", 404)
    try:
        e["detail_json"] = json.loads(e["detail"]) if (e["detail"] or "")[:1] in "{[" and e["detail"] else None
    except ValueError:
        e["detail_json"] = None
    return e


def audit_facets():
    """Daftar user dan action yang pernah tercatat, untuk pilihan filter."""
    return {"users": [r["username"] for r in q("SELECT DISTINCT username FROM audit ORDER BY username")],
            "actions": [r["action"] for r in q("SELECT DISTINCT action FROM audit ORDER BY action")],
            "total": q("SELECT COUNT(*) c FROM audit", one=True)["c"]}


def last_logins():
    """Waktu login terakhir tiap user, diambil dari audit log."""
    return {r["object"]: r["t"] for r in q("SELECT object, MAX(ts) t FROM audit WHERE action='login' GROUP BY object")}
