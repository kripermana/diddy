"""Thread latar: refresh DDNS dan pemeriksaan drift."""

import time

from .audit import audit
from .config import C
from .core.log import log
from .core.runtime import LAST_DDNS, LAST_DRIFT
from .core.util import now
from .deploy.drift import drift_repair, drift_report
from .dns.ddns import ddns_refresh
from .dns.errlog import collect_errors
from .metrics import collect


def background_worker(app):
    """Satu thread latar untuk tugas terjadwal yang berdiri sendiri: metrik, DDNS, dan cek drift."""
    iv = int(C["ddns_refresh_interval"])
    miv = int(C["metrics_interval"])
    div = int(C["drift_check_interval"])
    auto = C.getboolean("drift_auto_repair")
    log.info("background thread started (DDNS %s, drift check %s, auto repair %s)",
             f"{iv}s" if iv > 0 else "off", f"{div}s" if div > 0 else "off", auto)
    last_ddns = last_drift = time.time()
    last_metrics = 0.0
    while True:
        time.sleep(5)
        try:
            with app.app_context():
                if miv > 0 and time.time() - last_metrics >= miv:
                    last_metrics = time.time()
                    collect()
                    try:
                        collect_errors()
                    except Exception as e:  # log error DNS tidak boleh menghentikan worker
                        log.warning("dns errors: %s", e)
                if iv > 0 and time.time() - last_ddns >= iv:
                    last_ddns = time.time()
                    ch = ddns_refresh()
                    LAST_DDNS["ts"] = now()
                    LAST_DDNS["changed"] = ch
                    if ch:
                        log.info("DDNS: updated zone(s) %s", ", ".join(ch))
                if div > 0 and time.time() - last_drift >= div:
                    last_drift = time.time()
                    items = drift_report()
                    bad = [i for i in items if i["state"] in ("missing", "modified")]
                    LAST_DRIFT.update(ts=now(), items=items, repaired=[])
                    if bad:
                        log.warning("drift: %s", ", ".join(i["path"] for i in bad))
                        audit("drift-detected", "services",
                              ", ".join(f"{i['path']} ({i['state']})" for i in bad))
                        if auto:
                            LAST_DRIFT["repaired"] = drift_repair(items)
        except Exception as e:  # never let the thread die
            log.warning("background task failed: %s", e)
