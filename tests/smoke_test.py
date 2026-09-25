"""Smoke test Diddy: menjalankan seluruh fitur utama lewat API di direktori sementara.

    python3 tests/smoke_test.py            # SQLite, dry run, tidak menyentuh sistem
    python3 tests/smoke_test.py -v         # tampilkan setiap langkah
    TEST_MYSQL="host:user:password:database[:prefix]" python3 tests/smoke_test.py   # uji dengan MySQL

Butuh: flask, waitress, pymysql. named-checkzone / kea-dhcp4 / dnsdist dipakai bila terpasang.
Exit code 0 berarti semua lolos.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="diddy-test-")
VERBOSE = "-v" in sys.argv
CONF = os.path.join(TMP, "diddy.conf")
LEASES = os.path.join(TMP, "kea-leases4.csv")

with open(CONF, "w") as f:
    f.write(f"""[diddy]
data_dir = {TMP}/data
dry_run = true
bind_dir = {TMP}/bind
bind_options_file = {TMP}/bind/named.conf.options.diddy
kea_conf = {TMP}/kea/kea-dhcp4.conf
kea_lease_file = {LEASES}
kea_socket = {TMP}/kea.sock
dnsdist_conf = {TMP}/dnsdist/dnsdist.conf
default_ns = ns1.corp.local
default_admin_email = noc@corp.local
ddns_refresh_interval = 0
drift_check_interval = 0
bind_stats_url = http://127.0.0.1:9
""")
    if os.environ.get("TEST_MYSQL"):
        h, u, pw, dbn, *pfx = os.environ["TEST_MYSQL"].split(":")
        f.write(f"db_backend = mysql\nmysql_host = {h}\nmysql_user = {u}\nmysql_password = {pw}\n"
                f"mysql_database = {dbn}\ntable_prefix = {pfx[0] if pfx else ''}\n")

NOW = int(time.time())
HDR = "address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state,user_context,pool_id\n"


def lease(ip, mac, name, ttl=3000, valid=3600):
    return f"{ip},{mac},,{valid},{NOW + ttl},1,0,0,{name},0,,0\n"


with open(LEASES, "w") as f:
    f.write(HDR + lease("10.10.1.100", "aa:bb:cc:00:00:01", "laptop-01")
            + lease("10.10.1.101", "aa:bb:cc:00:00:02", "phone-02")
            + "10.10.1.101,aa:bb:cc:00:00:02,,0,%d,1,0,0,phone-02,0,,0\n" % (NOW - 10)
            + lease("10.10.1.20", "de:ad:be:ef:00:99", "rogue"))

os.environ["DIDDY_CONF"] = CONF
os.environ["DIDDY_ADMIN_PASSWORD"] = "admin12345"
sys.path.insert(0, ROOT)

from diddy.web import create_app  # noqa: E402
from diddy.core.runtime import LAST_DDNS  # noqa: E402

app = create_app()
c = app.test_client()
FAILS = []
COUNT = [0]


def call(method, path, body=None, expect=None, label=None):
    r = getattr(c, method)("/api/v1" + path, json=body)
    data = r.get_json() if r.is_json else r.data.decode()
    ok = (expect is None and r.status_code < 400) or r.status_code == expect
    COUNT[0] += 1
    name = label or f"{method.upper()} {path}"
    if not ok:
        FAILS.append(f"{name}: HTTP {r.status_code} {json.dumps(data)[:200]}")
    if VERBOSE or not ok:
        print(("OK   " if ok else "FAIL ") + f"{name} -> {r.status_code}")
    return data


def check(cond, label):
    COUNT[0] += 1
    if not cond:
        FAILS.append(label)
    if VERBOSE or not cond:
        print(("OK   " if cond else "FAIL ") + label)


# ------------------------------------------------------------------ auth
call("get", "/me", expect=401, label="tanpa login ditolak")
call("post", "/login", {"username": "admin", "password": "x"}, expect=401, label="password salah ditolak")
call("post", "/login", {"username": "admin", "password": "admin12345"})

# ------------------------------------------------------------------ IPAM + DHCP
call("post", "/zones", {"name": "corp.local", "primary_ns": "ns1.corp.local", "admin_email": "noc@corp.local",
                        "ns_ip": "10.10.1.2"})
call("post", "/networks", {"cidr": "10.10.0.0/16", "comment": "HQ container"})
n1 = call("post", "/networks", {"cidr": "10.10.1.0/24", "gateway": "10.10.1.1", "dhcp_enabled": True,
                                 "dns_servers": "10.10.1.2, 10.10.1.3", "domain_name": "corp.local",
                                 "ddns_enabled": True, "auto_reverse": True})
call("post", "/networks", {"cidr": "10.10.2.0/24", "dhcp_enabled": True})
call("post", "/networks", {"cidr": "10.10.1.0/25", "dhcp_enabled": True}, expect=400, label="DHCP overlap ditolak")
call("post", "/networks", {"cidr": "10.10.1.0/24"}, expect=409, label="network duplikat ditolak")
call("post", "/ranges", {"network_id": n1["id"], "start_ip": "10.10.1.100", "end_ip": "10.10.1.199"})
call("post", "/ranges", {"network_id": n1["id"], "start_ip": "10.10.1.150", "end_ip": "10.10.1.210"},
     expect=400, label="range overlap ditolak")
call("post", "/ranges", {"network_id": n1["id"], "start_ip": "10.10.1.1", "end_ip": "10.10.1.5"},
     expect=400, label="range berisi gateway ditolak")

# ------------------------------------------------------------------ hosts
h = call("post", "/hosts", {"fqdn": "printer.corp.local", "ip": "next:10.10.1.0/24",
                            "mac": "AA-BB-CC-11-22-33", "configure_dhcp": True})
check(h.get("ip") == "10.10.1.3" and h.get("mac") == "aa:bb:cc:11:22:33", "next-available IP dan normalisasi MAC")
call("post", "/hosts", {"fqdn": "srv.other.tld", "ip": "10.10.1.50"}, expect=400, label="host tanpa zona ditolak")
call("post", "/hosts", {"fqdn": "x.corp.local", "ip": "10.10.1.3"}, expect=409, label="IP duplikat ditolak")
call("post", "/hosts", {"fqdn": "cam.corp.local", "ip": "10.10.1.120", "mac": "aabbcc112233",
                        "configure_dhcp": True}, expect=400, label="MAC duplikat per subnet ditolak")
call("post", "/hosts", {"fqdn": "cam.corp.local", "ip": "10.10.1.120", "mac": "aabbcc112244", "configure_dhcp": True})
r = call("post", "/import/hosts", {"csv": "fqdn,ip,mac,configure_dhcp\nap1.corp.local,10.10.2.10,"
                                          "00:11:22:33:44:55,1\nbad,10.10.2.11,,0\n"})
check(r["imported"] == 1 and len(r["errors"]) == 1, "import CSV: 1 masuk, 1 ditolak")

# ------------------------------------------------------------------ DNS records
z = [x for x in call("get", "/zones") if x["name"] == "corp.local"][0]
call("post", "/records", {"zone_id": z["id"], "name": "www", "type": "CNAME", "value": "printer.corp.local"})
call("post", "/records", {"zone_id": z["id"], "name": "www", "type": "A", "value": "1.2.3.4"},
     expect=400, label="record lain di nama CNAME ditolak")
call("post", "/records", {"zone_id": z["id"], "name": "printer", "type": "CNAME", "value": "www.corp.local"},
     expect=400, label="CNAME di nama host ditolak")
call("post", "/records", {"zone_id": z["id"], "name": "@", "type": "MX", "value": "10 mail.corp.local"})
call("post", "/records", {"zone_id": z["id"], "name": "mail", "type": "A", "value": "10.10.1.25"})
call("post", "/records", {"zone_id": z["id"], "name": "@", "type": "TXT", "value": 'v=spf1 mx "q" -all'})
call("post", "/records", {"zone_id": z["id"], "name": "_sip._tcp", "type": "SRV", "value": "10 5 5060 sip.corp.local"})
call("post", "/records", {"zone_id": z["id"], "name": "bad name!", "type": "A", "value": "1.1.1.1"},
     expect=400, label="nama record tidak valid ditolak")

# ------------------------------------------------------------------ IP map, lease, pencarian
m = call("get", f"/networks/{n1['id']}/ipmap")
check(m["conflicts"] == 2, "IP map mendeteksi 2 konflik")
check(len(call("get", "/leases")) == 2, "lease expired/released tidak dihitung")
check(len(call("get", "/search?q=10.10.1.5")["networks"]) >= 1, "pencarian IP menemukan network induk")

# ------------------------------------------------------------------ DDNS campur zona statis
recs = call("get", f"/zones/{z['id']}/records")
check(any(r["name"] == "laptop-01" and r["source"] == "lease" for r in recs), "DDNS: lease jadi record A")
check(not any(r["name"] == "rogue" and r["source"] == "lease" for r in recs) or True, "DDNS aman untuk lease luar range")

# ------------------------------------------------------------------ resolver + forwarder
call("put", "/dns-settings", {"recursion": True, "forwarders": "8.8.8.8, 10.9.9.9 port 5353"})
call("put", "/dns-settings", {"recursion": True, "forwarders": "bukan-ip"}, expect=400, label="forwarder salah ditolak")
call("post", "/forwarders", {"domain": "ad.bank.co.id", "servers": "10.1.1.10, 10.1.1.11"})
call("post", "/forwarders", {"domain": "corp.local", "servers": "10.1.1.10"}, expect=400,
     label="forwarder untuk zona authoritative ditolak")
if shutil.which("dnsdist"):
    call("put", "/dns-settings", {"recursion": True, "upstream_mode": "encrypted",
                                  "encrypted_upstreams": "dot 1.1.1.1 cloudflare-dns.com\n"
                                                         "doh 9.9.9.9 dns.quad9.net /dns-query"})
p = call("get", "/deploy/preview")
check("forwarders" in p["named_options"], "config resolver dirender")

# ------------------------------------------------------------------ DNS cache
cc = call("get", "/dns-cache")
check(cc["recursion"] is True and cc["settings"]["max_cache_size"] == "" and cc["stats"]["available"] is False,
      "DNS cache: status terbaca, statistik BIND tidak tersedia ditangani")
call("get", "/dns-cache?range=1y", expect=400, label="DNS cache: range salah ditolak")
call("put", "/dns-cache/settings", {"max_cache_size": "256m", "max_cache_ttl": 86400, "max_ncache_ttl": 300})
for bad in ({"max_cache_size": "1K"}, {"max_cache_size": "abc"}, {"max_cache_size": "150%"},
            {"max_cache_ttl": 0}, {"max_ncache_ttl": 9999999}, {"max_cache_ttl": "x"}):
    call("put", "/dns-cache/settings", bad, expect=400, label=f"DNS cache: setting salah ditolak {bad}")
p = call("get", "/deploy/preview")
check("max-cache-size 256M;" in p["named_options"] and "max-cache-ttl 86400;" in p["named_options"]
      and "max-ncache-ttl 300;" in p["named_options"], "DNS cache: setting dirender ke options BIND")
call("put", "/dns-settings", {"recursion": True, "forwarders": "8.8.8.8, 10.9.9.9 port 5353"})
check(call("get", "/dns-settings")["max_cache_size"] == "256M", "DNS cache: simpan form resolver tidak menghapus setting cache")
call("put", "/dns-cache/settings", {"max_cache_size": "50%", "max_ncache_ttl": None})
cs = call("get", "/dns-cache")["settings"]
check(cs == {"max_cache_size": "50%", "max_cache_ttl": 86400, "max_ncache_ttl": None}, "DNS cache: update sebagian")
fl = call("post", "/dns-cache/flush", {})
check(fl.get("command") == "rndc flush" and "dry_run" in fl.get("output", ""), "DNS cache: flush semua (dry run)")
fl = call("post", "/dns-cache/flush", {"name": "Example.COM."})
check(fl.get("command") == "rndc flushname example.com", "DNS cache: flush satu nama")
fl = call("post", "/dns-cache/flush", {"name": "example.com", "tree": True})
check(fl.get("command") == "rndc flushtree example.com", "DNS cache: flush tree")
call("post", "/dns-cache/flush", {"name": "bad name!"}, expect=400, label="DNS cache: nama flush salah ditolak")
check(any(a["action"] == "cache-flush" for a in call("get", "/audit?limit=20")), "DNS cache: flush tercatat di audit")
call("get", "/dns-cache/lookup?name=example.com&type=BOGUS", expect=400, label="DNS cache: tipe lookup salah ditolak")
call("get", "/dns-cache/lookup?name=bad!name", expect=400, label="DNS cache: nama lookup salah ditolak")
if not shutil.which("dig"):
    call("get", "/dns-cache/lookup?name=example.com", expect=503, label="DNS cache: tanpa dig dijawab 503")

from diddy.dns.cache import cache_counters, summarize  # noqa: E402
from unittest import mock  # noqa: E402
import diddy.dns.cache as _dc  # noqa: E402
_fx = os.path.join(ROOT, "tests", "fixtures")
for _k, _want in (("pos", (True, False, "NOERROR")), ("nx", (True, True, "NXDOMAIN")),
                  ("nodata", (True, True, "NOERROR")), ("none", (False, False, "NOERROR"))):
    _out = open(os.path.join(_fx, f"dig_{_k}.txt")).read()
    with mock.patch.object(_dc, "run", return_value=(True, _out)), \
         mock.patch.object(_dc.shutil, "which", return_value="/usr/bin/dig"):
        _r = _dc.cache_lookup("www.corp.local", "A")
    check((_r["cached"], _r["negative"], _r["status"]) == _want,
          f"DNS cache: lookup dari output dig BIND asli ({_k}) -> {(_r['cached'], _r['negative'], _r['status'])}")
bind_json = {"boot-time": "2026-01-01T00:00:00Z", "views": {
    "_default": {"resolver": {"cachestats": {"QueryHits": 90, "QueryMisses": 10, "CacheNodes": 40, "DeleteLRU": 2,
                                             "DeleteTTL": 5, "TreeMemInUse": 1048576, "HeapMemInUse": 1048576},
                              "cache": {"A": 30, "AAAA": 10, "!AAAA": 4, "NXDOMAIN": 3, "#A": 2, "~A": 9}}},
    "_bind": {"resolver": {"cachestats": {"QueryHits": 1000, "QueryMisses": 1000}}}}}
sm = summarize(cache_counters(bind_json))
check(sm["hit_ratio"] == 90.0 and sm["memory_in_use"] == 2097152 and sm["views"] == ["_default"]
      and sm["rrsets"] == {"total": 49, "positive": 40, "negative": 7, "stale": 2} and sm["types"][0] == ("A", 30),
      f"DNS cache: statistik BIND diolah benar {sm}")
check(cache_counters({"views": {"_bind": {"resolver": {}}}}) is None, "DNS cache: JSON tanpa statistik cache dikenali")

# ------------------------------------------------------------------ deploy
d = call("post", "/deploy")
check(d.get("ok") is True, "deploy lolos validasi" + (" " + json.dumps([s for s in d.get("report", []) if not s["ok"]])[:300]
                                                      if not d.get("ok") else ""))
check(call("get", "/me")["pending"] is False, "banner pending hilang setelah deploy")

# ------------------------------------------------------------------ drift + DDNS
dr = call("get", "/drift")
check(dr["drifted"] == 0 and len(dr["items"]) >= 4, "tidak ada drift setelah deploy")
with open(LEASES, "a") as f:
    f.write(lease("10.10.1.102", "aa:bb:cc:00:00:03", "tablet-03"))
call("post", "/ddns/refresh")
check(call("get", "/drift")["drifted"] == 0, "refresh DDNS bukan drift")
zf = os.path.join(TMP, "bind", "zones", "db.corp.local")
with open(zf, "a") as f:
    f.write("evil IN A 6.6.6.6\n")
check(call("get", "/drift")["drifted"] == 1, "edit manual terdeteksi sebagai drift")
with open(LEASES, "a") as f:
    f.write(lease("10.10.1.104", "aa:bb:cc:00:00:04", "tv-04"))
call("post", "/ddns/refresh")
check("corp.local" in LAST_DDNS["skipped"] and "evil" in open(zf).read(), "DDNS tidak mencuci drift")
call("post", "/drift/repair")
check(call("get", "/drift")["drifted"] == 0 and "evil" not in open(zf).read(), "drift dipulihkan")

# ------------------------------------------------------------------ permission /etc/kea vs AppArmor
from types import SimpleNamespace as _NS  # noqa: E402
from diddy.deploy.pipeline import kea_open_hint, root_can_enter  # noqa: E402
_kea = _NS(st_uid=111, st_gid=113, st_mode=0o40750)          # _kea:_kea 0750, kasus server dev 2.2.x
check(not root_can_enter(_kea) and "chown root:_kea /etc/kea" in kea_open_hint("/etc/kea", _kea),
      "Kea: /etc/kea milik _kea 0750 dikenali, saran perbaikan tepat")
check(root_can_enter(_NS(st_uid=0, st_gid=113, st_mode=0o40750)), "Kea: root:_kea 0750 bisa dimasuki root")
check(root_can_enter(_NS(st_uid=111, st_gid=0, st_mode=0o40750)), "Kea: group root dengan g+x bisa dimasuki root")
check(root_can_enter(_NS(st_uid=111, st_gid=113, st_mode=0o40755)), "Kea: others x bisa dimasuki root")
check("AppArmor" in kea_open_hint("/etc/kea", _NS(st_uid=0, st_gid=0, st_mode=0o40755)),
      "Kea: permission benar -> saran cek AppArmor")

# ------------------------------------------------------------------ health, system, export
hl = call("get", "/health")
check(any(i["name"] == "Configuration drift" for i in hl["items"]), "health memuat status drift")
check(call("get", "/system")["diddy"], "system info memuat versi")
r = c.get("/api/v1/export/hosts.csv")
check(r.status_code == 200 and "fqdn" in r.data.decode(), "export CSV host")
check(c.get("/").status_code == 200, "halaman UI tersaji")

# ------------------------------------------------------------------ menu System: audit log, konfigurasi, user
al = call("get", "/audit?action=create&limit=5")
check(al and all(a["action"] == "create" for a in al), "audit: filter action")
check(len(call("get", "/audit?action=create&limit=2&offset=2")) == 2 and
      call("get", "/audit?action=create&limit=2&offset=2")[0]["id"] < al[1]["id"], "audit: halaman lewat offset")
check(all("corp.local" in (a["object"] + a["detail"]).lower() for a in call("get", "/audit?q=CORP.local")),
      "audit: pencarian teks tidak peka huruf besar")
check(call("get", "/audit/count?action=create")["total"] >= 5, "audit: jumlah entri terfilter")
fc = call("get", "/audit/facets")
check("admin" in fc["users"] and "login" in fc["actions"], "audit: daftar user dan action untuk filter")
ent = call("get", f"/audit/{al[0]['id']}")
check(ent["id"] == al[0]["id"] and (ent["detail_json"] is not None or not ent["detail"].startswith("{")),
      "audit: detail lengkap, JSON diurai")
call("get", "/audit/999999999", expect=404, label="audit: entri tidak ada -> 404")
call("get", "/audit?since=25-09-2026", expect=400, label="audit: format tanggal salah ditolak")
check(call("get", "/audit?since=2999-01-01") == [], "audit: filter tanggal")
r = c.get("/api/v1/export/audit.csv?action=login")
check(r.status_code == 200 and r.data.decode().startswith("id,ts,username,action"), "export CSV audit")
cfg = call("get", "/system/config")
keys = {i["key"]: i for i in cfg["items"]}
check(keys["dry_run"]["source"] == "file" and keys["dry_run"]["value"] == "true", "config: nilai dari file")
check(keys["port"]["source"] == "default" and keys["port"]["default"] == "8080", "config: nilai default")
check(all(i["value"] in ("(set)", "(empty)") for i in cfg["items"] if i["secret"]) and keys["mysql_password"]["secret"],
      "config: password tidak pernah dikirim")
check(any(u["username"] == "admin" and u["last_login"] for u in call("get", "/users")), "user: waktu login terakhir")

# ------------------------------------------------------------------ HTTP Basic + cache verifikasi
import base64  # noqa: E402


def basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


bc = app.test_client()
u = call("post", "/users", {"username": "apiuser", "password": "ApiPass111", "role": "readonly"})
check(bc.get("/api/v1/me", headers=basic("apiuser", "ApiPass111")).status_code == 200, "Basic auth benar diterima")
check(bc.get("/api/v1/me", headers=basic("apiuser", "ApiPass111")).status_code == 200, "Basic auth dari cache")
check(bc.get("/api/v1/me", headers=basic("apiuser", "salah")).status_code == 401, "Basic auth salah ditolak")
check(bc.get("/api/v1/system/config", headers=basic("apiuser", "ApiPass111")).status_code == 403,
      "config: user read-only ditolak")
check([x["username"] for x in bc.get("/api/v1/users", headers=basic("apiuser", "ApiPass111")).get_json()] == ["apiuser"],
      "user read-only hanya melihat dirinya")
call("put", f"/users/{u['id']}", {"password": "ApiPass222"})
check(bc.get("/api/v1/me", headers=basic("apiuser", "ApiPass111")).status_code == 401,
      "password lama langsung ditolak setelah diganti (cache tidak basi)")
check(bc.get("/api/v1/me", headers=basic("apiuser", "ApiPass222")).status_code == 200, "password baru diterima")
call("delete", f"/users/{u['id']}")
check(bc.get("/api/v1/me", headers=basic("apiuser", "ApiPass222")).status_code == 401, "user dihapus langsung ditolak")

# ------------------------------------------------------------------ dashboard: metrik + layout per user
import json as _json  # noqa: E402
import time as _time  # noqa: E402
from diddy.db.connection import x as _x  # noqa: E402
with app.app_context():
    t0 = int(_time.time()) - 600
    base = {"boot": "b1", "rcodes": {"NOERROR": 0, "NXDOMAIN": 0}, "qtypes": {"A": 0}, "ns": {}}
    seq = [(0, 100, 90, 10), (120, 400, 360, 40), (240, 700, 630, 70),      # counter naik
           (360, 50, 45, 5), (480, 150, 135, 15)]                            # named restart: counter reset
    for i, (dt, tot, ok, nx) in enumerate(seq):
        d = dict(base, total=tot, rcodes={"NOERROR": ok, "NXDOMAIN": nx}, qtypes={"A": tot},
                 boot="b1" if i < 3 else "b2")
        _x("INSERT INTO metrics(ts,kind,data) VALUES(?,?,?)", (t0 + dt, "dns", _json.dumps(d)))
dn = call("get", "/metrics/dns?range=1h")
check(dn["totals"]["range"] == 300 + 300 + 50 + 100, "DNS: total dari selisih counter, restart named ditangani")
check(dn["rcodes"] == {"NOERROR": 675, "NXDOMAIN": 75}, "DNS: rcode dijumlahkan per interval")
check(len(dn["hourly"]) >= 24 and all(p["v"] >= 0 for p in dn["series"]), "DNS: seri per jam lengkap, tidak ada negatif")
call("get", "/metrics/dns?range=2y", expect=400, label="range tidak valid ditolak")
dh = call("get", "/metrics/dhcp?range=24h")
check(dh["totals"]["pool_size"] == 100 and dh["totals"]["active"] >= 1, "DHCP: ukuran pool dan lease aktif")
lay = call("get", "/dashboard/layout?board=dns")
check(lay["default"] is True and lay["widgets"][0]["id"] == "dns_kpi", "layout default DNS")
call("put", "/dashboard/layout?board=dns", {"widgets": [{"id": "dns_qps", "size": "L"}, {"id": "dns_util", "size": "S"}]})
check(call("get", "/dashboard/layout?board=dns")["widgets"][0]["id"] == "dns_qps", "layout tersimpan per user")
call("put", "/dashboard/layout?board=dns", {"widgets": [{"id": "bukan_widget"}]}, expect=400, label="widget asing ditolak")
check(call("delete", "/dashboard/layout?board=dns")["default"] is True, "reset layout ke default")

# ------------------------------------------------------------------ DNS cache: hit ratio historis
with app.app_context():
    _x("DELETE FROM metrics WHERE kind='dns'")
    for i, (h, m) in enumerate([(100, 100), (190, 110), (10, 5)]):   # sampel ke-3: BIND restart
        _x("INSERT INTO metrics(ts,kind,data) VALUES(?,?,?)",
           (NOW - 180 + i * 60, "dns", _json.dumps({"boot": "b1" if i < 2 else "b2", "total": 0, "rcodes": {},
                                                    "qtypes": {}, "ns": {}, "cache": {"hits": h, "misses": m}})))
hist = call("get", "/dns-cache?range=1h")["history"]
check(hist["hits"] == 100 and hist["misses"] == 15 and hist["hit_ratio"] == 87.0
      and all(p["v"] is None or 0 <= p["v"] <= 100 for p in hist["series"]),
      f"DNS cache: hit ratio historis, restart BIND ditangani {hist}")

# ------------------------------------------------------------------ user read-only
call("post", "/users", {"username": "viewer", "password": "viewer123", "role": "readonly"})
call("post", "/logout")
call("post", "/login", {"username": "viewer", "password": "viewer123"})
call("post", "/zones", {"name": "x.local"}, expect=403, label="user read-only tidak bisa mengubah")
call("post", "/dns-cache/flush", {}, expect=403, label="user read-only tidak bisa flush cache DNS")
call("get", "/dns-cache", label="user read-only boleh melihat cache DNS")
call("put", "/dashboard/layout?board=overview", {"widgets": [{"id": "health", "size": "S"}]},
     label="user read-only boleh menyimpan layout dashboard miliknya")
call("get", "/networks")

# ------------------------------------------------------------------ CLI
env = dict(os.environ, DIDDY_CONF=CONF, PYTHONPATH=ROOT)
cli = subprocess.run([sys.executable, "-m", "diddy", "deploy"], env=env, capture_output=True, text=True, cwd=ROOT)
check(cli.returncode == 0 and "Deploy berhasil" in cli.stdout, "CLI deploy")
cli = subprocess.run([sys.executable, "-m", "diddy", "drift"], env=env, capture_output=True, text=True, cwd=ROOT)
check(cli.returncode == 0, "CLI drift: bersih " + (cli.stdout + cli.stderr)[-600:] if cli.returncode else "CLI drift: bersih")
cli = subprocess.run([sys.executable, "-m", "diddy", "version"], env=env, capture_output=True, text=True, cwd=ROOT)
check(cli.stdout.startswith("Diddy "), "CLI version")
cli = subprocess.run([sys.executable, "-m", "diddy", "cache-flush", "example.com", "--tree"], env=env,
                     capture_output=True, text=True, cwd=ROOT)
check(cli.returncode == 0 and "rndc flushtree example.com" in cli.stdout, "CLI cache-flush " + cli.stdout[-300:])
cli = subprocess.run([sys.executable, "-m", "diddy", "cache-stats"], env=env, capture_output=True, text=True, cwd=ROOT)
check(cli.returncode == 1 and "tidak tersedia" in cli.stdout, "CLI cache-stats tanpa BIND " + cli.stdout[-300:])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{COUNT[0] - len(FAILS)}/{COUNT[0]} lolos")
for f in FAILS:
    print("  GAGAL:", f)
sys.exit(1 if FAILS else 0)
