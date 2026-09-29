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
    # log error BIND (SERVFAIL, REFUSED, upstream gagal) untuk halaman DNS errors
    "bind_log_manage": "true",
    "bind_log_file": "/var/log/named/diddy-errors.log",
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


# Keterangan tiap variabel untuk halaman System > Configuration: (grup, teks UI dalam bahasa Inggris).
META = {
    "listen": ("Web server", "Address the Diddy web UI and API listen on"),
    "port": ("Web server", "TCP port of the web UI and API"),
    "data_dir": ("General", "Directory for the SQLite database, initial admin password and deploy copies"),
    "dry_run": ("General", "Write files but never reload BIND, Kea or dnsdist"),
    "default_ns": ("DNS (BIND)", "Primary name server used for new zones"),
    "default_admin_email": ("DNS (BIND)", "SOA contact e-mail used for new zones"),
    "bind_dir": ("DNS (BIND)", "Directory where Diddy writes zone files and named.conf.diddy"),
    "dns_reload_cmd": ("DNS (BIND)", "Command that reloads BIND after a deploy"),
    "dns_service": ("DNS (BIND)", "systemd unit of BIND, used for health checks"),
    "bind_options_file": ("DNS (BIND)", "File included inside options {} of named.conf.options"),
    "bind_local_addr": ("DNS (BIND)", "BIND address queried for DNS cache lookups"),
    "kea_conf": ("DHCP (Kea)", "kea-dhcp4 configuration file written by Diddy"),
    "kea_socket": ("DHCP (Kea)", "Kea control socket for config reload and statistics"),
    "kea_reload_fallback_cmd": ("DHCP (Kea)", "Command used when the control socket cannot reload Kea"),
    "kea_lease_file": ("DHCP (Kea)", "Kea memfile lease CSV (lease storage memfile)"),
    "dhcp_service": ("DHCP (Kea)", "systemd unit of Kea DHCPv4, used for health checks"),
    "dhcp_interfaces": ("DHCP (Kea)", "Interfaces Kea listens on, * for all"),
    "kea_lease_backend": ("DHCP (Kea)", "Kea lease storage: memfile or mysql"),
    "kea_db_host": ("DHCP (Kea)", "MySQL host of the Kea lease database"),
    "kea_db_port": ("DHCP (Kea)", "MySQL port of the Kea lease database"),
    "kea_db_name": ("DHCP (Kea)", "Name of the Kea lease database"),
    "kea_db_user": ("DHCP (Kea)", "User of the Kea lease database"),
    "kea_db_password": ("DHCP (Kea)", "Password of the Kea lease database"),
    "dnsdist_conf": ("Encrypted upstream (dnsdist)", "dnsdist configuration file written by Diddy"),
    "dnsdist_service": ("Encrypted upstream (dnsdist)", "systemd unit of dnsdist"),
    "dnsdist_listen": ("Encrypted upstream (dnsdist)", "Local address dnsdist listens on for BIND"),
    "dnsdist_port": ("Encrypted upstream (dnsdist)", "Local port dnsdist listens on for BIND"),
    "ddns_ttl": ("DDNS", "TTL of A/PTR records created from DHCP leases"),
    "ddns_refresh_interval": ("DDNS", "Seconds between DDNS refreshes from the lease table, 0 = off"),
    "drift_check_interval": ("Drift", "Seconds between checks of service files against the last deploy, 0 = off"),
    "drift_auto_repair": ("Drift", "Restore drifted files automatically"),
    "metrics_interval": ("Dashboard statistics", "Seconds between BIND/Kea statistics samples, 0 = off"),
    "metrics_retention_days": ("Dashboard statistics", "Days of statistics kept in the database"),
    "bind_stats_url": ("Dashboard statistics", "BIND statistics-channel URL"),
    "bind_stats_manage": ("Dashboard statistics", "Let Diddy add the statistics-channel to named.conf.diddy"),
    "dns_capacity_qps": ("Dashboard statistics", "Queries per second treated as 100% DNS utilization"),
    "bind_log_manage": ("DNS errors", "Let Diddy add a logging block to named.conf.diddy for the DNS errors page"),
    "bind_log_file": ("DNS errors", "File BIND writes SERVFAIL, REFUSED and upstream failures to"),
    "db_backend": ("Database", "Diddy database: sqlite or mysql"),
    "mysql_host": ("Database", "MySQL host of the Diddy database"),
    "mysql_port": ("Database", "MySQL port of the Diddy database"),
    "mysql_user": ("Database", "MySQL user of the Diddy database"),
    "mysql_password": ("Database", "MySQL password of the Diddy database"),
    "mysql_database": ("Database", "MySQL database name"),
    "table_prefix": ("Database", "Prefix for Diddy's own tables, e.g. ddi_"),
}
SECRET = re.compile(r"password|secret|token|key$")
FILE = {"section": None, "keys": set(), "unknown": []}   # isi file config, untuk membedakan dari default


def load_conf():
    """Baca config. Section [diddy]; config LiteDDI lama dengan section [liteddi] tetap terbaca."""
    raw = configparser.ConfigParser(interpolation=None)
    raw.read(CONF_PATH)
    section = "diddy" if raw.has_section("diddy") else "liteddi" if raw.has_section("liteddi") else None
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict({"diddy": DEFAULTS})
    if section:
        cp.read_dict({"diddy": dict(raw[section])})
        FILE.update(section=section, keys=set(raw[section].keys()),
                    unknown=sorted(k for k in raw[section].keys() if k not in DEFAULTS))
    return cp["diddy"]


def describe_conf():
    """Semua variabel config beserta nilai efektif, default, asal nilai, dan keterangannya.

    Nilai rahasia (password dan sejenisnya) tidak pernah dikirim; hanya ditandai terisi atau kosong.
    """
    items = []
    for k, default in DEFAULTS.items():
        group, desc = META.get(k, ("Other", ""))
        secret = bool(SECRET.search(k))
        val = C[k]
        items.append({"key": k, "group": group, "description": desc, "secret": secret,
                      "value": ("(set)" if val else "(empty)") if secret else val,
                      "default": ("(empty)" if not default else "(set)") if secret else default,
                      "source": "file" if k in FILE["keys"] else "default",
                      "changed": val != default})
    for k in FILE["unknown"]:
        items.append({"key": k, "group": "Unknown", "description": "Not a Diddy setting, ignored. Check for typos.",
                      "secret": bool(SECRET.search(k)), "value": "(hidden)", "default": "", "source": "file",
                      "changed": True})
    return items


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
