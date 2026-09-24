"""Membaca lease aktif dari Kea (MySQL atau memfile CSV)."""

import csv
import datetime
import time

from ..config import C, KEA_MYSQL
from ..core.log import log
from ..db.connection import mysql_connect


def read_leases():
    return read_leases_mysql() if KEA_MYSQL else read_leases_memfile()


def read_leases_mysql():
    """Active leases from Kea's MySQL lease backend (table lease4)."""
    try:
        con = mysql_connect(C["kea_db_host"], C["kea_db_port"], C["kea_db_user"], C["kea_db_password"],
                            C["kea_db_name"], timeout=5)
    except Exception as e:
        log.warning("Kea lease DB unreachable: %s", e)
        return []
    try:
        with con.cursor() as cur:
            cur.execute("SELECT INET_NTOA(address) AS ip, LOWER(HEX(hwaddr)) AS mac, hostname,"
                        " DATE_FORMAT(expire, '%Y-%m-%d %H:%i:%s') AS expires, subnet_id FROM lease4"
                        " WHERE state=0 AND valid_lifetime>0 AND expire>NOW()")
            out = []
            for r in cur.fetchall():
                m = r["mac"] or ""
                out.append({"ip": r["ip"], "mac": ":".join(m[i:i + 2] for i in range(0, len(m), 2)),
                            "hostname": (r["hostname"] or "").rstrip("."), "expires": r["expires"],
                            "subnet_id": r["subnet_id"]})
            return out
    except Exception as e:
        log.warning("Kea lease query failed: %s", e)
        return []
    finally:
        con.close()


def read_leases_memfile():
    """Active leases from Kea memfile CSV (later lines override earlier ones)."""
    path, t, out = C["kea_lease_file"], time.time(), {}
    for f in (path + ".2", path):
        try:
            with open(f, newline="") as fh:
                for row in csv.DictReader(fh):
                    a = row.get("address")
                    if not a:
                        continue
                    try:
                        exp = int(row.get("expire") or 0)
                        vl = int(row.get("valid_lifetime") or 0)
                    except ValueError:
                        continue
                    if exp > t and vl > 0 and (row.get("state") or "0") == "0":
                        out[a] = {"ip": a, "mac": (row.get("hwaddr") or "").lower(),
                                  "hostname": (row.get("hostname") or "").rstrip("."),
                                  "expires": datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S"),
                                  "subnet_id": row.get("subnet_id")}
                    else:
                        out.pop(a, None)
        except (FileNotFoundError, PermissionError):
            continue
    return list(out.values())
