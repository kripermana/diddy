"""Import dan export zona DNS beserta record-nya.

Format:
  bind  zone file RFC 1035 (BIND, Infoblox, Windows DNS, named-compilezone). Dibaca dengan parser
        sendiri: $ORIGIN, $TTL, owner kosong, kurung multi-baris, TTL berunit (1h, 1d), TXT bertanda kutip.
  csv   kolom name,type,value[,ttl][,comment][,zone]; name relatif terhadap zona atau absolut berakhiran titik.
  json  {"zone": {...}, "records": [...]} atau {"zones": [...]}: backup Diddy ke Diddy.

Import selalu dihitung dulu sebagai rencana (plan_import) yang bisa ditampilkan tanpa mengubah apa pun.
Penerapan (apply_import) menghitung ulang rencana yang sama lalu menulis semuanya dalam satu transaksi.
Record dari host object dan record DDNS tidak pernah diimport sebagai record manual.
"""

import csv
import io
import json
import re

from ..config import C
from ..core.errors import ApiError
from ..db.connection import q, transaction
from .ddns import dynamic_records
from .zones import (DYN_BEGIN, DYN_END, LEGACY_DYN_BEGIN, REC_TYPES, derived_records, get_zone, normalize_record,
                    render_zone, validate_zone, zone_records)

FORMATS = ("bind", "csv", "json")
MODES = ("merge", "replace")
MAX_RECORDS = 100000
PREVIEW_ROWS = 300
CLASSES = {"IN", "CH", "HS", "CS"}
TTL_RE = re.compile(r"^(\d+|(\d+[smhdw])+)$", re.I)
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class _Q(str):
    """Token yang berasal dari string bertanda kutip (TXT)."""


# ------------------------------------------------------------------ parser zone file
def parse_ttl(tok):
    tok = tok.lower()
    if tok.isdigit():
        return int(tok)
    return sum(int(n) * UNITS[u] for n, u in re.findall(r"(\d+)([smhdw])", tok))


def _tokens(line):
    """Pecah satu baris jadi token. Komentar (;) di luar kutip dibuang, kurung jadi token sendiri."""
    out, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c in " \t\r":
            i += 1
        elif c == ";":
            break
        elif c in "()":
            out.append(c)
            i += 1
        elif c == '"':
            buf, j = [], i + 1
            while j < n and line[j] != '"':
                if line[j] == "\\" and j + 1 < n:
                    if line[j + 1:j + 4].isdigit() and len(line[j + 1:j + 4]) == 3:
                        buf.append(chr(int(line[j + 1:j + 4])))
                        j += 4
                        continue
                    buf.append(line[j + 1])
                    j += 2
                    continue
                buf.append(line[j])
                j += 1
            out.append(_Q("".join(buf)))
            i = j + 1
        else:
            j = i
            while j < n and line[j] not in ' \t\r;()"':
                j += 2 if line[j] == "\\" else 1
            out.append(line[i:j])
            i = j
    return out


def _logical_lines(text):
    """(nomor baris, owner dikosongkan?, token) per record; baris dalam kurung digabung, blok DDNS dilewati."""
    out, buf, depth, start, blank, dyn = [], [], 0, 0, False, False
    for no, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if s.startswith(DYN_BEGIN[:40]) or s.startswith(LEGACY_DYN_BEGIN[:40]):
            dyn = True
            continue
        if s.startswith(DYN_END):
            dyn = False
            continue
        if dyn:
            continue
        toks = _tokens(raw)
        if not toks:
            continue
        if depth == 0:
            start, blank, buf = no, raw[:1] in (" ", "\t"), []
        for t in toks:
            if t == "(" and not isinstance(t, _Q):
                depth += 1
            elif t == ")" and not isinstance(t, _Q):
                depth = max(0, depth - 1)
            else:
                buf.append(t)
        if depth == 0 and buf:
            out.append((start, blank, buf))
            buf = []
    if buf:
        out.append((start, blank, buf))
    return out


