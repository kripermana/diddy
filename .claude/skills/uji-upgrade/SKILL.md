---
name: uji-upgrade
description: Prosedur menguji upgrade.sh dan install.sh Diddy, termasuk migrasi dari LiteDDI dan rollback, di container tanpa systemd. Pakai setiap kali upgrade.sh, install.sh, layout direktori, config, atau skema database berubah.
---

# Uji upgrade dan rollback

Tujuan: membuktikan upgrade berhasil DAN kegagalan mengembalikan sistem persis seperti semula.

## Persiapan
- Stub `systemctl` di PATH yang menjalankan `ExecStart` dari unit file (`/etc/systemd/system/<svc>.service`)
  dan `WorkingDirectory`-nya. Jalankan app terlepas penuh:
  `( cd "$WD" && exec setsid sh -c "exec $CMD" ) >>/tmp/svc.log 2>&1 </dev/null &`
- Stub `apt-get` yang langsung `exit 0`.
- Jangan pakai `pkill -f "<pola>"` bila pola muncul di command line shell sendiri (shell ikut terbunuh).
- Siapkan instalasi awal yang realistis: data di database, deploy sudah jalan, include BIND terpasang,
  `named-checkconf` lolos.

## Jalur sukses
1. Catat md5 file penting (named.conf.local, named.conf.options, config, database SQLite) dan `diddy stats`.
2. Jalankan `upgrade.sh` dengan `SKIP_DB_DUMP=1 SKIP_DNSDIST=1`, simpan log.
3. Periksa: versi baru jalan, `stats` sama dengan sebelum, `named-checkconf` lolos, drift bersih,
   login dengan password lama berhasil, service/CLI lama sudah dilepas bila ada rename.

## Jalur gagal (wajib)
1. Rusak paket dengan cara yang lolos pre-check tapi gagal saat service start
   (misal `raise` di `diddy/worker.py`, yang hanya diimpor oleh `serve`).
2. Jalankan `upgrade.sh`: harus berakhir "dikembalikan".
3. Bandingkan md5 dengan catatan awal: harus identik. Tidak boleh ada sisa direktori/unit/CLI versi baru.
4. Pastikan versi lama menjawab di API dan venv lama utuh.

Laporkan kedua jalur beserta buktinya. Sebut titik awal versi mana yang diuji dan mana yang belum.
