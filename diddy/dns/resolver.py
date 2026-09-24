"""Resolver: recursion, ACL, forwarder, conditional forwarder, dnsdist."""

import ipaddress
import json
import shutil

from ..config import C
from ..core.errors import ApiError
from ..core.util import boolv, now, parse_ip, split_list, valid_fqdn
from ..db.connection import q, state_get
from ..ipam.networks import all_nets
from ..version import VERSION


DNS_DEFAULTS = {"recursion": False, "forwarders": [], "forward_policy": "first",
                "allow_recursion": [], "auto_allow_ipam": True, "dnssec_validation": "auto",
                "upstream_mode": "plain", "encrypted_upstreams": [], "validate_certificates": True,
                "dnsdist_policy": "leastOutstanding"}


DNSDIST_POLICIES = ["leastOutstanding", "firstAvailable", "roundrobin", "wrandom"]


UPSTREAM_PRESETS = {
    "cloudflare": [{"protocol": "dot", "address": "1.1.1.1", "hostname": "cloudflare-dns.com"},
                   {"protocol": "dot", "address": "1.0.0.1", "hostname": "cloudflare-dns.com"}],
    "quad9": [{"protocol": "dot", "address": "9.9.9.9", "hostname": "dns.quad9.net"},
              {"protocol": "dot", "address": "149.112.112.112", "hostname": "dns.quad9.net"}],
    "google": [{"protocol": "dot", "address": "8.8.8.8", "hostname": "dns.google"},
               {"protocol": "dot", "address": "8.8.4.4", "hostname": "dns.google"}],
    "adguard": [{"protocol": "dot", "address": "94.140.14.14", "hostname": "dns.adguard-dns.com"}],
}


def parse_upstream(v):
    """'dot 1.1.1.1 cloudflare-dns.com' atau 'doh 9.9.9.9 dns.quad9.net /dns-query'."""
    if isinstance(v, dict):
        parts = [v.get("protocol", "dot"), v.get("address", ""), v.get("hostname", "")]
        if v.get("path"):
            parts.append(v["path"])
        if v.get("port"):
            parts[1] = f"{parts[1]}:{v['port']}"
    else:
        parts = str(v).replace("\t", " ").split()
    if len(parts) < 3:
        raise ApiError(f"Format upstream: '<dot|doh> <ip>[:port] <nama-sertifikat> [path]', bukan '{v}'")
    proto = parts[0].lower().replace("tls", "dot").replace("https", "doh")
    if proto not in ("dot", "doh"):
        raise ApiError(f"Protokol harus dot atau doh, bukan '{parts[0]}'")
    addr, _, port = parts[1].partition(":")
    a = parse_ip(addr, "IP upstream")
    port = int(port) if port.isdigit() else (853 if proto == "dot" else 443)
    if not 1 <= port <= 65535:
        raise ApiError(f"Port tidak valid: {port}")
    host = valid_fqdn(parts[2], "nama sertifikat upstream")
    if proto == "dot" and len(parts) > 3:
        raise ApiError(f"DoT tidak memakai path: '{' '.join(parts[3:])}'")
    if len(parts) > 4:
        raise ApiError(f"Terlalu banyak bagian pada upstream: '{v}'")
    path = parts[3] if len(parts) > 3 else "/dns-query"
    if proto == "doh" and not path.startswith("/"):
        raise ApiError("Path DoH harus diawali '/'")
    return {"protocol": proto, "address": str(a), "port": port, "hostname": host,
            "path": path if proto == "doh" else ""}


def dns_settings():
    saved = state_get("dns_settings")
    out = dict(DNS_DEFAULTS)
    if saved:
        try:
            out.update(json.loads(saved))
        except ValueError:
            pass
    return out


def parse_server(v, field="server"):
    """'8.8.8.8' atau '10.1.1.1 port 5353' -> bentuk yang dipakai BIND."""
    v = (v or "").strip().lower().replace(" port ", "#")
    if "#" in v:
        ip_part, _, port = v.partition("#")
        a = parse_ip(ip_part, field)
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ApiError(f"Port tidak valid pada {field}: '{v}'")
        return f"{a} port {int(port)}"
    return str(parse_ip(v, field))


