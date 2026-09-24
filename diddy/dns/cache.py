"""Cache DNS BIND: pengaturan cache, statistik, lookup isi cache, dan flush.

Pengaturan (max-cache-size, max-cache-ttl, max-ncache-ttl) disimpan bersama setting resolver dan baru
berlaku setelah deploy, seperti perubahan config lain. Flush dan lookup adalah operasi langsung ke BIND
yang sedang berjalan (rndc / dig), jadi tidak menandai perubahan tertunda. Flush tetap dicatat di audit log.
"""

import ipaddress
import json
import re
import shutil
import urllib.request

from ..audit import audit
from ..config import C, DRY
from ..core.errors import ApiError
from ..core.util import run, valid_fqdn

CACHE_DEFAULTS = {"max_cache_size": "", "max_cache_ttl": None, "max_ncache_ttl": None}
MAX_TTL = 604800   # 7 hari: BIND memotong max-ncache-ttl di sini, batas yang sama dipakai untuk max-cache-ttl
MIN_SIZE = 2 * 1024 * 1024   # BIND mengabaikan max-cache-size di bawah 2 MB
_SIZE_RE = re.compile(r"^(\d+)([kmg]?)$")
_PCT_RE = re.compile(r"^(\d{1,3})%$")
_MULT = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
LOOKUP_TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "PTR", "SRV", "TXT", "SOA", "CAA", "DS", "DNSKEY"]


# ------------------------------------------------------------------ pengaturan
def _ttl(v, label, minimum):
    if v in (None, ""):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ApiError(f"{label} harus berupa angka (detik)")
    if not minimum <= v <= MAX_TTL:
        raise ApiError(f"{label} harus {minimum} sampai {MAX_TTL} detik (7 hari), atau kosong untuk default BIND")
    return v


def _size(v):
    s = str(v or "").strip().lower().replace(" ", "")
    if s in ("", "default"):
        return ""
    if s == "unlimited":
        return s
    m = _PCT_RE.match(s)
    if m:
        if not 1 <= int(m.group(1)) <= 100:
            raise ApiError("Max cache size dalam persen harus 1% sampai 100% dari RAM")
        return f"{int(m.group(1))}%"
    m = _SIZE_RE.match(s)
    if not m:
        raise ApiError(f"Max cache size tidak valid: '{v}'. Contoh: 512M, 2G, 50%, unlimited, atau kosong")
    if int(m.group(1)) * _MULT[m.group(2)] < MIN_SIZE:
        raise ApiError("Max cache size minimal 2M; nilai lebih kecil diabaikan BIND")
    return f"{int(m.group(1))}{m.group(2).upper()}"


def validate_cache_settings(d, base=None):
    """Validasi pengaturan cache. Kunci yang tidak dikirim memakai nilai dari `base` (setting tersimpan),
    supaya form resolver yang tidak memuat field cache tidak menghapus pengaturan cache."""
    base = base or {}
    v = {k: d[k] if k in d else base.get(k, default) for k, default in CACHE_DEFAULTS.items()}
    return {"max_cache_size": _size(v["max_cache_size"]),
            "max_cache_ttl": _ttl(v["max_cache_ttl"], "Max cache TTL", 1),
            "max_ncache_ttl": _ttl(v["max_ncache_ttl"], "Max negative cache TTL", 0)}


def render_cache_options(cfg):
    """Baris options{} BIND untuk cache. Kosong = default BIND, tidak ditulis."""
    out = []
    if cfg.get("max_cache_size"):
        out.append(f"max-cache-size {cfg['max_cache_size']};")
    if cfg.get("max_cache_ttl") is not None:
        out.append(f"max-cache-ttl {int(cfg['max_cache_ttl'])};")
    if cfg.get("max_ncache_ttl") is not None:
        out.append(f"max-ncache-ttl {int(cfg['max_ncache_ttl'])};")
    return out


# ------------------------------------------------------------------ statistik
def fetch_bind_json(path):
    with urllib.request.urlopen(C["bind_stats_url"].rstrip("/") + path, timeout=3) as r:
        return json.loads(r.read())


def cache_counters(server):
    """Counter kumulatif cache dari JSON statistics-channel, dijumlah untuk semua view kecuali _bind.

    Mengembalikan None bila JSON tidak memuat statistik cache (mis. versi BIND lama).
    """
    cs, rr, views = {}, {}, []
    for name, v in (server.get("views") or {}).items():
        res = (v or {}).get("resolver") or {}
        if name == "_bind" or not (res.get("cachestats") or res.get("cache")):
            continue
        views.append(name)
        for k, val in (res.get("cachestats") or {}).items():
            if isinstance(val, (int, float)):
                cs[k] = cs.get(k, 0) + int(val)
        for k, val in (res.get("cache") or {}).items():
            if isinstance(val, (int, float)):
                rr[k] = rr.get(k, 0) + int(val)
    if not views:
        return None
    return {"views": views, "stats": cs, "rrsets": rr}


