#!/usr/bin/env python3
"""Simulator klien DHCP untuk menguji Kea dan DDNS Diddy: banyak MAC dan hostname acak.

Setiap klien menjalankan DORA (DISCOVER, OFFER, REQUEST, ACK) dengan MAC acak dan hostname
(option 12) pilihan. Hasilnya dicek ke DNS dengan dig (record A dan PTR dari DDNS Diddy) dan,
bila API Diddy diisi, ke angka lease dan utilisasi pool di Diddy.

Contoh:
  sudo python3 tools/dhcp_sim.py -c lab.ini                       # semua skenario di config
  sudo python3 tools/dhcp_sim.py -c lab.ini --only sanity,weird    # skenario tertentu
  sudo python3 tools/dhcp_sim.py -i eth1 --network 10.151.10.0/24 --clients 50   # cepat, tanpa config
  sudo python3 tools/dhcp_sim.py -c lab.ini --release-all          # lepas semua lease buatan tool ini
  python3 tools/dhcp_sim.py --example-config > lab.ini             # contoh config

Skenario: sanity, reservation, weird, duplicate, fill, exhaust, release, expire.
Butuh root (raw socket) dan scapy (pip install scapy). dig opsional, untuk cek DNS.
Jalankan dari mesin yang satu L2 dengan interface Kea, bukan dari server Kea itu sendiri.
Jangan dijalankan di VLAN produksi: tool ini sengaja bisa menghabiskan pool.
(c) 2026 kripermana, MIT License
"""
import argparse
import base64
import configparser
import datetime
import getpass
import ipaddress
import json
import math
import os
import random
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SCENARIOS = ["sanity", "reservation", "weird", "duplicate", "fill", "exhaust", "release", "expire"]
EMPTY = "(kosong)"   # di daftar hostname: klien tidak mengirim option 12 sama sekali

EXAMPLE = """\
; Config dhcp_sim.py. Semua nilai bisa ditimpa lewat opsi command line (lihat --help).
[lab]
interface   = eth1                   ; interface yang satu L2 dengan Kea
network     = 10.151.10.0/24         ; network yang diuji; lease di luar network ini menghentikan tool
pool        = 10.151.10.51 - 10.151.10.250   ; kosongkan bila diambil dari Diddy (butuh diddy_url)
dns_server  = 10.151.10.2            ; server yang ditanya dig; kosong = tanpa cek DNS
domain      =                        ; kosong = diambil dari network di Diddy (DDNS domain, lalu domain name)
mac_prefix  = 02:dd                  ; semua MAC buatan tool diawali ini (02 = locally administered)
timeout     = 3                      ; detik menunggu OFFER/ACK
retries     = 1                      ; ulangi DISCOVER bila tidak ada OFFER
parallel    = 10                     ; klien yang diproses bersamaan
ddns_wait   = 75                     ; detik menunggu refresh DDNS Diddy (ddns_refresh_interval + jeda)
lease_time  =                        ; detik, untuk skenario expire; kosong = dari ACK
state_file  = .dhcp_sim-state.json   ; lease buatan tool, untuk --release-all
log_dir     = .                      ; tempat file log JSON

; Opsional: cocokkan angka dengan Diddy lewat API. Password dari --diddy-password,
; variabel DIDDY_PASSWORD, atau ditanyakan saat mulai.
diddy_url   =
diddy_user  = admin

[hostnames]
style    = random                    ; random | list | file
prefixes = laptop, pc, hp, kasir, cam, printer
suffix   = 6                         ; panjang akhiran acak: laptop-k3x9qa
list     =                           ; bila style = list: dipakai bergiliran
file     =                           ; bila style = file: satu hostname per baris
weird    = Laptop_Budi, PC Kantor, HP'S-PHONE, UPPERCASE-PC, pc1.domainlain.com, ___, (kosong),
           nama-yang-sangat-panjang-sekali-melebihi-batas-enam-puluh-tiga-karakter-label-dns

[scenarios]
sanity      = 5                      ; jumlah klien normal; 0 = lewati
reservation =                        ; MAC = IP yang diharapkan, pisahkan koma: 02:00:00:00:00:20 = 10.151.10.20
weird       = yes                    ; hostname di [hostnames] weird
duplicate   = kasir-01 x 2           ; hostname sama dari beberapa MAC; kosong = lewati
fill        = 70, 90                 ; isi pool sampai persentase ini; kosong = lewati
exhaust     = 20                     ; klien tambahan setelah pool penuh (harus tanpa OFFER); 0 = lewati
release     = yes                    ; RELEASE semua lease buatan tool di akhir
expire      = no                     ; yes = tidak RELEASE, tunggu lease habis lalu cek record DDNS hilang
"""


