# CLAUDE.md — konteks proyek Diddy untuk Claude Code

Diddy (dulu **LiteDDI**, versi 1.x) adalah DDI (DNS, DHCP, IPAM) ringan untuk Linux ala Infoblox, dibangun di atas
BIND9 dan ISC Kea. Slogan: *Your DDI Friend*. Pembuat: kripermana. Lisensi MIT. Versi saat ini ada di `diddy/version.py`.

Pemilik proyek adalah network engineer (L2, managed service untuk klien perbankan). Bahasa percakapan: Indonesia.
Dia suka jawaban singkat, hasil jadi yang sudah diuji, dan ingin diberi tahu bila ada data/sumber yang tidak tersedia.

## Konvensi
- Kode, komentar, docstring, dokumentasi, pesan CLI/installer: **Bahasa Indonesia**. Teks UI web: **Inggris**.
- Setiap perubahan fitur: naikkan `VERSION` di `diddy/version.py` (semver), tambah entri di `CHANGELOG.md`,
  perbarui baris versi di `docs/INSTALL.md`, `docs/ARCHITECTURE.md`, `docs/API.md`.
- Endpoint baru: tambahkan ke `docs/API.md` dan `tools/make_postman.py`, lalu
  `python3 tools/make_postman.py > docs/Diddy.postman_collection.json`.
- Aturan import antar-lapisan ada di `docs/ARCHITECTURE.md`. Logika bisnis di modul domain, endpoint di `routes`.
- Grafik UI ditulis sendiri di `diddy/static/charts.js` (SVG, tanpa library CDN: server target tidak punya internet).
- Rilis dikemas sebagai `diddy-X.Y.Z.tar.gz` dengan root folder `diddy/`.

## Skill proyek
- `/uji-diddy`: verifikasi lengkap sebelum menyatakan selesai.
- `/rilis-diddy`: checklist rilis versi baru.
- `/uji-upgrade`: uji upgrade.sh/install.sh termasuk rollback.

## Menguji (wajib sebelum menyatakan selesai)
```bash
python3 -m pyflakes diddy tests tools
python3 -m pycodestyle --max-line-length=120 diddy
python3 tests/smoke_test.py                                   # SQLite, dry run, direktori sementara (100 skenario)
TEST_MYSQL="127.0.0.1:user:pass:db:ddi_" python3 tests/smoke_test.py   # MySQL + prefix tabel
python3 tools/api_test.py --url http://127.0.0.1:8080 -u admin -p PASS --write   # ke server yang berjalan
```
Jalankan lokal: `DIDDY_CONF=/path/diddy.conf python3 -m diddy serve` (set `dry_run = true` dan arahkan semua path ke /tmp).
`named-checkzone`, `kea-dhcp4 -t`, dan `dnsdist --check-config` dipakai otomatis bila terpasang.

## Invarian yang tidak boleh dilanggar
1. **Database adalah sumber kebenaran.** File BIND/Kea/dnsdist selalu dirender dari DB, divalidasi di staging, baru ditulis.
   Validasi gagal = tidak ada file live yang disentuh.
2. **Staging jangan di /tmp**: AppArmor Ubuntu menolak `kea-dhcp4` membaca /tmp. Pakai `/etc/kea/` dan `/etc/bind/diddy/.staging`.
3. **Pending vs drift**: *pending* = DB berubah belum di-deploy (butuh persetujuan manusia, jangan auto-deploy).
   *Drift* = file service diubah di luar Diddy (boleh dipulihkan). Setiap file yang ditulis Diddy wajib lewat `record_file()`.
4. **DDNS satu penulis**: Diddy yang menulis blok dinamis di zone file dari tabel lease; jangan aktifkan `allow-update` BIND.
   Refresh DDNS harus melewati zona yang sedang drift (jangan mencuci drift).
5. **Upgrade aman**: `upgrade.sh` backup dulu, hitung data sebelum/sesudah (`diddy stats`), rollback penuh bila beda atau gagal.
6. **Kompatibilitas LiteDDI** tetap dijaga: section config `[liteddi]`, `LITEDDI_CONF`, file `liteddi.db`,
   penanda DDNS lama (`LEGACY_DYN_BEGIN`), migrasi `/opt/liteddi` -> `/opt/diddy` di `upgrade.sh`.
7. Counter BIND/Kea kumulatif; `metrics.py` menghitung selisih dan menangani restart (boot-time berubah / counter turun).

## Jebakan yang pernah terjadi (jangan diulang)
- Bentrok nama class CSS: `.bar` (progress bar) dan `.stack` (layout) pernah merusak grafik SVG. Pakai nama unik.
- Rename mekanis pernah mengganti nama file SQLite dan section config -> app baru melihat DB kosong. Selalu uji migrasi.
- `set -o pipefail` + `grep -q` di bash memicu SIGPIPE dan hasil palsu; hindari pipe untuk pengecekan.
- `pkill -f "<pola>"` di skrip uji bisa membunuh shell-nya sendiri bila pola ada di command line.
- `request.authorization` memverifikasi scrypt tiap request (~90 ms); sudah di-cache di `auth.py`, pertahankan invalidasinya.

## Backlog / ide yang belum dikerjakan
- Mode service non-root (user sistem `diddy` + sudoers terbatas untuk reload).
- DoT/DoH untuk client (listener BIND 9.18 sudah didukung, belum ada UI).
- Negative TTL SOA per zona (sekarang tetap 300 detik).
- Pin collation `utf8mb4_unicode_ci` di DDL tabel MySQL.
- DHCPv6 belum didukung; reservasi DHCP belum ke host backend MySQL Kea.
- Workflow GitHub Actions (`.github/workflows/ci.yml`) belum pernah dijalankan di GitHub sungguhan.
- Drag-and-drop widget dashboard baru diverifikasi dengan event HTML5 sintetis, belum dengan mouse sungguhan.
