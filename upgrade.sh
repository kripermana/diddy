#!/usr/bin/env bash
# Diddy upgrade: pasang versi baru di atas instalasi yang sudah ada.
#
#   cd /tmp && tar xzf diddy-<versi>.tar.gz && cd diddy && sudo ./upgrade.sh
#
# Dua mode, dipilih otomatis:
#   upgrade  : Diddy sudah terpasang di /opt/diddy, versinya dinaikkan
#   migrasi  : LiteDDI (nama lama, <= 1.9) di /opt/liteddi dipindah menjadi Diddy:
#              /opt/liteddi -> /opt/diddy, /etc/liteddi -> /etc/diddy, /var/lib/liteddi -> /var/lib/diddy,
#              /etc/bind/liteddi -> /etc/bind/diddy, include BIND disesuaikan, service & CLI diganti.
#
# Semua yang disentuh dibackup ke /var/backups/diddy/<tanggal>. Kalau gagal, otomatis dikembalikan
# persis seperti semula (termasuk service LiteDDI pada mode migrasi). Database hanya dibackup.
# Variabel: SKIP_DB_DUMP=1 lewati mysqldump, SKIP_DNSDIST=1 jangan pasang dnsdist, BACKUP_DIR=/path lain.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Jalankan sebagai root: sudo ./upgrade.sh"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX=/opt/diddy
CONF=/etc/diddy/diddy.conf
LEGACY_PREFIX=/opt/liteddi
LEGACY_CONF=/etc/liteddi/liteddi.conf
LOGFILE="${LOGFILE:-/var/log/diddy-install.log}"
[ -f "$SRC/diddy/__main__.py" ] || { echo "Jalankan dari direktori hasil extract paket Diddy."; exit 1; }

if [ -d "$PREFIX/diddy" ] && [ -f "$CONF" ]; then
  MODE=upgrade
elif [ -d "$LEGACY_PREFIX" ] && [ -f "$LEGACY_CONF" ]; then
  MODE=migrate
  for p in "$PREFIX" /etc/diddy /etc/bind/diddy; do
    [ ! -e "$p" ] || { echo "Mode migrasi butuh $p belum ada, tapi ternyata sudah ada. Periksa dulu secara manual."; exit 1; }
  done
else
  echo "Diddy/LiteDDI belum terpasang di server ini. Pakai ./install.sh untuk instalasi pertama."; exit 1
fi

[ -f "$LOGFILE" ] || install -m 0600 /dev/null "$LOGFILE"
{ echo; echo "=============================================================="
  echo "Diddy upgrade ($MODE) dimulai $(date '+%F %T') oleh ${SUDO_USER:-$(id -un)}"
  echo "=============================================================="; } >> "$LOGFILE"
exec > >(tee -a "$LOGFILE") 2>&1

ACTIVE_CONF="$CONF"; [ "$MODE" = migrate ] && ACTIVE_CONF="$LEGACY_CONF"
getv() { sed -n "s/^$1 *= *//p" "$ACTIVE_CONF" | head -1; }
ver() { sed -n 's/^VERSION = "\(.*\)"/\1/p' "$1" 2>/dev/null | head -1; }
if [ "$MODE" = migrate ]; then
  OLD_VER="$(ver "$LEGACY_PREFIX/liteddi/version.py")"; [ -n "$OLD_VER" ] || OLD_VER="$(ver "$LEGACY_PREFIX/liteddi.py")"
  OLD_NAME=LiteDDI
else
  OLD_VER="$(ver "$PREFIX/diddy/version.py")"; OLD_NAME=Diddy
fi
NEW_VER="$(ver "$SRC/diddy/version.py")"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="${BACKUP_DIR:-/var/backups/diddy}/$TS"

echo "=============================================================="
echo " $OLD_NAME $OLD_VER  ->  Diddy $NEW_VER   (mode: $MODE)"
[ "$MODE" = migrate ] && echo " Migrasi nama: /opt/liteddi, /etc/liteddi, /var/lib/liteddi, /etc/bind/liteddi -> diddy"
echo " Backup            $BACKUP"
echo " Log               $LOGFILE"
echo "=============================================================="
if [ "$MODE" = upgrade ] && [ "$OLD_VER" = "$NEW_VER" ]; then
  echo "Versi sama. Hentikan dengan Ctrl-C, atau tunggu 5 detik untuk copy ulang."; sleep 5
