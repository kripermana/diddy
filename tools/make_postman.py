#!/usr/bin/env python3
"""Buat Postman collection Diddy (v2.1), juga bisa di-import ke Insomnia dan Bruno.

    python3 tools/make_postman.py > docs/Diddy.postman_collection.json

Urutan request dirancang untuk Collection Runner: folder 1-9 membuat objek uji di ruang terpisah
(network 198.18.X.0/24, zona apitest-X.invalid), mengujinya, lalu menghapusnya lagi.
Folder "Z" berisi operasi yang menyentuh service (deploy, drift repair, ping sweep): jalankan manual.
"""
import json
import sys

import os  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from diddy.version import VERSION  # noqa: E402

ZONE = "apitest-{{octet}}.invalid"
CIDR = "198.18.{{octet}}.0/24"
NET = "198.18.{{octet}}"


def req(name, method, path, body=None, expect=200, save=None, tests=None, desc="", query=None):
    """save = (variabel, ekspresi JS atas `j` = JSON respons)."""
    js = [f'pm.test("{method} {path} -> {expect}", () => pm.response.to.have.status({expect}));']
    if save:
        js.append("const j = pm.response.json();")
        js.append(f'if (pm.response.code === {expect}) pm.collectionVariables.set("{save[0]}", {save[1]});')
    js += tests or []
    raw = "{{baseUrl}}/api/v1" + path
    url = {"raw": raw + (("?" + "&".join(f"{k}={v}" for k, v in query)) if query else ""),
           "host": ["{{baseUrl}}"], "path": ["api", "v1"] + [p for p in path.strip("/").split("/") if p]}
    if query:
        url["query"] = [{"key": k, "value": v} for k, v in query]
    r = {"method": method, "header": [], "url": url, "description": desc}
    if body is not None:
        r["header"] = [{"key": "Content-Type", "value": "application/json"}]
        raw_body = json.dumps(body, indent=2)
        for var in ("networkId", "zoneId"):
            raw_body = raw_body.replace('"{{%s}}"' % var, "{{%s}}" % var)
        r["body"] = {"mode": "raw", "raw": raw_body, "options": {"raw": {"language": "json"}}}
    return {"name": name, "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": js}}],
            "request": r}


def folder(name, items, desc=""):
    return {"name": name, "item": items, "description": desc}


FIRST_PRE = ['pm.collectionVariables.set("octet", String(Math.floor(Math.random() * 250) + 1));']