# ------------------------------------------------------------------ config
def load_config(args):
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
    cp.read_string(EXAMPLE)
    # nilai contoh yang spesifik lab tidak dipakai sebagai default
    cp["lab"]["interface"] = cp["lab"]["network"] = cp["lab"]["pool"] = cp["lab"]["dns_server"] = ""
    if args.config:
        if not os.path.exists(args.config):
            die(f"file config tidak ada: {args.config}")
        cp.read(args.config)
    lab, hn, sc = cp["lab"], cp["hostnames"], cp["scenarios"]
    over = {"interface": args.interface, "network": args.network, "pool": args.pool, "dns_server": args.server,
            "domain": args.domain, "mac_prefix": args.mac_prefix, "timeout": args.timeout,
            "parallel": args.parallel, "ddns_wait": args.ddns_wait, "diddy_url": args.diddy_url,
            "diddy_user": args.diddy_user, "state_file": args.state_file}
    for k, v in over.items():
        if v is not None:
            lab[k] = str(v)
    if args.clients is not None:          # mode cepat: hanya N klien acak
        for s in SCENARIOS:
            sc[s] = "no"
        sc["sanity"] = str(args.clients)
        sc["release"] = "no"
    return lab, hn, sc


def split_list(v):
    return [x.strip() for x in re.split(r"[,\n]", v or "") if x.strip()]


def truthy(v):
    return (v or "").strip().lower() in ("yes", "true", "1", "on", "ya")


def die(msg):
    print(f"GAGAL: {msg}", file=sys.stderr)
    sys.exit(2)


# ------------------------------------------------------------------ hostname dan MAC
def dyn_label(hostname):
    """Sama dengan diddy/dns/ddns.py: label DNS yang dibuat Diddy dari hostname lease."""
    n = (hostname or "").strip().lower().split(".")[0]
    return re.sub(r"[^a-z0-9-]", "-", n).strip("-")[:63].strip("-")


class Names:
    def __init__(self, hn):
        self.style = (hn.get("style") or "random").strip()
        self.prefixes = split_list(hn.get("prefixes")) or ["host"]
        self.suffix = int(hn.get("suffix") or 6)
        self.items, self.i = [], 0
        if self.style == "list":
            self.items = split_list(hn.get("list"))
        elif self.style == "file":
            with open(hn.get("file")) as f:
                self.items = [x.strip() for x in f if x.strip()]
        if self.style in ("list", "file") and not self.items:
            die(f"hostnames style = {self.style}, tapi daftarnya kosong")
        self.weird = split_list(hn.get("weird"))

    def next(self):
        if self.items:
            n = self.items[self.i % len(self.items)]
            self.i += 1
            return n
        tail = "".join(random.choices(string.ascii_lowercase + string.digits, k=self.suffix))
        return f"{random.choice(self.prefixes)}-{tail}"


class Macs:
    def __init__(self, prefix):
        self.pre = [int(b, 16) for b in prefix.split(":") if b]
        if len(self.pre) > 5 or any(not 0 <= b <= 255 for b in self.pre):
            die(f"mac_prefix tidak valid: {prefix}")
        self.used = set()

    def next(self):
        while True:
            m = ":".join(f"{b:02x}" for b in self.pre + [random.randint(0, 255) for _ in range(6 - len(self.pre))])
            if m not in self.used:
                self.used.add(m)
                return m


