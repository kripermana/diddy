# Fungsi bersama install.sh dan upgrade.sh untuk Kea. Di-source, bukan dijalankan langsung.
#
# kea_perms  : /etc/kea milik root dengan group user service Kea (mode 0750), config 0640.
#              Deploy Diddy menjalankan `kea-dhcp4 -t` sebagai root di bawah profil AppArmor kea-dhcp4,
#              yang tidak memberi capability dac_override/dac_read_search. Kalau /etc/kea milik user lain
#              (mis. _kea:_kea 0750), root tidak bisa masuk dan cek config gagal "Unable to open file".
#              Config berisi password database lease, jadi tidak boleh dibaca semua user (0644).
# kea_dropin : drop-in systemd supaya Kea menunggu jaringan dan mencoba start lagi bila gagal, misalnya
#              database lease MySQL belum terjangkau saat boot (Kea tidak mengulang koneksi saat startup).

KEA_DIR=/etc/kea

kea_group() {   # group untuk /etc/kea: group utama user service Kea, atau _kea, atau root
  local u
  u="$(systemctl show -p User --value "$1" 2>/dev/null || true)"
  if [ -n "$u" ] && [ "$u" != root ] && id -gn "$u" >/dev/null 2>&1; then
    id -gn "$u"
  elif getent group _kea >/dev/null 2>&1; then
    echo _kea
  else
    echo root
  fi
}

kea_perms() {   # $1 = nama service Kea
  local grp f
  [ -d "$KEA_DIR" ] || { echo "      $KEA_DIR tidak ada, lewati"; return 0; }
  grp="$(kea_group "$1")"
  chown "root:$grp" "$KEA_DIR" || return 1
  chmod 0750 "$KEA_DIR" || return 1
  for f in "$KEA_DIR"/kea-dhcp4.conf*; do
    [ -f "$f" ] || continue
    chown "root:$grp" "$f" || return 1
    chmod 0640 "$f" || return 1
  done
  echo "      $KEA_DIR: root:$grp 0750, kea-dhcp4.conf* 0640"
}

kea_dropin_path() { echo "/etc/systemd/system/$1.service.d/diddy.conf"; }

kea_dropin() {   # $1 = nama service Kea
  local p
  p="$(kea_dropin_path "$1")"
  install -d -m 0755 "$(dirname "$p")" || return 1
  cat > "$p" <<'EOF' || return 1
# Dipasang oleh Diddy: tunggu jaringan siap, dan start ulang bila Kea gagal
# (mis. database lease MySQL belum terjangkau saat boot).
[Unit]
After=network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=10
EOF
  chmod 0644 "$p" || return 1
  systemctl daemon-reload || return 1
  echo "      drop-in systemd: $p"
}
