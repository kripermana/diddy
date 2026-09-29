"""Detail error DNS dari log BIND: SERVFAIL, REFUSED, dan server upstream yang gagal.

Statistics-channel BIND hanya memberi jumlah per rcode. Detail per domain, upstream, dan klien diambil dari log
BIND yang ditulis lewat blok `logging` di named.conf.diddy (render_named_conf, `bind_log_manage`):
  query-errors  `client @.. IP#port (x): query failed (failure) for NAME/IN/TYPE at query.c:N`   -> servfail
  lame-servers  `timed out resolving 'NAME/TYPE/IN': IP#53` (juga connection refused, unexpected RCODE, ...)
                                                                                              -> upstream
  security      `client @.. IP#port (x): query (cache) 'NAME/TYPE/IN' denied (...)`         -> refused
Format di atas diambil dari BIND 9.18 (Ubuntu 24.04); build distro tidak punya dnstap, jadi NXDOMAIN per domain
tidak bisa dilacak.

Log dibaca bertahap (offset + inode di tabel state, rotasi ditangani) dan diringkas per 5 menit ke tabel
`dns_events`, dibuang setelah `metrics_retention_days`.
"""

import collections
import datetime
import ipaddress
import os
import re
import shutil
import threading
import time

from ..config import C, DRY
from ..core.util import run
from ..db.connection import q, state_get, state_set, transaction, x
from ..metrics import RANGES
from .resolver import dns_settings, recursion_acl

KINDS = ("servfail", "refused", "upstream")
BUCKET = 300
MAX_READ = 64 * 2**20
TOP = 50
STATUS = {"ts": None, "lines": 0, "events": 0, "error": None, "last_prune": 0.0}
_LOCK = threading.Lock()
_LAST_UPSTREAM = collections.OrderedDict()   # nama -> (alasan, server) dari baris lame-servers terakhir

_TS = r"^(?P<ts>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2})(?:\.\d+)?\s+"
QF_RE = re.compile(_TS + r"query-errors: [^:]+: client @\S+ (?P<ip>[^\s#]+)#\d+ \([^)]*\): "
                   r"query failed \((?P<res>[^)]+)\) for (?P<name>\S+?)/IN/(?P<type>\S+)")
UP_RE = re.compile(_TS + r"lame-servers: [^:]+: (?P<res>.+?) resolving '(?P<name>[^']*?)/(?P<type>[^/']+)/IN': "
                   r"(?P<ip>[^\s#]+)#\d+")
LAME_RE = re.compile(_TS + r"lame-servers: [^:]+: lame server resolving '(?P<name>[^']*)' .*?: (?P<ip>[^\s#]+)#\d+")
DENY_RE = re.compile(_TS + r"security: [^:]+: client @\S+ (?P<ip>[^\s#]+)#\d+ \([^)]*\): (?P<what>[^']*?) "
                     r"'(?P<name>[^']*?)/(?P<type>[^/']+)/IN' denied(?: \((?P<why>[^)]+)\))?")


# ------------------------------------------------------------------ blok logging BIND
def logging_conflict():
    """True bila config BIND yang berjalan sudah punya blok `logging` selain milik Diddy.

    BIND hanya menerima satu blok logging; validasi deploy memeriksa named.conf.diddy sendirian, jadi bentrok
    ini harus dicek dari config lengkap (`named-checkconf -p`) sebelum Diddy menulis bloknya.
    """
    if DRY or not shutil.which("named-checkconf"):
        return False
    ok, out = run(["named-checkconf", "-p"], timeout=20)
    if not ok:
        # blok admin ditambahkan setelah blok Diddy ada: config tidak valid sampai blok Diddy dicabut
        return "'logging' redefined" in out
    return any("diddy_errors" not in m.group(1) for m in re.finditer(r"^logging \{(.*?)^\};", out, re.S | re.M))


def deploy_log_file():
    """File log untuk blok logging di named.conf.diddy, atau None bila tidak dikelola / bentrok."""
    if not C.getboolean("bind_log_manage"):
        state_set("errlog:conflict", "0")
        return None
    conflict = logging_conflict()
    state_set("errlog:conflict", "1" if conflict else "0")
    return None if conflict else C["bind_log_file"]


def ensure_log_dir():
    """Direktori log dibuat milik user BIND (AppArmor Ubuntu mengizinkan /var/log/named/**)."""
    d = os.path.dirname(C["bind_log_file"]) or "."
    if os.path.isdir(d):
        return
    os.makedirs(d, mode=0o750, exist_ok=True)
    import pwd
    for user in ("bind", "named"):
        try:
            pw = pwd.getpwnam(user)
        except KeyError:
            continue
        os.chown(d, pw.pw_uid, pw.pw_gid)
        break