fi

# jumlah data sebelum upgrade, dihitung dengan kode baru terhadap database yang sedang dipakai
VENV_PY="$PREFIX/venv/bin/python"; [ "$MODE" = migrate ] && VENV_PY="$LEGACY_PREFIX/venv/bin/python"
stats_now() { (cd "$SRC" && DIDDY_CONF="$1" "$2" -m diddy stats 2>&1 | tail -1); }
STATS_BEFORE="$(stats_now "$ACTIVE_CONF" "$VENV_PY" || true)"
case "$STATS_BEFORE" in zones=*) ;; *)
  echo "Berhenti sebelum mengubah apa pun: paket Diddy $SRC tidak bisa membaca database lewat $ACTIVE_CONF."
  echo "Error: $STATS_BEFORE"
  echo "Periksa koneksi database, atau paket yang dipakai rusak/tidak lengkap."
  exit 1;; esac

# ---------------------------------------------------------------- backup
echo "[1/8] Backup"
echo "      data saat ini: $STATS_BEFORE"
install -d -m 0700 "$BACKUP"
echo "$MODE" > "$BACKUP/mode"
save() { [ -e "$1" ] && cp -a "$1" "$BACKUP/$2" || true; }
if [ "$MODE" = migrate ]; then
  tar -C / -cf "$BACKUP/opt-liteddi.tar" --exclude=opt/liteddi/venv opt/liteddi
  save /etc/liteddi etc-liteddi
  save /var/lib/liteddi var-lib-liteddi
  save /etc/bind/liteddi bind-liteddi
  save /etc/systemd/system/liteddi.service liteddi.service
  save /usr/local/bin/liteddi liteddi-cmd
else
  save "$PREFIX/diddy" diddy-pkg
  save /etc/systemd/system/diddy.service diddy.service
  save /usr/local/bin/diddy diddy-cmd
  save "$CONF" diddy.conf
  save /etc/bind/diddy bind-diddy
fi
save /etc/bind/named.conf.local named.conf.local
save /etc/bind/named.conf.options named.conf.options
save /etc/kea/kea-dhcp4.conf kea-dhcp4.conf
# owner/mode /etc/kea dan drop-in systemd Kea, supaya rollback bisa mengembalikannya persis
. "$SRC/deploy/kea-setup.sh"
KEA_SVC="$(getv dhcp_service || true)"; KEA_SVC="${KEA_SVC:-kea-dhcp4-server}"
KEA_DROPIN="$(kea_dropin_path "$KEA_SVC")"
if [ -d "$KEA_DIR" ]; then
  (cd "$KEA_DIR" && stat -c '%u:%g %a %n' . kea-dhcp4.conf* 2>/dev/null) > "$BACKUP/kea-perms" || true
fi
save "$KEA_DROPIN" kea-dropin.conf
[ -d "$(dirname "$KEA_DROPIN")" ] && touch "$BACKUP/kea-dropin-dir-existed"
if [ "${SKIP_DB_DUMP:-0}" != "1" ] && [ "$(getv db_backend)" = "mysql" ] && command -v mysqldump >/dev/null; then
  if MYSQL_PWD="$(getv mysql_password)" mysqldump --single-transaction --no-tablespaces \
      -h "$(getv mysql_host)" -P "$(getv mysql_port)" -u "$(getv mysql_user)" \
      "$(getv mysql_database)" 2>/dev/null | gzip > "$BACKUP/db.sql.gz"; then
    echo "      dump database: $BACKUP/db.sql.gz ($(du -h "$BACKUP/db.sql.gz" | cut -f1))"
  else
    rm -f "$BACKUP/db.sql.gz"; echo "      PERINGATAN: mysqldump gagal, dilanjutkan tanpa dump database"
  fi
fi
echo "      tersimpan di $BACKUP"

# ---------------------------------------------------------------- pemulihan
health_check() {   # API hidup bila menjawab 200/401
  local port host code i
  port="$(sed -n 's/^port *= *//p' "$1" | head -1)"; [ -n "$port" ] || port=8080
  host="$(sed -n 's/^listen *= *//p' "$1" | head -1)"; case "$host" in ""|0.0.0.0|::) host=127.0.0.1;; esac
  for i in 1 2 3 4 5 6 7 8 9 10; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://$host:$port/api/v1/me" || true)"
    case "$code" in 200|401) [ "${2:-}" = quiet ] || echo "      API menjawab (HTTP $code) di http://$host:$port"; return 0;; esac
    sleep 1
  done
  return 1
}

