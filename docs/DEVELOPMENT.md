# Pengembangan Diddy

Panduan untuk developer: konvensi kode, cara menguji, invarian yang tidak boleh dilanggar, jebakan yang pernah
terjadi, checklist rilis, dan uji upgrade. Arsitektur dan aturan import antar-lapisan ada di
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Konvensi
- Kode, komentar, docstring, dokumentasi, pesan CLI/installer: **Bahasa Indonesia**. Teks UI web: **Inggris**.
- Setiap perubahan fitur: naikkan `VERSION` di `diddy/version.py` (semver), tambah entri di `CHANGELOG.md`,
  perbarui baris versi di `docs/INSTALL.md`, `docs/ARCHITECTURE.md`, `docs/API.md`.
- Endpoint baru: tambahkan ke `docs/API.md` dan `tools/make_postman.py`, lalu
  `python3 tools/make_postman.py > docs/Diddy.postman_collection.json`.
- Logika bisnis di modul domain, endpoint di modul `routes` (lihat aturan lapisan di `ARCHITECTURE.md`).
- Grafik UI ditulis sendiri di `diddy/static/charts.js` (SVG, tanpa library CDN: server target tidak punya internet).
- Rilis dikemas sebagai `diddy-X.Y.Z.tar.gz` dengan root folder `diddy/`.

## Menguji (wajib sebelum menyatakan selesai)
```bash
python3 -m pyflakes diddy tests tools
python3 -m pycodestyle --max-line-length=120 diddy
node --check diddy/static/app.js && node --check diddy/static/charts.js
python3 tests/smoke_test.py                                   # SQLite, dry run, direktori sementara
TEST_MYSQL="127.0.0.1:user:pass:db:ddi_" python3 tests/smoke_test.py   # MySQL + prefix tabel (database uji kosong)
python3 tools/api_test.py --url http://127.0.0.1:8080 -u admin -p PASS --write   # ke server yang berjalan
```
Jalankan lokal: `DIDDY_CONF=/path/diddy.conf python3 -m diddy serve` (set `dry_run = true` dan arahkan semua path
ke direktori sementara). `named-checkzone`, `kea-dhcp4 -t`, dan `dnsdist --check-config` dipakai otomatis bila
terpasang; pasang `bind9-utils` dan `kea-dhcp4-server` supaya validator asli ikut diuji.

Tidak ada CI otomatis di repository ini: jalankan semua langkah di atas secara lokal sebelum commit.

Urutan verifikasi lengkap:
1. Lint (tiga perintah pertama di atas).
2. Smoke test SQLite, harus semua lolos.
3. Smoke test MySQL bila MySQL tersedia.
4. Uji API ke server lokal: config sementara dengan `dry_run = true`,
   `DIDDY_CONF=... DIDDY_ADMIN_PASSWORD=... python3 -m diddy serve &`, lalu `tools/api_test.py --write`.
   Ulangi tanpa `--write` dengan user read-only.
5. Bila endpoint berubah: regenerasi Postman collection; bila newman ada, jalankan folder 0 sampai 9 (bukan folder Z).
6. Bila UI berubah: buka halaman terkait di browser, pastikan tidak ada error JavaScript, periksa tema terang dan
   satu tema gelap, serta lebar layar ponsel. Waspadai bentrok nama class CSS lama (`.bar`, `.stack`).
7. Bila menyentuh config BIND/Kea/dnsdist: pastikan validator asli (`named-checkzone`, `named-checkconf`,
   `kea-dhcp4 -t`, `dnsdist --check-config`) dijalankan oleh deploy dan lolos.

Sebutkan terang-terangan langkah yang tidak bisa dijalankan (misalnya tidak ada MySQL atau systemd).

## Invarian yang tidak boleh dilanggar
1. **Database adalah sumber kebenaran.** File BIND/Kea/dnsdist selalu dirender dari DB, divalidasi di staging, baru
   ditulis. Validasi gagal = tidak ada file live yang disentuh.
2. **Staging jangan di /tmp**: AppArmor Ubuntu menolak `kea-dhcp4` membaca /tmp. Pakai `/etc/kea/` dan
   `/etc/bind/diddy/.staging`.
3. **Pending vs drift**: *pending* = DB berubah belum di-deploy (butuh persetujuan manusia, jangan auto-deploy).
   *Drift* = file service diubah di luar Diddy (boleh dipulihkan). Setiap file yang ditulis Diddy wajib lewat
   `record_file()`.
4. **DDNS satu penulis**: Diddy yang menulis blok dinamis di zone file dari tabel lease; jangan aktifkan
   `allow-update` BIND. Refresh DDNS harus melewati zona yang sedang drift (jangan mencuci drift).
5. **Upgrade aman**: `upgrade.sh` backup dulu, hitung data sebelum/sesudah (`diddy stats`), rollback penuh bila beda
   atau gagal.
6. **Kompatibilitas LiteDDI** tetap dijaga: section config `[liteddi]`, `LITEDDI_CONF`, file `liteddi.db`,
   penanda DDNS lama (`LEGACY_DYN_BEGIN`), migrasi `/opt/liteddi` -> `/opt/diddy` di `upgrade.sh`.
7. Counter BIND/Kea kumulatif; `metrics.py` menghitung selisih dan menangani restart (boot-time berubah / counter
   turun).
8. **Service yang dimatikan dari Diddy tetap mati**: deploy, perbaikan drift, dan refresh DDNS tidak boleh me-reload
   atau me-restart service selama status `services_stopped` aktif.

## Jebakan yang pernah terjadi (jangan diulang)
- Bentrok nama class CSS: `.bar` (progress bar) dan `.stack` (layout) pernah merusak grafik SVG. Pakai nama unik
  berawalan per fitur (`svc-`, `zx-`, `aud-`, `cfg-`, `cache-`).
