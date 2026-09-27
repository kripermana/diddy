# Diddy

**Your DDI Friend**

DDI (DNS, DHCP, IPAM) ringan untuk Linux, konsepnya mengikuti Infoblox NIOS tapi seluruh komponennya open source:

| Komponen | Dipakai |
|---|---|
| DNS authoritative | ISC BIND9 |
| DHCPv4 | ISC Kea |
| Database | MySQL 8 / MariaDB (data Diddy + lease Kea); SQLite tetap bisa untuk lab |
| Aplikasi | Python 3 + Flask + Waitress, UI web tanpa build step |

Minimal 1 vCPU / 1 GB RAM (MySQL ikut di server yang sama): cocok untuk VM kecil, LXC Proxmox, atau mini PC.

## Fitur

| Konsep Infoblox | Di Diddy |
|---|---|
| Network / Network Container | Network bertingkat (parent/child otomatis dari CIDR), site, VLAN, utilisasi |
| IP Map | Grid per IP berwarna: free, DHCP range, host, lease, DNS-only, gateway, unmanaged; deteksi konflik |
| Next Available IP | Tombol di UI dan `next:<cidr>` di API (`func:nextavailableip:<cidr>` juga diterima) |
| Host Record | Satu objek = A/AAAA + PTR otomatis + reservasi DHCP |
| Upstream terenkripsi | Mode encrypted: Diddy menulis config dnsdist otomatis, BIND forward ke proxy lokal, upstream lewat DoT atau DoH |
| DNS forwarder / resolver | Recursion dengan ACL per network, upstream forwarder, dan conditional forwarder per domain (mis. domain AD) |
| DNS Zone & records | Zona forward/reverse, A, AAAA, CNAME, MX, TXT, NS, PTR, SRV, validasi konflik CNAME |
| DHCP Range, Fixed Address, Options | Pool, reservasi MAC, router, DNS server, domain name, lease time |
| DDNS (DHCP ke DNS) | Client DHCP otomatis jadi record A dan PTR di zona yang sama dengan record statis; record statis selalu menang |
| DHCP Lease viewer | Lease aktif dibaca langsung dari tabel `lease4` Kea di MySQL, bisa dijadikan reservasi |
| Discovery | Ping sweep, IP hidup tanpa objek tampil sebagai "unmanaged" |
| Restart Services banner | Banner perubahan tertunda, lalu Deploy: validasi `named-checkzone` dan `kea-dhcp4 -t` dulu, baru reload |
| WAPI | REST API `/api/v1` dengan HTTP Basic auth |
| Menu System | System information, konfigurasi (`diddy.conf` read-only, password disembunyikan), user dengan login terakhir, dan audit log dengan pencarian, filter, detail lengkap, serta export CSV |
| Deteksi drift | Diddy menyimpan salinan tiap file yang ditulisnya, membandingkan berkala dengan file yang dipakai service, mencatat ke audit log, dan bisa mengembalikannya otomatis |
| Grid status / health | Halaman Health (service, database, config BIND, disk, status deploy, DDNS) dan System information |
| Import/export zona | Zone file BIND (RFC 1035), CSV, dan JSON dengan pratinjau sebelum import, mode merge atau replace; export per zona atau semua zona |
| Kendali service | Menu Services: review and deploy, reload, restart (dengan konfirmasi), shut down (konfirmasi + password) dan start BIND/Kea/dnsdist tanpa mematikan Diddy |
| Tampilan | 5 tema: Light, Dark, Dracula, Nord, Solarized; pilihan tersimpan di browser |
| CSV Import/Export | Import host, export network/host/record/lease |

Serial SOA naik otomatis (format `YYYYMMDDnn`) hanya bila isi zona berubah.

> Diddy sebelumnya bernama **LiteDDI** (versi 1.x). Server yang masih memakai LiteDDI cukup menjalankan `sudo ./upgrade.sh` dari paket Diddy: semua direktori, config, include BIND, service, dan CLI dipindah otomatis, dan dikembalikan utuh bila ada yang gagal.

## Instalasi (Debian 12+ / Ubuntu 22.04+)

```bash
tar xzf diddy-1.0.0.tar.gz && cd diddy
sudo ./install.sh
```

Installer otomatis memasang MySQL (atau MariaDB di Debian), membuat database `diddy` dan `kea` beserta user dengan password acak, lalu menjalankan `kea-admin db-init`. Password DB tersimpan di `/etc/diddy/diddy.conf` (mode 640).

Nama database, user, password, dan prefix tabel bisa ditentukan sendiri:

```bash
sudo DB_NAME=ddi_prod DB_USER=ddi_app DB_PASS='RahasiaKuat123' TABLE_PREFIX=ddi_ \
     KEA_DB_NAME=ddi_lease KEA_DB_USER=ddi_kea KEA_DB_PASS='RahasiaKea123' ./install.sh
```