def _abs(name, origin):
    """Nama absolut tanpa titik akhir. None bila relatif dan origin belum diketahui."""
    name = name.lower()
    if name == "@":
        return origin
    if name.endswith("."):
        return name[:-1]
    return f"{name}.{origin}" if origin else None


def parse_bind(text, origin=None):
    """Baca zone file. Mengembalikan daftar bundle zona (lihat _bundle)."""
    origin = (origin or "").strip().lower().rstrip(".") or None
    first_origin, default_ttl, soa, last_owner = None, None, None, None
    entries, skipped = [], []
    for no, blank, toks in _logical_lines(text):
        head = toks[0]
        if not isinstance(head, _Q) and head.startswith("$"):
            d = head.upper()
            if d == "$ORIGIN" and len(toks) > 1:
                origin = _abs(toks[1], origin) or toks[1].lower().rstrip(".")
                first_origin = first_origin or origin
            elif d == "$TTL" and len(toks) > 1 and TTL_RE.match(toks[1]):
                default_ttl = parse_ttl(toks[1])
            else:
                skipped.append({"line": no, "text": " ".join(toks)[:120], "reason": f"{d} is not supported"})
            continue
        i = 0
        if blank:
            owner = last_owner
        else:
            owner = _abs(head, origin)
            i = 1
        if owner is None:
            skipped.append({"line": no, "text": " ".join(toks)[:120],
                            "reason": "relative name without $ORIGIN: give the zone name"})
            continue
        last_owner, ttl, rtype = owner, None, None
        while i < len(toks):
            t = toks[i]
            i += 1
            if not isinstance(t, _Q) and ttl is None and TTL_RE.match(t):
                ttl = parse_ttl(t)
            elif t.upper() in CLASSES:
                continue
            else:
                rtype = t.upper()
                break
        rd = toks[i:]
        text_line = " ".join(toks)[:120]
        if not rtype:
            skipped.append({"line": no, "text": text_line, "reason": "no record type"})
            continue
        if rtype == "SOA":
            if len(rd) >= 7:
                soa = {"owner": owner, "mname": _abs(rd[0], origin) or rd[0], "rname": _abs(rd[1], origin) or rd[1],
                       "minimum": parse_ttl(rd[6]) if TTL_RE.match(rd[6]) else None}
            continue
        if rtype not in REC_TYPES:
            skipped.append({"line": no, "text": text_line, "reason": f"record type {rtype} is not supported by Diddy"})
            continue
        need = {"MX": 2, "SRV": 4}.get(rtype, 1)
        if len(rd) < need:
            skipped.append({"line": no, "text": text_line, "reason": "incomplete record data"})
            continue
        if rtype in ("CNAME", "NS", "PTR"):
            value = _abs(rd[0], origin) or rd[0]
        elif rtype == "MX":
            value = f"{rd[0]} {_abs(rd[1], origin) or rd[1]}"
        elif rtype == "SRV":
            value = f"{rd[0]} {rd[1]} {rd[2]} {_abs(rd[3], origin) or rd[3]}"
        elif rtype == "TXT":
            value = "".join(rd) if all(isinstance(t, _Q) for t in rd) else " ".join(rd)
        else:
            value = rd[0]
        entries.append({"line": no, "fq": owner, "type": rtype, "value": value, "ttl": ttl, "comment": ""})
    zone = (soa["owner"] if soa else None) or first_origin or origin
    meta = None
    if soa:
        meta = {"primary_ns": soa["mname"], "admin_email": _rname_to_email(soa["rname"]),
                "ttl": default_ttl or soa["minimum"] or 3600}
    elif default_ttl:
        meta = {"ttl": default_ttl}
    return [_bundle(zone, meta, entries, skipped)]