restore_kea() {   # owner/mode /etc/kea dan drop-in systemd Kea seperti sebelum upgrade
  local own mode name
  if [ -f "$BACKUP/kea-perms" ]; then
    while read -r own mode name; do
      [ -e "$KEA_DIR/$name" ] || continue
      chown "$own" "$KEA_DIR/$name" || true
      chmod "$mode" "$KEA_DIR/$name" || true
    done < "$BACKUP/kea-perms"
  fi
  if [ -f "$BACKUP/kea-dropin.conf" ]; then
    install -m 0644 "$BACKUP/kea-dropin.conf" "$KEA_DROPIN"
  else
    rm -f "$KEA_DROPIN"
    [ -f "$BACKUP/kea-dropin-dir-existed" ] || rmdir "$(dirname "$KEA_DROPIN")" 2>/dev/null || true
  fi
}

restore_legacy() {   # kembalikan LiteDDI persis seperti sebelum migrasi
  systemctl stop diddy >/dev/null 2>&1 || true
  systemctl disable diddy >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/diddy.service /usr/local/bin/diddy
  local venv_tmp=""
  if [ -d "$PREFIX/venv" ]; then venv_tmp="$(mktemp -d)"; mv "$PREFIX/venv" "$venv_tmp/venv"; fi
  rm -rf "$PREFIX" /etc/diddy /etc/bind/diddy /var/lib/diddy "$LEGACY_PREFIX"
  tar -C / -xf "$BACKUP/opt-liteddi.tar"
  [ -n "$venv_tmp" ] && mv "$venv_tmp/venv" "$LEGACY_PREFIX/venv" && rm -rf "$venv_tmp"
  rm -rf /etc/liteddi /var/lib/liteddi /etc/bind/liteddi
  [ -d "$BACKUP/etc-liteddi" ] && cp -a "$BACKUP/etc-liteddi" /etc/liteddi
  [ -d "$BACKUP/var-lib-liteddi" ] && cp -a "$BACKUP/var-lib-liteddi" /var/lib/liteddi
  [ -d "$BACKUP/bind-liteddi" ] && cp -a "$BACKUP/bind-liteddi" /etc/bind/liteddi
  [ -f "$BACKUP/named.conf.local" ] && cp -a "$BACKUP/named.conf.local" /etc/bind/named.conf.local
  [ -f "$BACKUP/named.conf.options" ] && cp -a "$BACKUP/named.conf.options" /etc/bind/named.conf.options
  [ -f "$BACKUP/liteddi.service" ] && install -m 0644 "$BACKUP/liteddi.service" /etc/systemd/system/liteddi.service
  [ -f "$BACKUP/liteddi-cmd" ] && install -m 0755 "$BACKUP/liteddi-cmd" /usr/local/bin/liteddi
  restore_kea
  systemctl daemon-reload || true
  systemctl enable liteddi >/dev/null 2>&1 || true
  systemctl restart liteddi || true
}

restore_upgrade() {  # kembalikan paket Diddy versi sebelumnya
  rm -rf "$PREFIX/diddy" "$PREFIX/diddy.new"
  [ -d "$BACKUP/diddy-pkg" ] && cp -a "$BACKUP/diddy-pkg" "$PREFIX/diddy"
  [ -f "$BACKUP/diddy.service" ] && install -m 0644 "$BACKUP/diddy.service" /etc/systemd/system/diddy.service
  [ -f "$BACKUP/diddy-cmd" ] && install -m 0755 "$BACKUP/diddy-cmd" /usr/local/bin/diddy
  [ -f "$BACKUP/named.conf.options" ] && cp -a "$BACKUP/named.conf.options" /etc/bind/named.conf.options
  restore_kea
  systemctl daemon-reload || true
  systemctl restart diddy || true
}

