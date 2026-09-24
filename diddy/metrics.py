"""Statistik untuk dashboard: sampel counter BIND dan Kea, disimpan per menit, diolah jadi grafik.

Sumber data
    DNS  : statistics-channel BIND (JSON), counter kumulatif sejak named start.
    DHCP : jumlah lease aktif dari Kea (MySQL/CSV), plus counter paket dari control socket Kea bila ada.

Counter kumulatif diubah menjadi selisih per interval. Restart named atau Kea (boot-time berubah atau
counter turun) dianggap reset, sehingga grafik tidak pernah bernilai negatif.
"""
import datetime
import ipaddress
import json
import time
import urllib.request

from .config import C
from .core.log import log
from .db.connection import q, x
from .dhcp.kea import kea_command
from .dns.cache import cache_counters
from .dhcp.leases import read_leases
from .ipam.networks import all_nets, smallest_net

DNS_NS_KEYS = ["QryAuthAns", "QryNoauthAns", "QryRecursion", "QryNXDOMAIN", "QrySERVFAIL", "QryFailure",
               "QryDropped", "RecQryRej", "QryUDP", "QryTCP", "Requestv4", "Requestv6"]
KEA_PKT_KEYS = ["pkt4-received", "pkt4-discover-received", "pkt4-offer-sent", "pkt4-request-received",
                "pkt4-ack-sent", "pkt4-nak-sent", "pkt4-release-received", "pkt4-decline-received",
                "pkt4-inform-received"]
RANGES = {"1h": (3600, 60), "6h": (21600, 300), "24h": (86400, 900), "7d": (604800, 7200)}
STATUS = {"ts": None, "dns": None, "dns_error": None, "kea": None, "kea_error": None, "last_prune": 0.0}


# ------------------------------------------------------------------ pengambilan sampel
def fetch_bind_stats():
    url = C["bind_stats_url"].rstrip("/") + "/json/v1/server"
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.loads(r.read())


def dns_sample():
    d = fetch_bind_stats()
    ns = d.get("nsstats") or {}
    total = (d.get("opcodes") or {}).get("QUERY")
    if total is None:
        total = ns.get("Requestv4", 0) + ns.get("Requestv6", 0)
    cc = cache_counters(d)
    return {"boot": d.get("boot-time"), "total": int(total),
            "rcodes": {k: int(v) for k, v in (d.get("rcodes") or {}).items() if v},
            "qtypes": {k: int(v) for k, v in (d.get("qtypes") or {}).items() if v},
            "ns": {k: int(ns.get(k, 0)) for k in DNS_NS_KEYS},
            "cache": {"hits": cc["stats"].get("QueryHits", 0), "misses": cc["stats"].get("QueryMisses", 0)}
            if cc else None}


def dhcp_sample():
    nets = [n for n in all_nets() if n["dhcp_enabled"]]
    per_net = {n["cidr"]: 0 for n in nets}
    active = 0
    for lease in read_leases():
        active += 1
        n = smallest_net(lease["ip"], nets)
        if n:
            per_net[n["cidr"]] += 1
    out = {"active": active, "per_net": per_net, "kea": None}
    try:
        r = kea_command("statistic-get-all")
        if r.get("result") == 0:
            a = r.get("arguments") or {}
            out["kea"] = {k: int(a[k][0][0]) for k in KEA_PKT_KEYS if k in a and a[k]}
            STATUS.update(kea=True, kea_error=None)
    except Exception as e:  # Kea tidak jalan / socket tidak ada: lease tetap tercatat
        STATUS.update(kea=False, kea_error=str(e)[:200])
    return out


def collect():
    """Ambil satu sampel DNS dan DHCP, simpan, dan buang sampel yang sudah lewat masa simpan."""
    ts = int(time.time())
    try:
        x("INSERT INTO metrics(ts,kind,data) VALUES(?,?,?)", (ts, "dns", json.dumps(dns_sample())))
        STATUS.update(dns=True, dns_error=None)
    except Exception as e:
        STATUS.update(dns=False, dns_error=str(e)[:200])
    try:
        x("INSERT INTO metrics(ts,kind,data) VALUES(?,?,?)", (ts, "dhcp", json.dumps(dhcp_sample())))
    except Exception as e:
        log.warning("metrics: sampel DHCP gagal: %s", e)
    STATUS["ts"] = ts
    if time.time() - STATUS["last_prune"] > 3600:
        STATUS["last_prune"] = time.time()
        keep = int(float(C["metrics_retention_days"]) * 86400)
        x("DELETE FROM metrics WHERE ts < ?", (ts - keep,))