def validate_dns_settings(d):
    out = dict(DNS_DEFAULTS)
    out["recursion"] = bool(boolv(d.get("recursion")))
    out["forwarders"] = [parse_server(v, "forwarder") for v in split_list(d.get("forwarders"))]
    pol = (d.get("forward_policy") or "first").lower()
    if pol not in ("first", "only"):
        raise ApiError("Forward policy harus 'first' atau 'only'")
    out["forward_policy"] = pol
    acl = []
    for v in split_list(d.get("allow_recursion")):
        try:
            acl.append(str(ipaddress.ip_network(v, strict=False)))
        except ValueError:
            raise ApiError(f"Bukan network yang valid: '{v}'")
    out["allow_recursion"] = acl
    out["auto_allow_ipam"] = bool(boolv(d.get("auto_allow_ipam", True)))
    dv = (d.get("dnssec_validation") or "auto").lower()
    if dv not in ("auto", "no"):
        raise ApiError("DNSSEC validation harus 'auto' atau 'no'")
    out["dnssec_validation"] = dv
    if out["recursion"] and not out["forwarders"] and not boolv(d.get("allow_resolver_without_forwarders")):
        out["forward_policy"] = "first"
    if out["recursion"] and not acl and not out["auto_allow_ipam"]:
        raise ApiError("Tentukan network yang boleh melakukan recursion, atau centang 'pakai network IPAM'")
    mode = (d.get("upstream_mode") or "plain").lower()
    if mode not in ("plain", "encrypted"):
        raise ApiError("Upstream mode harus 'plain' atau 'encrypted'")
    out["upstream_mode"] = mode
    ups = d.get("encrypted_upstreams")
    preset = (d.get("preset") or "").lower()
    if preset and preset in UPSTREAM_PRESETS and not ups:
        ups = UPSTREAM_PRESETS[preset]
    if isinstance(ups, str):
        ups = [line for line in ups.splitlines() if line.strip() and not line.strip().startswith("#")]
    out["encrypted_upstreams"] = [parse_upstream(u) for u in (ups or [])]
    out["validate_certificates"] = bool(boolv(d.get("validate_certificates", True)))
    pol = d.get("dnsdist_policy") or "leastOutstanding"
    if pol not in DNSDIST_POLICIES:
        raise ApiError(f"Policy dnsdist harus salah satu dari: {', '.join(DNSDIST_POLICIES)}")
    out["dnsdist_policy"] = pol
    if mode == "encrypted":
        if not out["recursion"]:
            raise ApiError("Nyalakan recursion dulu: upstream terenkripsi hanya dipakai "
                           "saat server meresolve untuk client")
        if not out["encrypted_upstreams"]:
            raise ApiError("Isi minimal satu upstream terenkripsi, atau pilih preset")
        if not shutil.which("dnsdist"):
            raise ApiError("dnsdist belum terpasang di server ini. Pasang dengan 'apt install dnsdist', "
                           "atau jalankan ./upgrade.sh dari paket Diddy terbaru")
    return out


def recursion_acl(cfg):
    acl = ["127.0.0.1", "::1"] + list(cfg["allow_recursion"])
    if cfg["auto_allow_ipam"]:
        acl += [n["cidr"] for n in all_nets()]
    seen, out = set(), []
    for a in acl:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def proxy_target():
    return f"{C['dnsdist_listen']} port {int(C['dnsdist_port'])}"


def render_dnsdist(cfg=None):
    """Config dnsdist: proxy lokal yang meneruskan query BIND ke upstream DoT/DoH."""
    cfg = cfg or dns_settings()
    listen = f"{C['dnsdist_listen']}:{int(C['dnsdist_port'])}"
    out = [f"-- Generated by Diddy {VERSION} on {now()} - DO NOT EDIT, ditimpa tiap deploy",
           f"setLocal('{listen}')",
           "setACL({'127.0.0.1/32', '::1/128'})",
           "setSecurityPollSuffix('')"]
    for i, u in enumerate(cfg["encrypted_upstreams"], start=1):
        opts = [f'address="{u["address"]}:{u["port"]}"', 'tls="openssl"',
                f'subjectName="{u["hostname"]}"',
                f'validateCertificates={"true" if cfg["validate_certificates"] else "false"}',
                f'name="{u["protocol"]}{i}-{u["hostname"]}"']
        if u["protocol"] == "doh":
            opts.insert(2, f'dohPath="{u["path"]}"')
        out.append("newServer({" + ", ".join(opts) + "})")
    out.append(f"setServerPolicy({cfg['dnsdist_policy']})")
    return "\n".join(out) + "\n"


def render_options():
    """Isi file yang di-include dari dalam blok options{} milik BIND."""
    cfg = dns_settings()
    out = ["// Generated by Diddy - DO NOT EDIT. Included from named.conf.options"]
    if cfg["recursion"]:
        acl = recursion_acl(cfg)
        out.append("recursion yes;")
        out.append("allow-recursion { " + " ".join(a + ";" for a in acl) + " };")
        out.append("allow-query-cache { " + " ".join(a + ";" for a in acl) + " };")
        fwd = [proxy_target()] if cfg["upstream_mode"] == "encrypted" else cfg["forwarders"]
        if fwd:
            out.append("forwarders { " + " ".join(f + ";" for f in fwd) + " };")
            out.append(f"forward {cfg['forward_policy']};")
        if cfg["upstream_mode"] == "encrypted":
            out.append("// upstream terenkripsi: BIND -> dnsdist lokal -> " +
                       ", ".join(f"{u['protocol'].upper()} {u['address']} ({u['hostname']})"
                                 for u in cfg["encrypted_upstreams"]))
        out.append(f"dnssec-validation {cfg['dnssec_validation']};")
    else:
        out.append("recursion no;")
        out.append("allow-recursion { none; };")
    return "\n".join(out) + "\n"


def validate_forwarder(d, fid=None):
    dom = valid_fqdn(d.get("domain"), "domain")
    if q("SELECT 1 FROM zones WHERE name=?", (dom,), one=True):
        raise ApiError(f"'{dom}' adalah zona authoritative di Diddy, tidak bisa sekaligus di-forward")
    dup = q("SELECT id FROM forwarders WHERE domain=? AND id<>?", (dom, fid or 0), one=True)
    if dup:
        raise ApiError(f"Conditional forwarder untuk '{dom}' sudah ada", 409)
    servers = [parse_server(v, "server") for v in split_list(d.get("servers"))]
    if not servers:
        raise ApiError("Isi minimal satu IP server tujuan")
    pol = (d.get("policy") or "only").lower()
    if pol not in ("first", "only"):
        raise ApiError("Policy harus 'first' atau 'only'")
    return {"domain": dom, "servers": ", ".join(servers), "policy": pol,
            "comment": str(d.get("comment") or "")[:200]}