# ------------------------------------------------------------------ DHCP
class Dhcp:
    def __init__(self, iface, timeout, retries, network):
        self.iface, self.timeout, self.retries, self.network = iface, timeout, retries, network
        self.abort = None   # alasan berhenti (lease di luar network)

    @staticmethod
    def _opts(pkt):
        return {o[0]: o[1] for o in pkt[DHCP].options if isinstance(o, tuple)}

    def dora(self, mac, hostname):
        """Satu klien: DISCOVER, OFFER, REQUEST, ACK. Mengembalikan dict hasil."""
        r = {"mac": mac, "hostname": hostname, "ip": None, "status": "NO_OFFER", "server_id": None,
             "server_mac": None, "lease_time": None, "t": time.time()}
        if self.abort:
            r["status"] = "SKIPPED"
            return r
        xid = random.getrandbits(32)
        base = Ether(src=mac, dst="ff:ff:ff:ff:ff:ff") / IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=68, dport=67)
        boot = BOOTP(chaddr=mac2str(mac), xid=xid, flags=0x8000)
        hn = [("hostname", hostname.encode())] if hostname else []
        disc = base / boot / DHCP(options=[("message-type", "discover")] + hn + [("param_req_list", [1, 3, 6, 15, 51]), "end"])
        offer = None
        for _ in range(1 + self.retries):
            offer = srp1(disc, iface=self.iface, timeout=self.timeout, verbose=0)
            if offer is not None and DHCP in offer:
                break
        if offer is None or DHCP not in offer:
            return r
        o = self._opts(offer)
        if o.get("message-type") != 2:
            r["status"] = "NAK" if o.get("message-type") == 6 else "NO_OFFER"
            return r
        ip = offer[BOOTP].yiaddr
        if ipaddress.ip_address(ip) not in self.network:
            self.abort = f"OFFER {ip} dari {o.get('server_id')} di luar {self.network}: server DHCP yang salah?"
            r.update(status="OUTSIDE", ip=ip)
            return r
        r.update(ip=ip, server_id=o.get("server_id"), server_mac=offer[Ether].src)
        req = base / boot / DHCP(options=[("message-type", "request"), ("requested_addr", ip),
                                          ("server_id", r["server_id"])] + hn + [("param_req_list", [1, 3, 6, 15, 51]), "end"])
        ack = srp1(req, iface=self.iface, timeout=self.timeout, verbose=0)
        if ack is None or DHCP not in ack:
            r["status"] = "NO_ACK"
            return r
        a = self._opts(ack)
        r["status"] = {5: "ACK", 6: "NAK"}.get(a.get("message-type"), "NO_ACK")
        r["lease_time"] = a.get("lease_time")
        r["t"] = time.time()
        return r

    def release(self, lease):
        pkt = (Ether(src=lease["mac"], dst=lease["server_mac"]) / IP(src=lease["ip"], dst=lease["server_id"])
               / UDP(sport=68, dport=67)
               / BOOTP(chaddr=mac2str(lease["mac"]), ciaddr=lease["ip"], xid=random.getrandbits(32))
               / DHCP(options=[("message-type", "release"), ("server_id", lease["server_id"]), "end"]))
        sendp(pkt, iface=self.iface, verbose=0)


# ------------------------------------------------------------------ DNS dan Diddy
def dig(server, name, rtype):
    if not server or not shutil.which("dig"):
        return None
    args = ["dig", "+short", "+time=2", "+tries=2", "@" + server]
    args += ["-x", name] if rtype == "PTR" else [name, rtype]
    out = subprocess.run(args, capture_output=True, text=True).stdout
    return sorted(x.rstrip(".") for x in out.split() if x and not x.startswith(";"))


class Diddy:
    def __init__(self, url, user, password):
        self.url = url.rstrip("/") + "/api/v1"
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def get(self, path):
        req = urllib.request.Request(self.url + path, headers={"Authorization": self.auth})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def network(self, cidr):
        return next((n for n in self.get("/networks") if n["cidr"] == cidr), None)

    def pool(self, cidr):
        return next((p for p in self.get("/metrics/dhcp?range=1h")["pools"] if p["cidr"] == cidr), None)

    def leases(self):
        return {x["mac"].lower(): x for x in self.get("/leases")}


