"""Koneksi database (MySQL/SQLite) dan helper query."""

import sqlite3
from contextlib import contextmanager

from flask import g

from ..config import C, DB, MYSQL, PFX
from .tables import _TBL_RE


def mysql_connect(host, port, user, password, database, timeout=10):
    import pymysql
    return pymysql.connect(host=host, port=int(port), user=user, password=password, database=database,
                           charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                           connect_timeout=timeout)


def connect():
    if MYSQL:
        return mysql_connect(C["mysql_host"], C["mysql_port"], C["mysql_user"], C["mysql_password"],
                             C["mysql_database"])
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def sql(s):
    """Queries use sqlite '?' placeholders and unprefixed table names; translate both."""
    if MYSQL:
        s = s.replace("?", "%s").replace("INSERT OR REPLACE", "REPLACE")
    if PFX:
        s = _TBL_RE.sub(lambda m: f"{m.group(1)} {PFX}{m.group(2)}", s)
    return s


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_e=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def q(query, args=(), one=False):
    cur = db().cursor()
    cur.execute(sql(query), args)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return (rows[0] if rows else None) if one else rows


def x(query, args=()):
    cur = db().cursor()
    cur.execute(sql(query), args)
    last = cur.lastrowid
    db().commit()
    cur.close()
    return last


class _Tx:
    def __init__(self, cur):
        self.cur = cur

    def x(self, query, args=()):
        self.cur.execute(sql(query), args)
        return self.cur.lastrowid

    def many(self, query, rows):
        if rows:
            self.cur.executemany(sql(query), rows)


@contextmanager
def transaction():
    """Beberapa perintah tulis sebagai satu kesatuan: semua tersimpan, atau tidak sama sekali.

    Contoh: `with transaction() as t: zid = t.x("INSERT ..."); t.many("INSERT ...", rows)`
    """
    con = db()
    cur = con.cursor()
    if MYSQL:
        con.begin()
    try:
        yield _Tx(cur)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        cur.close()


def state_get(k, default=None):
    r = q("SELECT v FROM state WHERE k=?", (k,), one=True)
    return r["v"] if r else default


def state_set(k, v):
    x("INSERT OR REPLACE INTO state(k,v) VALUES(?,?)", (k, v))