def _rname_to_email(rname):
    """hostmaster.example.com -> hostmaster@example.com (titik pertama yang tidak di-escape)."""
    m = re.match(r"^((?:[^.\\]|\\.)+)\.(.+)$", rname or "")
    return (m.group(1).replace("\\.", ".") + "@" + m.group(2)) if m else rname


def _bundle(zone, meta, entries, skipped):
    return {"zone": (zone or "").lower().rstrip(".") or None, "meta": meta, "entries": entries, "skipped": skipped}


# ------------------------------------------------------------------ parser CSV dan JSON
def _fq(name, zone):
    name = (name or "@").strip().lower()
    if name.endswith("."):
        return name[:-1]
    if not zone:
        return None
    return zone if name in ("@", "") else f"{name}.{zone}"


def parse_csv(text, zone=None):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise ApiError("The CSV file is empty or has no header row")
    cols = {c.strip().lower() for c in rows[0].keys() if c}
    missing = {"name", "type", "value"} - cols
    if missing:
        raise ApiError(f"CSV header must contain name,type,value (missing: {', '.join(sorted(missing))})")
    bundles = {}
    for no, raw in enumerate(rows, 2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        zn = (r.get("zone") or zone or "").lower().rstrip(".") or None
        b = bundles.setdefault(zn, _bundle(zn, None, [], []))
        fq = _fq(r.get("name"), zn)
        if not fq:
            b["skipped"].append({"line": no, "text": ",".join(v or "" for v in raw.values())[:120],
                                 "reason": "no zone: add a zone column"})
            continue
        ttl = r.get("ttl") or None
        b["entries"].append({"line": no, "fq": fq, "type": (r.get("type") or "").upper(), "value": r.get("value", ""),
                             "ttl": ttl, "comment": r.get("comment", "")})
    return list(bundles.values())


def parse_json(text, zone=None):
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ApiError(f"Invalid JSON: {e}")
    if isinstance(data, dict) and "zones" in data:
        items = data["zones"]
    else:
        items = data if isinstance(data, list) else [data]
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ApiError("Each JSON zone must be an object with 'zone' and 'records'")
        zmeta = it.get("zone") if isinstance(it.get("zone"), dict) else {"name": it.get("zone") or zone}
        zn = (zmeta.get("name") or zone or "").lower().rstrip(".") or None
        meta = {k: zmeta[k] for k in ("primary_ns", "admin_email", "ttl", "comment") if zmeta.get(k) not in (None, "")}
        entries = []
        for no, r in enumerate(it.get("records") or [], 1):
            if not isinstance(r, dict):
                continue
            entries.append({"line": no, "fq": _fq(r.get("name"), zn), "type": str(r.get("type") or "").upper(),
                            "value": str(r.get("value") or ""), "ttl": r.get("ttl"), "comment": r.get("comment") or ""})
        out.append(_bundle(zn, meta or None, entries, []))
    return out


def detect_format(text, filename=""):
    f = (filename or "").lower()
    if f.endswith(".json") or text.lstrip()[:1] in ("{", "["):
        return "json"
    if f.endswith(".csv") or re.match(r"^﻿?\s*\"?(zone|name)\"?\s*,", text):
        return "csv"
    return "bind"


def parse(text, fmt=None, zone=None, filename=""):
    if not (text or "").strip():
        raise ApiError("The file is empty")
    fmt = fmt or detect_format(text, filename)
    if fmt not in FORMATS:
        raise ApiError(f"format must be one of: {', '.join(FORMATS)}")
    bundles = {"bind": parse_bind, "csv": parse_csv, "json": parse_json}[fmt](text, zone)
    if sum(len(b["entries"]) for b in bundles) > MAX_RECORDS:
        raise ApiError(f"Too many records in one import (max {MAX_RECORDS})")
    return fmt, bundles


# ------------------------------------------------------------------ rencana dan penerapan
def plan_import(text, fmt=None, zone=None, mode="merge", target_id=None, filename=""):
    """Rencana import tanpa menulis apa pun. `zone` = nama zona tujuan (juga origin nama relatif);
    `target_id` = zona yang sudah ada (import record ke zona itu)."""
    if mode not in MODES:
        raise ApiError(f"mode must be one of: {', '.join(MODES)}")
    target = get_zone(target_id) if target_id else None
    if target:
        zone = target["name"]
    fmt, bundles = parse(text, fmt, zone, filename)
    if target and (len(bundles) > 1 or (bundles[0]["zone"] and bundles[0]["zone"] != target["name"]
                                        and fmt != "bind")):
        raise ApiError(f"The file is for another zone ({bundles[0]['zone']}), not {target['name']}")
    hosts = q("SELECT * FROM hosts WHERE configure_dns=1")
    all_zones = q("SELECT * FROM zones")
    plans = [_plan_zone(b, mode, target, hosts, all_zones) for b in bundles]
    return {"format": fmt, "mode": mode, "zones": plans,
            "totals": {k: sum(p[k] for p in plans) for k in ("add_count", "skip_count", "delete_count")}}


def _plan_zone(b, mode, target, hosts, all_zones):
    name = target["name"] if target else b["zone"]
    p = {"zone": name, "exists": False, "create": False, "meta": None, "add": [], "skipped": list(b["skipped"]),
         "delete_count": 0, "error": None}
    if not name:
        p["error"] = "Zone name unknown: the file has no $ORIGIN or SOA. Enter the zone name."
        return _counts(p)
    z = target or next((x for x in all_zones if x["name"] == name), None)
    try:
        if z:
            p["exists"], p["meta"] = True, {k: z[k] for k in ("primary_ns", "admin_email", "ttl")}
        else:
            p["create"], p["meta"] = True, validate_zone(dict(b["meta"] or {}, name=name))
    except ApiError as e:
        p["error"] = str(e)
        return _counts(p)
    zttl, pns = int(p["meta"]["ttl"]), p["meta"]["primary_ns"]
    existing = q("SELECT * FROM records WHERE zone_id=?", (z["id"],)) if z else []
    if mode == "replace":
        p["delete_count"], existing = len(existing), []
    zrow = z or {"id": -1, "name": name}
    zlist = all_zones + ([] if z else [zrow])
    derived = {(r["name"], r["type"], r["value"]) for r in derived_records(zrow, zlist, hosts)}
    host_names = {r[0] for r in derived if r[1] in ("A", "AAAA")}
    have = {(r["name"], r["type"], r["value"]) for r in existing}
    types = {}
    for r in existing:
        types.setdefault(r["name"], set()).add(r["type"])
    for e in b["entries"]:
        def skip(reason):
            p["skipped"].append({"line": e["line"], "text": f"{e['fq']} {e['type']} {e['value']}"[:120],
                                 "reason": reason})
        if not e["fq"] or not (e["fq"] == name or e["fq"].endswith("." + name)):
            skip(f"name is outside zone {name}")
            continue
        try:
            r = normalize_record(name, {"name": e["fq"], "type": e["type"], "value": e["value"], "ttl": e["ttl"],
                                        "comment": e["comment"]})
        except ApiError as ex:
            skip(str(ex))
            continue
        if r["ttl"] == zttl:
            r["ttl"] = None
        key = (r["name"], r["type"], r["value"])
        if r["name"] == "@" and r["type"] == "NS" and r["value"] == pns:
            skip("primary name server: Diddy writes this NS record itself")
        elif key in derived:
            skip("already provided by a host object")
        elif key in have:
            skip("identical record already exists")
        elif r["type"] == "CNAME" and (types.get(r["name"]) or r["name"] in host_names):
            skip(f"CNAME '{r['_fq']}' conflicts with other records of the same name")
        elif "CNAME" in types.get(r["name"], ()):
            skip(f"'{r['_fq']}' is a CNAME; no other records can share that name")
        else:
            have.add(key)
            types.setdefault(r["name"], set()).add(r["type"])
            p["add"].append({k: r[k] for k in ("name", "type", "value", "ttl", "comment")})
    return _counts(p)


def _counts(p):
    p["add_count"], p["skip_count"] = len(p["add"]), len(p["skipped"])
    return p


def apply_import(plan):
    """Tulis rencana import dalam satu transaksi. Mengembalikan ringkasan per zona."""
    done = []
    with transaction() as t:
        for p in plan["zones"]:
            if p["error"] or (not p["add"] and not p["delete_count"] and not p["create"]):
                continue
            if p["create"]:
                m = p["meta"]
                zid = t.x("INSERT INTO zones(name,ttl,primary_ns,admin_email,comment) VALUES(?,?,?,?,?)",
                          (p["zone"], m["ttl"], m["primary_ns"], m["admin_email"], m.get("comment", "")))
            else:
                zid = q("SELECT id FROM zones WHERE name=?", (p["zone"],), one=True)["id"]
                if plan["mode"] == "replace":
                    t.x("DELETE FROM records WHERE zone_id=?", (zid,))
            t.many("INSERT INTO records(zone_id,name,type,value,ttl,comment) VALUES(?,?,?,?,?,?)",
                   [(zid, r["name"], r["type"], r["value"], r["ttl"], r["comment"]) for r in p["add"]])
            done.append({"zone": p["zone"], "id": zid, "created": p["create"], "added": p["add_count"],
                         "deleted": p["delete_count"] if plan["mode"] == "replace" and not p["create"] else 0,
                         "skipped": p["skip_count"]})
    return done


def preview(plan):
    """Rencana untuk ditampilkan: daftar record dibatasi supaya respons tetap kecil."""
    out = dict(plan, zones=[])
    for p in plan["zones"]:
        out["zones"].append(dict(p, add=p["add"][:PREVIEW_ROWS], skipped=p["skipped"][:PREVIEW_ROWS]))
    return out


# ------------------------------------------------------------------ export
def export_zone(zid, fmt="bind", dynamic=False):
    """Isi file export dan nama filenya. bind = zone file seperti yang dilayani BIND (record host ikut,
    record DDNS hanya bila dynamic=True); csv dan json berisi record manual saja (host object bukan record)."""
    z = get_zone(zid)
    if fmt == "bind":
        dyn = dynamic_records().get(z["id"], []) if dynamic else None
        text, _serial, _h = render_zone(z, dyn)
        head = (f"; Exported from Diddy: zone {z['name']}\n"
                "; Records from host objects are included; import skips them when the same host object exists.\n")
        return head + text.split("\n", 2)[2], f"{z['name']}.zone", "text/dns"
    recs = [r for r in zone_records(z) if r["source"] == "manual"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["name", "type", "value", "ttl", "comment"])
        for r in recs:
            w.writerow([r["name"], r["type"], r["value"], "" if r["ttl"] is None else r["ttl"], r["comment"] or ""])
        return buf.getvalue(), f"{z['name']}.csv", "text/csv"
    if fmt == "json":
        return json.dumps(_zone_json(z, recs), indent=2) + "\n", f"{z['name']}.json", "application/json"
    raise ApiError(f"format must be one of: {', '.join(FORMATS)}")


def _zone_json(z, recs):
    return {"zone": {k: z[k] for k in ("name", "ttl", "primary_ns", "admin_email", "comment")},
            "records": [{k: r[k] for k in ("name", "type", "value", "ttl", "comment")} for r in recs]}


def export_all():
    """Semua zona dan record manualnya dalam satu JSON (backup, bisa diimport kembali)."""
    zones = []
    for z in q("SELECT * FROM zones ORDER BY name"):
        zones.append(_zone_json(z, [r for r in zone_records(z) if r["source"] == "manual"]))
    return json.dumps({"diddy_zones": 1, "default_ns": C["default_ns"], "zones": zones}, indent=2) + "\n"