# ------------------------------------------------------------------ jalannya uji
class Run:
    def __init__(self, args):
        self.args = args
        lab, hn, sc = load_config(args)
        self.lab, self.sc = lab, sc
        if not lab.get("interface"):
            die("interface belum diisi (config [lab] interface atau -i)")
        if not lab.get("network"):
            die("network belum diisi (config [lab] network atau --network)")
        self.network = ipaddress.ip_network(lab["network"], strict=False)
        self.names = Names(hn)
        self.macs = Macs(lab.get("mac_prefix") or "02:dd")
        self.dhcp = Dhcp(lab["interface"], float(lab.get("timeout") or 3), int(lab.get("retries") or 0), self.network)
        self.parallel = max(1, int(lab.get("parallel") or 10))
        self.dns = (lab.get("dns_server") or "").strip()
        self.domain = (lab.get("domain") or "").strip().rstrip(".")
        self.ddns_wait = int(lab.get("ddns_wait") or 75)
        self.state_file = lab.get("state_file") or ".dhcp_sim-state.json"
        self.only = set(split_list(args.only)) if args.only else None
        if self.only and self.only - set(SCENARIOS):
            die("skenario tidak dikenal: " + ", ".join(sorted(self.only - set(SCENARIOS))))
        self.diddy = None
        if lab.get("diddy_url"):
            pw = args.diddy_password or os.environ.get("DIDDY_PASSWORD") or getpass.getpass(
                f"Password Diddy untuk {lab.get('diddy_user') or 'admin'}: ")
            self.diddy = Diddy(lab["diddy_url"], lab.get("diddy_user") or "admin", pw)
        self.pool = self._pool(lab.get("pool"))
        self.leases = []          # semua hasil DORA, urut waktu
        self.checks = []          # (skenario, nama, ok True/False/None, detail)
        self.lock = threading.Lock()
        self.started = time.time()

    # ---------------------------------------------------------- persiapan
    def _pool(self, text):
        rngs = []
        for part in split_list(text):
            lo, _, hi = part.partition("-")
            rngs.append((ipaddress.ip_address(lo.strip()), ipaddress.ip_address((hi or lo).strip())))
        if not rngs and self.diddy:
            net = self.diddy.network(str(self.network))
            if net:
                rngs = [(ipaddress.ip_address(r["start_ip"]), ipaddress.ip_address(r["end_ip"]))
                        for r in self.diddy.get(f"/ranges?network_id={net['id']}")]
        return rngs

    def pool_size(self):
        return sum(int(hi) - int(lo) + 1 for lo, hi in self.pool)

    def in_pool(self, ip):
        a = ipaddress.ip_address(ip)
        return any(lo <= a <= hi for lo, hi in self.pool)

    def enabled(self, name):
        if self.only is not None:
            return name in self.only
        v = (self.sc.get(name) or "").strip().lower()
        return v not in ("", "0", "no", "false", "off", "tidak")

    def resolve_domain(self):
        if self.domain or not self.diddy:
            return "config" if self.domain else None
        net = self.diddy.network(str(self.network))
        if not net:
            print(f"  ! network {self.network} tidak ada di Diddy")
            return None
        if not net.get("ddns_enabled"):
            print(f"  ! DDNS belum aktif di network {self.network}: record DNS tidak akan dibuat")
        self.domain = (net.get("ddns_domain") or net.get("domain_name") or "").rstrip(".")
        return "DDNS domain" if net.get("ddns_domain") else "domain name" if self.domain else None

    def plan(self):
        src = self.resolve_domain()
        dns = (f"{self.dns} (domain {self.domain}, dari {src})" if self.dns and self.domain
               else "tidak dicek" + ("" if shutil.which("dig") else " (dig tidak terpasang)"))
        chosen = [s for s in SCENARIOS if self.enabled(s)]
        print(f"Diddy DHCP simulator | {self.lab['interface']} | network {self.network}")
        print(f"  pool      : {', '.join(f'{lo}-{hi}' for lo, hi in self.pool) or '(tidak diketahui)'}"
              f" ({self.pool_size()} alamat)")
        print(f"  DNS       : {dns}")
        print(f"  Diddy API : {self.lab.get('diddy_url') or 'tidak dipakai'}")
        print(f"  MAC       : {self.lab.get('mac_prefix')}:xx..., {self.parallel} klien paralel")
        print(f"  skenario  : {', '.join(chosen) or '(tidak ada)'}")
        return chosen

    # ---------------------------------------------------------- utilitas
    def check(self, scen, name, ok, detail=""):
        self.checks.append({"scenario": scen, "check": name, "ok": ok, "detail": detail})
        tag = "LOLOS" if ok else "DILEWATI" if ok is None else "GAGAL"
        print(f"      {name:<58} {tag}" + (f"  {detail}" if detail and not ok else ""))

    def clients(self, specs, label):
        """Jalankan DORA untuk daftar (mac, hostname) secara paralel, cetak tiap hasil."""
        print(f"      {label}: {len(specs)} klien")
        with ThreadPoolExecutor(self.parallel) as ex:
            res = list(ex.map(lambda s: self.dhcp.dora(*s), specs))
        for r in res:
            if self.args.verbose or r["status"] != "ACK":
                print(f"        {str(r['hostname'] or EMPTY)[:28]:<28} {r['mac']}  {r['ip'] or '-':<15} {r['status']}")
        with self.lock:
            self.leases += res
            self.save_state()
        if self.dhcp.abort:
            die(self.dhcp.abort)
        return res

    def save_state(self):
        st = self.load_state()
        for r in self.leases:
            if r["status"] == "ACK":
                st[r["mac"]] = {k: r[k] for k in ("ip", "hostname", "server_id", "server_mac")}
        with open(self.state_file, "w") as f:
            json.dump(st, f, indent=1)

    def load_state(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def current_leased(self):
        """Lease aktif di pool menurut Diddy; tanpa Diddy: hitungan lease buatan tool ini saja."""
        if self.diddy:
            p = self.diddy.pool(str(self.network))
            if p:
                return p["leased"], p["pct"]
        n = len({r["mac"] for r in self.leases if r["status"] == "ACK" and self.in_pool(r["ip"])})
        return n, round(n * 100 / self.pool_size(), 1) if self.pool_size() else 0

    def wait(self, secs, why):
        print(f"      menunggu {secs} detik: {why}", end="", flush=True)
        end = time.time() + secs
        while time.time() < end:
            time.sleep(min(5, max(0, end - time.time())))
            print(".", end="", flush=True)
        print()

    # ---------------------------------------------------------- DDNS
    def verify_dns(self, groups):
        if not (self.dns and self.domain and shutil.which("dig")):
            self.check("ddns", "cek DNS", None, "dns_server/domain kosong atau dig tidak ada")
            return
        lts = [r["lease_time"] for _, rows in groups for r in rows if r["status"] == "ACK" and r["lease_time"]]
        if lts and min(lts) <= self.ddns_wait + 5:
            print(f"      ! lease time {min(lts)} detik <= ddns_wait {self.ddns_wait}: klien simulasi tidak renew, "
                  "jadi lease bisa habis sebelum dicek. Naikkan lease time network uji atau turunkan ddns_wait.")
        self.wait(self.ddns_wait, "refresh DDNS Diddy")
        stored = self.diddy.leases() if self.diddy else {}
        for scen, rows in groups:
            print(f"      [{scen}]")
            if scen == "duplicate":
                self.verify_duplicate(rows, stored)
                continue
            for r in rows:
                if r["status"] != "ACK" or scen == "reservation":
                    continue
                if r["lease_time"] and time.time() > r["t"] + r["lease_time"]:
                    self.check(scen, f"{str(r['hostname'])[:22]!r} -> lease sudah habis", None,
                               "lease habis sebelum dicek (klien simulasi tidak renew)")
                    continue
                kea_name = stored.get(r["mac"], {}).get("hostname") if stored else None
                label = dyn_label(kea_name if kea_name is not None else r["hostname"])
                sent = r["hostname"] if r["hostname"] is not None else EMPTY
                via = f" (Kea: {kea_name!r})" if stored and kea_name != r["hostname"] else ""
                name = f"{str(sent)[:22]!r}{via} -> "
                if not label:
                    ptr = dig(self.dns, r["ip"], "PTR")
                    self.check(scen, name + "tanpa record", ptr == [], f"PTR {r['ip']} = {ptr}")
                    continue
                fq = f"{label}.{self.domain}"
                a, ptr = dig(self.dns, fq, "A"), dig(self.dns, r["ip"], "PTR")
                self.check(scen, name + fq[:34], a == [r["ip"]] and ptr == [fq], f"A={a} PTR={ptr}")

    def verify_duplicate(self, rows, stored):
        acked = [r for r in rows if r["status"] == "ACK"]
        if not acked:
            self.check("duplicate", "ada lease", False, "tidak ada ACK")
            return
        fq = f"{dyn_label(acked[0]['hostname'])}.{self.domain}"
        a = dig(self.dns, fq, "A")
        latest = max(acked, key=lambda r: r["t"])["ip"]
        self.check("duplicate", f"{len(acked)} lease, 1 record {fq[:30]}", a is not None and len(a) == 1 and a[0] in
                   {r["ip"] for r in acked}, f"A={a}")
        if a and len(a) == 1 and a[0] != latest:
            print(f"        catatan: record menunjuk {a[0]}, lease terbaru {latest} (lease time sama, urutan bebas)")

    # ---------------------------------------------------------- skenario
    def run(self):
        chosen = self.plan()
        if not chosen:
            die("tidak ada skenario yang dipilih")
        if not self.args.yes and input("Lanjut? [y/N] ").strip().lower() not in ("y", "ya", "yes"):
            print("Dibatalkan.")
            return 0
        base_leased, _ = self.current_leased() if self.pool else (0, 0)
        groups = []
        step = 0

        def title(s, extra=""):
            nonlocal step
            step += 1
            print(f"\n[{step}] {s}{extra}")

        if self.enabled("sanity"):
            n = int(self.sc["sanity"]) if self.sc.get("sanity", "").strip().isdigit() else 5
            title("sanity", f" ({n} klien)")
            res = self.clients([(self.macs.next(), self.names.next()) for _ in range(n)], "DORA")
            ok = [r for r in res if r["status"] == "ACK"]
            self.check("sanity", f"{len(ok)}/{n} ACK", len(ok) == n)
            if self.pool:
                self.check("sanity", "semua IP dari pool", all(self.in_pool(r["ip"]) for r in ok))
            groups.append(("sanity", res))
        if self.enabled("reservation"):
            title("reservation")
            pairs = [p.split("=") for p in split_list(self.sc.get("reservation"))]
            if not pairs:
                self.check("reservation", "daftar reservasi", None, "kosong di config")
            for mac, ip in ((m.strip().lower(), i.strip()) for m, i in pairs):
                self.macs.used.add(mac)
                r = self.clients([(mac, self.names.next())], f"{mac} -> {ip}")[0]
                self.check("reservation", f"{mac} dapat {ip}", r["status"] == "ACK" and r["ip"] == ip,
                           f"{r['status']} {r['ip']}")
        if self.enabled("weird"):
            title("weird hostnames")
            specs = [(self.macs.next(), None if w == EMPTY else w) for w in self.names.weird]
            res = self.clients(specs, "DORA")
            self.check("weird", f"{sum(r['status'] == 'ACK' for r in res)}/{len(res)} ACK",
                       all(r["status"] == "ACK" for r in res))
            groups.append(("weird", res))
        if self.enabled("duplicate"):
            m = re.fullmatch(r"\s*(\S+)\s*x\s*(\d+)\s*", self.sc.get("duplicate") or "kasir-01 x 2")
            name, n = (m.group(1), int(m.group(2))) if m else ("kasir-01", 2)
            title("duplicate", f" \"{name}\" x {n}")
            res = []
            for _ in range(n):   # berurutan supaya jelas mana yang terbaru
                res += self.clients([(self.macs.next(), name)], "DORA")
                time.sleep(1.1)
            self.check("duplicate", f"{sum(r['status'] == 'ACK' for r in res)}/{n} ACK", all(r["status"] == "ACK" for r in res))
            groups.append(("duplicate", res))
        if groups:
            title("verifikasi DDNS")
            self.verify_dns(groups)

        size = self.pool_size()
        if self.enabled("fill"):
            title("fill")
            if not size:
                self.check("fill", "ukuran pool", None, "pool tidak diketahui")
            for pct in (float(p) for p in split_list(self.sc.get("fill") or "70, 90")):
                have, _ = self.current_leased()
                need = max(0, math.ceil(size * pct / 100) - have)
                self.clients([(self.macs.next(), self.names.next()) for _ in range(need)], f"isi sampai {pct:g}%")
                time.sleep(1)
                leased, dpct = self.current_leased()
                src = "Diddy" if self.diddy else "hitungan tool"
                self.check("fill", f"{pct:g}%: {leased}/{size} lease ({src} {dpct}%)", dpct >= pct, f"{dpct}%")
        if self.enabled("exhaust"):
            extra = int(self.sc["exhaust"]) if (self.sc.get("exhaust") or "").strip().isdigit() else 20
            title("exhaust", f" (+{extra} klien setelah pool penuh)")
            have, _ = self.current_leased()
            free = max(0, size - have)
            res = self.clients([(self.macs.next(), self.names.next()) for _ in range(free + extra)],
                               f"{free} sisa pool + {extra} tambahan")
            acks = sum(r["status"] == "ACK" for r in res)
            no = sum(r["status"] == "NO_OFFER" for r in res)
            nak = sum(r["status"] == "NAK" for r in res)
            # NAK wajar di sini: klien paralel bisa ditawari alamat terakhir yang sama, yang kalah dapat NAK
            exact = self.diddy is not None
            self.check("exhaust", f"{acks} ACK (sisa pool {free}), {no} tanpa OFFER, {nak} NAK",
                       (acks == free if exact else acks <= free) and len(res) - acks >= extra,
                       f"ACK={acks} NO_OFFER={no} NAK={nak}")
            leased, dpct = self.current_leased()
            self.check("exhaust", f"pool penuh: {leased}/{size} ({dpct}%)", dpct >= 100 or not exact, f"{dpct}%")
        if self.enabled("expire") and not self.args.release_all:
            title("expire")
            lt = int(self.lab.get("lease_time") or 0) or max([r["lease_time"] or 0 for r in self.leases] or [0])
            if not lt:
                self.check("expire", "lease time", None, "tidak diketahui")
            else:
                self.wait(lt + self.ddns_wait + 10, f"lease {lt} detik habis + refresh DDNS")
                self.verify_gone("expire", base_leased)
        elif self.enabled("release"):
            title("release")
            n = self.release_all()
            print(f"      {n} lease dilepas")
            if self.diddy or self.dns:
                self.wait(max(10, self.ddns_wait if self.dns else 10), "Diddy membaca lease dan refresh DDNS")
                self.verify_gone("release", base_leased)
        return self.summary()

    def verify_gone(self, scen, base_leased):
        if self.diddy:
            leased, dpct = self.current_leased()
            self.check(scen, f"lease kembali ke awal: {leased} (awal {base_leased})", leased <= base_leased,
                       f"{leased} lease, {dpct}%")
        if self.dns and self.domain and shutil.which("dig"):
            sample = [r for r in self.leases if r["status"] == "ACK" and dyn_label(r["hostname"])][:10]
            gone = [r for r in sample if not dig(self.dns, f"{dyn_label(r['hostname'])}.{self.domain}", "A")]
            self.check(scen, f"record DDNS hilang ({len(gone)}/{len(sample)} sampel)", len(gone) == len(sample))

    def release_all(self):
        """RELEASE semua lease yang tercatat di file state (hanya lease buatan tool ini)."""
        st = self.load_state()
        for mac, v in st.items():
            if v.get("server_mac") and v.get("server_id"):
                self.dhcp.release(dict(v, mac=mac))
        with open(self.state_file, "w") as f:
            json.dump({}, f)
        return len(st)

    def summary(self):
        ok = sum(c["ok"] is True for c in self.checks)
        bad = sum(c["ok"] is False for c in self.checks)
        skip = sum(c["ok"] is None for c in self.checks)
        dur = int(time.time() - self.started)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self.lab.get("log_dir") or ".", f"dhcp_sim-{stamp}.json")
        with open(path, "w") as f:
            json.dump({"network": str(self.network), "domain": self.domain, "checks": self.checks,
                       "clients": self.leases}, f, indent=1, default=str)
        print(f"\nRingkasan: {ok} lolos, {bad} gagal, {skip} dilewati  ({dur // 60}m{dur % 60:02d}s)")
        for c in self.checks:
            if c["ok"] is False:
                print(f"  GAGAL [{c['scenario']}] {c['check']}: {c['detail']}")
        print(f"Detail per klien: {path}")
        return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description="Simulator klien DHCP untuk menguji Kea dan DDNS Diddy.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n")[1])
    ap.add_argument("-c", "--config", help="file config INI (lihat --example-config)")
    ap.add_argument("-i", "--interface")
    ap.add_argument("--network", help="network yang diuji, mis. 10.151.10.0/24")
    ap.add_argument("--pool", help="mis. '10.151.10.51 - 10.151.10.250'")
    ap.add_argument("--server", help="DNS server untuk dig")
    ap.add_argument("--domain", help="domain DDNS; kosong = dari Diddy")
    ap.add_argument("--mac-prefix")
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--parallel", type=int)
    ap.add_argument("--ddns-wait", type=int)
    ap.add_argument("--diddy-url")
    ap.add_argument("--diddy-user")
    ap.add_argument("--diddy-password", help="lebih aman lewat variabel DIDDY_PASSWORD")
    ap.add_argument("--state-file")
    ap.add_argument("--clients", type=int, help="mode cepat: N klien acak saja, tanpa skenario lain")
    ap.add_argument("--only", help="skenario dipisah koma: " + ",".join(SCENARIOS))
    ap.add_argument("--release-all", action="store_true", help="RELEASE semua lease di file state, lalu keluar")
    ap.add_argument("--example-config", action="store_true", help="cetak contoh config lalu keluar")
    ap.add_argument("-y", "--yes", action="store_true", help="tanpa konfirmasi")
    ap.add_argument("-v", "--verbose", action="store_true", help="tampilkan setiap klien")
    args = ap.parse_args()
    if args.example_config:
        print(EXAMPLE, end="")
        return 0
    if os.geteuid() != 0:
        die("butuh root untuk raw socket: jalankan dengan sudo")
    r = Run(args)
    if args.release_all:
        n = r.release_all()
        print(f"{n} lease dilepas (RELEASE) dari {r.state_file}")
        return 0
    try:
        return r.run()
    except urllib.error.URLError as e:
        die(f"API Diddy: {e}")
    except KeyboardInterrupt:
        print("\nDihentikan. Lease yang sudah didapat tercatat di", r.state_file, "- lepas dengan --release-all")
        return 130


if __name__ == "__main__":
    if "--example-config" not in sys.argv and "-h" not in sys.argv and "--help" not in sys.argv:
        try:
            from scapy.all import BOOTP, DHCP, IP, UDP, Ether, conf, mac2str, sendp, srp1
            conf.checkIPaddr = False
        except ImportError:
            die("butuh scapy: sudo pip install scapy (atau apt install python3-scapy)")
    sys.exit(main())