# ------------------------------------------------------------------ pengolahan
def load(kind, since):
    """Sampel sejak `since`, ditambah satu sampel sebelumnya sebagai titik awal selisih."""
    prev = q("SELECT ts,data FROM metrics WHERE kind=? AND ts<? ORDER BY ts DESC LIMIT 1", (kind, since), one=True)
    rows = q("SELECT ts,data FROM metrics WHERE kind=? AND ts>=? ORDER BY ts", (kind, since))
    out = []
    for r in ([prev] if prev else []) + rows:
        try:
            d = json.loads(r["data"])
        except ValueError:
            continue
        d["ts"] = int(r["ts"])
        out.append(d)
    return out


def _delta(cur, prev, reset):
    if reset or cur < prev:
        return cur
    return cur - prev


def dns_deltas(samples):
    """Selisih per interval: (ts, detik, total, rcodes, qtypes, ns)."""
    out = []
    for p, c in zip(samples, samples[1:]):
        reset = c.get("boot") != p.get("boot")
        secs = max(1, c["ts"] - p["ts"])
        rc = {k: _delta(v, p["rcodes"].get(k, 0), reset) for k, v in c["rcodes"].items()}
        qt = {k: _delta(v, p["qtypes"].get(k, 0), reset) for k, v in c["qtypes"].items()}
        ns = {k: _delta(v, p["ns"].get(k, 0), reset) for k, v in c["ns"].items()}
        out.append((c["ts"], secs, _delta(c["total"], p["total"], reset), rc, qt, ns))
    return out


def bucketize(points, start, end, step, agg="sum"):
    """points: [(ts, nilai)] -> [(awal_bucket, nilai)] untuk setiap bucket, termasuk yang kosong."""
    first = start - start % step
    buckets = {b: [] for b in range(first, end + 1, step)}
    for ts, v in points:
        b = ts - ts % step
        if b in buckets:
            buckets[b].append(v)
    res = []
    for b in sorted(buckets):
        vals = buckets[b]
        if agg == "sum":
            res.append((b, sum(vals)))
        elif agg == "max":
            res.append((b, max(vals) if vals else None))
        else:
            res.append((b, round(sum(vals) / len(vals), 1) if vals else None))
    return res


def _range(name):
    return RANGES.get(name, RANGES["24h"])


def _hourly(points, now):
    start = now - 86400
    return [{"t": b, "v": v} for b, v in bucketize([p for p in points if p[0] >= start], start, now, 3600)]


def dns_report(range_name):
    now = int(time.time())
    span, step = _range(range_name)
    since = now - max(span, 86400)
    d = dns_deltas(load("dns", since))
    win = [r for r in d if r[0] >= now - span]
    per_ts = [(r[0], r[2]) for r in d]
    series = []
    for b, v in bucketize([(r[0], r[2]) for r in win], now - span, now, step):
        series.append({"t": b, "v": round(v / step, 2)})   # rata-rata query per detik
    peak = max((r[2] / r[1] for r in win), default=0)
    total_range = sum(r[2] for r in win)
    rcodes, qtypes, ns = {}, {}, {}
    for r in win:
        for src, dst in ((r[3], rcodes), (r[4], qtypes), (r[5], ns)):
            for k, v in src.items():
                dst[k] = dst.get(k, 0) + v
    midnight = int(datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp())
    recent = [r for r in d if r[0] >= now - 300]
    cur_qps = sum(r[2] for r in recent) / max(1, sum(r[1] for r in recent)) if recent else 0
    cap = float(C["dns_capacity_qps"]) or 1
    return {
        "range": range_name, "step": step, "series": series, "hourly": _hourly(per_ts, now),
        "totals": {"range": total_range,
                   "last_hour": sum(r[2] for r in d if r[0] >= now - 3600),
                   "last_24h": sum(r[2] for r in d if r[0] >= now - 86400),
                   "today": sum(r[2] for r in d if r[0] >= midnight),
                   "avg_qps": round(total_range / span, 2), "peak_qps": round(peak, 2),
                   "current_qps": round(cur_qps, 2)},
        "utilization": {"current_qps": round(cur_qps, 2), "capacity_qps": cap,
                        "pct": round(min(100.0, cur_qps * 100 / cap), 1)},
        "rcodes": dict(sorted(((k, v) for k, v in rcodes.items() if v), key=lambda kv: -kv[1])),
        "qtypes": dict(sorted(((k, v) for k, v in qtypes.items() if v), key=lambda kv: -kv[1])),
        "answers": {"authoritative": ns.get("QryAuthAns", 0), "non_authoritative": ns.get("QryNoauthAns", 0),
                    "recursion": ns.get("QryRecursion", 0), "refused": ns.get("RecQryRej", 0),
                    "udp": ns.get("QryUDP", 0), "tcp": ns.get("QryTCP", 0)},
        "zones": zone_record_counts(),
        "source": source_status("dns"),
    }


