"""Salinan file yang ditulis Diddy, deteksi drift, dan pemulihannya."""

import datetime
import hashlib
import os
import shutil

from ..audit import audit
from ..config import C, DRY
from ..core.util import now, run
from ..db.connection import q, state_get, x


def record_file(path, text):
    """Simpan salinan file yang baru ditulis, sebagai acuan deteksi drift."""
    x("INSERT OR REPLACE INTO deployed(path,sha,content,ts) VALUES(?,?,?,?)",
      (path, hashlib.sha256(text.encode()).hexdigest(), text, now()))


def drift_report():
    """Bandingkan file yang dipakai service dengan salinan terakhir yang ditulis Diddy."""
    items = []
    for row in q("SELECT path,sha,ts FROM deployed ORDER BY path"):
        try:
            cur = open(row["path"]).read()
        except FileNotFoundError:
            items.append({"path": row["path"], "state": "missing", "deployed_at": row["ts"], "detail": "file hilang"})
            continue
        except PermissionError as e:
            items.append({"path": row["path"], "state": "unknown", "deployed_at": row["ts"], "detail": str(e)})
            continue
        if hashlib.sha256(cur.encode()).hexdigest() != row["sha"]:
            try:
                mt = datetime.datetime.fromtimestamp(os.path.getmtime(row["path"])).strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                mt = "?"
            items.append({"path": row["path"], "state": "modified", "deployed_at": row["ts"],
                          "detail": f"diubah di luar Diddy, terakhir {mt}"})
        else:
            items.append({"path": row["path"], "state": "ok", "deployed_at": row["ts"], "detail": ""})
    return items


def drift_repair(items=None):
    """Kembalikan file yang berubah ke isi terakhir yang di-deploy, lalu reload service."""
    items = items if items is not None else drift_report()
    bad = [i for i in items if i["state"] in ("missing", "modified")]
    fixed = []
    for i in bad:
        row = q("SELECT content FROM deployed WHERE path=?", (i["path"],), one=True)
        if not row:
            continue
        os.makedirs(os.path.dirname(i["path"]) or ".", exist_ok=True)
        if i["state"] == "modified":
            shutil.copy2(i["path"], i["path"] + ".drift.bak")
        tmp = i["path"] + ".tmp"
        open(tmp, "w").write(row["content"])
        os.replace(tmp, i["path"])
        fixed.append(i["path"])
    if fixed and not DRY and state_get("services_stopped") != "1":   # jangan nyalakan service yang dihentikan
        run(C["dns_reload_cmd"])
        if any("kea" in f for f in fixed):
            run(["systemctl", "restart", C["dhcp_service"]], timeout=30)
        if any("dnsdist" in f for f in fixed):
            run(["systemctl", "restart", C["dnsdist_service"]], timeout=30)
    if fixed:
        audit("drift-repair", "services", f"{len(fixed)} file dikembalikan: " + ", ".join(fixed))
    return fixed