# ------------------------------------------------------------------ baca dan ringkas log
def _epoch(ts):
    return int(time.mktime(datetime.datetime.strptime(ts, "%d-%b-%Y %H:%M:%S").timetuple()))


def _reason(res):
    r = res.strip()
    low = r.lower()
    if "timed out" in low:
        return "timeout"
    if "unexpected rcode" in low:
        return "upstream " + r.split()[0].upper()
    if "trust chain" in low or "insecurity" in low or "signature" in low or "dnssec" in low:
        return "DNSSEC"
    if low.startswith("formerr"):
        return "upstream FORMERR"
    return low[:64]


def parse_line(line):
    """Satu baris log -> (epoch, kind, name, qtype, server, client, reason) atau None."""
    m = QF_RE.match(line)
    if m:
        name = m.group("name").lower().rstrip(".") or "."
        reason, server = _LAST_UPSTREAM.get(name, (None, ""))
        if reason is None:
            reason = "unknown" if m.group("res") == "failure" else _reason(m.group("res"))
        return _epoch(m.group("ts")), "servfail", name, m.group("type"), server, m.group("ip"), reason
    m = UP_RE.match(line) or LAME_RE.match(line)
    if m:
        name = m.group("name").lower().rstrip(".") or "."
        reason = "lame delegation" if "lame server" in line else _reason(m.group("res"))
        _LAST_UPSTREAM[name] = (reason, m.group("ip"))
        _LAST_UPSTREAM.move_to_end(name)
        while len(_LAST_UPSTREAM) > 5000:
            _LAST_UPSTREAM.popitem(last=False)
        qtype = m.groupdict().get("type") or ""
        return _epoch(m.group("ts")), "upstream", name, qtype, m.group("ip"), "", reason
    m = DENY_RE.match(line)
    if m:
        name = m.group("name").lower().rstrip(".") or "."
        why = (m.group("why") or m.group("what") or "denied").replace(" did not match", "")
        return _epoch(m.group("ts")), "refused", name, m.group("type"), "", m.group("ip"), why[:64]
    return None


