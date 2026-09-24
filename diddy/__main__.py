"""Command line Diddy.

    python -m diddy serve                      jalankan web UI + API
    python -m diddy deploy [--force]           deploy dari CLI (exit 2 bila ada perubahan tertunda)
    python -m diddy drift [--repair]           lihat / perbaiki drift config service
    python -m diddy reset-password USER [PW]   reset password user
    python -m diddy migrate-sqlite FILE [--force] [--src-prefix=xxx_]
    python -m diddy rebase-paths OLD NEW          ganti awalan path acuan drift (dipakai migrasi)
    python -m diddy stats                         jumlah objek per tabel
    python -m diddy cache-stats                   statistik cache DNS BIND
    python -m diddy cache-flush [NAMA] [--tree]   hapus cache DNS: semua, satu nama, atau nama + turunannya
    python -m diddy cache-lookup NAMA [TIPE]      lihat isi cache untuk satu nama (tanpa resolusi baru)
"""
import hashlib
import logging
import secrets
import sys
import threading

from flask import g
from werkzeug.security import generate_password_hash

from .config import C, DRY
from .version import NAME, SLOGAN, VERSION

CLI_USER = {"id": 0, "username": "cli", "role": "admin"}


def _app():
    from .web import create_app
    return create_app()


def cmd_serve():
    from .worker import background_worker
    app = _app()
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    host, port = C["listen"], C.getint("port")
    print(f"[diddy] v{VERSION} listening on http://{host}:{port} (dry_run={DRY})", flush=True)
    if any(int(C[k]) > 0 for k in ("ddns_refresh_interval", "drift_check_interval", "metrics_interval")):
        threading.Thread(target=background_worker, args=(app,), daemon=True).start()
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port)
    return 0


def cmd_deploy(force=False):
    from .db import state_get
    from .deploy.pipeline import run_deploy
    with _app().app_context():
        g.user = CLI_USER
        if state_get("pending") == "1" and not force:
            print("Ada perubahan yang belum di-deploy dari UI. Deploy otomatis dilewati supaya\n"
                  "perubahan yang belum siap tidak ikut terdorong. Jalankan Deploy dari UI,\n"
                  "atau ulangi perintah ini dengan --force.")
            return 2
        result, _status = run_deploy()
        for st in result.get("report", []):
            print(("  OK   " if st["ok"] else "  GAGAL ") + st["step"])
            if st["output"] and not st["ok"]:
                print("        " + st["output"][:400].replace("\n", "\n        "))
        print("Deploy berhasil" if result.get("ok") else "Deploy gagal, tidak ada file service yang diubah")
        return 0 if result.get("ok") else 1


def cmd_drift(repair=False):
    from .deploy.drift import drift_repair, drift_report
    with _app().app_context():
        g.user = CLI_USER
        items = drift_report()
        bad = [i for i in items if i["state"] in ("missing", "modified")]
        for i in items:
            print(f"  {i['state']:<9} {i['path']}" + (f"  ({i['detail']})" if i["detail"] else ""))
        if not items:
            print("  belum ada file yang pernah di-deploy")
        if bad and repair:
            print("Mengembalikan:", ", ".join(drift_repair(items)))
            return 0
        return 1 if bad else 0


def cmd_reset_password(user, pw=None):
    from .db import connect, init_db, sql
    init_db()
    pw = pw or secrets.token_urlsafe(9)
    con = connect()
    cur = con.cursor()
    cur.execute(sql("UPDATE users SET pw_hash=? WHERE username=?"), (generate_password_hash(pw), user))
    n = cur.rowcount
    con.commit()
    print(f"password for {user}: {pw}" if n else f"user {user} not found")
    return 0 if n else 1


def cmd_stats():
    """Jumlah objek per tabel. Dipakai upgrade.sh untuk memastikan data tetap utuh setelah migrasi."""
    from .db import q
    with _app().app_context():
        parts = []
        for t in ("zones", "records", "networks", "ranges", "hosts", "forwarders", "users"):
            parts.append(f"{t}={q(f'SELECT COUNT(*) AS c FROM {t}', one=True)['c']}")
        print(" ".join(parts))
    return 0


