"""Pipeline deploy: render, validasi, tulis config, reload service."""

import json
import os
import shutil

from ..audit import audit
from ..config import C, DRY
from ..core.util import run
from ..db.connection import q, state_get, state_set, x
from .drift import record_file
from ..dhcp.kea import kea_command, render_kea
from ..dns.ddns import dynamic_records
from ..dns.resolver import dns_settings, render_dnsdist, render_options
from ..dns.zones import render_named_conf, render_zone


def build():
    zs = q("SELECT * FROM zones ORDER BY name")
    dynmap = dynamic_records(zs)
    rendered = [(z,) + render_zone(z, dynmap.get(z["id"])) for z in zs]
    cfg = dns_settings()
    dd = render_dnsdist(cfg) if cfg["upstream_mode"] == "encrypted" else None
    return zs, rendered, render_named_conf(zs), json.dumps(render_kea(), indent=2) + "\n", render_options(), dd


def run_deploy():
    """Jalankan seluruh pipeline deploy. Mengembalikan (hasil, kode_status_http)."""
    zs, rendered, named, kea, options, dd = build()
    report, ok = [], True

    def step(name, success, output=""):
        nonlocal ok
        report.append({"step": name, "ok": success, "output": output})
        ok = ok and success

    # Staging lives next to the real files, never in /tmp: the AppArmor profiles that Debian and
    # Ubuntu ship for kea-dhcp4 and named only allow reading their own directories.
    stage = os.path.join(C["bind_dir"], ".staging")
    kea_stage = os.path.join(os.path.dirname(C["kea_conf"]) or ".", ".diddy-staging.conf")
    dd_stage = os.path.join(os.path.dirname(C["dnsdist_conf"]) or ".", ".diddy-staging.conf")
    try:
        os.makedirs(stage, exist_ok=True)
        os.makedirs(os.path.dirname(C["kea_conf"]) or ".", exist_ok=True)
        # 1) validate everything in a staging dir before touching live config
        for z, text, _, _ in rendered:
            p = os.path.join(stage, "db." + z["name"])
            open(p, "w").write(text)
            if shutil.which("named-checkzone"):
                step(f"check zone {z['name']}", *run(["named-checkzone", z["name"], p]))
        pconf = os.path.join(stage, "named.conf.diddy")
        open(pconf, "w").write(named)
        popt = os.path.join(stage, "named.conf.options.diddy")
        open(popt, "w").write(options)
        if shutil.which("named-checkconf"):
            step("check named.conf.diddy", *run(["named-checkconf", pconf]))
            wrapper = os.path.join(stage, "named.conf.optcheck")
            open(wrapper, "w").write('options {\n    directory "/var/cache/bind";\n'
                                     f'    include "{popt}";\n}};\n')
            step("check resolver options", *run(["named-checkconf", wrapper]))
        open(kea_stage, "w").write(kea)
        os.chmod(kea_stage, 0o640)
        kea_bin = shutil.which(
            "kea-dhcp4") or ("/usr/sbin/kea-dhcp4" if os.path.exists("/usr/sbin/kea-dhcp4") else None)
        if kea_bin:
            kok, kout = run([kea_bin, "-t", kea_stage])
            if not kok and "Unable to open file" in kout:
                kout += (f"\n(file {kea_stage} ada dan bisa dibaca root; kalau pesan ini muncul di Ubuntu/Debian,"
                         " cek AppArmor: sudo dmesg | grep -i apparmor | grep kea)")
            step("check kea-dhcp4.conf", kok, kout)
        if dd is not None:
            os.makedirs(os.path.dirname(C["dnsdist_conf"]) or ".", exist_ok=True)
            open(dd_stage, "w").write(dd)
            if shutil.which("dnsdist"):
                step("check dnsdist.conf", *run(["dnsdist", "--check-config", "-C", dd_stage], timeout=30))
            else:
                step("check dnsdist.conf", False, "dnsdist tidak terpasang: 'apt install dnsdist'")
        if not ok:
            audit("deploy", "services", "validation failed")
            return {"ok": False, "report": report}, 422

        # 2) write live files
        zdir = os.path.join(C["bind_dir"], "zones")
        os.makedirs(zdir, exist_ok=True)
        keep = set()
        for z, text, serial, h in rendered:
            fn = "db." + z["name"]
            keep.add(fn)
            tmp = os.path.join(zdir, "." + fn + ".tmp")
            open(tmp, "w").write(text)
            os.replace(tmp, os.path.join(zdir, fn))
            record_file(os.path.join(zdir, fn), text)
        for fn in os.listdir(zdir):
            if fn.startswith("db.") and fn not in keep:
                os.remove(os.path.join(zdir, fn))
                x("DELETE FROM deployed WHERE path=?", (os.path.join(zdir, fn),))
        ncp = os.path.join(C["bind_dir"], "named.conf.diddy")
        open(ncp, "w").write(named)
        record_file(ncp, named)
        optf = C["bind_options_file"]
        os.makedirs(os.path.dirname(optf) or ".", exist_ok=True)
        open(optf, "w").write(options)
        record_file(optf, options)
        kp = C["kea_conf"]
        os.makedirs(os.path.dirname(kp), exist_ok=True)
        st = os.stat(kp) if os.path.exists(kp) else None
        if st:
            shutil.copy2(kp, kp + ".diddy.bak")
        tmp = kp + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as fh:
            fh.write(kea)
        if st:  # keep owner/group/mode, e.g. root:_kea 0640 (file holds the lease DB password)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except PermissionError:
                pass
            os.chmod(tmp, st.st_mode & 0o777)
        os.replace(tmp, kp)
        record_file(kp, kea)
        ndyn = sum(len(v) for v in dynamic_records([z for z, _, _, _ in rendered]).values())
        if dd is not None:
            ddp = C["dnsdist_conf"]
            if os.path.exists(ddp):
                shutil.copy2(ddp, ddp + ".diddy.bak")
            open(ddp, "w").write(dd)
            os.chmod(ddp, 0o644)
            record_file(ddp, dd)
        else:
            x("DELETE FROM deployed WHERE path=?", (C["dnsdist_conf"],))
        # buang acuan drift untuk file yang tidak lagi dikelola (zona dihapus, bind_dir pindah, dll)
        current = {os.path.join(zdir, "db." + z["name"]) for z, _, _, _ in rendered} | {ncp, optf, kp}
        if dd is not None:
            current.add(C["dnsdist_conf"])
        for row in q("SELECT path FROM deployed"):
            if row["path"] not in current:
                x("DELETE FROM deployed WHERE path=?", (row["path"],))
        step("write configuration files", True,
             f"{len(rendered)} zone(s) -> {zdir}; DHCP -> {kp}" + (f"; {ndyn} DHCP dynamic record(s)" if ndyn else ""))

        for z, _, serial, h in rendered:
            x("UPDATE zones SET serial=? WHERE id=?", (serial, z["id"]))
            state_set("zonehash:" + z["name"], h)

        # 3) reload services
        if DRY:
            step("reload services", True, "dry_run = true: services not reloaded")
        else:
            if dd is not None:   # proxy lebih dulu, supaya BIND tidak forward ke port mati
                svc = C["dnsdist_service"]
                eok, eout = run(["systemctl", "enable", "--now", svc], timeout=30)
                rok, rout = run(["systemctl", "restart", svc], timeout=30)
                step("reload DNS proxy (dnsdist)", eok and rok, (eout + " " + rout).strip() or "ok")
            elif state_get("dnsdist_enabled") == "1":
                run(["systemctl", "disable", "--now", C["dnsdist_service"]], timeout=30)
                step("stop DNS proxy (dnsdist)", True, "upstream kembali ke DNS biasa")
            state_set("dnsdist_enabled", "1" if dd is not None else "0")
            step("reload DNS (BIND)", *run(C["dns_reload_cmd"]))
            try:
                r = kea_command("config-reload")
                if r.get("result") != 0:
                    raise RuntimeError(r.get("text"))
                step("reload DHCP (Kea)", True, r.get("text", ""))
            except Exception as e:  # socket missing, Kea not running, etc.
                s_ok, out = run(C["kea_reload_fallback_cmd"])
                step("reload DHCP (Kea)", s_ok, f"control socket failed ({e}); fallback: {out or 'ok'}")
        if ok:
            state_set("pending", "0")
        audit("deploy", "services", "success" if ok else "completed with errors")
        return {"ok": ok, "report": report}, 200
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        for f in (kea_stage, dd_stage):
            if os.path.exists(f):
                os.remove(f)