rollback() {
  echo
  echo "[!] GAGAL: $1"
  echo "[!] Mengembalikan $OLD_NAME $OLD_VER"
  if [ "$MODE" = migrate ]; then restore_legacy; else restore_upgrade; fi
  sleep 3
  local c="$CONF"; [ "$MODE" = migrate ] && c="$LEGACY_CONF"
  if health_check "$c" quiet; then
    echo "[!] Selesai dikembalikan, $OLD_NAME $OLD_VER jalan lagi. Database tidak diubah."
  else
    echo "[!] Sudah dikembalikan tapi service belum menjawab. Cek: journalctl -u ${OLD_NAME,,} -n 50"
  fi
  echo "[!] Backup lengkap: $BACKUP"
  exit 1
}

# ---------------------------------------------------------------- migrasi nama
if [ "$MODE" = migrate ]; then
  echo "[2/8] Migrasi LiteDDI -> Diddy"
  systemctl stop liteddi >/dev/null 2>&1 || true
  mv "$LEGACY_PREFIX" "$PREFIX"
  install -d -m 0755 /etc/diddy
  mv "$LEGACY_CONF" "$CONF"
  find /etc/liteddi -mindepth 1 -maxdepth 1 -exec mv {} /etc/diddy/ \; 2>/dev/null || true
  rmdir /etc/liteddi 2>/dev/null || true
  sed -i -e 's/^\[liteddi\]/[diddy]/' -e 's#/etc/bind/liteddi#/etc/bind/diddy#g' \
         -e 's#/var/lib/liteddi#/var/lib/diddy#g' -e 's#named\.conf\.options\.liteddi#named.conf.options.diddy#g' "$CONF"
  chmod 0640 "$CONF"
  if [ -d /var/lib/liteddi ]; then
    mv /var/lib/liteddi /var/lib/diddy
    [ -f /var/lib/diddy/liteddi.db ] && [ ! -e /var/lib/diddy/diddy.db ] && mv /var/lib/diddy/liteddi.db /var/lib/diddy/diddy.db
  fi
  if [ -d /etc/bind/liteddi ]; then
    mv /etc/bind/liteddi /etc/bind/diddy
    [ -f /etc/bind/diddy/named.conf.liteddi ] && mv /etc/bind/diddy/named.conf.liteddi /etc/bind/diddy/named.conf.diddy
    [ -f /etc/bind/diddy/named.conf.options.liteddi ] && \
      mv /etc/bind/diddy/named.conf.options.liteddi /etc/bind/diddy/named.conf.options.diddy
    [ -f /etc/bind/diddy/named.conf.diddy ] && sed -i 's#/etc/bind/liteddi#/etc/bind/diddy#g' /etc/bind/diddy/named.conf.diddy
  fi
  [ -f /etc/bind/named.conf.local ] && \
    sed -i 's#/etc/bind/liteddi/named\.conf\.liteddi#/etc/bind/diddy/named.conf.diddy#' /etc/bind/named.conf.local
  [ -f /etc/bind/named.conf.options ] && \
    sed -i 's#/etc/bind/liteddi/named\.conf\.options\.liteddi#/etc/bind/diddy/named.conf.options.diddy#' /etc/bind/named.conf.options
  if command -v named-checkconf >/dev/null; then
    out="$(named-checkconf 2>&1)" || rollback "named-checkconf menolak config setelah migrasi: $out"
    echo "      named-checkconf: ok"
  fi
  systemctl disable liteddi >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/liteddi.service /usr/local/bin/liteddi
  echo "      direktori, config, include BIND dipindah; service & CLI liteddi dilepas"
else
  echo "[2/8] Migrasi nama: tidak perlu"
fi

# ---------------------------------------------------------------- aplikasi baru
echo "[3/8] Copy aplikasi Diddy $NEW_VER"
rm -rf "$PREFIX/diddy.new"
cp -a "$SRC/diddy" "$PREFIX/diddy.new"
find "$PREFIX/diddy.new" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$PREFIX/diddy" "$PREFIX/liteddi" "$PREFIX/static" && rm -f "$PREFIX/liteddi.py"
mv "$PREFIX/diddy.new" "$PREFIX/diddy"
install -m 0755 "$SRC/deploy/diddy" /usr/local/bin/diddy

