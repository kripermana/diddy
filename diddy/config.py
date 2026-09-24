"""Konfigurasi: nilai default dan pembacaan /etc/diddy/diddy.conf.

Modul ini dibaca sekali saat start. Nilai turunan (DRY, MYSQL, PFX, ...) dihitung di sini
supaya modul lain cukup `from .config import C, MYSQL` tanpa membaca file lagi.
"""
import configparser
import os
import re

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
# DIDDY_CONF; nama lama LITEDDI_CONF (<= 1.9) tetap diterima.
CONF_PATH = os.environ.get("DIDDY_CONF") or os.environ.get("LITEDDI_CONF") or "/etc/diddy/diddy.conf"

DEFAULTS = {
    "listen": "0.0.0.0",
    "port": "8080",
    "data_dir": "/var/lib/diddy",
    "dry_run": "false",
    "default_ns": "ns1.example.local",
    "default_admin_email": "hostmaster@example.local",
    "bind_dir": "/etc/bind/diddy",
    "dns_reload_cmd": "rndc reload",
    "dns_service": "named",
    "kea_conf": "/etc/kea/kea-dhcp4.conf",
    "kea_socket": "/run/kea/kea4-ctrl-socket",
    "kea_reload_fallback_cmd": "systemctl restart kea-dhcp4-server",
    "kea_lease_file": "/var/lib/kea/kea-leases4.csv",
    "dhcp_service": "kea-dhcp4-server",
    "dhcp_interfaces": "*",
    # file yang di-include dari dalam blok options{} named.conf.options
    "bind_options_file": "/etc/bind/diddy/named.conf.options.diddy",
    # dnsdist: proxy lokal untuk upstream terenkripsi (DoT/DoH)
    "dnsdist_conf": "/etc/dnsdist/dnsdist.conf",
    "dnsdist_service": "dnsdist",
    "dnsdist_listen": "127.0.0.1",
    "dnsdist_port": "5353",
    # DDNS: DHCP clients registered in DNS straight from the lease table
    "ddns_ttl": "60",
    "ddns_refresh_interval": "60",
    # pemeriksaan drift: file service vs config terakhir yang di-deploy Diddy
    "drift_check_interval": "300",
    "drift_auto_repair": "false",
    # statistik untuk dashboard: sampel tiap metrics_interval detik, disimpan metrics_retention_days hari
    "metrics_interval": "60",
    "metrics_retention_days": "7",
    "bind_stats_url": "http://127.0.0.1:8053",
    "bind_stats_manage": "true",
    "dns_capacity_qps": "1000",
    # alamat BIND lokal yang ditanya untuk melihat isi cache (dig +norecurse)
    "bind_local_addr": "127.0.0.1",
    # Diddy database: mysql | sqlite
    "db_backend": "sqlite",
    "mysql_host": "127.0.0.1",
    "mysql_port": "3306",
    "mysql_user": "diddy",
    "mysql_password": "",
    "mysql_database": "diddy",
    # optional prefix for Diddy's own tables, e.g. ddi_ -> ddi_networks
    "table_prefix": "",
    # Kea lease storage: memfile | mysql
    "kea_lease_backend": "memfile",
    "kea_db_host": "127.0.0.1",
    "kea_db_port": "3306",
    "kea_db_name": "kea",
    "kea_db_user": "kea",
    "kea_db_password": "",
}


def load_conf():
    """Baca config. Section [diddy]; config LiteDDI lama dengan section [liteddi] tetap terbaca."""
    raw = configparser.ConfigParser(interpolation=None)
    raw.read(CONF_PATH)
    section = "diddy" if raw.has_section("diddy") else "liteddi" if raw.has_section("liteddi") else None
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict({"diddy": DEFAULTS})
    if section:
        cp.read_dict({"diddy": dict(raw[section])})
    return cp["diddy"]


C = load_conf()
DRY = C.getboolean("dry_run")
DATA = C["data_dir"]
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "diddy.db")
if not os.path.exists(DB) and os.path.exists(os.path.join(DATA, "liteddi.db")):
    DB = os.path.join(DATA, "liteddi.db")   # database SQLite dari LiteDDI (<= 1.9)
MYSQL = C["db_backend"].strip().lower() == "mysql"
KEA_MYSQL = C["kea_lease_backend"].strip().lower() == "mysql"
PFX = C["table_prefix"].strip()
if PFX and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,19}", PFX):
    raise SystemExit("table_prefix must start with a letter and contain only letters, digits or _ (max 20 chars)")
