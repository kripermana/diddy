"""Skema tabel, migrasi kolom, inisialisasi, dan migrasi SQLite -> MySQL."""

import os
import secrets
import sqlite3
import sys

from werkzeug.security import generate_password_hash

from ..config import C, DATA, MYSQL
from ..core.util import now
from .connection import connect, sql
from .tables import T, TABLES


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'readonly', created TEXT);
CREATE TABLE IF NOT EXISTS networks(id INTEGER PRIMARY KEY, cidr TEXT UNIQUE NOT NULL,
  comment TEXT DEFAULT '', gateway TEXT DEFAULT '', dhcp_enabled INTEGER DEFAULT 0,
  dns_servers TEXT DEFAULT '', domain_name TEXT DEFAULT '', lease_time INTEGER DEFAULT 86400,
  vlan TEXT DEFAULT '', site TEXT DEFAULT '', ddns_enabled INTEGER DEFAULT 0, ddns_domain TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS ranges(id INTEGER PRIMARY KEY,
  network_id INTEGER NOT NULL REFERENCES networks(id) ON DELETE CASCADE,
  start_ip TEXT NOT NULL, end_ip TEXT NOT NULL, comment TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS zones(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  ttl INTEGER DEFAULT 3600, primary_ns TEXT NOT NULL, admin_email TEXT NOT NULL,
  serial INTEGER DEFAULT 0, comment TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,
  zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
  name TEXT NOT NULL, type TEXT NOT NULL, value TEXT NOT NULL, ttl INTEGER, comment TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS hosts(id INTEGER PRIMARY KEY, fqdn TEXT NOT NULL, ip TEXT UNIQUE NOT NULL,
  mac TEXT DEFAULT '', configure_dns INTEGER DEFAULT 1, configure_dhcp INTEGER DEFAULT 0,
  comment TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS discovered(ip TEXT PRIMARY KEY, last_seen TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, username TEXT, action TEXT,
  object TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS forwarders(id INTEGER PRIMARY KEY, domain TEXT NOT NULL UNIQUE,
  servers TEXT NOT NULL, policy TEXT DEFAULT 'only', comment TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS deployed(path TEXT PRIMARY KEY, sha TEXT NOT NULL, content TEXT NOT NULL, ts TEXT);
CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_metrics_kind_ts ON metrics(kind, ts);
CREATE TABLE IF NOT EXISTS dns_events(id INTEGER PRIMARY KEY, bucket INTEGER NOT NULL, kind TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '', qtype TEXT NOT NULL DEFAULT '', server TEXT NOT NULL DEFAULT '',
  client TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_dns_events_kind_bucket ON dns_events(kind, bucket);
CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
"""


_T = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"


SCHEMA_MYSQL = [
    "CREATE TABLE IF NOT EXISTS users(id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE,"
    " pw_hash VARCHAR(255) NOT NULL, role VARCHAR(16) NOT NULL DEFAULT 'readonly', created VARCHAR(19))" + _T,
    "CREATE TABLE IF NOT EXISTS networks(id INT AUTO_INCREMENT PRIMARY KEY, cidr VARCHAR(64) NOT NULL UNIQUE,"
    " comment VARCHAR(255) NOT NULL DEFAULT '', gateway VARCHAR(45) NOT NULL DEFAULT '',"
    " dhcp_enabled TINYINT NOT NULL DEFAULT 0, dns_servers VARCHAR(255) NOT NULL DEFAULT '',"
    " domain_name VARCHAR(255) NOT NULL DEFAULT '', lease_time INT NOT NULL DEFAULT 86400,"
    " vlan VARCHAR(255) NOT NULL DEFAULT '', site VARCHAR(255) NOT NULL DEFAULT '',"
    " ddns_enabled TINYINT NOT NULL DEFAULT 0, ddns_domain VARCHAR(255) NOT NULL DEFAULT '')" + _T,
    "CREATE TABLE IF NOT EXISTS ranges(id INT AUTO_INCREMENT PRIMARY KEY, network_id INT NOT NULL,"
    " start_ip VARCHAR(45) NOT NULL, end_ip VARCHAR(45) NOT NULL, comment VARCHAR(255) NOT NULL DEFAULT '',"
    " FOREIGN KEY (network_id) REFERENCES networks(id) ON DELETE CASCADE)" + _T,
    "CREATE TABLE IF NOT EXISTS zones(id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE,"
    " ttl INT NOT NULL DEFAULT 3600, primary_ns VARCHAR(255) NOT NULL, admin_email VARCHAR(255) NOT NULL,"
    " serial BIGINT NOT NULL DEFAULT 0, comment VARCHAR(255) NOT NULL DEFAULT '')" + _T,
    "CREATE TABLE IF NOT EXISTS records(id INT AUTO_INCREMENT PRIMARY KEY, zone_id INT NOT NULL,"
    " name VARCHAR(255) NOT NULL, type VARCHAR(10) NOT NULL, value TEXT COLLATE utf8mb4_bin NOT NULL, ttl INT NULL,"
    " comment VARCHAR(255) NOT NULL DEFAULT '', INDEX idx_rec_name(zone_id, name),"
    " FOREIGN KEY (zone_id) REFERENCES zones(id) ON DELETE CASCADE)" + _T,
    "CREATE TABLE IF NOT EXISTS hosts(id INT AUTO_INCREMENT PRIMARY KEY, fqdn VARCHAR(255) NOT NULL,"
    " ip VARCHAR(45) NOT NULL UNIQUE, mac VARCHAR(17) NOT NULL DEFAULT '', configure_dns TINYINT NOT NULL DEFAULT 1,"
    " configure_dhcp TINYINT NOT NULL DEFAULT 0, comment VARCHAR(255) NOT NULL DEFAULT '',"
    " INDEX idx_host_fqdn(fqdn), INDEX idx_host_mac(mac))" + _T,
    "CREATE TABLE IF NOT EXISTS discovered(ip VARCHAR(45) PRIMARY KEY, last_seen VARCHAR(19))" + _T,
    "CREATE TABLE IF NOT EXISTS audit(id BIGINT AUTO_INCREMENT PRIMARY KEY, ts VARCHAR(19), username VARCHAR(64),"
    " action VARCHAR(32), object VARCHAR(512), detail TEXT, INDEX idx_audit_ts(ts))" + _T,
    "CREATE TABLE IF NOT EXISTS forwarders(id INT AUTO_INCREMENT PRIMARY KEY,"
    " domain VARCHAR(255) NOT NULL UNIQUE, servers VARCHAR(500) NOT NULL,"
    " policy VARCHAR(8) NOT NULL DEFAULT 'only', comment VARCHAR(255) NOT NULL DEFAULT '')" + _T,
    "CREATE TABLE IF NOT EXISTS deployed(path VARCHAR(400) PRIMARY KEY, sha CHAR(64) NOT NULL,"
    " content LONGTEXT NOT NULL, ts VARCHAR(19))" + _T,
    "CREATE TABLE IF NOT EXISTS metrics(id BIGINT AUTO_INCREMENT PRIMARY KEY, ts BIGINT NOT NULL,"
    " kind VARCHAR(8) NOT NULL, data MEDIUMTEXT NOT NULL, INDEX idx_metrics_kind_ts(kind, ts))" + _T,
    "CREATE TABLE IF NOT EXISTS dns_events(id BIGINT AUTO_INCREMENT PRIMARY KEY, bucket BIGINT NOT NULL,"
    " kind VARCHAR(12) NOT NULL, name VARCHAR(255) NOT NULL DEFAULT '', qtype VARCHAR(12) NOT NULL DEFAULT '',"
    " server VARCHAR(64) NOT NULL DEFAULT '', client VARCHAR(64) NOT NULL DEFAULT '',"
    " reason VARCHAR(64) NOT NULL DEFAULT '', count INT NOT NULL DEFAULT 1,"
    " INDEX idx_dns_events_kind_bucket(kind, bucket))" + _T,
    "CREATE TABLE IF NOT EXISTS state(k VARCHAR(300) PRIMARY KEY, v TEXT)" + _T,
]


def ensure_columns(cur):
    """Add columns introduced by newer Diddy versions to an existing database."""
    wanted = {"networks": [("ddns_enabled", "TINYINT NOT NULL DEFAULT 0", "INTEGER DEFAULT 0"),
                           ("ddns_domain", "VARCHAR(255) NOT NULL DEFAULT ''", "TEXT DEFAULT ''")]}
    for table, cols in wanted.items():
        tn = T(table)
        if MYSQL:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name=%s", (C["mysql_database"], tn))
            have = {list(r.values())[0].lower() for r in cur.fetchall()}
        else:
            cur.execute(f"PRAGMA table_info({tn})")
            have = {r[1].lower() for r in cur.fetchall()}
        for name, mysql_def, sqlite_def in cols:
            if name not in have:
                cur.execute(f"ALTER TABLE {tn} ADD COLUMN {name} {mysql_def if MYSQL else sqlite_def}")


def init_db():
    con = connect()
    cur = con.cursor()
    if MYSQL:
        for stmt in SCHEMA_MYSQL:
            cur.execute(sql(stmt))
    else:
        con.executescript(sql(SCHEMA))
    ensure_columns(cur)
    cur.execute(sql("SELECT 1 FROM users"))
    if not cur.fetchone():
        pw = (os.environ.get("DIDDY_ADMIN_PASSWORD") or os.environ.get("LITEDDI_ADMIN_PASSWORD")
              or secrets.token_urlsafe(9))
        cur.execute(sql("INSERT INTO users(username,pw_hash,role,created) VALUES(?,?,?,?)"),
                    ("admin", generate_password_hash(pw), "admin", now()))
        p = os.path.join(DATA, "initial_admin_password")
        with open(p, "w") as f:
            f.write(pw + "\n")
        os.chmod(p, 0o600)
        print(f"[diddy] created user 'admin' / password '{pw}' (also saved in {p})", flush=True)
    con.commit()
    con.close()


def migrate_sqlite(path, force=False, src_prefix=""):
    """Copy every table from an existing Diddy SQLite file into the configured MySQL database."""
    if not MYSQL:
        sys.exit("Set db_backend = mysql in the config first.")
    src = sqlite3.connect(path)
    src.row_factory = sqlite3.Row
    dst = connect()
    cur = dst.cursor()
    for t in ("networks", "zones", "hosts"):
        cur.execute(f"SELECT COUNT(*) AS c FROM {T(t)}")
        if cur.fetchone()["c"] and not force:
            sys.exit(f"MySQL table '{t}' already has data. Re-run with --force to overwrite everything.")
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    for t in TABLES:
        cur.execute(f"DELETE FROM {T(t)}")
    for t in TABLES:
        try:
            rows = src.execute(f"SELECT * FROM {src_prefix + t}").fetchall()
        except sqlite3.OperationalError:   # tabel dari versi Diddy yang lebih baru dari file sumber
            print(f"  {t:<11} tidak ada di sumber, dilewati")
            continue
        if rows:
            cols = rows[0].keys()
            cur.executemany(f"INSERT INTO {T(t)}({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))})",
                            [tuple(r) for r in rows])
        print(f"  {t:<11} {len(rows)} row(s)")
    cur.execute("SET FOREIGN_KEY_CHECKS=1")
    dst.close()
    print("Migration finished. Restart Diddy and deploy once so BIND/Kea match the database.")