def cmd_rebase_paths(old, new):
    """Ganti awalan path pada acuan drift, dipakai saat direktori config dipindah (LiteDDI -> Diddy)."""
    from .db import q, x
    with _app().app_context():
        g.user = CLI_USER
        n = 0
        for row in q("SELECT path, content, ts FROM deployed"):
            if old not in row["path"] and old not in row["content"]:
                continue
            path, content = row["path"].replace(old, new), row["content"].replace(old, new)
            x("DELETE FROM deployed WHERE path=?", (row["path"],))
            x("INSERT OR REPLACE INTO deployed(path,sha,content,ts) VALUES(?,?,?,?)",
              (path, hashlib.sha256(content.encode()).hexdigest(), content, row["ts"]))
            n += 1
        print(f"acuan drift diperbarui: {n} file ({old} -> {new})")
        return 0


def cmd_cache_stats():
    from .dns.cache import cache_stats
    s = cache_stats()
    if not s["available"]:
        print(f"Statistik cache tidak tersedia dari {s['url']}: {s['error']}")
        return 1
    r = s["rrsets"]
    ratio = "-" if s["hit_ratio"] is None else f"{s['hit_ratio']}%"
    mem = "-" if s["memory_in_use"] is None else f"{s['memory_in_use'] / 1048576:.1f} MB"
    print(f"  hit ratio      {ratio} ({s['query_hits']} hit, {s['query_misses']} miss sejak BIND start)")
    print(f"  RRset          {r['total']} (positif {r['positive']}, negatif {r['negative']}, stale {r['stale']})")
    print(f"  memori         {mem}")
    print(f"  dibuang        {s['evicted_lru'] if s['evicted_lru'] is not None else '-'} (LRU), "
          f"{s['expired_ttl'] if s['expired_ttl'] is not None else '-'} (TTL habis)")
    if s["types"]:
        print("  tipe terbanyak " + ", ".join(f"{t}={n}" for t, n in s["types"]))
    return 0


def cmd_cache_flush(name, tree):
    from .core.errors import ApiError
    from .dns.cache import cache_flush
    with _app().app_context():
        g.user = CLI_USER
        try:
            r = cache_flush(name, tree)
        except ApiError as e:
            print(f"Gagal: {e}")
            return 1
        print(f"{r['command']}: {r['output']}")
        return 0


def cmd_cache_lookup(name, rtype):
    from .core.errors import ApiError
    from .dns.cache import cache_lookup
    try:
        r = cache_lookup(name, rtype)
    except ApiError as e:
        print(f"Gagal: {e}")
        return 1
    src = "zona authoritative" if r["authoritative"] else "cache" if r["cached"] else "tidak ada di cache"
    print(f"{r['name']} {r['type']}: {r['status']}, {src}")
    for rec in r["records"]:
        print(f"  {rec['name']:<40} {rec['ttl']:>7} {rec['type']:<6} {rec['value']}")
    return 0


def cmd_migrate(path, force, src_prefix):
    from .db import init_db, migrate_sqlite
    init_db()
    migrate_sqlite(path, force, src_prefix)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "serve"
    if cmd == "serve":
        return cmd_serve()
    if cmd == "deploy":
        return cmd_deploy("--force" in argv)
    if cmd == "drift":
        return cmd_drift("--repair" in argv)
    if cmd == "reset-password" and len(argv) >= 2:
        return cmd_reset_password(argv[1], argv[2] if len(argv) > 2 else None)
    if cmd == "migrate-sqlite" and len(argv) >= 2:
        src_pfx = next((a.split("=", 1)[1] for a in argv if a.startswith("--src-prefix=")), "")
        return cmd_migrate(argv[1], "--force" in argv, src_pfx)
    if cmd == "stats":
        return cmd_stats()
    if cmd == "rebase-paths" and len(argv) >= 3:
        return cmd_rebase_paths(argv[1], argv[2])
    if cmd == "cache-stats":
        return cmd_cache_stats()
    if cmd == "cache-flush":
        args = [a for a in argv[1:] if not a.startswith("--")]
        return cmd_cache_flush(args[0] if args else None, "--tree" in argv)
    if cmd == "cache-lookup" and len(argv) >= 2:
        return cmd_cache_lookup(argv[1], argv[2] if len(argv) > 2 else "A")
    if cmd in ("version", "--version", "-V"):
        print(f"{NAME} {VERSION} - {SLOGAN}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
