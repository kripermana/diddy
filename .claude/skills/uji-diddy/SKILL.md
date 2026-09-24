---
name: uji-diddy
description: Verifikasi lengkap Diddy sebelum menyatakan pekerjaan selesai - lint, smoke test SQLite dan MySQL, uji API ke server berjalan, dan cek tampilan. Pakai setiap kali selesai mengubah kode Diddy.
---

# Verifikasi Diddy

Jalankan berurutan. Laporkan angka lolos/total setiap langkah. Berhenti dan perbaiki bila ada yang gagal.

1. Lint
   ```bash
   python3 -m pyflakes diddy tests tools
   python3 -m pycodestyle --max-line-length=120 diddy
   node --check diddy/static/app.js && node --check diddy/static/charts.js
   ```
2. Smoke test SQLite: `python3 tests/smoke_test.py` (harus semua lolos).
3. Smoke test MySQL bila MySQL tersedia:
   `TEST_MYSQL="127.0.0.1:user:pass:db:ddi_" python3 tests/smoke_test.py` (pakai database uji kosong).
4. Uji API ke server lokal:
   - buat config sementara dengan `dry_run = true` dan semua path di /tmp,
   - `DIDDY_CONF=... DIDDY_ADMIN_PASSWORD=... python3 -m diddy serve &`,
   - `python3 tools/api_test.py --url http://127.0.0.1:PORT -u admin -p PASS --write`.
5. Bila endpoint berubah: `python3 tools/make_postman.py > docs/Diddy.postman_collection.json`,
   lalu bila newman ada, jalankan folder 0 sampai 9 (bukan folder Z).
6. Bila UI berubah: buka halaman terkait di browser (Playwright bila tersedia), cek tidak ada error JavaScript,
   dan periksa tema terang serta satu tema gelap. Waspadai bentrok nama class CSS lama (`.bar`, `.stack`).
7. Bila menyentuh config BIND/Kea/dnsdist: pastikan validator asli (`named-checkzone`, `named-checkconf`,
   `kea-dhcp4 -t`, `dnsdist --check-config`) dijalankan oleh deploy dan lolos.

Sebutkan terang-terangan langkah yang tidak bisa dijalankan di lingkungan ini (misalnya tidak ada MySQL atau systemd).