Pakai server MySQL yang sudah ada (eksternal, tanpa memasang mysql-server di server DDI):

```bash
sudo DB_HOST=10.0.0.5 DB_ADMIN_USER=root DB_ADMIN_PASSWORD='rahasia' ./install.sh
```

Kalau hanya data Diddy yang mau di MySQL, dan lease Kea tetap di file CSV:

```bash
sudo DB_NAME=ddi_prod DB_USER=ddi_app DB_PASS='RahasiaKuat123' KEA_LEASE_BACKEND=memfile ./install.sh
```

Kalau database dan user sudah disiapkan DBA, lewati pembuatannya:

```bash
sudo DB_HOST=10.0.0.5 SKIP_DB_CREATE=1 DB_NAME=ddi_prod DB_USER=ddi_app DB_PASS='...' \
     KEA_DB_NAME=ddi_lease KEA_DB_USER=ddi_kea KEA_DB_PASS='...' TABLE_PREFIX=ddi_ ./install.sh
```

Buka `http://IP-SERVER:8080`. Password admin awal ditampilkan di akhir instalasi (juga di `/var/lib/diddy/initial_admin_password`).

Sebelum deploy DHCP, set interface di `/etc/diddy/diddy.conf`:

```ini
dhcp_interfaces = ens19
```

lalu `sudo systemctl restart diddy`. Untuk uji coba tanpa menyentuh service, set `dry_run = true`.

Port yang perlu dibuka: 53/udp+tcp (DNS), 67/udp (DHCP), 8080/tcp (UI).

## Alur kerja

1. DNS: buat zona forward, misal `corp.local` (isi NS IP untuk glue record).
2. IPAM: tambah network, centang "Serve DHCP" dan "Create reverse zone".
3. Buka network, tambah DHCP range.
4. Tambah host (next free IP atau klik sel di IP map).
5. Klik "Review and deploy" pada banner kuning.

## API

Referensi lengkap, contoh, dan cara menguji ada di [`docs/API.md`](docs/API.md). Uji cepat ke server yang berjalan:

```bash
python3 tools/api_test.py --url http://SERVER:8080 -u admin -p 'PASSWORD'
```

Postman collection siap-import: [`docs/Diddy.postman_collection.json`](docs/Diddy.postman_collection.json).

```bash
# host baru di IP bebas berikutnya + reservasi DHCP
curl -u admin:PASS -H 'Content-Type: application/json' \
  -d '{"fqdn":"srv01.corp.local","ip":"next:10.10.1.0/24","mac":"aa:bb:cc:dd:ee:ff","configure_dhcp":true}' \
  http://SERVER:8080/api/v1/hosts

curl -u admin:PASS http://SERVER:8080/api/v1/networks/1/next_available?num=5
curl -u admin:PASS -X POST http://SERVER:8080/api/v1/deploy
```

Endpoint utama: `networks`, `networks/<id>/ipmap`, `networks/<id>/next_available`, `networks/<id>/discover`, `ranges`, `hosts`, `import/hosts`, `zones`, `zones/<id>/records`, `records`, `leases`, `search?q=`, `deploy/preview`, `deploy`, `audit`, `audit/<id>`, `system/config`, `users`, `export/<networks|hosts|records|leases|audit>.csv`.

## File yang dikelola

| File | Keterangan |
|---|---|
| `/etc/bind/diddy/named.conf.diddy` | Deklarasi zona, di-include dari `named.conf.local` |
| `/etc/bind/diddy/zones/db.*` | Zone file |
| `/etc/kea/kea-dhcp4.conf` | Ditimpa saat deploy; backup `.diddy.bak`, config asli `.orig` |
| MySQL `diddy` (bisa diganti) | Semua data IPAM/DNS/DHCP, user, audit log; nama tabel bisa diberi prefix |
| MySQL `kea` (bisa diganti) | Lease DHCP; nama tabel mengikuti skema Kea (`lease4`), tidak bisa diberi prefix |

Jangan edit file di atas secara manual; semua ditimpa saat deploy.

## Melanjutkan pengembangan dengan Claude Code

`CLAUDE.md` berisi konteks proyek (konvensi, cara menguji, invarian, jebakan, backlog) dan dibaca otomatis oleh Claude Code saat dibuka di folder ini, misalnya lewat ekstensi Claude Code di VS Code. Skill proyek ada di `.claude/skills/`: `/uji-diddy`, `/rilis-diddy`, `/uji-upgrade`.

## Riwayat versi dan GitHub

Semua perubahan tercatat di [`CHANGELOG.md`](CHANGELOG.md). Untuk menyusun repository git dengan satu commit dan satu tag per versi dari tarball rilis:

