"""Diddy - Your DDI Friend

DNS, DHCP dan IPAM (DDI) ringan untuk Linux, dibangun di atas BIND9 dan ISC Kea.

Copyright (c) 2026 kripermana. Released under the MIT License.

Struktur package:
    config.py          baca /etc/diddy/diddy.conf
    web.py             Flask app factory
    __main__.py        CLI: serve, deploy, drift, reset-password, migrate-sqlite
    core/              error, log, util, state runtime
    db/                koneksi MySQL/SQLite, skema, prefix tabel
    auth.py, users.py, audit.py
    ipam/              network, utilisasi, IP map, discovery
    dhcp/              lease Kea, DHCP range, config Kea
    dns/               zona, record, DDNS, resolver, forwarder, dnsdist
    hosts.py           host object (DNS + PTR + reservasi DHCP)
    deploy/            pipeline deploy dan deteksi drift
    system.py          dashboard, health, system info, pencarian, export
    worker.py          thread latar DDNS + drift
"""
from .version import AUTHOR, NAME, SLOGAN, VERSION

__all__ = ["AUTHOR", "NAME", "SLOGAN", "VERSION"]
