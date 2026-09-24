#!/usr/bin/env bash
# Diddy installer for Debian 12+ / Ubuntu 22.04+
#
# MySQL boleh lokal (dipasang otomatis) atau eksternal (hanya client yang dipasang).
#
#   sudo ./install.sh
#       -> pasang MySQL/MariaDB di server ini, nama dan password digenerate
#
#   sudo DB_HOST=10.0.0.5 DB_ADMIN_USER=dbadmin DB_ADMIN_PASSWORD='rahasia' ./install.sh
#       -> pakai MySQL eksternal, installer yang membuat database dan user
#
#   sudo DB_HOST=10.0.0.5 SKIP_DB_CREATE=1 DB_NAME=... DB_USER=... DB_PASS=... \
#        KEA_DB_NAME=... KEA_DB_USER=... KEA_DB_PASS=... ./install.sh
#       -> database dan user sudah disiapkan DBA, installer hanya menulis config
#
# Variabel:
#   DB_HOST DB_PORT DB_NAME DB_USER DB_PASS TABLE_PREFIX
#   DB_ADMIN_USER DB_ADMIN_PASSWORD DB_ACCOUNT_HOST SKIP_DB_CREATE
#   KEA_LEASE_BACKEND (mysql|memfile) KEA_DB_HOST KEA_DB_NAME KEA_DB_USER KEA_DB_PASS
#   SKIP_DNSDIST=1    -> jangan pasang dnsdist (proxy untuk upstream DoT/DoH)
#   FORCE_RECONFIG=1  -> tulis ulang /etc/diddy/diddy.conf dari variabel di atas
#                        (config lama dibackup ke .bak)
#   Nama lama MYSQL_HOST, MYSQL_PORT, MYSQL_ADMIN_USER, MYSQL_ADMIN_PASSWORD,
#   MYSQL_ACCOUNT_HOST masih diterima.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Jalankan sebagai root: sudo ./install.sh"; exit 1; }
if [ -d /opt/liteddi ] && [ ! -d /opt/diddy ]; then
  echo "LiteDDI (nama lama Diddy) terdeteksi di /opt/liteddi."
  echo "Jalankan ./upgrade.sh untuk memigrasikannya menjadi Diddy tanpa kehilangan data."
  exit 1
fi
SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX=/opt/diddy
CONF=/etc/diddy/diddy.conf

# ---------------------------------------------------------------- log
LOGFILE="${LOGFILE:-/var/log/diddy-install.log}"
if ! install -m 0600 -D /dev/null "$LOGFILE.probe" 2>/dev/null; then
  LOGFILE="/tmp/diddy-install.log"
fi
rm -f "$LOGFILE.probe"
[ -f "$LOGFILE" ] || install -m 0600 /dev/null "$LOGFILE"
chmod 0600 "$LOGFILE" 2>/dev/null || true
{
  echo
  echo "=============================================================="
  echo "Diddy install dimulai $(date '+%F %T') oleh ${SUDO_USER:-$(id -un)}"
  echo "Sumber: $SRC"
  echo "=============================================================="
} >> "$LOGFILE"
exec > >(tee -a "$LOGFILE") 2>&1

finish() {
  local rc=$?
  sync 2>/dev/null || true
  if [ "$rc" -ne 0 ]; then
    echo
    echo "Instalasi BERHENTI (exit $rc). Log lengkap: $LOGFILE"
    echo "Lihat 20 baris terakhir:  sudo tail -20 $LOGFILE"
  fi
  return $rc
}
trap finish EXIT

# ---------------------------------------------------------------- variabel
SET_VARS=""
for v in DB_HOST DB_PORT DB_NAME DB_USER DB_PASS TABLE_PREFIX KEA_LEASE_BACKEND \
         KEA_DB_HOST KEA_DB_NAME KEA_DB_USER KEA_DB_PASS MYSQL_HOST MYSQL_PORT; do
  eval "isset=\${$v+yes}"
  [ "${isset:-}" = yes ] && SET_VARS="$SET_VARS $v"