F = [
    folder("0. Autentikasi", [
        req("Siapa saya (Basic auth)", "GET", "/me",
            tests=['pm.test("versi Diddy terbaca", () => pm.expect(pm.response.json().version).to.be.a("string"));'],
            desc="Semua request memakai HTTP Basic dari variabel `username` dan `password` di collection."),
        req("Login sesi (untuk browser)", "POST", "/login",
            {"username": "{{username}}", "password": "{{password}}"},
            desc="Hanya dibutuhkan untuk cookie sesi. Klien API cukup pakai Basic auth."),
    ]),
    folder("1. Sistem", [
        req("Health", "GET", "/health",
            tests=['pm.test("ada status overall", () => '
                   'pm.expect(pm.response.json().overall).to.be.oneOf(["ok","warn","fail"]));']),
        req("System information", "GET", "/system"),
        req("Dashboard", "GET", "/dashboard"),
        req("Pencarian global", "GET", "/search", query=[("q", "10.")]),
        req("Audit log", "GET", "/audit", query=[("limit", "20")]),
        req("Statistik DNS", "GET", "/metrics/dns", query=[("range", "24h")],
            desc="range: 1h, 6h, 24h, 7d. Berisi query/detik, request per jam, total, rcode, qtype, utilisasi."),
        req("Statistik DHCP", "GET", "/metrics/dhcp", query=[("range", "24h")],
            desc="Lease aktif dari waktu ke waktu, pemakaian pool per network, paket Kea bila tersedia."),
        req("Layout dashboard saya", "GET", "/dashboard/layout", query=[("board", "overview")]),
    ]),
    folder("2. IPAM", [
        req("Daftar network", "GET", "/networks"),
        req("Buat network uji", "POST", "/networks",
            {"cidr": CIDR, "gateway": f"{NET}.1", "dhcp_enabled": True, "dns_servers": f"{NET}.2",
             "domain_name": ZONE, "lease_time": 86400, "vlan": "999", "site": "API test",
             "comment": "Postman", "auto_reverse": False},
            expect=201, save=("networkId", "j.id")),
        req("Detail network", "GET", "/networks/{{networkId}}"),
        req("Ubah network", "PUT", "/networks/{{networkId}}",
            {"gateway": f"{NET}.1", "dhcp_enabled": True, "domain_name": ZONE, "comment": "Postman diubah"}),
        req("IP bebas berikutnya", "GET", "/networks/{{networkId}}/next_available", query=[("num", "3")]),
        req("IP map", "GET", "/networks/{{networkId}}/ipmap",
            tests=['pm.test("256 sel untuk /24", () => pm.expect(pm.response.json().cells.length).to.eql(256));']),
        req("Network duplikat ditolak", "POST", "/networks", {"cidr": CIDR}, expect=409),
    ]),
    folder("3. DHCP", [
        req("Buat DHCP range", "POST", "/ranges",
            {"network_id": "{{networkId}}", "start_ip": f"{NET}.100", "end_ip": f"{NET}.150",
             "comment": "Postman"}, expect=201, save=("rangeId", "j.id")),
        req("Ubah DHCP range", "PUT", "/ranges/{{rangeId}}", {"start_ip": f"{NET}.100", "end_ip": f"{NET}.160"}),
        req("Range berisi gateway ditolak", "POST", "/ranges",
            {"network_id": "{{networkId}}", "start_ip": f"{NET}.1", "end_ip": f"{NET}.5"}, expect=400),
        req("Daftar range di network", "GET", "/ranges", query=[("network_id", "{{networkId}}")]),
        req("Lease aktif", "GET", "/leases"),
    ]),
    folder("4. DNS", [
        req("Buat zona uji", "POST", "/zones",
            {"name": ZONE, "primary_ns": f"ns1.{ZONE}", "admin_email": "noc@example.invalid",
             "ns_ip": f"{NET}.2", "ttl": 3600, "comment": "Postman"},
            expect=201, save=("zoneId", "j.id")),
        req("Daftar zona", "GET", "/zones"),
        req("Detail zona", "GET", "/zones/{{zoneId}}"),
        req("Ubah zona", "PUT", "/zones/{{zoneId}}",
            {"primary_ns": f"ns1.{ZONE}", "admin_email": "noc@example.invalid", "ttl": 1800}),
        req("Record A", "POST", "/records",
            {"zone_id": "{{zoneId}}", "name": "app", "type": "A", "value": f"{NET}.30", "ttl": 300},
            expect=201, save=("recordId", "j.id")),
        req("Record MX", "POST", "/records",
            {"zone_id": "{{zoneId}}", "name": "@", "type": "MX", "value": f"10 mail.{ZONE}"},
            expect=201, save=("recordMxId", "j.id")),
        req("Ubah record", "PUT", "/records/{{recordId}}",
            {"name": "app", "type": "A", "value": f"{NET}.31", "ttl": 600}),
        req("CNAME bentrok ditolak", "POST", "/records",
            {"zone_id": "{{zoneId}}", "name": "app", "type": "CNAME", "value": f"www.{ZONE}"}, expect=400),
        req("Isi zona", "GET", "/zones/{{zoneId}}/records"),
    ]),
    folder("5. Hosts", [
        req("Host di IP bebas berikutnya", "POST", "/hosts",
            {"fqdn": f"srv01.{ZONE}", "ip": f"next:{CIDR}", "mac": "02:00:5e:00:53:01",
             "configure_dns": True, "configure_dhcp": True, "comment": "Postman"},
            expect=201, save=("hostId", "j.id"),
            desc="`ip` bisa berupa IP, atau `next:<cidr>` / `func:nextavailableip:<cidr>`."),
        req("Detail host", "GET", "/hosts/{{hostId}}"),
        req("Ubah host", "PUT", "/hosts/{{hostId}}",
            {"fqdn": f"srv01.{ZONE}", "ip": f"{NET}.3", "mac": "02:00:5e:00:53:01",
             "configure_dns": True, "configure_dhcp": True, "comment": "Postman diubah"}),
        req("IP duplikat ditolak", "POST", "/hosts", {"fqdn": f"dup.{ZONE}", "ip": f"{NET}.3"}, expect=409),
        req("Import host CSV", "POST", "/import/hosts",
            {"csv": f"fqdn,ip,mac,configure_dns,configure_dhcp,comment\nimport01.{ZONE},{NET}.40,,1,0,Postman\n"},
            tests=['pm.test("1 host masuk", () => pm.expect(pm.response.json().imported).to.eql(1));']),
        req("Cari host hasil import", "GET", "/hosts",
            tests=['const want = "import01.apitest-" + pm.collectionVariables.get("octet") + ".invalid";',
                   'const h = pm.response.json().find(x => x.fqdn === want);',
                   'if (h) pm.collectionVariables.set("importHostId", h.id);',
                   'pm.test("host import ketemu", () => pm.expect(h).to.be.an("object"));']),
    ]),
    folder("6. Resolver", [
        req("Setting resolver", "GET", "/dns-settings",
            desc="Hanya dibaca. Mengubahnya (PUT) memengaruhi resolver produksi, contohnya ada di folder Z."),
        req("Daftar conditional forwarder", "GET", "/forwarders"),
        req("Buat conditional forwarder", "POST", "/forwarders",
            {"domain": "fwd-{{octet}}.invalid", "servers": "192.0.2.53", "policy": "only", "comment": "Postman"},
            expect=201, save=("forwarderId", "j.id")),
        req("Ubah conditional forwarder", "PUT", "/forwarders/{{forwarderId}}",
            {"domain": "fwd-{{octet}}.invalid", "servers": "192.0.2.53, 192.0.2.54", "policy": "first"}),
    ]),
    folder("7. Deploy dan drift (baca saja)", [
        req("Preview file yang akan ditulis", "GET", "/deploy/preview"),
        req("Status drift", "GET", "/drift"),
    ]),
    folder("8. Users dan export", [
        req("Daftar user", "GET", "/users"),
        req("Buat user read-only", "POST", "/users",
            {"username": "apitest{{octet}}", "password": "ApiTest12345", "role": "readonly"},
            expect=201, save=("userId", "j.id")),
        req("Ganti password user", "PUT", "/users/{{userId}}", {"password": "ApiTest67890"}),
        req("Simpan layout dashboard DHCP", "PUT", "/dashboard/layout?board=dhcp",
            {"widgets": [{"id": "dhcp_kpi", "size": "L"}, {"id": "dhcp_pools", "size": "M"}]}),
        req("Reset layout dashboard DHCP", "DELETE", "/dashboard/layout?board=dhcp"),
        req("Export hosts CSV", "GET", "/export/hosts.csv"),
        req("Export records CSV", "GET", "/export/records.csv"),
    ]),
    folder("9. Bersih-bersih", [
        req("Hapus record A", "DELETE", "/records/{{recordId}}"),
        req("Hapus record MX", "DELETE", "/records/{{recordMxId}}"),
        req("Hapus host import", "DELETE", "/hosts/{{importHostId}}"),
        req("Hapus host", "DELETE", "/hosts/{{hostId}}"),
        req("Hapus range", "DELETE", "/ranges/{{rangeId}}"),
        req("Hapus forwarder", "DELETE", "/forwarders/{{forwarderId}}"),
        req("Hapus user", "DELETE", "/users/{{userId}}"),
        req("Hapus zona", "DELETE", "/zones/{{zoneId}}"),
        req("Hapus network", "DELETE", "/networks/{{networkId}}"),
    ]),
    folder("Z. Menyentuh service (jalankan manual)", [
        req("Deploy ke BIND/Kea/dnsdist", "POST", "/deploy",
            desc="Menerapkan SEMUA perubahan tertunda ke service. Hasil 422 berarti validasi gagal dan tidak ada "
                 "file yang diubah."),
        req("Pulihkan drift", "POST", "/drift/repair"),
        req("Refresh DDNS sekarang", "POST", "/ddns/refresh"),
        req("Ping sweep network", "POST", "/networks/{{networkId}}/discover",
            desc="Mengirim ping ke seluruh network (maks /22)."),
        req("Contoh ubah resolver", "PUT", "/dns-settings",
            {"recursion": True, "auto_allow_ipam": True, "upstream_mode": "plain", "forwarders": "10.1.1.53",
             "forward_policy": "first", "dnssec_validation": "auto"},
            desc="PERHATIAN: mengganti setting resolver produksi setelah deploy."),
        req("Logout sesi", "POST", "/logout"),
    ], desc="Folder ini sengaja tidak ikut Collection Runner. Jalankan satu per satu dengan sadar."),
]