def cache_report(range_name):
    """Hit ratio cache BIND per bucket, dari selisih QueryHits/QueryMisses di sampel DNS."""
    now = int(time.time())
    span, step = _range(range_name)
    samples = [s for s in load("dns", now - span) if s.get("cache")]
    hits, misses = [], []
    for p, c in zip(samples, samples[1:]):
        reset = c.get("boot") != p.get("boot")
        hits.append((c["ts"], _delta(c["cache"]["hits"], p["cache"]["hits"], reset)))
        misses.append((c["ts"], _delta(c["cache"]["misses"], p["cache"]["misses"], reset)))
    series = [{"t": b, "v": round(h * 100 / (h + m), 1) if h + m else None}
              for (b, h), (_, m) in zip(bucketize(hits, now - span, now, step),
                                        bucketize(misses, now - span, now, step))]
    th, tm = sum(v for _, v in hits), sum(v for _, v in misses)
    return {"range": range_name, "step": step, "series": series, "hits": th, "misses": tm,
            "hit_ratio": round(th * 100 / (th + tm), 1) if th + tm else None}


def zone_record_counts():
    from .dns.ddns import dynamic_records
    from .dns.zones import zone_records
    zs = q("SELECT * FROM zones ORDER BY name")
    dyn = dynamic_records(zs)
    out = []
    for z in zs:
        recs = zone_records(z)
        out.append({"zone": z["name"], "manual": sum(1 for r in recs if r["source"] == "manual"),
                    "host": sum(1 for r in recs if r["source"] == "host"), "dhcp": len(dyn.get(z["id"], []))})
    return out


def pool_usage():
    """Pemakaian pool DHCP saat ini per network: ukuran range, lease aktif di range, reservasi."""
    leases = read_leases()
    hosts = q("SELECT ip FROM hosts WHERE configure_dhcp=1")
    out = []
    for n in all_nets():
        if not n["dhcp_enabled"] or n["_n"].version != 4:
            continue
        rng = [(int(ipaddress.ip_address(r["start_ip"])), int(ipaddress.ip_address(r["end_ip"])))
               for r in q("SELECT * FROM ranges WHERE network_id=?", (n["id"],))]
        size = sum(hi - lo + 1 for lo, hi in rng)
        in_range = [ls for ls in leases if any(lo <= int(ipaddress.ip_address(ls["ip"])) <= hi for lo, hi in rng)]
        net_leases = [ls for ls in leases if ipaddress.ip_address(ls["ip"]) in n["_n"]]
        resv = sum(1 for h in hosts if ipaddress.ip_address(h["ip"]) in n["_n"])
        out.append({"id": n["id"], "cidr": n["cidr"], "comment": n["comment"], "pool_size": size,
                    "leased": len(in_range), "leases_total": len(net_leases), "reservations": resv,
                    "free": max(0, size - len(in_range)),
                    "pct": round(len(in_range) * 100 / size, 1) if size else 0})
    return sorted(out, key=lambda p: -p["pct"])


def dhcp_report(range_name):
    now = int(time.time())
    span, step = _range(range_name)
    samples = load("dhcp", now - max(span, 86400))
    win = [s for s in samples if s["ts"] >= now - span]
    points = [(s["ts"], s["active"]) for s in win]
    series = [{"t": b, "v": v} for b, v in bucketize(points, now - span, now, step, agg="avg")]
    packets, hourly_ack = {}, []
    ks = [s for s in samples if s.get("kea")]
    pts = []
    for p, c in zip(ks, ks[1:]):
        for k, v in c["kea"].items():
            dv = _delta(v, p["kea"].get(k, 0), False)
            if c["ts"] >= now - span:
                packets[k] = packets.get(k, 0) + dv
            if k == "pkt4-ack-sent":
                pts.append((c["ts"], dv))
    hourly_ack = _hourly(pts, now) if ks else []
    pools = pool_usage()
    size = sum(p["pool_size"] for p in pools)
    leased = sum(p["leased"] for p in pools)
    active_now = len(read_leases())
    return {
        "range": range_name, "step": step, "series": series, "pools": pools, "packets": packets,
        "hourly_ack": hourly_ack,
        "totals": {"active": active_now, "pool_size": size, "leased_in_pools": leased,
                   "free": max(0, size - leased), "pct": round(leased * 100 / size, 1) if size else 0,
                   "peak_active": max((s["active"] for s in win), default=active_now),
                   "acks_24h": sum(v for t, v in pts if t >= now - 86400) if ks else None},
        "source": source_status("dhcp"),
    }


def source_status(kind):
    last = q("SELECT MAX(ts) AS t FROM metrics WHERE kind=?", (kind,), one=True)
    info = {"last_sample": last["t"] if last else None, "interval": int(C["metrics_interval"])}
    if kind == "dns":
        info.update(available=STATUS["dns"], error=STATUS["dns_error"], url=C["bind_stats_url"])
    else:
        info.update(kea_stats=STATUS["kea"], kea_error=STATUS["kea_error"])
    return info
