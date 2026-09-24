# Changelog

Semua perubahan penting Diddy dicatat di sini. Versi 1.x dirilis dengan nama LiteDDI. Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/) dan penomoran [Semantic Versioning](https://semver.org/lang/id/).

## [2.2.3]
### Diperbaiki
- Deploy gagal di `check kea-dhcp4.conf` dengan "Unable to open file" setelah reboot, bila `/etc/kea` dimiliki `_kea:_kea` 0750: `kea-dhcp4 -t` berjalan sebagai root, dan profil AppArmor-nya tidak memberi `dac_read_search`/`dac_override`. `install.sh` dan `upgrade.sh` kini mengatur `/etc/kea` menjadi `root:<group service Kea>` 0750 dan `kea-dhcp4.conf*` 0640. Config yang berisi password database lease juga tidak lagi bisa dibaca semua user.
- Kea tidak start saat boot bila database lease MySQL belum terjangkau (Kea tidak mengulang koneksi saat startup). `install.sh` dan `upgrade.sh` memasang drop-in systemd `kea-dhcp4-server.service.d/diddy.conf`: tunggu `network-online.target`, `Restart=on-failure`, `RestartSec=10`.
### Diubah
- Pesan error deploy untuk "Unable to open file" kini menyebut owner/mode direktori yang salah beserta perintah perbaikannya.
- `upgrade.sh` mencatat owner/mode `/etc/kea` dan drop-in Kea saat backup, dan mengembalikannya persis bila rollback. `uninstall.sh` menghapus drop-in tersebut.

## [2.2.0]
### Ditambahkan
- Halaman **DNS cache** (DNS > Resolver > DNS cache) untuk mengelola cache resolver BIND:
  - Statistik saat ini: hit ratio, jumlah RRset (positif, negatif, stale), cache node, memori terpakai, entri yang dibuang karena cache penuh atau TTL habis, dan jenis record terbanyak.
  - Grafik hit ratio 1 jam, 6 jam, 24 jam, atau 7 hari. Counter hit/miss ikut disimpan collector metrik tiap menit, dan restart BIND ditangani.
  - Lookup isi cache untuk satu nama tanpa memicu resolusi baru (`dig +norecurse`), lengkap dengan sisa TTL. IP otomatis dijadikan lookup PTR.
  - Flush seluruh cache (`rndc flush`), satu nama (`rndc flushname`), atau satu nama beserta turunannya (`rndc flushtree`). Flush dicatat di audit log dan tidak menandai perubahan tertunda.
  - Pengaturan `max-cache-size`, `max-cache-ttl`, `max-ncache-ttl`, dirender ke options BIND dan berlaku setelah deploy.
- Endpoint `/dns-cache`, `/dns-cache/settings`, `/dns-cache/flush`, `/dns-cache/lookup`.
- Perintah CLI `diddy cache-stats`, `diddy cache-flush [NAMA] [--tree]`, `diddy cache-lookup NAMA [TIPE]`.
- Config `bind_local_addr` (default `127.0.0.1`): alamat BIND yang ditanya untuk lookup cache.
- Lookup cache mengenali jawaban negatif yang ter-cache (NXDOMAIN dan NODATA) dari record SOA di bagian authority, lengkap dengan sisa TTL negatifnya.
- Skill Claude Code proyek (`.claude/skills/`) dan bagian README-nya ikut disertakan.

## [2.1.0]
### Ditambahkan
- Dashboard baru dengan tab **Overview**, **DNS**, dan **DHCP**, bergaya widget ala Infoblox, dengan pilihan rentang 1 jam, 6 jam, 24 jam, dan 7 hari serta refresh otomatis tiap menit.
- Widget DNS: queries per second, requests per hour 24 jam beserta totalnya, DNS utilization (gauge terhadap `dns_capacity_qps`), response code, query type, answer source, dan records per zone.
- Widget DHCP: lease aktif dari waktu ke waktu, pool utilization, pemakaian pool per network, dan trafik paket Kea (DISCOVER, OFFER, REQUEST, ACK, NAK, RELEASE, DECLINE).
- Dashboard bisa diatur per user: tambah, hapus, ubah ukuran, dan susun ulang widget; tersimpan per user per tab.
- Collector statistik (`metrics.py`): sampel counter BIND (statistics-channel JSON) dan Kea (`statistic-get-all`) tiap menit, deteksi restart, masa simpan bisa diatur.
- Statistics-channel BIND dikelola otomatis di `named.conf.diddy` (`bind_stats_manage`).
- Grafik SVG tanpa library eksternal (`static/charts.js`), bisa dipakai di server tanpa akses internet.
- Endpoint `/metrics/dns`, `/metrics/dhcp`, `/dashboard/layout`. Status collector tampil di Health.

## [2.0.1]
### Diubah
- Slogan menjadi *"Your DDI Friend"*.

## [2.0.0]
### Diubah
- **Nama software menjadi Diddy**, dengan slogan *"Your network, fully addressed."*
- Package Python `diddy`, service `diddy`, perintah CLI `diddy`, variabel `DIDDY_CONF`.
- Lokasi baru: `/opt/diddy`, `/etc/diddy/diddy.conf`, `/var/lib/diddy`, `/etc/bind/diddy`, log `/var/log/diddy-install.log`.
- Default database untuk instalasi baru: `diddy`.
### Ditambahkan
- Migrasi otomatis dari LiteDDI lewat `upgrade.sh`: memindah direktori, mengganti nama config dan database SQLite, menyesuaikan include BIND, mengganti service dan CLI, lalu memperbarui acuan drift (`diddy rebase-paths`).
- Penjaga kontinuitas data di `upgrade.sh`: jumlah zona, record, network, range, host, forwarder, dan user dihitung sebelum dan sesudah; bila berbeda, semua dikembalikan sebelum deploy.
- Perintah `diddy stats`.
- Kompatibilitas: config dengan section `[liteddi]`, variabel `LITEDDI_CONF` dan `LITEDDI_ADMIN_PASSWORD`, penanda blok DDNS lama, dan pilihan tema lama tetap dikenali.
- `tools/import-history.sh` menyambung tarball `liteddi-1.x` dan `diddy-2.x` dalam satu riwayat git.
### Diperbaiki
- Acuan drift untuk file yang tidak lagi dikelola (zona dihapus, direktori pindah) dibuang saat deploy.

## [1.9.1]
### Ditambahkan
- `tools/api_test.py`: uji API ke server yang berjalan, mode baca saja atau siklus buat/ubah/hapus dengan pembersihan otomatis.
- Postman collection `docs/LiteDDI.postman_collection.json` (60 request) dan dokumentasi `docs/API.md`.
### Diperbaiki
- HTTP Basic auth memverifikasi hash password di setiap request (~90 ms). Hasil verifikasi kini di-cache 5 menit di memori, dan langsung batal saat password diganti atau user dihapus. Rata-rata respons API turun dari sekitar 95 ms menjadi 9 ms.

## [1.9.0]
### Diubah
- Kode dipecah dari satu file `liteddi.py` menjadi package modular `liteddi/`: `core`, `db`, `ipam`, `dhcp`, `dns`, `deploy`, serta `auth`, `users`, `hosts`, `system`, `worker`.
- Service berjalan dengan `python -m liteddi serve`.
- `upgrade.sh` mengenali layout lama (satu file) dan layout baru, termasuk rollback ke layout lama bila upgrade gagal.
### Ditambahkan
- Perintah `liteddi` di `/usr/local/bin`: `deploy`, `drift`, `reset-password`, `migrate-sqlite`, `version`.
- `tests/smoke_test.py`: 75 skenario uji, bisa dijalankan dengan SQLite atau MySQL.
- `docs/ARCHITECTURE.md`: peta modul, aturan import, alur deploy.

## [1.8.3]
### Diperbaiki
- Refresh DDNS tidak lagi terdeteksi sebagai drift. Salinan acuan ikut diperbarui saat LiteDDI sendiri menulis zone file.
- Refresh DDNS melewati zone file yang sedang drift, supaya editan manual tidak ikut disahkan. Status tampil di Health.

## [1.8.2]
### Diperbaiki
- Pemeriksaan drift tidak lagi ikut mati saat `ddns_refresh_interval = 0`. Kedua tugas latar punya jadwal sendiri.

## [1.8.1]
### Diperbaiki
- Kontras teks di sel IP map dan tombol utama pada tema Dracula dan Nord.
- Kolom detail audit log dipotong satu baris, isi lengkap muncul saat kursor diarahkan.

## [1.8.0]
### Ditambahkan
- Deteksi drift: salinan setiap file yang ditulis LiteDDI dibandingkan berkala dengan file yang dipakai service, dicatat ke audit log, tampil di Health dan halaman Deploy.
- Pemulihan drift dari UI atau otomatis (`drift_auto_repair`), file lama disimpan sebagai `.drift.bak`.
- CLI `deploy` dan `drift`. `upgrade.sh` menjalankan deploy otomatis bila tidak ada perubahan tertunda.

## [1.7.0]
### Ditambahkan
- Upstream terenkripsi DoT/DoH lewat dnsdist lokal yang dikonfigurasi otomatis, dengan preset Cloudflare, Quad9, Google, dan AdGuard.
- Validasi `dnsdist --check-config` di pipeline deploy.
- Installer memasang dnsdist dalam keadaan mati sampai dipakai.

## [1.6.0]
### Ditambahkan
- Lima tema tampilan: Light, Dark, Dracula, Nord, Solarized.
- Halaman Health dan System information.
- Versi dan copyright di Dashboard dan footer.

## [1.5.0]
### Ditambahkan
- Resolver: recursion dengan ACL (otomatis dari network IPAM), upstream forwarder, DNSSEC validation.
- Conditional forwarder per domain.
- Include resolver di dalam blok `options{}` `named.conf.options`.

## [1.4.0]
### Ditambahkan
- `upgrade.sh`: backup aplikasi, config, dan dump database, health check, rollback otomatis bila gagal.

## [1.3.7]
### Diperbaiki
- File staging validasi dipindah dari `/tmp` ke `/etc/kea` dan `/etc/bind/liteddi/.staging` karena ditolak AppArmor.

## [1.3.6]
### Diperbaiki
- Saat login MySQL gagal, installer menampilkan host yang terbaca MySQL beserta perintah GRANT yang dibutuhkan.
- Kredensial LiteDDI selalu diverifikasi, termasuk saat config sudah ada.

## [1.3.5]
### Ditambahkan
- Log installer di `/var/log/liteddi-install.log`.

## [1.3.4]
### Diperbaiki
- Output asli `kea-admin db-init` ditampilkan saat gagal, dengan diagnosis binary logging atau hak akses.

## [1.3.3]
### Ditambahkan
- `FORCE_RECONFIG=1` untuk menulis ulang config; installer berhenti bila config lama berbeda dengan variabel yang diberikan.
- Pengecekan login database sebelum instalasi dilanjutkan.

## [1.3.2]
### Diubah
- Variabel installer diseragamkan menjadi `DB_*`; nama `MYSQL_*` tetap diterima.
### Ditambahkan
- Peringatan variabel yang tidak dikenal dan ringkasan rencana sebelum instalasi.
### Diperbaiki
- Deteksi paket memakai `apt-cache policy`; perbaikan SIGPIPE pada `pipefail`.

## [1.3.1]
### Diperbaiki
- `SKIP_DB_CREATE=1` untuk MySQL eksternal tidak lagi mewajibkan akun admin.
### Ditambahkan
- `MYSQL_ACCOUNT_HOST` untuk menentukan host akun MySQL.

## [1.3.0]
### Ditambahkan
- DDNS: client DHCP didaftarkan sebagai record A dan PTR di zona yang sama dengan record statis; record statis selalu menang.
- Refresh DDNS berkala dan tombol refresh manual.
- Penambahan kolom database otomatis untuk instalasi lama.

## [1.2.1]
### Ditambahkan
- `KEA_LEASE_BACKEND=memfile` untuk menyimpan lease Kea tanpa database.

## [1.2.0]
### Ditambahkan
- Nama database, user, password, dan prefix tabel bisa ditentukan sendiri.
- `SKIP_DB_CREATE=1` untuk database yang disiapkan DBA.

## [1.1.0]
### Ditambahkan
- Backend MySQL/MariaDB untuk data LiteDDI dan lease Kea.
- Migrasi dari SQLite (`migrate-sqlite`).
- Installer memasang dan menyiapkan MySQL lokal atau memakai server eksternal.

## [1.0.0]
### Ditambahkan
- Rilis pertama: IPAM (network bertingkat, IP map, next available IP, ping sweep), DNS authoritative BIND9 (zona, record, reverse otomatis), DHCPv4 ISC Kea (range, option, reservasi, lease), host object, deploy dengan validasi, audit log, role admin/read-only, REST API, import/export CSV, installer.