collection = {
    "info": {
        "name": f"Diddy {VERSION} API",
        "description": "REST API Diddy: IPAM, DNS, DHCP, resolver, deploy.\n\n"
                       "Isi variabel collection `baseUrl`, `username`, `password`, lalu jalankan folder 0 sampai 9 "
                       "dengan Collection Runner. Objek uji dibuat di network 198.18.X.0/24 dan zona "
                       "apitest-X.invalid, lalu dihapus lagi di folder 9.\n\n(c) 2026 kripermana, MIT License.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "auth": {"type": "basic", "basic": [{"key": "username", "value": "{{username}}", "type": "string"},
                                        {"key": "password", "value": "{{password}}", "type": "string"}]},
    "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": [
        'if (!pm.collectionVariables.get("octet")) pm.collectionVariables.set("octet", '
        'String(Math.floor(Math.random() * 250) + 1));']}}],
    "variable": [{"key": "baseUrl", "value": "http://127.0.0.1:8080"},
                 {"key": "username", "value": "admin"},
                 {"key": "password", "value": ""},
                 {"key": "octet", "value": ""}],
    "item": F,
}
first = F[0]["item"][0]
first["event"].append({"listen": "prerequest", "script": {"type": "text/javascript", "exec": FIRST_PRE}})
json.dump(collection, sys.stdout, indent=2, ensure_ascii=False)
print()
