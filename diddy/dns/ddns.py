"""DDNS: record A/PTR dari lease DHCP, disisipkan ke zona campuran."""

import datetime
import hashlib
import ipaddress
import os
import re
import shutil

from ..config import C, DRY
from ..core.log import log
from ..core.runtime import LAST_DDNS
from ..core.util import run
from ..db.connection import q, state_get, x
from ..deploy.drift import record_file
from ..dhcp.leases import read_leases
from .zones import DYN_BEGIN, DYN_END, LEGACY_DYN_BEGIN, find_zone, fmt_rr, rel
from ..ipam.networks import all_nets, smallest_net


def dyn_label(hostname):
    """Turn a client-supplied host name into one safe DNS label."""
    n = (hostname or "").strip().lower().split(".")[0]
    return re.sub(r"[^a-z0-9-]", "-", n).strip("-")[:63].strip("-")


def dynamic_records(zones=None, leases=None):
    """A and PTR records built from live DHCP leases, for networks with DDNS enabled.

    Static objects always win: a lease is skipped when its IP belongs to a host object or when the
    name it would take is already used by a host or a manual record.
    """
    zones = q("SELECT * FROM zones") if zones is None else zones
    nets = [n for n in all_nets() if n["ddns_enabled"]]
    out = {}
    if not nets:
        return out
    leases = read_leases() if leases is None else leases
    static_ips, static_names = set(), set()
    for h in q("SELECT * FROM hosts"):
        static_ips.add(h["ip"])
        if h["configure_dns"]:
            static_names.add(h["fqdn"])
            static_names.add(ipaddress.ip_address(h["ip"]).reverse_pointer)
    for r in q("SELECT r.name, z.name AS zone FROM records r JOIN zones z ON z.id=r.zone_id"):
        static_names.add(r["zone"] if r["name"] == "@" else f"{r['name']}.{r['zone']}")
    ttl = int(C["ddns_ttl"])
    taken = set()
    for lease in sorted(leases, key=lambda lease: lease.get("expires") or "", reverse=True):
        if lease["ip"] in static_ips:
            continue
        net = smallest_net(lease["ip"], nets)
        if not net:
            continue
        label = dyn_label(lease.get("hostname"))
        if not label:
            continue
        fq = f"{label}.{net['ddns_domain'] or net['domain_name']}"
        if fq in static_names or fq in taken:
            continue
        z = find_zone(fq, False, zones)
        if not z:
            continue
        taken.add(fq)
        note = f"DHCP lease {lease['mac']} until {lease.get('expires', '')}".strip()
        out.setdefault(z["id"], []).append(
            {"id": f"lease-{lease['ip']}", "zone_id": z["id"], "name": rel(fq, z["name"]), "type": "A",
             "value": lease["ip"], "ttl": ttl, "comment": note, "source": "lease"})
        ptr = ipaddress.ip_address(lease["ip"]).reverse_pointer
        rz = find_zone(ptr, True, zones)
        if rz and ptr not in static_names:
            out.setdefault(rz["id"], []).append(
                {"id": f"lease-ptr-{lease['ip']}", "zone_id": rz["id"], "name": rel(ptr, rz["name"]), "type": "PTR",
                 "value": fq, "ttl": ttl, "comment": note, "source": "lease"})
    return out


def ddns_refresh():
    """Rewrite only the dynamic block inside already deployed zone files, then reload BIND.

    The static part of each file is left untouched, so changes that are still pending in the UI
    are never pushed to the live services by accident.
    """
    zdir = os.path.join(C["bind_dir"], "zones")
    if not os.path.isdir(zdir):
        return []
    zs = q("SELECT * FROM zones")
    dynmap = dynamic_records(zs)
    today = int(datetime.date.today().strftime("%Y%m%d")) * 100 + 1
    changed, skipped = [], []
    for z in zs:
        path = os.path.join(zdir, "db." + z["name"])
        if not os.path.exists(path):
            continue
        cur = open(path).read()
        begin = DYN_BEGIN if DYN_BEGIN in cur else LEGACY_DYN_BEGIN
        if begin not in cur or DYN_END not in cur:
            continue
        # Jangan menumpuk DDNS di atas file yang sudah diubah orang di luar Diddy:
        # itu akan "mencuci" drift sungguhan menjadi seolah sah. Biarkan drift terlihat.
        snap = q("SELECT sha FROM deployed WHERE path=?", (path,), one=True)
        if snap and hashlib.sha256(cur.encode()).hexdigest() != snap["sha"]:
            skipped.append(z["name"])
            log.warning("DDNS: zone %s dilewati karena file-nya drift, perbaiki drift dulu", z["name"])
            continue
        block = "\n".join(fmt_rr(r["name"], r["ttl"], r["type"], r["value"]) for r in dynmap.get(z["id"], []))
        head, _, rest = cur.partition(begin)
        old, _, tail = rest.partition(DYN_END)
        if old.strip("\n") == block and begin == DYN_BEGIN:
            continue
        serial = max((z["serial"] or 0) + 1, today)
        head = re.sub(r"^(\s*)\d+( ; serial)", lambda m: f"{m.group(1)}{serial}{m.group(2)}", head, count=1,
                      flags=re.M)
        text = head + DYN_BEGIN + "\n" + (block + "\n" if block else "") + DYN_END + tail
        tmp = path + ".tmp"
        open(tmp, "w").write(text)
        if shutil.which("named-checkzone"):
            ok, out = run(["named-checkzone", z["name"], tmp])
            if not ok:
                os.remove(tmp)
                log.warning("DDNS: zone %s failed validation, keeping the old file: %s", z["name"], out)
                continue
        os.replace(tmp, path)
        record_file(path, text)   # perubahan oleh Diddy sendiri: acuan drift ikut diperbarui
        x("UPDATE zones SET serial=? WHERE id=?", (serial, z["id"]))
        changed.append(z["name"])
    LAST_DDNS["skipped"] = skipped
    if changed and not DRY and state_get("services_stopped") != "1":   # BIND sengaja dihentikan: cukup tulis file
        for name in changed:
            ok, out = run(["rndc", "reload", name]) if shutil.which("rndc") else (False, "")
            if not ok:
                run(C["dns_reload_cmd"])
                break
    return changed