def _read(path, offset):
    """Teks lengkap per baris mulai dari offset; mengembalikan (teks, offset baru)."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(MAX_READ)
    end = data.rfind(b"\n") + 1
    return data[:end].decode("utf-8", "replace"), offset + end


def read_new(path):
    """Baris baru sejak pembacaan terakhir. Rotasi BIND (file lama jadi .0) dan truncate ditangani."""
    st = os.stat(path)
    inode, off = state_get("errlog:inode"), int(state_get("errlog:offset") or 0)
    parts = []
    if inode and inode != str(st.st_ino):
        old = path + ".0"
        if os.path.exists(old) and str(os.stat(old).st_ino) == inode:
            parts.append(_read(old, off)[0])
        off = 0
    elif st.st_size < off:
        off = 0
    text, off = _read(path, off)
    parts.append(text)
    state_set("errlog:inode", str(st.st_ino))
    state_set("errlog:offset", str(off))
    return "".join(parts)


def collect_errors():
    """Baca baris baru dari log BIND dan simpan ringkasannya. Aman dipanggil dari worker dan dari API."""
    path = C["bind_log_file"]
    with _LOCK:
        STATUS["ts"] = int(time.time())
        if not os.path.exists(path):
            STATUS["error"] = f"{path} does not exist yet"
            return 0
        try:
            text = read_new(path)
        except OSError as e:
            STATUS["error"] = str(e)[:200]
            return 0
        agg = collections.Counter()
        lines = 0
        for line in text.splitlines():
            lines += 1
            ev = parse_line(line)
            if ev:
                t, *rest = ev
                agg[(t - t % BUCKET, *rest)] += 1
        if agg:
            with transaction() as tx:
                tx.many("INSERT INTO dns_events(bucket,kind,name,qtype,server,client,reason,count) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        [(b, kd, nm[:255], qt[:12], sv[:64], cl[:64], rs[:64], n)
                         for (b, kd, nm, qt, sv, cl, rs), n in agg.items()])
        STATUS.update(lines=STATUS["lines"] + lines, events=STATUS["events"] + sum(agg.values()), error=None)
        if time.time() - STATUS["last_prune"] > 3600:
            STATUS["last_prune"] = time.time()
            keep = int(float(C["metrics_retention_days"]) * 86400)
            x("DELETE FROM dns_events WHERE bucket < ?", (int(time.time()) - keep,))
        return sum(agg.values())


# ------------------------------------------------------------------ laporan
def _forwarder_ips():
    cfg = dns_settings()
    ips = {}
    if cfg["upstream_mode"] == "encrypted":
        ips[C["dnsdist_listen"]] = "dnsdist proxy"
    for f in cfg["forwarders"]:
        ips[str(f).split()[0]] = "forwarder"
    for r in q("SELECT domain, servers FROM forwarders"):
        for s in r["servers"].split(","):
            if s.strip():
                ips[s.split()[0].strip()] = f"conditional forwarder ({r['domain']})"
    return ips


def _allowed(ip, nets):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in nets:
        try:
            if a in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


def source_status():
    path = C["bind_log_file"]
    return {"managed": C.getboolean("bind_log_manage"), "log_file": path, "exists": os.path.exists(path),
            "conflict": state_get("errlog:conflict") == "1", "last_read": STATUS["ts"], "error": STATUS["error"]}


def dns_errors_report(range_name="24h", kind="servfail"):
    """Ringkasan untuk halaman DNS errors: total, garis waktu, top domain, upstream, dan klien."""
    if kind not in ("servfail", "refused"):
        kind = "servfail"
    collect_errors()
    span, step = RANGES.get(range_name, RANGES["24h"])
    step = max(step, BUCKET)
    now = int(time.time())
    since = now - span
    rows = q("SELECT bucket, kind, name, qtype, server, client, reason, SUM(count) AS c FROM dns_events "
             "WHERE bucket >= ? GROUP BY bucket, kind, name, qtype, server, client, reason", (since - BUCKET,))
    totals = {k: 0 for k in KINDS}
    series = collections.Counter()
    names, servers, clients = {}, {}, {}
    fwd = _forwarder_ips()
    for r in rows:
        c = int(r["c"])
        totals[r["kind"]] += c
        if r["kind"] == kind:
            series[r["bucket"] - r["bucket"] % step] += c
            n = names.setdefault(r["name"], {"name": r["name"], "count": 0, "types": collections.Counter(),
                                             "reasons": collections.Counter(), "clients": set(), "last": 0})
            n["count"] += c
            n["types"][r["qtype"]] += c
            n["reasons"][r["reason"]] += c
            n["clients"].add(r["client"])
            n["last"] = max(n["last"], r["bucket"])
            cl = clients.setdefault(r["client"], {"client": r["client"], "count": 0, "names": collections.Counter(),
                                                  "reasons": collections.Counter(), "last": 0})
            cl["count"] += c
            cl["names"][r["name"]] += c
            cl["reasons"][r["reason"]] += c
            cl["last"] = max(cl["last"], r["bucket"])
        if r["kind"] == "upstream":
            s = servers.setdefault(r["server"], {"server": r["server"], "role": fwd.get(r["server"], ""), "count": 0,
                                                 "reasons": collections.Counter(), "names": collections.Counter(),
                                                 "last": 0})
            s["count"] += c
            s["reasons"][r["reason"]] += c
            s["names"][r["name"]] += c
            s["last"] = max(s["last"], r["bucket"])
    acl = recursion_acl(dns_settings()) if kind == "refused" else []

    def top(d, key="count"):
        return sorted(d.values(), key=lambda v: -v[key])[:TOP]

    def when(t):
        return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else ""

    out_names = [{"name": n["name"], "count": n["count"], "types": [t for t, _ in n["types"].most_common(3)],
                  "reasons": n["reasons"].most_common(3), "clients": len(n["clients"] - {""}), "last": when(n["last"])}
                 for n in top(names)]
    out_servers = [{"server": s["server"], "role": s["role"], "count": s["count"],
                    "reasons": s["reasons"].most_common(3), "names": [k for k, _ in s["names"].most_common(3)],
                    "last": when(s["last"])} for s in top(servers)]
    out_clients = [{"client": cl["client"], "count": cl["count"], "names": [k for k, _ in cl["names"].most_common(3)],
                    "reasons": cl["reasons"].most_common(2), "last": when(cl["last"]),
                    **({"allowed": _allowed(cl["client"], acl)} if kind == "refused" else {})}
                   for cl in top(clients) if cl["client"]]
    first = since - since % step
    timeline = [{"t": b, "v": series.get(b, 0)} for b in range(first, now + 1, step)]
    return {"range": range_name, "kind": kind, "totals": totals, "series": timeline, "names": out_names,
            "servers": out_servers, "clients": out_clients, "source": source_status()}