done
FORCE_RECONFIG="${FORCE_RECONFIG:-0}"
DB_HOST="${DB_HOST:-${MYSQL_HOST:-127.0.0.1}}"
DB_PORT="${DB_PORT:-${MYSQL_PORT:-3306}}"
DB_NAME="${DB_NAME:-diddy}"
DB_USER="${DB_USER:-diddy}"
DB_ADMIN_USER="${DB_ADMIN_USER:-${MYSQL_ADMIN_USER:-}}"
DB_ADMIN_PASSWORD="${DB_ADMIN_PASSWORD:-${MYSQL_ADMIN_PASSWORD:-}}"
DB_ACCOUNT_HOST="${DB_ACCOUNT_HOST:-${MYSQL_ACCOUNT_HOST:-}}"
TABLE_PREFIX="${TABLE_PREFIX:-}"
SKIP_DB_CREATE="${SKIP_DB_CREATE:-0}"
KEA_LEASE_BACKEND="${KEA_LEASE_BACKEND:-mysql}"
KEA_DB_HOST="${KEA_DB_HOST:-$DB_HOST}"
KEA_DB_NAME="${KEA_DB_NAME:-kea}"
KEA_DB_USER="${KEA_DB_USER:-kea}"

KNOWN=" DB_HOST DB_PORT DB_NAME DB_USER DB_PASS DB_ADMIN_USER DB_ADMIN_PASSWORD DB_ACCOUNT_HOST TABLE_PREFIX SKIP_DB_CREATE KEA_LEASE_BACKEND KEA_DB_HOST KEA_DB_NAME KEA_DB_USER KEA_DB_PASS MYSQL_HOST MYSQL_PORT MYSQL_ADMIN_USER MYSQL_ADMIN_PASSWORD MYSQL_ACCOUNT_HOST MYSQL_PWD "
for v in $(env | sed -n 's/^\(DB_[A-Z_]*\|MYSQL_[A-Z_]*\|KEA_[A-Z_]*\|TABLE_[A-Z_]*\|SKIP_[A-Z_]*\)=.*/\1/p'); do
  case "$KNOWN" in
    *" $v "*) ;;
    *) echo "PERINGATAN: variabel '$v' tidak dikenal dan akan diabaikan (salah ketik?)";;
  esac
done

# cek login ke MySQL; kalau gagal, tampilkan error asli dan GRANT yang persis dibutuhkan
db_login_check() {   # host port user pass db label
  local h="$1" p="$2" u="$3" pw="$4" d="$5" label="$6" out seen
  out="$(MYSQL_PWD="$pw" mysql -h "$h" -P "$p" -u "$u" "$d" -e "SELECT 1" 2>&1)" && {
    echo "      login $u@$h ke $d ($label): ok"; return 0; }
  echo "      GAGAL login ke database $label '$d' sebagai '$u' di $h:$p"
  echo "$out" | grep -vi "ssl-verify-server-cert" | sed 's/^/        /'
  seen="$(echo "$out" | sed -n "s/.*'$u'@'\([^']*\)'.*/\1/p" | head -1)"
  if [ -n "$seen" ]; then
    echo "      MySQL melihat server ini sebagai host '$seen'."
    echo "      Minta DBA menjalankan (sesuaikan password):"
    echo "        CREATE USER IF NOT EXISTS '$u'@'$seen' IDENTIFIED BY '<password>';"
    echo "        GRANT ALL PRIVILEGES ON \`$d\`.* TO '$u'@'$seen';"
    echo "        FLUSH PRIVILEGES;"
    echo "      Akun '$u' mungkin sudah ada tapi untuk host lain, atau passwordnya berbeda."
  else
    echo "      Cek nama database, user, password, dan host akun MySQL-nya."
  fi
  return 1
}

ident() {
  case "$1" in
    [A-Za-z]*) [ "${#1}" -le 32 ] && [ -z "$(printf '%s' "$1" | tr -d 'A-Za-z0-9_')" ] && return 0 ;;
  esac
  echo "Nama tidak valid: '$1' (huruf/angka/underscore, awali huruf, maks 32)"; exit 1
}
ident "$DB_NAME"; ident "$DB_USER"; ident "$KEA_DB_NAME"; ident "$KEA_DB_USER"
[ -z "$TABLE_PREFIX" ] || ident "${TABLE_PREFIX%_}"
[ "$DB_NAME" != "$KEA_DB_NAME" ] || { echo "DB_NAME dan KEA_DB_NAME tidak boleh sama"; exit 1; }
case "$KEA_LEASE_BACKEND" in mysql|memfile) ;; *) echo "KEA_LEASE_BACKEND harus mysql atau memfile"; exit 1;; esac

