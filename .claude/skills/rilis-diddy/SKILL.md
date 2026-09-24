---
name: rilis-diddy
description: Checklist merilis versi baru Diddy - nomor versi, changelog, dokumen, Postman, verifikasi, dan paket tar.gz. Pakai saat pekerjaan siap dirilis.
---

# Rilis Diddy

1. Tentukan versi (semver): patch untuk perbaikan, minor untuk fitur, major bila path/nama/CLI/perilaku upgrade berubah.
2. Ubah `VERSION` di `diddy/version.py`.
3. Tambah entri paling atas di `CHANGELOG.md` (bagian Ditambahkan / Diubah / Diperbaiki, Bahasa Indonesia).
4. Perbarui baris "Dokumen ini untuk Diddy X.Y.Z" di `docs/INSTALL.md`, `docs/ARCHITECTURE.md`, `docs/API.md`.
5. Endpoint baru: `docs/API.md` dan `tools/make_postman.py`, lalu regenerasi `docs/Diddy.postman_collection.json`.
6. Config baru: tambahkan ke `DEFAULTS` di `diddy/config.py` dan ke template `deploy/diddy.conf` dengan komentar.
7. Skema baru: DDL SQLite dan MySQL di `diddy/db/schema.py`; kolom baru untuk tabel lama lewat `ensure_columns()`;
   tabel baru ke `TABLES` di `diddy/db/tables.py`.
8. Bila perlu langkah upgrade: ubah `upgrade.sh` dan jalankan skill `uji-upgrade`.
9. Jalankan skill `uji-diddy` sampai semua lolos.
10. Kemas: hapus `__pycache__`, lalu `tar czf diddy-X.Y.Z.tar.gz diddy` dari folder induk (root folder `diddy/`).
11. Laporkan: apa yang berubah, hasil uji, dan apakah `upgrade.sh` berubah.
