#!/usr/bin/env python3
"""Uji API Diddy terhadap server yang sedang berjalan. Hanya butuh Python 3 standar.

    python3 tools/api_test.py -u admin -p 'PASSWORD'                        # baca saja, aman
    python3 tools/api_test.py -u admin -p 'PASSWORD' --write                # + siklus buat/ubah/hapus
    python3 tools/api_test.py --url https://ddi01.corp.local -u admin -p '...' --insecure

Mode --write membuat objek uji di ruang terpisah (network 198.18.X.0/24, zona apitest-X.invalid,
forwarder, user) lalu menghapusnya lagi. Tidak pernah memanggil deploy, jadi BIND/Kea tidak disentuh.
Setelahnya banner "pending changes" akan muncul walaupun isi database sudah kembali seperti semula.
Exit code 0 berarti semua lolos.
"""
import argparse
import base64
import json
import random
import ssl
import sys
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser(description="Uji API Diddy")
ap.add_argument("--url", default="http://127.0.0.1:8080", help="alamat Diddy (default http://127.0.0.1:8080)")
ap.add_argument("-u", "--user", default="admin")
ap.add_argument("-p", "--password", required=True)
ap.add_argument("--write", action="store_true", help="uji juga create/update/delete (objek uji dihapus lagi)")
ap.add_argument("--insecure", action="store_true", help="abaikan sertifikat HTTPS (self-signed)")
ap.add_argument("-v", "--verbose", action="store_true", help="tampilkan body respons")
args = ap.parse_args()

BASE = args.url.rstrip("/") + "/api/v1"
AUTH = "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
CTX = ssl._create_unverified_context() if args.insecure else None
RESULTS = []


