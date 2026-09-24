#!/usr/bin/env bash
# Remove Diddy (keeps BIND/Kea packages and /var/lib/diddy database)
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Jalankan sebagai root"; exit 1; }
systemctl disable --now diddy 2>/dev/null || true
rm -f /etc/systemd/system/kea-dhcp4-server.service.d/diddy.conf
rmdir /etc/systemd/system/kea-dhcp4-server.service.d 2>/dev/null || true
rm -f /etc/systemd/system/diddy.service && systemctl daemon-reload
sed -i '\#diddy/named.conf.diddy#d' /etc/bind/named.conf.local
sed -i '\#diddy/named.conf.options.diddy#d' /etc/bind/named.conf.options
[ -f /etc/kea/kea-dhcp4.conf.orig ] && cp -a /etc/kea/kea-dhcp4.conf.orig /etc/kea/kea-dhcp4.conf
systemctl disable --now dnsdist 2>/dev/null || true
systemctl restart named kea-dhcp4-server 2>/dev/null || true
rm -rf /opt/diddy /etc/bind/diddy /usr/local/bin/diddy
echo "Diddy dihapus. Database MySQL diddy & kea serta /etc/diddy tidak dihapus (hapus manual bila tidak perlu)."
