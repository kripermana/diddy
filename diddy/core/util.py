"""Fungsi bantu umum: waktu, validasi input, eksekusi perintah."""

import datetime
import ipaddress
import re
import shlex
import shutil
import subprocess

from flask import request

from .errors import ApiError


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def human_time(seconds):
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return (f"{d}d " if d else "") + f"{h}h {m}m"


def body():
    return request.get_json(silent=True) or {}


def boolv(v):
    return 1 if v in (True, 1, "1", "true", "True", "on", "yes") else 0


HOST_RE = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)(\.(?!-)[a-z0-9_-]{1,63}(?<!-))*$")


LABEL_RE = re.compile(r"^(\*|[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$")


def valid_fqdn(s, field="name"):
    s = (s or "").strip().lower().rstrip(".")
    if not s or len(s) > 253 or not HOST_RE.match(s):
        raise ApiError(f"Invalid {field}: '{s}'")
    return s


def parse_ip(s, field="IP address", version=None):
    try:
        a = ipaddress.ip_address((s or "").strip())
    except ValueError:
        raise ApiError(f"Invalid {field}: '{s}'")
    if version and a.version != version:
        raise ApiError(f"{field} must be IPv{version}")
    return a


def parse_net(s):
    try:
        return ipaddress.ip_network((s or "").strip(), strict=False)
    except ValueError:
        raise ApiError(f"Invalid network: '{s}'")


def normalize_mac(s):
    h = re.sub(r"[^0-9a-fA-F]", "", s or "")
    if len(h) != 12:
        raise ApiError(f"Invalid MAC address: '{s}'")
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).lower()


def run(cmd, timeout=60):
    args = cmd if isinstance(cmd, list) else shlex.split(cmd)
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return False, f"command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return False, f"timeout: {' '.join(args)}"


def svc_state(name):
    """Status unit systemd (active, inactive, failed, ...), atau 'unknown' bila systemctl tidak ada."""
    if not shutil.which("systemctl"):
        return "unknown"
    ok, out = run(["systemctl", "is-active", name], timeout=5)
    st = (out.splitlines() or ["unknown"])[0].strip()
    return st if re.fullmatch(r"[a-z-]+", st) else "unknown"


def clean(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}


def split_list(v):
    if isinstance(v, list):
        v = ", ".join(str(i) for i in v)
    v = re.sub(r"\s*port\s*(\d+)", r"#\1", str(v or ""), flags=re.I)   # '10.1.1.1 port 5353' -> '10.1.1.1#5353'
    return [i for i in re.split(r"[,;\s]+", v.strip()) if i]