def call(method, path, body=None, expect=200, label=None, auth=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if auth:
        req.add_header("Authorization", AUTH)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        print(f"\nTidak bisa terhubung ke {args.url}: {e}")
        sys.exit(2)
    ms = (time.time() - t0) * 1000
    try:
        payload = json.loads(raw) if raw and raw[:1] in (b"{", b"[") else raw.decode(errors="replace")
    except ValueError:
        payload = raw.decode(errors="replace")
    ok = status == expect
    RESULTS.append((ok, method, path, status, expect, ms, label))
    mark = "OK  " if ok else "GAGAL"
    print(f"{mark} {method:<6} {path:<48} {status} ({ms:5.0f} ms){'  ' + label if label else ''}")
    if not ok or args.verbose:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        print("       " + text[:400])
    return payload if ok else None


def section(title):
    print(f"\n== {title}")


# ------------------------------------------------------------------ baca saja
section("Autentikasi")
call("GET", "/me", expect=401, label="tanpa login harus ditolak", auth=False)
me = call("GET", "/me")
if me is None:
    print("\nLogin gagal. Cek username/password dan alamat server.")
    sys.exit(1)
print(f"       login sebagai {me['user']['username']} ({me['user']['role']}), Diddy {me['version']}")

section("Sistem")
h = call("GET", "/health")
if h:
    for i in h["items"]:
        print(f"       [{i['state']:<7}] {i['name']}: {i['detail'][:70]}")
call("GET", "/system")
call("GET", "/dashboard")
call("GET", "/search?q=10.")
audit = call("GET", "/audit?limit=5") or []
call("GET", "/audit?action=login&limit=5&offset=0")
call("GET", "/audit/count?action=login")
call("GET", "/audit/facets")
if audit:
    call("GET", f"/audit/{audit[0]['id']}")
if me["user"]["role"] == "admin":
    call("GET", "/system/config")

section("IPAM, DHCP, DNS")
nets = call("GET", "/networks") or []
call("GET", "/ranges")
call("GET", "/leases")
call("GET", "/hosts")
zones = call("GET", "/zones") or []
if nets:
    call("GET", f"/networks/{nets[0]['id']}")
    call("GET", f"/networks/{nets[0]['id']}/next_available?num=3")
    small = next((n for n in nets if n["cidr"].count(".") == 3 and int(n["cidr"].split("/")[1]) >= 20), None)
    if small:
        call("GET", f"/networks/{small['id']}/ipmap")
if zones:
    call("GET", f"/zones/{zones[0]['id']}")
    call("GET", f"/zones/{zones[0]['id']}/records")
    for fmt in ("bind", "csv", "json"):
        call("GET", f"/zones/{zones[0]['id']}/export?format={fmt}")
call("GET", "/zones/export")

section("Resolver, deploy, drift")
call("GET", "/dns-settings")
call("GET", "/forwarders")
call("GET", "/deploy/preview")
call("GET", "/drift")
call("GET", "/services")
call("GET", "/users")
for kind in ("networks", "hosts", "records", "leases", "audit"):
    call("GET", f"/export/{kind}.csv")

# ------------------------------------------------------------------ siklus tulis
if args.write:
    if me["user"]["role"] != "admin":
        print("\n--write butuh user admin. User read-only seharusnya ditolak:")
        call("POST", "/zones", {"name": "x.invalid"}, expect=403, label="read-only ditolak")
    else:
        was_pending = me["pending"]
        o = random.randint(1, 250)
        cidr, zone = f"198.18.{o}.0/24", f"apitest-{o}.invalid"
        created = {}
        section(f"Siklus tulis (network {cidr}, zona {zone})")
        z = call("POST", "/zones", {"name": zone, "primary_ns": f"ns1.{zone}", "admin_email": "noc@example.invalid",
                                    "ns_ip": f"198.18.{o}.2", "comment": "api_test.py"}, expect=201)
        n = call("POST", "/networks", {"cidr": cidr, "gateway": f"198.18.{o}.1", "dhcp_enabled": True,
                                       "domain_name": zone, "comment": "api_test.py"}, expect=201)
        if z and n:
            created.update(zone=z["id"], network=n["id"])
            zf = f"imp1 IN A 198.18.{o}.21\nimp2 IN CNAME imp1\ncaa IN CAA 0 issue x\n"
            pv = call("POST", "/zones/import/preview", {"text": zf, "zone_id": z["id"]})
            if pv and pv["totals"]["add_count"] != 2:
                RESULTS.append((False, "POST", "/zones/import/preview", 200, 200, 0, "harus 2 record ditambah"))
            call("POST", "/zones/import", {"text": zf, "zone_id": z["id"]})   # ikut terhapus bersama zonanya
            call("GET", f"/networks/{n['id']}")
            call("PUT", f"/networks/{n['id']}", {"gateway": f"198.18.{o}.1", "dhcp_enabled": True,
                                                 "domain_name": zone, "comment": "api_test.py diubah"})
            r = call("POST", "/ranges", {"network_id": n["id"], "start_ip": f"198.18.{o}.100",
                                         "end_ip": f"198.18.{o}.150"}, expect=201)
            if r:
                created["range"] = r["id"]
                call("PUT", f"/ranges/{r['id']}", {"start_ip": f"198.18.{o}.100", "end_ip": f"198.18.{o}.160"})
            call("POST", "/ranges", {"network_id": n["id"], "start_ip": f"198.18.{o}.1",
                                     "end_ip": f"198.18.{o}.5"}, expect=400, label="range berisi gateway ditolak")
            hst = call("POST", "/hosts", {"fqdn": f"srv01.{zone}", "ip": f"next:{cidr}", "mac": "02:00:5e:00:53:01",
                                          "configure_dns": True, "configure_dhcp": True}, expect=201)
            if hst:
                created["host"] = hst["id"]
                print(f"       next-available memberi {hst['ip']}")
                call("GET", f"/hosts/{hst['id']}")
                call("PUT", f"/hosts/{hst['id']}", {"fqdn": f"srv01.{zone}", "ip": hst["ip"],
                                                    "mac": "02:00:5e:00:53:01", "configure_dns": True,
                                                    "configure_dhcp": True, "comment": "diubah"})
                call("POST", "/hosts", {"fqdn": f"dup.{zone}", "ip": hst["ip"]}, expect=409,
                     label="IP duplikat ditolak")
            imp = call("POST", "/import/hosts", {"csv": f"fqdn,ip\nimport01.{zone},198.18.{o}.40\n"})
            if imp and imp["imported"] == 1:
                found = [x for x in (call("GET", "/hosts") or []) if x["fqdn"] == f"import01.{zone}"]
                if found:
                    created["import_host"] = found[0]["id"]
            rec = call("POST", "/records", {"zone_id": z["id"], "name": "www", "type": "CNAME",
                                            "value": f"srv01.{zone}"}, expect=201)
            if rec:
                created["record"] = rec["id"]
                call("PUT", f"/records/{rec['id']}", {"name": "www", "type": "CNAME", "value": f"srv01.{zone}",
                                                      "ttl": 300})
            call("POST", "/records", {"zone_id": z["id"], "name": "www", "type": "A", "value": "198.18.0.9"},
                 expect=400, label="record lain di nama CNAME ditolak")
            call("GET", f"/zones/{z['id']}/records")
            call("GET", f"/networks/{n['id']}/ipmap")
            call("GET", f"/networks/{n['id']}/next_available?num=2")
            call("GET", f"/search?q={zone}")
        f = call("POST", "/forwarders", {"domain": f"fwd-{o}.invalid", "servers": "192.0.2.53",
                                         "policy": "only", "comment": "api_test.py"}, expect=201)
        if f:
            created["forwarder"] = f["id"]
            call("PUT", f"/forwarders/{f['id']}", {"domain": f"fwd-{o}.invalid",
                                                   "servers": "192.0.2.53, 192.0.2.54", "policy": "first"})
        u = call("POST", "/users", {"username": f"apitest{o}", "password": "ApiTest12345", "role": "readonly"},
                 expect=201)
        if u:
            created["user"] = u["id"]
            call("PUT", f"/users/{u['id']}", {"password": "ApiTest67890"})
        call("GET", "/deploy/preview")

        section("Bersih-bersih objek uji")
        for key, path in (("record", "/records/"), ("import_host", "/hosts/"), ("host", "/hosts/"),
                          ("range", "/ranges/"), ("forwarder", "/forwarders/"), ("user", "/users/"),
                          ("zone", "/zones/"), ("network", "/networks/")):
            if key in created:
                call("DELETE", f"{path}{created[key]}")
        left = [x for x in (call("GET", "/networks") or []) if x["cidr"] == cidr] + \
               [x for x in (call("GET", "/zones") or []) if x["name"] == zone]
        ok = not left
        RESULTS.append((ok, "CHECK", "sisa objek uji", 0, 0, 0, None))
        print(("OK  " if ok else "GAGAL") + " semua objek uji terhapus" + ("" if ok else f": {left}"))
        if not was_pending:
            print("\n       Catatan: banner 'pending changes' sekarang muncul karena ada perubahan lalu dihapus lagi.\n"
                  "       Isi database sudah sama seperti sebelum uji; deploy tidak wajib.")

# ------------------------------------------------------------------ ringkasan
fails = [r for r in RESULTS if not r[0]]
timed = sorted((r for r in RESULTS if r[5]), key=lambda r: r[5])
avg = sum(r[5] for r in timed) / max(1, len(timed))
slow = f", paling lambat {timed[-1][5]:.0f} ms ({timed[-1][1]} {timed[-1][2]})" if timed else ""
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} lolos, rata-rata {avg:.0f} ms{slow}")
for r in fails:
    print(f"  GAGAL {r[1]} {r[2]}: dapat {r[3]}, harusnya {r[4]}")
sys.exit(1 if fails else 0)