patch_named_options() {
  local optfile="/etc/bind/named.conf.options"
  local inc='include "/etc/bind/diddy/named.conf.options.diddy";'
  [ -f /etc/bind/diddy/named.conf.options.diddy ] || \
    echo "// Diddy: diisi saat deploy pertama (resolver & forwarder)" > /etc/bind/diddy/named.conf.options.diddy
  python3 - "$optfile" <<'PY'
import shutil, sys
p = sys.argv[1]
inc = 'include "/etc/bind/diddy/named.conf.options.diddy";'
try:
    s = open(p).read()
except FileNotFoundError:
    print("      %s tidak ada, lewati" % p); raise SystemExit(0)
if inc in s:
    print("      include resolver Diddy sudah ada"); raise SystemExit(0)
i = s.find("options")
j = s.find("{", i) if i >= 0 else -1
if j < 0:
    print("      PERINGATAN: blok options{} tidak ketemu di %s, tambahkan include ini manual:" % p)
    print("        " + inc); raise SystemExit(0)
depth = 0
for k in range(j, len(s)):
    if s[k] == "{":
        depth += 1
    elif s[k] == "}":
        depth -= 1
        if depth == 0:
            break
shutil.copy2(p, p + ".diddy.bak")
open(p, "w").write(s[:k] + "    " + inc + "\n" + s[k:])
print("      include resolver ditambahkan ke %s (backup .diddy.bak)" % p)
PY
}

echo "[4/8] Include resolver BIND, permission Kea, dan dnsdist"
install -d /etc/bind/diddy
patch_named_options
chgrp -R bind /etc/bind/diddy 2>/dev/null || true
chmod -R g+rX /etc/bind/diddy 2>/dev/null || true
kea_perms "$KEA_SVC" || rollback "gagal mengatur permission $KEA_DIR"
kea_dropin "$KEA_SVC" || rollback "gagal memasang drop-in systemd $KEA_DROPIN"
if ! command -v dnsdist >/dev/null && [ "${SKIP_DNSDIST:-0}" != "1" ]; then
  echo "      memasang dnsdist (untuk upstream DoT/DoH, service tetap mati sampai dipakai)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq dnsdist >/dev/null 2>&1 && \
    systemctl disable --now dnsdist >/dev/null 2>&1 || echo "      dnsdist tidak tersedia, dilewati"
fi

echo "[5/8] Dependency Python"
if [ -x "$PREFIX/venv/bin/python" ]; then
  "$PREFIX/venv/bin/python" -m pip install -q -r "$SRC/requirements.txt" || rollback "pip install gagal"
else
  echo "      venv tidak ada, dependency dianggap dari paket sistem"
fi

echo "[6/8] Service systemd"
install -m 0644 "$SRC/deploy/diddy.service" /etc/systemd/system/diddy.service
systemctl daemon-reload
systemctl enable diddy >/dev/null 2>&1 || true
systemctl restart diddy || rollback "service diddy gagal start"

echo "[7/8] Cek kesehatan"
health_check "$CONF" || rollback "API Diddy tidak menjawab"
STATS_AFTER="$(stats_now "$CONF" "$PREFIX/venv/bin/python" || true)"
if [ "$STATS_AFTER" != "$STATS_BEFORE" ]; then
  rollback "data yang terbaca berbeda setelah upgrade (sebelum: $STATS_BEFORE | sesudah: $STATS_AFTER)"
fi
echo "      data utuh: $STATS_AFTER"

echo "[8/8] Sinkronkan config service"
export DIDDY_CONF="$CONF"
if [ "$MODE" = migrate ]; then
  (cd "$PREFIX" && "$PREFIX/venv/bin/python" -m diddy rebase-paths /etc/bind/liteddi /etc/bind/diddy) 2>&1 | sed 's/^/      /'
fi
if (cd "$PREFIX" && "$PREFIX/venv/bin/python" -m diddy deploy) 2>&1 | sed 's/^/      /'; then
  SYNC="config service sudah disinkronkan otomatis"
else
  rc=$?
  if [ "$rc" = 2 ]; then SYNC="ADA PERUBAHAN TERTUNDA: buka UI lalu klik Deploy bila sudah siap"
  else SYNC="deploy otomatis GAGAL, cek pesan di atas lalu ulangi dari UI"; fi
fi

echo
echo "Selesai: $OLD_NAME $OLD_VER -> Diddy $NEW_VER"
[ "$MODE" = migrate ] && echo "Perintah CLI sekarang 'diddy' (bukan 'liteddi'), service 'diddy', config $CONF"
echo "Sinkronisasi : $SYNC"
echo "Backup       : $BACKUP"
echo "Log          : $LOGFILE"