```bash
mkdir tarballs && cp ~/Downloads/diddy-*.tar.gz tarballs/
diddy/tools/import-history.sh tarballs diddy-repo
cd diddy-repo
git remote add origin git@github.com:<username>/diddy.git
git push -u origin main --tags
```

Setiap push ke GitHub menjalankan lint dan `tests/smoke_test.py` lewat GitHub Actions (`.github/workflows/ci.yml`).

## Upgrade

```bash
tar xzf diddy-<versi>.tar.gz && cd diddy
sudo ./upgrade.sh
```

Script ini membackup aplikasi, config, zone file, config Kea, dan dump database ke `/var/backups/diddy/<tanggal>`, memasang versi baru, restart service, lalu mengecek API benar-benar menjawab. Kalau gagal, otomatis rollback ke versi sebelumnya. Database, config, dan zone file tidak diubah. Setelah upgrade, jalankan Deploy sekali dari UI.

Opsi: `SKIP_DB_DUMP=1` untuk melewati `mysqldump`, `BACKUP_DIR=/path` untuk lokasi backup lain.

## Struktur kode

Sejak 1.9.0 Diddy berupa package Python modular, bukan satu file lagi. Rinciannya ada di [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```
diddy/
├── __main__.py        CLI (serve, deploy, drift, reset-password, migrate-sqlite, stats, cache-*)
├── web.py             Flask app factory, registrasi blueprint
├── config.py          baca /etc/diddy/diddy.conf
├── core/              error, log, util, state runtime
├── db/                koneksi MySQL/SQLite, skema, prefix tabel
├── ipam/              network, utilisasi, IP map, discovery
├── dhcp/              lease Kea, DHCP range, config Kea
├── dns/               zona, record, DDNS, resolver, forwarder, dnsdist, cache
├── deploy/            pipeline deploy, deteksi drift
├── hosts.py  users.py  auth.py  audit.py  system.py  worker.py
└── static/            web UI
tests/smoke_test.py    uji fungsional seluruh fitur
```

Menjalankan test (tidak menyentuh sistem, pakai SQLite di direktori sementara):

```bash
python3 tests/smoke_test.py
TEST_MYSQL="127.0.0.1:user:pass:database:prefix_" python3 tests/smoke_test.py   # dengan MySQL
```

## CLI

```bash
sudo diddy deploy
sudo diddy drift --repair
sudo diddy reset-password admin 'PasswordBaru123'
sudo diddy cache-stats
sudo diddy cache-flush example.com --tree
sudo diddy zone-import corp.local.zone --dry-run      # lihat dulu apa yang akan diimport
sudo diddy zone-import corp.local.zone                # import zone file BIND, CSV, atau JSON
sudo diddy zone-export corp.local > corp.local.zone   # --format=csv|json, tanpa nama zona = semua zona (JSON)
sudo diddy version
```

Perintah `diddy` dipasang installer di `/usr/local/bin`, dan hanya pembungkus untuk `python -m diddy` dengan config sistem.

`deploy` menolak jalan bila ada perubahan tertunda dari UI (exit code 2), kecuali diberi `--force`. Dipakai `upgrade.sh` supaya tidak perlu deploy manual setelah upgrade.

## Maintenance

```bash
sudo systemctl status diddy
sudo journalctl -u diddy -f
sudo diddy reset-password admin 'PasswordBaru123'
sudo ./uninstall.sh          # database MySQL tidak dihapus
```

Backup:

```bash
sudo mysqldump --single-transaction diddy | gzip > diddy-$(date +%F).sql.gz
sudo mysqldump --single-transaction kea | gzip > kea-$(date +%F).sql.gz
```

Pindah dari versi SQLite (1.0) ke MySQL: set `db_backend = mysql` di config, lalu

```bash
sudo diddy migrate-sqlite /var/lib/diddy/diddy.db
# tambahkan --src-prefix=xxx_ bila database SQLite lama memakai prefix
sudo systemctl restart diddy   # lalu Deploy sekali dari UI
```

Setelah upgrade paket Kea, jalankan `kea-admin db-upgrade mysql -u kea -p <pass> -n kea` bila Kea minta upgrade skema.

## Batasan versi 1.0

DHCP hanya IPv4; reservasi DHCP masih ditulis ke file config Kea (belum ke host backend MySQL) (IPv6 tetap bisa dikelola di IPAM dan DNS). Belum ada Grid/HA multi-member, DNS secondary/zone transfer otomatis, DDNS, dan DNSSEC; semuanya bisa ditambahkan di atas BIND/Kea. UI dirancang untuk jaringan internal, jadi letakkan di belakang reverse proxy HTTPS bila diakses lintas segmen.

Lisensi MIT. (c) 2026 kripermana.
