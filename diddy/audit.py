"""Audit log dan penanda perubahan tertunda."""

import json

from flask import g

from .core.util import now
from .db.connection import state_set, x


def audit(action, obj, detail=""):
    user = g.get("user") or {}
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    x("INSERT INTO audit(ts,username,action,object,detail) VALUES(?,?,?,?,?)",
      (now(), user.get("username", "system"), action, obj[:500], detail[:60000]))


def changed(action, obj, detail=""):
    audit(action, obj, detail)
    state_set("pending", "1")