getv() { [ -f "$CONF" ] && sed -n "s/^$1 *= *//p" "$CONF" | head -1; }

# config lama vs variabel yang diberikan sekarang
CONF_DIFF=""
if [ -f "$CONF" ]; then
  cmp_conf() {   # $1 = kunci di config, $2 = nilai dari variabel, $3.. = nama variabel terkait
    local key="$1" val="$2"; shift 2
    local used=0 v
    for v in "$@"; do case "$SET_VARS" in *" $v "*|*" $v") used=1;; esac; done
    [ "$used" = 1 ] || return 0
    local cur; cur="$(getv "$key")"
    [ "$cur" = "$val" ] || CONF_DIFF="$CONF_DIFF
  $key: config '$cur'  <->  diminta '$val'"
  }
  cmp_conf mysql_host "$DB_HOST" DB_HOST MYSQL_HOST
  cmp_conf mysql_port "$DB_PORT" DB_PORT MYSQL_PORT
  cmp_conf mysql_database "$DB_NAME" DB_NAME
  cmp_conf mysql_user "$DB_USER" DB_USER
  cmp_conf mysql_password "${DB_PASS:-}" DB_PASS
  cmp_conf table_prefix "$TABLE_PREFIX" TABLE_PREFIX
  cmp_conf kea_lease_backend "$KEA_LEASE_BACKEND" KEA_LEASE_BACKEND
  cmp_conf kea_db_host "$KEA_DB_HOST" KEA_DB_HOST
  cmp_conf kea_db_name "$KEA_DB_NAME" KEA_DB_NAME
  cmp_conf kea_db_user "$KEA_DB_USER" KEA_DB_USER
  cmp_conf kea_db_password "${KEA_DB_PASS:-}" KEA_DB_PASS
fi
if [ -n "$CONF_DIFF" ] && [ "$FORCE_RECONFIG" != "1" ]; then
  echo "=============================================================="
  echo " BERHENTI: $CONF sudah ada dan isinya BEDA dengan variabel yang kamu berikan."
  echo " Installer tidak pernah menimpa config yang sudah ada, jadi variabel tadi akan diabaikan."
  echo "$CONF_DIFF" | sed 's/^/ /'
  echo
  echo " Pilih salah satu:"
  echo "   1. Tulis ulang config dari variabel ini:  tambahkan FORCE_RECONFIG=1"
  echo "   2. Pakai config yang ada apa adanya   :  jalankan tanpa variabel DB_*/KEA_*"
  echo "   3. Edit manual                        :  $CONF, lalu jalankan ulang tanpa variabel"
  echo "=============================================================="
  exit 1
fi
if [ -n "$CONF_DIFF" ]; then
  cp -a "$CONF" "$CONF.bak.$(date +%Y%m%d%H%M%S)"
  rm -f "$CONF"
  echo "FORCE_RECONFIG=1: config lama dibackup, config baru akan ditulis"
fi

LOCAL_DB=0
case "$DB_HOST" in 127.0.0.1|localhost|::1) LOCAL_DB=1;; esac
if [ "$LOCAL_DB" = 0 ] && [ "$SKIP_DB_CREATE" != "1" ] && [ ! -f "$CONF" ]; then
  [ -n "$DB_ADMIN_USER" ] && [ -n "$DB_ADMIN_PASSWORD" ] || {
    echo "MySQL eksternal ($DB_HOST) butuh DB_ADMIN_USER dan DB_ADMIN_PASSWORD untuk membuat database,"
    echo "atau pakai SKIP_DB_CREATE=1 bila database dan user sudah disiapkan DBA."; exit 1; }
fi

# ---------------------------------------------------------------- rencana
echo "=============================================================="
echo " Diddy installer            (log: $LOGFILE)"
if [ "$LOCAL_DB" = 1 ]; then
  echo " MySQL       : LOKAL di server ini (paket server ikut dipasang)"
else
  echo " MySQL       : EKSTERNAL $DB_HOST:$DB_PORT (server TIDAK dipasang)"