- Rename mekanis pernah mengganti nama file SQLite dan section config -> app baru melihat DB kosong. Selalu uji migrasi.
- `set -o pipefail` + `grep -q` di bash memicu SIGPIPE dan hasil palsu; hindari pipe untuk pengecekan.
- `pkill -f "<pola>"` di skrip uji bisa membunuh shell-nya sendiri bila pola ada di command line.
- `request.authorization` memverifikasi scrypt tiap request (~90 ms); sudah di-cache di `auth.py`, pertahankan
  invalidasinya.
- Respons async yang datang terlambat (pindah halaman/tab/rentang cepat) bisa menimpa tampilan baru: pakai token
  navigasi seperti di `route()` dan `loadDash()`.
- Kea membersihkan hostname sendiri sebelum disimpan di lease (karakter tidak valid dibuang, >63 karakter dikosongkan);
  DDNS Diddy bekerja dari nama yang sudah disimpan Kea.

## Checklist rilis
1. Tentukan versi (semver): patch untuk perbaikan, minor untuk fitur, major bila path/nama/CLI/perilaku upgrade berubah.
2. Ubah `VERSION` di `diddy/version.py`.
3. Tambah entri paling atas di `CHANGELOG.md` (bagian Ditambahkan / Diubah / Diperbaiki, Bahasa Indonesia).
4. Perbarui baris "Dokumen ini untuk Diddy X.Y.Z" di `docs/INSTALL.md`, `docs/ARCHITECTURE.md`, `docs/API.md`.
5. Endpoint baru: `docs/API.md` dan `tools/make_postman.py`, lalu regenerasi `docs/Diddy.postman_collection.json`.
6. Config baru: tambahkan ke `DEFAULTS` dan `META` di `diddy/config.py` dan ke template `deploy/diddy.conf` dengan
   komentar.
7. Skema baru: DDL SQLite dan MySQL di `diddy/db/schema.py`; kolom baru untuk tabel lama lewat `ensure_columns()`;
   tabel baru ke `TABLES` di `diddy/db/tables.py`.
8. Bila perlu langkah upgrade: ubah `upgrade.sh` dan jalankan uji upgrade (bagian berikut).
9. Jalankan verifikasi lengkap sampai semua lolos.
10. Kemas: hapus `__pycache__`, lalu `tar czf diddy-X.Y.Z.tar.gz diddy` dari folder induk (root folder `diddy/`).
11. Catat: apa yang berubah, hasil uji, dan apakah `upgrade.sh` berubah.

## Uji upgrade dan rollback
Wajib setiap kali `upgrade.sh`, `install.sh`, layout direktori, config, atau skema database berubah. Tujuannya
membuktikan upgrade berhasil DAN kegagalan mengembalikan sistem persis seperti semula. Bisa dilakukan di container
tanpa systemd.

Persiapan:
- Stub `systemctl` di PATH yang menjalankan `ExecStart` dari unit file (`/etc/systemd/system/<svc>.service`) dan
  `WorkingDirectory`-nya. Jalankan app terlepas penuh:
  `( cd "$WD" && exec setsid sh -c "exec $CMD" ) >>/tmp/svc.log 2>&1 </dev/null &`
- Stub `apt-get` yang langsung `exit 0`.
- Siapkan instalasi awal yang realistis: data di database, deploy sudah jalan, include BIND terpasang,
  `named-checkconf` lolos.

Jalur sukses:
1. Catat md5 file penting (named.conf.local, named.conf.options, config, database SQLite) dan `diddy stats`.
2. Jalankan `upgrade.sh` dengan `SKIP_DB_DUMP=1 SKIP_DNSDIST=1`, simpan log.
3. Periksa: versi baru jalan, `stats` sama dengan sebelum, `named-checkconf` lolos, drift bersih, login dengan
   password lama berhasil, service/CLI lama sudah dilepas bila ada rename.

Jalur gagal (wajib):
1. Rusak paket dengan cara yang lolos pre-check tapi gagal saat service start (misal `raise` di `diddy/worker.py`,
   yang hanya diimpor oleh `serve`).
2. Jalankan `upgrade.sh`: harus berakhir "dikembalikan".
3. Bandingkan md5 dengan catatan awal: harus identik. Tidak boleh ada sisa direktori/unit/CLI versi baru.
4. Pastikan versi lama menjawab di API dan venv lama utuh.

Catat kedua jalur beserta buktinya, termasuk versi awal mana yang diuji dan mana yang belum.

## Uji lab DHCP dan DDNS
`tools/dhcp_sim.py` mensimulasikan banyak klien DHCP (MAC dan hostname acak) dari mesin yang satu L2 dengan Kea, lalu
mengecek hasil DDNS dengan `dig` dan API Diddy. Contoh config: `python3 tools/dhcp_sim.py --example-config`.
Jangan dijalankan di VLAN produksi.

## Backlog / ide yang belum dikerjakan
- Mode service non-root (user sistem `diddy` + sudoers terbatas untuk reload).
- DoT/DoH untuk client (listener BIND 9.18 sudah didukung, belum ada UI).
- Negative TTL SOA per zona (sekarang tetap 300 detik).
- Pengaturan `recursive-clients` BIND dari UI.
- Pin collation `utf8mb4_unicode_ci` di DDL tabel MySQL.
- DHCPv6 belum didukung; reservasi DHCP belum ke host backend MySQL Kea.
- Tipe record CAA dan DNSSEC belum didukung (import zona melewatinya dengan alasan).
- Mekanisme plugin untuk edisi Enterprise (loader, hook, slot menu UI).