def summarize(counters):
    """Ringkasan untuk UI: hit ratio sejak BIND start, jumlah RRset per jenis, memori, entri yang dibuang."""
    cs, rr = counters["stats"], counters["rrsets"]
    hits, misses = cs.get("QueryHits", 0), cs.get("QueryMisses", 0)
    pos, neg, stale, types = 0, 0, 0, {}
    for k, v in rr.items():
        if k.startswith("~"):          # ancient: sudah kedaluwarsa, menunggu dibersihkan
            continue
        if k.startswith("#"):          # stale
            stale += v
        elif k.startswith("!") or k == "NXDOMAIN":   # jawaban negatif (NXRRSET / NXDOMAIN)
            neg += v
        else:
            pos += v
            types[k] = types.get(k, 0) + v
    mem = [cs[k] for k in ("TreeMemInUse", "HeapMemInUse") if k in cs]
    return {"views": counters["views"],
            "query_hits": hits, "query_misses": misses,
            "hit_ratio": round(hits * 100 / (hits + misses), 1) if hits + misses else None,
            "nodes": cs.get("CacheNodes"),
            "memory_in_use": sum(mem) if mem else None,
            "evicted_lru": cs.get("DeleteLRU"), "expired_ttl": cs.get("DeleteTTL"),
            "rrsets": {"total": pos + neg + stale, "positive": pos, "negative": neg, "stale": stale},
            "types": sorted(types.items(), key=lambda kv: -kv[1])[:12]}


def cache_stats():
    """Statistik cache BIND saat ini. Tidak pernah melempar error: kegagalan dilaporkan di hasil."""
    try:
        d = fetch_bind_json("/json/v1/server")
        counters = cache_counters(d)
        if counters is None:   # sebagian versi hanya menaruh statistik view di dokumen lengkap
            d = fetch_bind_json("/json/v1")
            counters = cache_counters(d)
    except Exception as e:
        return {"available": False, "url": C["bind_stats_url"], "error": str(e)[:200]}
    if counters is None:
        return {"available": False, "url": C["bind_stats_url"],
                "error": "statistics-channel BIND tidak memuat statistik cache (recursion mati atau versi BIND lama)"}
    out = summarize(counters)
    out.update(available=True, url=C["bind_stats_url"], boot=d.get("boot-time"))
    return out


# ------------------------------------------------------------------ operasi langsung
def cache_flush(name=None, tree=False):
    """Hapus isi cache BIND: seluruhnya, satu nama (flushname), atau satu nama beserta semua turunannya
    (flushtree). Operasi langsung ke BIND yang berjalan, bukan perubahan config."""
    name = valid_fqdn(name, "domain name") if (name or "").strip() else None
    if name:
        cmd = ["rndc", "flushtree" if tree else "flushname", name]
        target = f"dns cache {name}" + (" (tree)" if tree else "")
    else:
        cmd = ["rndc", "flush"]
        target = "dns cache (all)"
    if DRY:
        audit("cache-flush", target, "dry_run: rndc tidak dijalankan")
        return {"ok": True, "command": " ".join(cmd), "output": "dry_run = true: rndc tidak dijalankan"}
    if not shutil.which("rndc"):
        raise ApiError("rndc tidak ditemukan di server ini (paket bind9-utils)", 503)
    ok, out = run(cmd, timeout=30)
    if not ok:
        raise ApiError(f"rndc gagal: {out[:300]}", 502)
    audit("cache-flush", target, out[:500])
    return {"ok": True, "command": " ".join(cmd), "output": out or "ok"}


def _lookup_target(name, rtype):
    """IP diterima langsung untuk lookup PTR: 10.1.2.3 -> 3.2.1.10.in-addr.arpa."""
    try:
        return ipaddress.ip_address((name or "").strip()).reverse_pointer, "PTR"
    except ValueError:
        return valid_fqdn(name, "domain name"), rtype


def cache_lookup(name, rtype="A"):
    """Apa yang diingat BIND untuk satu nama, tanpa memicu resolusi baru (dig +norecurse ke BIND lokal).

    TTL yang tampil adalah sisa waktu di cache. Flag `aa` berarti jawabannya dari zona authoritative
    milik server ini, bukan dari cache.
    """
    rtype = (rtype or "A").strip().upper()
    if rtype not in LOOKUP_TYPES:
        raise ApiError(f"Tipe tidak didukung: {rtype}. Pilihan: {', '.join(LOOKUP_TYPES)}")
    name, rtype = _lookup_target(name, rtype)
    if not shutil.which("dig"):
        raise ApiError("dig tidak ditemukan di server ini (paket bind9-dnsutils)", 503)
    ok, out = run(["dig", "+norecurse", "+noall", "+comments", "+answer", "+time=2", "+tries=1",
                   "@" + C["bind_local_addr"], name, rtype], timeout=10)
    m = re.search(r"status: (\w+)", out)
    if not m:
        raise ApiError(f"BIND di {C['bind_local_addr']} tidak menjawab: {out[:300]}", 502)
    fl = re.search(r"flags: ([a-z ]*);", out)
    flags = fl.group(1).split() if fl else []
    recs = []
    for line in out.splitlines():
        p = line.split(None, 4)
        if line.startswith(";") or len(p) < 5 or not p[1].isdigit():
            continue
        recs.append({"name": p[0].rstrip(".") or ".", "ttl": int(p[1]), "type": p[3], "value": p[4]})
    return {"name": name, "type": rtype, "status": m.group(1), "authoritative": "aa" in flags,
            "cached": bool(recs) and "aa" not in flags, "records": recs}