fi
echo " Database    : $DB_NAME, user $DB_USER, prefix tabel '${TABLE_PREFIX:-(tidak ada)}'"
if [ "$KEA_LEASE_BACKEND" = mysql ]; then
  echo " Lease Kea   : MySQL $KEA_DB_HOST -> $KEA_DB_NAME, user $KEA_DB_USER"
else
  echo " Lease Kea   : memfile, tanpa database"
fi
[ "$SKIP_DB_CREATE" = "1" ] && echo " Pembuatan DB: dilewati (SKIP_DB_CREATE=1)"
[ -f "$CONF" ] && echo " Catatan     : $CONF sudah ada, database/user/prefix tidak diubah"
echo "=============================================================="

# ---------------------------------------------------------------- paket
echo "[1/7] Install paket"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
have_pkg() {  # tanpa pipe: 'set -o pipefail' + grep -q bisa bikin SIGPIPE dan hasil palsu
  local out; out="$(apt-cache policy "$1" 2>/dev/null || true)"
  case "$out" in
    "") return 1 ;;
    *"Candidate: (none)"*) return 1 ;;
    *Candidate:*) return 0 ;;
    *) return 1 ;;
  esac
}
PKGS="python3 python3-venv bind9 bind9-utils kea-dhcp4-server kea-admin iputils-ping openssl"
# dnsdist dipakai hanya bila kamu menyalakan upstream terenkripsi (DoT/DoH) dari UI.
# Paketnya dipasang sekarang supaya tidak perlu apt lagi nanti; servicenya dimatikan sampai dipakai.
if [ "${SKIP_DNSDIST:-0}" != "1" ] && have_pkg dnsdist; then PKGS="$PKGS dnsdist"; DNSDIST=1; fi
DBPKG=""
if [ "$LOCAL_DB" = 1 ]; then
  for p in mysql-server mariadb-server; do have_pkg "$p" && { DBPKG="$p"; break; }; done
  [ -n "$DBPKG" ] || {
    echo "Repository ini tidak punya mysql-server maupun mariadb-server."
    echo "Pasang sendiri, atau ulangi dengan DB_HOST=<ip-server-mysql> untuk pakai MySQL eksternal."; exit 1; }
  echo "      database server: $DBPKG"
else
  for p in default-mysql-client mariadb-client mysql-client; do have_pkg "$p" && { DBPKG="$p"; break; }; done
  [ -n "$DBPKG" ] || { echo "Repository ini tidak punya paket mysql client."; exit 1; }
  echo "      database client: $DBPKG (server tidak dipasang)"
fi
apt-get install -y -qq $PKGS $DBPKG >/dev/null
if [ "$LOCAL_DB" = 1 ]; then
  systemctl enable --now mysql 2>/dev/null || systemctl enable --now mariadb 2>/dev/null || true
fi
if [ "${DNSDIST:-0}" = 1 ]; then
  # config bawaan dnsdist listen di port 53 dan akan bentrok dengan BIND: matikan dulu.
  systemctl disable --now dnsdist >/dev/null 2>&1 || true
  echo "      dnsdist terpasang, service dimatikan sampai upstream terenkripsi dinyalakan dari UI"
fi

if [ "$LOCAL_DB" = 1 ]; then
  SQL() { mysql -u root -e "$1"; }                     # root lewat unix socket
  ACCT_HOSTS="${DB_ACCOUNT_HOST:-localhost 127.0.0.1}"
else
  SQL() {
    [ -n "$DB_ADMIN_USER" ] || { echo "      (lewati SQL admin: DB_ADMIN_USER tidak diberikan)"; return 0; }
    MYSQL_PWD="$DB_ADMIN_PASSWORD" mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_ADMIN_USER" -e "$1"
  }
  ACCT_HOSTS="${DB_ACCOUNT_HOST:-%}"
fi

# ---------------------------------------------------------------- database
echo "[2/7] Database dan user MySQL"
install -d -m 0755 /etc/diddy
if [ ! -f "$CONF" ]; then
  LPW="${DB_PASS:-$(openssl rand -hex 16)}"; KPW="${KEA_DB_PASS:-$(openssl rand -hex 16)}"
  if [ "$SKIP_DB_CREATE" = "1" ]; then
    [ -n "${DB_PASS:-}" ] || { echo "SKIP_DB_CREATE=1 butuh DB_PASS"; exit 1; }
    [ "$KEA_LEASE_BACKEND" = memfile ] || [ -n "${KEA_DB_PASS:-}" ] || { echo "SKIP_DB_CREATE=1 butuh KEA_DB_PASS"; exit 1; }
    echo "      dilewati, database dan user dianggap sudah ada"
  else
    SQL "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    for H in $ACCT_HOSTS; do
      SQL "CREATE USER IF NOT EXISTS '$DB_USER'@'$H' IDENTIFIED BY '$LPW'; ALTER USER '$DB_USER'@'$H' IDENTIFIED BY '$LPW';
           GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'$H';"
    done
    if [ "$KEA_LEASE_BACKEND" = "mysql" ]; then
      SQL "CREATE DATABASE IF NOT EXISTS \`$KEA_DB_NAME\`;"
      for H in $ACCT_HOSTS; do
        SQL "CREATE USER IF NOT EXISTS '$KEA_DB_USER'@'$H' IDENTIFIED BY '$KPW'; ALTER USER '$KEA_DB_USER'@'$H' IDENTIFIED BY '$KPW';
             GRANT ALL PRIVILEGES ON \`$KEA_DB_NAME\`.* TO '$KEA_DB_USER'@'$H';"
      done
    else
      echo "      Kea memakai memfile, database lease tidak dibuat"
    fi
    SQL "FLUSH PRIVILEGES;"
  fi
  DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" DB_NAME="$DB_NAME" DB_USER="$DB_USER" DB_PASS="$LPW" \
  TABLE_PREFIX="$TABLE_PREFIX" KEA_LEASE_BACKEND="$KEA_LEASE_BACKEND" KEA_DB_HOST="$KEA_DB_HOST" \
  KEA_DB_NAME="$KEA_DB_NAME" KEA_DB_USER="$KEA_DB_USER" KEA_DB_PASS="$KPW" \
  python3 - "$SRC/deploy/diddy.conf" "$CONF" <<'PY'
import os, sys
tpl = open(sys.argv[1]).read()
for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASS", "TABLE_PREFIX",
          "KEA_LEASE_BACKEND", "KEA_DB_HOST", "KEA_DB_NAME", "KEA_DB_USER", "KEA_DB_PASS"):
    tpl = tpl.replace("__%s__" % k, os.environ[k])
open(sys.argv[2], "w").write(tpl)
PY
  chmod 0640 "$CONF"
else
  echo "      config sudah ada, database/user/prefix dipakai apa adanya"
fi
db_login_check "$(getv mysql_host)" "$(getv mysql_port)" "$(getv mysql_user)" \
               "$(getv mysql_password)" "$(getv mysql_database)" "Diddy" || exit 1

# ---------------------------------------------------------------- skema Kea
echo "[3/7] Skema lease Kea"
if [ "$(getv kea_lease_backend)" = "mysql" ]; then
  KH=$(getv kea_db_host); KPORT=$(getv kea_db_port); KU=$(getv kea_db_user)
  KP=$(getv kea_db_password); KN=$(getv kea_db_name)
  KEALOG=$(mktemp)

  # 1) bisa login ke database lease?
  if ! db_login_check "$KH" "$KPORT" "$KU" "$KP" "$KN" "lease Kea"; then
    echo "      Pilihan lain: pakai user yang sama dengan Diddy, jalankan ulang dengan"
    echo "        KEA_DB_USER=$(getv mysql_user) KEA_DB_PASS='<password>' FORCE_RECONFIG=1"
    echo "      atau simpan lease lokal dengan KEA_LEASE_BACKEND=memfile FORCE_RECONFIG=1"
    rm -f "$KEALOG"; exit 1
  fi

  # 2) skema sudah ada?
  if MYSQL_PWD="$KP" mysql -h "$KH" -P "$KPORT" -u "$KU" "$KN" -e "SELECT 1 FROM lease4 LIMIT 1" >/dev/null 2>&1; then
    echo "      tabel lease4 sudah ada"
  else
    SQL "SET GLOBAL log_bin_trust_function_creators = 1;" 2>/dev/null || \
      echo "      catatan: tidak bisa set log_bin_trust_function_creators dari sini (butuh hak admin)"
    if kea-admin db-init mysql -h "$KH" -P "$KPORT" -u "$KU" -p "$KP" -n "$KN" >"$KEALOG" 2>&1; then
      echo "      kea-admin db-init: ok"
    else
      echo "      kea-admin db-init GAGAL. Output aslinya:"
      grep -v "password on the command line" "$KEALOG" | tail -15 | sed 's/^/        /'
      echo
      if grep -qi "SUPER privilege\|log_bin_trust_function_creators" "$KEALOG"; then
        echo "      Penyebabnya binary logging MySQL. Minta DBA menjalankan SEKALI:"
        echo "        SET GLOBAL log_bin_trust_function_creators = 1;"
        echo "      lalu ulangi installer ini (config sudah tersimpan, jalankan tanpa variabel)."
      elif grep -qi "denied\|privilege" "$KEALOG"; then
        echo "      User '$KU' kurang hak di database '$KN'. Minta DBA menjalankan:"
        echo "        GRANT ALL PRIVILEGES ON \`$KN\`.* TO '$KU'@'<ip-server-ini>';"
      fi
      echo "      Alternatif tercepat: ulangi dengan KEA_LEASE_BACKEND=memfile FORCE_RECONFIG=1,"
      echo "      lease disimpan lokal dan data Diddy tetap di MySQL."
      rm -f "$KEALOG"; exit 1
    fi
  fi
  rm -f "$KEALOG"
else
  echo "      memfile, tidak ada skema yang dibuat"
fi

# ---------------------------------------------------------------- aplikasi
echo "[4/7] Copy aplikasi ke $PREFIX"
install -d "$PREFIX" /var/lib/diddy /etc/bind/diddy/zones
rm -rf "$PREFIX/diddy.new"
cp -a "$SRC/diddy" "$PREFIX/diddy.new"
find "$PREFIX/diddy.new" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$PREFIX/diddy" && mv "$PREFIX/diddy.new" "$PREFIX/diddy"
rm -f "$PREFIX/diddy.py" && rm -rf "$PREFIX/static"      # sisa layout lama (<= 1.8)
install -m 0755 "$SRC/deploy/diddy" /usr/local/bin/diddy
[ -d "$PREFIX/venv" ] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/python" -m pip install -q -r "$SRC/requirements.txt"


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

echo "[5/7] Hubungkan BIND ke Diddy"
[ -f /etc/bind/diddy/named.conf.diddy ] || echo "// Diddy: kosong sampai deploy pertama" > /etc/bind/diddy/named.conf.diddy
grep -q 'diddy/named.conf.diddy' /etc/bind/named.conf.local || \
  echo 'include "/etc/bind/diddy/named.conf.diddy";' >> /etc/bind/named.conf.local
patch_named_options
chgrp -R bind /etc/bind/diddy && chmod -R g+rX /etc/bind/diddy
named-checkconf && (systemctl reload named 2>/dev/null || systemctl restart named)

echo "[6/7] Backup config Kea bawaan"
if [ -f /etc/kea/kea-dhcp4.conf ] && [ ! -f /etc/kea/kea-dhcp4.conf.orig ]; then
  cp -a /etc/kea/kea-dhcp4.conf /etc/kea/kea-dhcp4.conf.orig
fi

echo "[7/7] Service systemd"
install -m 0644 "$SRC/deploy/diddy.service" /etc/systemd/system/diddy.service
systemctl daemon-reload
systemctl enable --now diddy >/dev/null
systemctl restart diddy
systemctl enable --now kea-dhcp4-server named >/dev/null 2>&1 || true
sleep 3

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Diddy terpasang:  http://${IP:-SERVER-IP}:8080"
[ -f /var/lib/diddy/initial_admin_password ] && echo "Login: admin / $(cat /var/lib/diddy/initial_admin_password)"
echo "Database: $(getv mysql_host):$(getv mysql_port) -> $(getv mysql_database) (prefix '$(getv table_prefix)')"
echo "Lease Kea: $(getv kea_lease_backend)$([ "$(getv kea_lease_backend)" = mysql ] && echo " -> $(getv kea_db_host)/$(getv kea_db_name)")"
echo "Password DB tersimpan di $CONF"
echo "Set dhcp_interfaces di $CONF sebelum deploy DHCP pertama."
echo "Perintah CLI  : sudo diddy deploy | drift | reset-password | version"
echo "Log instalasi: $LOGFILE"
