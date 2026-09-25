# Diddy: Prasyarat, Instalasi, dan Troubleshooting

Dokumen ini untuk Diddy 2.3.0 (sebelumnya LiteDDI) (edisi MySQL, DDNS, DNS forwarder, health view, dan tema). (c) 2026 kripermana, lisensi MIT. Semua perintah dijalankan sebagai root atau dengan `sudo`.

---

## 1. Prasyarat

### 1.1 Sistem operasi

| OS | Status |
|---|---|
| Ubuntu 24.04 LTS | Sudah diuji (BIND 9.18, Kea 2.4.1, MySQL 8.0) |
| Debian 12 | Didukung, installer otomatis memakai MariaDB |
| Ubuntu 22.04 LTS | Didukung (Kea 2.0.x), belum diuji penuh |

Arsitektur amd64 atau arm64. Bisa berjalan di VM, LXC Proxmox (privileged lebih mudah untuk ping sweep), atau bare metal.

### 1.2 Resource minimum

| Resource | Minimum | Rekomendasi |
|---|---|---|
| vCPU | 1 | 2 |
| RAM | 1 GB | 2 GB |
| Disk | 10 GB | 20 GB (log dan lease) |

### 1.3 Jaringan

| Kebutuhan | Keterangan |
|---|---|
| IP statis | Server DNS/DHCP wajib punya IP tetap |
| Port 8080/tcp | Web UI dan API Diddy |
| Port 53/udp + 53/tcp | DNS (BIND) |
| Port 67/udp | DHCP (Kea) |
| Port 3306/tcp | Hanya jika MySQL ada di server lain |
| DHCP relay | Untuk subnet di VLAN lain, router/L3 switch harus relay ke IP server ini (`ip helper-address <IP-Diddy>` di Cisco) |

### 1.4 Akses saat instalasi

- Akses root.
- Repository apt (online atau mirror lokal) untuk paket `bind9`, `kea-dhcp4-server`, `kea-admin`, `mysql-server`/`mariadb-server`, `python3-venv`.
- Akses ke PyPI (`pypi.org`, `files.pythonhosted.org`) untuk `flask`, `waitress`, `pymysql`. Kalau server tidak boleh ke internet, lihat bagian 2.7.

### 1.5 Pastikan tidak ada service yang bentrok

Cek sebelum install:

```bash
ss -lunp | grep -E ':53 |:67 '
ss -ltnp | grep -E ':53 |:8080 '
systemctl is-active isc-dhcp-server dnsmasq 2>/dev/null
```

Port 67 dan 53 tidak boleh dipakai `isc-dhcp-server`, `dnsmasq`, atau DNS server lain. `systemd-resolved` yang listen di `127.0.0.53` aman dibiarkan.

### 1.6 Siapkan informasi ini

| Item | Contoh |
|---|---|
| Nama name server utama | `ns1.corp.local` |
| Email admin zona (SOA) | `hostmaster@corp.local` |
| Interface yang melayani DHCP | `ens19` |
| Zona forward yang akan dibuat | `corp.local` |
| Nama database, user DB, password DB, prefix tabel | `ddi_prod`, `ddi_app`, `...`, `ddi_` |
| Network, gateway, DNS server, range DHCP | `10.10.1.0/24`, `10.10.1.1`, `10.10.1.2`, `.100-.199` |

---

## 2. Instalasi

### 2.1 Siapkan server

```bash
hostnamectl set-hostname ddi01
apt update && apt -y upgrade
timedatectl set-timezone Asia/Jakarta
```

Waktu server harus akurat karena berpengaruh pada lease DHCP dan serial DNS. Pastikan NTP aktif (`timedatectl` menunjukkan `System clock synchronized: yes`).

### 2.2 Copy dan extract paket

```bash
scp diddy-1.1.0.tar.gz user@ddi01:/tmp/
ssh user@ddi01
cd /tmp && tar xzf diddy-1.1.0.tar.gz && cd diddy
```

### 2.3 Jalankan installer

**Opsi A: MySQL di server yang sama (default)**

```bash
sudo ./install.sh
```

**Opsi B: nama database, user, password, dan prefix tabel sendiri**

```bash
sudo DB_NAME=ddi_prod DB_USER=ddi_app DB_PASS='RahasiaKuat123' TABLE_PREFIX=ddi_ \
     KEA_DB_NAME=ddi_lease KEA_DB_USER=ddi_kea KEA_DB_PASS='RahasiaKea123' ./install.sh
```

| Variabel | Default | Keterangan |
|---|---|---|
| `DB_NAME` | `diddy` | Database data Diddy |
| `DB_USER` / `DB_PASS` | `diddy` / acak | User MySQL untuk aplikasi |
| `TABLE_PREFIX` | kosong | Prefix tabel Diddy, misal `ddi_` menjadi `ddi_networks` |
| `KEA_DB_NAME` | `kea` | Database lease Kea |
| `KEA_DB_USER` / `KEA_DB_PASS` | `kea` / acak | User MySQL untuk Kea |
| `DB_HOST` / `DB_PORT` | `127.0.0.1` / `3306` | Lokasi server MySQL. Selain `127.0.0.1`, `localhost`, atau `::1` dianggap eksternal, jadi paket server tidak dipasang |
| `DB_ADMIN_USER` / `DB_ADMIN_PASSWORD` | kosong | Akun admin MySQL, dipakai hanya saat installer membuat database |
| `DB_ACCOUNT_HOST` | `%` (eksternal) | Host untuk akun MySQL, misal `10.10.1.5` |
| `KEA_DB_HOST` | sama dengan `DB_HOST` | Kalau lease Kea ditaruh di server MySQL lain |
| `KEA_LEASE_BACKEND` | `mysql` | `memfile` = lease Kea disimpan di file CSV, database Kea tidak dibuat |
| `SKIP_DB_CREATE` | `0` | `1` = database dan user sudah dibuat DBA, installer hanya menulis config |
| `FORCE_RECONFIG` | `0` | `1` = tulis ulang `diddy.conf` dari variabel ini; config lama dibackup ke `.bak.<tanggal>` |

Semua variabel berdiri sendiri. Kalau hanya `DB_NAME` dan `DB_USER` yang diisi, variabel `KEA_DB_*` otomatis memakai default (`kea`/`kea` dengan password acak).

Aturan penamaan: diawali huruf, hanya huruf, angka, dan underscore, maksimal 32 karakter (prefix maksimal 20). Nama tabel Kea (`lease4` dan lainnya) mengikuti skema resmi Kea dan tidak bisa diberi prefix, jadi database Diddy dan database Kea harus berbeda.

Kalau DBA yang menyiapkan database, hak minimal yang dibutuhkan:

```sql
CREATE DATABASE ddi_prod CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE ddi_lease;
CREATE USER 'ddi_app'@'10.10.1.5' IDENTIFIED BY '...';
CREATE USER 'ddi_kea'@'10.10.1.5' IDENTIFIED BY '...';
GRANT ALL PRIVILEGES ON ddi_prod.* TO 'ddi_app'@'10.10.1.5';
GRANT ALL PRIVILEGES ON ddi_lease.* TO 'ddi_kea'@'10.10.1.5';
```

Tabel Diddy dibuat otomatis saat aplikasi start pertama kali, dan skema lease dibuat oleh `kea-admin db-init`.

**Opsi C: MySQL di server lain (tanpa memasang mysql-server di sini)**

Installer akan mendeteksi `DB_HOST` yang bukan `127.0.0.1` atau `localhost`, lalu hanya memasang `default-mysql-client`. Tidak ada MySQL server yang dipasang di server DDI.

Kalau installer yang membuat database dan user (butuh akun admin di MySQL):

```bash
sudo DB_HOST=10.0.0.5 DB_ADMIN_USER=dbadmin DB_ADMIN_PASSWORD='rahasia' \
     DB_NAME=ddi_prod DB_USER=ddi_app KEA_DB_NAME=ddi_lease KEA_DB_USER=ddi_kea ./install.sh
```

Kalau DBA sudah menyiapkan semuanya dan kamu tidak punya akun admin:

```bash
sudo DB_HOST=10.0.0.5 SKIP_DB_CREATE=1 \
     DB_NAME=ddi_prod DB_USER=ddi_app DB_PASS='...' \
     KEA_DB_NAME=ddi_lease KEA_DB_USER=ddi_kea KEA_DB_PASS='...' ./install.sh
```

Yang harus disiapkan di server MySQL:

| Kebutuhan | Keterangan |
|---|---|
| Port 3306 terbuka | Dari IP server DDI ke server MySQL |
| `bind-address` | Jangan `127.0.0.1` saja, supaya bisa diakses dari luar |
| Akun MySQL | Host akun harus cocok dengan IP server DDI **seperti yang dilihat MySQL**, bukan IP manajemen atau IP lain di server yang sama. Kalau login gagal, installer menampilkan host yang terbaca MySQL beserta perintah `CREATE USER`/`GRANT` yang persis dibutuhkan. Misal `'ddi_app'@'10.10.1.5'`. Pakai `DB_ACCOUNT_HOST=10.10.1.5` agar installer membuat akun dengan host itu, default-nya `%` |
| Skema lease Kea | `kea-admin db-init` dijalankan installer. Di MySQL 8 dengan binary log aktif, DBA perlu `SET GLOBAL log_bin_trust_function_creators = 1;` sekali saja |
| Versi | MySQL 8.0 atau MariaDB 10.5 ke atas |

Catatan performa: Kea menulis lease ke database setiap kali ada request DHCP. Kalau latensi ke server MySQL tinggi, pakai `KEA_LEASE_BACKEND=memfile` agar lease tetap lokal, sedangkan data Diddy tetap di MySQL eksternal.

**Yang dikerjakan installer:**

1. Install BIND9, Kea DHCPv4, kea-admin, Python, dan MySQL server (lokal) atau client saja (eksternal).
2. Membuat database dan user, kecuali `SKIP_DB_CREATE=1`.
3. Menulis `/etc/diddy/diddy.conf` (mode 640, berisi password DB). File yang sudah ada tidak pernah ditimpa, kecuali `FORCE_RECONFIG=1`.
4. Inisialisasi skema lease Kea (`kea-admin db-init mysql`).
5. Copy aplikasi ke `/opt/diddy` dan membuat Python venv.
6. Menambahkan `include "/etc/bind/diddy/named.conf.diddy";` ke `/etc/bind/named.conf.local`.
7. Backup config Kea bawaan ke `/etc/kea/kea-dhcp4.conf.orig`, mengatur `/etc/kea` menjadi `root:<group service Kea>` 0750 dan `kea-dhcp4.conf*` 0640, memasang drop-in systemd `/etc/systemd/system/kea-dhcp4-server.service.d/diddy.conf` (tunggu jaringan, start ulang bila gagal), lalu memasang service systemd.

**Log instalasi:** semua output dicatat ke `/var/log/diddy-install.log` (mode 0600, ditambahkan tiap sesi, bukan ditimpa). Kalau instalasi berhenti, path log dan perintah untuk melihatnya ditampilkan di baris terakhir. Ganti lokasi dengan `LOGFILE=/path/lain.log`.

Di akhir, installer menampilkan URL, password admin awal (juga tersimpan di `/var/lib/diddy/initial_admin_password`), serta ringkasan database yang dipakai.

### 2.4 Konfigurasi setelah install

Edit `/etc/diddy/diddy.conf`:

```ini
default_ns = ns1.corp.local
default_admin_email = hostmaster@corp.local
dhcp_interfaces = ens19
```

Isi `dhcp_interfaces` dengan interface yang benar, jangan biarkan `*` di server dengan banyak interface. Lalu restart:

```bash
sudo systemctl restart diddy
```

Untuk uji coba tanpa mengubah BIND/Kea yang sedang jalan, set `dry_run = true`. File config tetap ditulis, tapi service tidak di-reload.

### 2.5 Firewall

Dengan UFW:

```bash
ufw allow 53
ufw allow 67/udp
ufw allow from 10.0.0.0/8 to any port 8080 proto tcp   # batasi ke subnet admin
ufw enable
```

### 2.6 Setup awal dari web UI

Buka `http://IP-SERVER:8080` dan login sebagai `admin`.

1. **System → Users → Change password**, lalu buat user read-only untuk tim lain bila perlu.
2. **DNS → Add zone**: `corp.local`. Isi *Name server IP* agar glue record `ns1` ikut dibuat.
3. **IPAM → Add network**: `10.10.1.0/24`, isi gateway, centang *Serve DHCP*, isi DNS server dan domain, centang *Create the reverse DNS zone*.
4. Buka network tersebut, lalu **Add DHCP range**: `10.10.1.100` sampai `10.10.1.199`.
5. **Add host at next free IP** atau klik sel di IP map untuk membuat host (A + PTR + reservasi DHCP).
6. Opsional, aktifkan DDNS: edit network, centang *DDNS: register DHCP clients in DNS*. Client dari pool akan muncul sebagai `<nama-client>.<domain>` di zona yang sama dengan record statis.
6. Klik **Review and deploy** pada banner kuning, lalu **Validate and deploy**.

### 2.7 Instalasi tanpa akses PyPI (opsional)

Pakai paket Python dari apt, bukan pip:

```bash
apt install -y python3-flask python3-waitress python3-pymysql
```

Setelah itu ubah `ExecStart` di `/etc/systemd/system/diddy.service` menjadi:

```ini
WorkingDirectory=/opt/diddy
ExecStart=/usr/bin/python3 -m diddy serve
```

Lalu hapus baris `pip install` di `install.sh` sebelum menjalankannya, dan jalankan `systemctl daemon-reload && systemctl restart diddy` setelah install selesai.

### 2.8 Verifikasi

```bash
# service
systemctl status diddy named kea-dhcp4-server mysql --no-pager

# API
curl -s -u admin:PASSWORD http://127.0.0.1:8080/api/v1/me

# DNS
dig @127.0.0.1 corp.local SOA +short
dig @127.0.0.1 printer.corp.local +short
dig @127.0.0.1 -x 10.10.1.3 +short

# DHCP
journalctl -u kea-dhcp4-server -n 30 --no-pager
mysql -u root kea -e "SELECT INET_NTOA(address) ip, HEX(hwaddr) mac, hostname, expire FROM lease4;"
```

Dashboard Diddy juga menampilkan status DNS, DHCP, database Diddy, dan database lease Kea.

### 2.9 HTTPS (disarankan)

Diddy berjalan di HTTP biasa. Untuk akses lintas segmen, taruh di belakang reverse proxy. Contoh nginx:

```nginx
server {
    listen 443 ssl;
    server_name ddi01.corp.local;
    ssl_certificate     /etc/ssl/certs/ddi01.crt;
    ssl_certificate_key /etc/ssl/private/ddi01.key;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

Lalu set `listen = 127.0.0.1` di `diddy.conf` supaya port 8080 tidak terbuka langsung.

### 2.10 Operasional rutin

| Tugas | Perintah |
|---|---|
| Log aplikasi | `journalctl -u diddy -f` |
| Backup | `mysqldump --single-transaction diddy \| gzip > diddy-$(date +%F).sql.gz` (ulangi untuk `kea`) |
| Restore | `gunzip -c diddy-TANGGAL.sql.gz \| mysql diddy`, lalu Deploy sekali |
| Reset password | `sudo diddy reset-password admin 'PassBaru123'` |
| Upgrade Diddy | Extract versi baru, jalankan `sudo ./upgrade.sh`. Backup, copy, restart, health check, deploy otomatis, dan rollback bila gagal. Tidak ada langkah deploy manual, kecuali memang ada perubahan tertunda dari UI |
| Rollback manual | Perintahnya dicetak di akhir upgrade; backup ada di `/var/backups/diddy/<tanggal>` |
| Upgrade skema Kea | `kea-admin db-upgrade mysql -u kea -p <pass> -n kea` |
| Uninstall | `sudo ./uninstall.sh` (database MySQL tidak dihapus) |

### 2.10b Migrasi dari LiteDDI

Diddy adalah nama baru LiteDDI mulai versi 2.0.0. Di server yang masih memakai LiteDDI:

```bash
tar xzf diddy-2.0.0.tar.gz && cd diddy
sudo ./upgrade.sh
```

Script mendeteksi `/opt/liteddi` dan otomatis masuk mode migrasi:

| Sebelum | Sesudah |
|---|---|
| `/opt/liteddi` | `/opt/diddy` |
| `/etc/liteddi/liteddi.conf` (section `[liteddi]`) | `/etc/diddy/diddy.conf` (section `[diddy]`) |
| `/var/lib/liteddi/liteddi.db` | `/var/lib/diddy/diddy.db` |
| `/etc/bind/liteddi/named.conf.liteddi` | `/etc/bind/diddy/named.conf.diddy` |
| service `liteddi`, perintah `liteddi` | service `diddy`, perintah `diddy` |

Database MySQL tidak diganti namanya: nama database, user, password, dan prefix tabel tetap seperti sebelumnya. Jumlah data dibandingkan sebelum dan sesudah migrasi; bila berbeda, atau service tidak mau start, semuanya dikembalikan ke LiteDDI seperti semula. `install.sh` menolak jalan selama `/opt/liteddi` masih ada, supaya tidak ada dua instalasi berdampingan.

### 2.11 File dan lokasi penting

| Lokasi | Isi |
|---|---|
| `/etc/diddy/diddy.conf` | Config Diddy + password DB |
| `/opt/diddy/` | Aplikasi dan venv |
| `/var/lib/diddy/` | Secret key sesi, password admin awal |
| `/etc/bind/diddy/named.conf.diddy` | Deklarasi zona dan conditional forwarder (dibuat saat deploy) |
| `/etc/bind/diddy/named.conf.options.diddy` | Setting resolver: recursion, forwarder, ACL. Di-include dari dalam blok `options{}` di `named.conf.options` |
| `/etc/bind/diddy/zones/db.*` | Zone file (dibuat saat deploy) |
| `/etc/kea/kea-dhcp4.conf` | Config Kea (ditimpa saat deploy, backup `.diddy.bak`) |
| MySQL `diddy` | Data IPAM/DNS/DHCP, user, audit |
| MySQL `kea` | Lease DHCP |

File BIND dan Kea di atas **jangan diedit manual**. Semuanya ditimpa pada deploy berikutnya.

---

## 3. Troubleshooting

Langkah pertama untuk semua masalah:

```bash
systemctl status diddy named kea-dhcp4-server mysql --no-pager
journalctl -u diddy -n 50 --no-pager
```

### 3.0 Cek cepat lewat UI

Sebelum menggali log, buka **Health** (link di footer atau tombol di Dashboard). Di situ ada status service Diddy, database, DNS, DHCP, database lease Kea, hasil `named-checkconf`, status deploy, DDNS, dan sisa disk. **System information** menampilkan versi Diddy, Python, BIND, Kea, OS, path config, dan database yang dipakai. Sertakan isi dua halaman itu kalau melaporkan masalah.

Tema tampilan (Light, Dark, Dracula, Nord, Solarized) ada di dropdown pojok kanan atas dan tersimpan per browser.

### 3.0a Kendali service dari UI (Services)

Tombol **Services** ada di samping tombol Health, di **System → Information** dan di halaman **Health** (khusus admin). Semua aksi berlaku untuk BIND, Kea DHCPv4, dan dnsdist (bila upstream terenkripsi dipakai). Diddy sendiri tidak pernah ikut dimatikan.

| Aksi | Yang dijalankan | Konfirmasi |
|---|---|---|
| Review and deploy | Buka halaman Deploy; deploy bisa dijalankan walau tidak ada perubahan tertunda | - |
| Reload services | `dns_reload_cmd` (default `rndc reload`), `config-reload` Kea lewat control socket (fallback `kea_reload_fallback_cmd`), `systemctl reload-or-restart` dnsdist | - |
| Restart services | `systemctl restart` tiap service | Dialog konfirmasi |
| Shut down services | `systemctl stop`: Kea dulu, lalu BIND, lalu dnsdist | Dialog konfirmasi + password user yang login |
| Start services | `systemctl start`: dnsdist, BIND, lalu Kea | - |

Selama service dimatikan dari Diddy:

- Banner merah tampil di semua halaman, lengkap dengan tombol **Start services**, dan Health memberi peringatan *Service control*.
- Reload dan restart ditolak. Deploy tetap menulis dan memvalidasi file, tetapi tidak me-reload service; config baru terpakai saat service dinyalakan.
- Perbaikan drift dan refresh DDNS tetap menulis file, tetapi tidak me-reload atau me-restart service, supaya service yang sengaja dimatikan tidak menyala sendiri.
- Semua aksi, termasuk percobaan shutdown dengan password salah, dicatat di audit log (`services-*`).

Kalau service dinyalakan manual dengan `systemctl start`, status "shut down" di Diddy tetap ada sampai tombol **Start services** diklik.

### 3.0b Drift: config service berubah di luar Diddy

Diddy menyimpan salinan setiap file yang ditulisnya (zone file, `named.conf.diddy`, file options, `kea-dhcp4.conf`, `dnsdist.conf`). Tiap `drift_check_interval` detik (default 300) salinan itu dibandingkan dengan file yang benar-benar dipakai service.

- Hasilnya tampil di **Health** dan di panel *Service configuration* pada halaman Deploy.
- Setiap drift dicatat ke audit log sebagai `drift-detected`, dan perbaikannya sebagai `drift-repair`.
- Tombol **Restore the last deployed files** mengembalikan file ke versi terakhir yang di-deploy. File yang sedang ada disimpan dulu sebagai `.drift.bak`.
- Set `drift_auto_repair = true` di config kalau mau dikembalikan otomatis tanpa menunggu klik.
- Dari CLI: `sudo diddy drift` untuk melihat, `sudo diddy drift --repair` untuk mengembalikan.

Perlu dibedakan: *pending changes* berarti database sudah berubah tapi belum di-deploy, dan ini butuh persetujuan manusia. *Drift* berarti file service berubah di luar Diddy, dan ini selalu boleh dikembalikan.

Perubahan yang dilakukan Diddy sendiri, termasuk refresh DDNS, tidak dihitung sebagai drift. Sebaliknya, kalau sebuah zone file sedang drift, refresh DDNS untuk zona itu **dilewati** sampai drift-nya diperbaiki. Tujuannya supaya editan manual tidak ikut "disahkan" oleh refresh otomatis. Status ini muncul di Health sebagai *DDNS refresh: Dilewati untuk zona ...*.

### 3.0c Dashboard dan statistik

Dashboard punya tiga tab: **Overview**, **DNS**, dan **DHCP**. Setiap user bisa mengatur widget miliknya sendiri lewat tombol **Customize**: tambah, hapus, ubah ukuran (S, M, L), dan susun ulang dengan drag atau tombol panah. Layout disimpan per user per tab, dan user read-only juga boleh menyimpan layout-nya.

Sumber data:

| Grafik | Asal data |
|---|---|
| DNS queries per second, requests per hour, rcode, qtype, answer sources | statistics-channel BIND di `127.0.0.1:8053`, ditambahkan otomatis ke `named.conf.diddy` saat deploy |
| DNS utilization | query/detik 5 menit terakhir dibanding `dns_capacity_qps` (default 1000). Sesuaikan dengan kapasitas yang kamu anggap 100% |
| Active DHCP leases, pool utilization | lease aktif Kea (MySQL atau CSV) dan DHCP range di Diddy |
| DHCP traffic (DISCOVER, OFFER, REQUEST, ACK, ...) | control socket Kea, perintah `statistic-get-all` |

Collector mengambil sampel setiap `metrics_interval` detik (default 60) dan menyimpannya `metrics_retention_days` hari (default 7). Counter BIND dan Kea bersifat kumulatif; Diddy menghitung selisihnya, dan restart named/Kea terdeteksi sehingga grafik tidak pernah negatif.

| Gejala | Solusi |
|---|---|
| Grafik DNS kosong, pesan *BIND statistics are not reachable* | Deploy sekali agar statistics-channel aktif, lalu tunggu 1 sampai 2 menit. Tes: `curl -s http://127.0.0.1:8053/json/v1/server \| head -c 200` |
| Sudah punya `statistics-channels` sendiri di port 8053 | Set `bind_stats_manage = false` dan arahkan `bind_stats_url` ke channel milikmu |
| Widget DHCP traffic: *Kea packet statistics are not available* | kea-dhcp4 tidak berjalan atau `kea_socket` tidak cocok dengan `socket-name` di config Kea. Grafik lease tetap jalan tanpa ini |
| Angka hari ini berbeda dengan log BIND | Hitungan dimulai sejak collector pertama kali jalan; query sebelum itu tidak tercatat |
| Health: *Dashboard statistics* kuning | Statistik BIND tidak terbaca; detail error tampil di sana |

### 3.1 Web UI tidak bisa dibuka

| Gejala | Penyebab | Solusi |
|---|---|---|
| Connection refused | Service `diddy` mati | `journalctl -u diddy -n 50`, perbaiki error, lalu `systemctl restart diddy` |
| Timeout dari PC lain | Firewall | `ufw allow 8080/tcp` atau cek firewall di jalur jaringan |
| Hanya bisa dari server itu sendiri | `listen = 127.0.0.1` | Ubah ke `0.0.0.0` atau akses lewat reverse proxy |
| `Address already in use` di log | Port 8080 dipakai aplikasi lain | `ss -ltnp \| grep 8080`, ganti `port` di config |

### 3.2 Installer mengabaikan variabel yang saya berikan

Installer tidak pernah menimpa `/etc/diddy/diddy.conf` yang sudah ada. Kalau config lama masih ada dari percobaan sebelumnya, semua variabel `DB_*` dan `KEA_*` akan berbeda dengan isinya, dan installer berhenti sambil menampilkan perbandingannya.

Pilihannya: tambahkan `FORCE_RECONFIG=1` untuk menulis ulang config (yang lama dibackup), jalankan tanpa variabel apa pun untuk memakai config yang ada, atau hapus `/etc/diddy/diddy.conf` lalu ulangi.

Gejala khas versi lama: `kea-admin db-init` mencoba login sebagai user `kea` padahal kamu memberi `KEA_DB_USER` yang lain. Itu karena nilainya diambil dari config lama, bukan dari variabel.

### 3.3 Diddy gagal start karena database

| Pesan di log | Solusi |
|---|---|
| `Access denied for user 'diddy'` | Password di config salah. Tes: `mysql -h 127.0.0.1 -u diddy -p diddy`. Reset: `ALTER USER 'diddy'@'localhost' IDENTIFIED BY '...';` lalu samakan di config |
| `Can't connect to MySQL server` | MySQL mati atau host/port salah. `systemctl status mysql`; untuk server eksternal cek port 3306 dan `bind-address` di MySQL |
| `Unknown database ...` | Buat dulu: `CREATE DATABASE <nama> CHARACTER SET utf8mb4;`, lalu restart Diddy (tabel dibuat otomatis) |
| UI tiba-tiba kosong setelah ubah config | `table_prefix` atau `mysql_database` berubah, sehingga Diddy membuat set tabel baru yang kosong. Kembalikan nilai lama, atau rename tabel lama: `RENAME TABLE networks TO ddi_networks;` untuk setiap tabel |
| `No module named 'pymysql'` | `/opt/diddy/venv/bin/pip install pymysql` |

### 3.4 Lupa password admin

```bash
sudo diddy reset-password admin 'PassBaru123'
```

### 3.5 Deploy gagal

Kalau validasi gagal, deploy berhenti dan **tidak ada** file BIND/Kea yang diubah. Baca output tiap langkah di halaman Deploy.

| Pesan | Penyebab | Solusi |
|---|---|---|
| `NS 'ns1.corp.local' has no address records` | NS di dalam zona tapi tidak ada A record-nya | Tambah A record `ns1` di zona tersebut |
| `has no address records (A or AAAA)` pada MX/SRV | Target belum punya A record | Warning saja. Tambahkan A record target agar layanannya berfungsi |
| `check kea-dhcp4.conf` gagal | Config Kea tidak valid | Baca pesan Kea. Biasanya interface di `dhcp_interfaces` tidak ada (`ip -br link`) |
| `Unable to open file /tmp/...` saat cek Kea | AppArmor hanya mengizinkan `kea-dhcp4` membaca `/etc/kea/**` | Sudah diperbaiki sejak 1.3.7: file staging ditaruh di `/etc/kea/` dan `/etc/bind/diddy/.staging`. Kalau masih muncul, cek `sudo dmesg \| grep -i apparmor \| grep kea` |
| `Unable to open file /etc/kea/.diddy-staging.conf`, dmesg: `apparmor="DENIED" ... capname="dac_read_search"` | `/etc/kea` bukan milik root (mis. `_kea:_kea` 0750). `kea-dhcp4 -t` dijalankan root, dan AppArmor tidak memberinya izin menembus permission | `sudo chown root:_kea /etc/kea /etc/kea/kea-dhcp4.conf && sudo chmod 0750 /etc/kea && sudo chmod 0640 /etc/kea/kea-dhcp4.conf`, atau jalankan `./upgrade.sh` 2.2.3 ke atas yang mengaturnya otomatis |
| Kea mati setelah reboot, log: `Unable to open database: Can't connect to server` | Database lease MySQL belum terjangkau saat Kea start; Kea tidak mengulang koneksi saat startup | Pastikan MySQL terjangkau, lalu `sudo systemctl restart kea-dhcp4-server`. Drop-in dari `install.sh`/`upgrade.sh` 2.2.3 ke atas membuat Kea menunggu jaringan dan mencoba lagi tiap 10 detik |
| `permission denied` saat menulis file | Diddy tidak jalan sebagai root | Pastikan unit systemd tidak diubah ke user lain |

Banner kuning "pending changes" hanya hilang setelah deploy sukses tanpa error.

### 3.6 Resolver dan forwarder

Atur dari UI: **DNS zones -> Resolver & forwarders**. Recursion default mati, jadi server hanya menjawab zona miliknya sendiri sampai kamu menyalakannya.

| Gejala | Solusi |
|---|---|
| Client dapat `REFUSED` untuk domain internet | Recursion masih mati, atau IP client tidak masuk ACL. Nyalakan recursion dan centang *Allow every network in IPAM*, atau tambahkan networknya |
| Semua query internet gagal padahal recursion on | Upstream forwarder tidak bisa dihubungi dan policy `only`. Ganti ke `first`, atau perbaiki upstream. Tes: `dig @<upstream> google.com` |
| Domain AD atau partner tidak resolve | Tambahkan conditional forwarder untuk domain itu. Jangan buat zona authoritative dengan nama yang sama, Diddy menolaknya |
| SERVFAIL untuk domain tertentu | Upstream tidak mendukung DNSSEC. Set DNSSEC validation ke `no`, atau ganti upstream |
| Include resolver hilang | Installer menambahkannya ke `named.conf.options`. Cek `grep diddy /etc/bind/named.conf.options`; kalau hilang jalankan `sudo ./upgrade.sh` |

Jangan jadikan server ini open resolver. Batasi ACL hanya ke network internal.

**Upstream terenkripsi (DoT/DoH).** Di *Edit resolver*, ubah *Upstream mode* menjadi `Encrypted`. Diddy akan menulis `/etc/dnsdist/dnsdist.conf`, memvalidasinya dengan `dnsdist --check-config`, menjalankan service dnsdist, lalu mengarahkan forwarder BIND ke `127.0.0.1 port 5353`. Kamu tidak perlu menyentuh file Lua-nya.

Format upstream, satu per baris:

```
dot 1.1.1.1 cloudflare-dns.com
doh 9.9.9.9 dns.quad9.net /dns-query
dot 10.5.5.5:8853 dns.bank.internal
```

Ada preset Cloudflare, Quad9, Google, dan AdGuard. Port default 853 untuk DoT dan 443 untuk DoH. Nama setelah IP adalah nama pada sertifikat TLS server tujuan, dan divalidasi kecuali kamu mematikan *Validate upstream TLS certificates*.

| Gejala | Solusi |
|---|---|
| `dnsdist belum terpasang` | `sudo apt install dnsdist`, atau jalankan `sudo ./upgrade.sh` dari paket Diddy terbaru |
| Deploy berhenti di `check dnsdist.conf` | Output `dnsdist --check-config` ditampilkan apa adanya di laporan deploy |
| Semua resolusi internet mati setelah pindah ke encrypted | Cek `systemctl status dnsdist` dan `journalctl -u dnsdist -n 50`. Umumnya port 853 diblok firewall keluar, atau sertifikat upstream tidak lolos validasi |
| Mau kembali ke DNS biasa | Ubah *Upstream mode* ke `Plain`, lalu Deploy. Diddy mematikan service dnsdist dan mengembalikan forwarder BIND |
| dnsdist bentrok di port 53 | Config bawaan paket memang listen di port 53. Installer mematikan servicenya, dan config dari Diddy selalu listen di `127.0.0.1:5353` |

Ingat: enkripsi ini hanya melindungi satu hop, yaitu dari server ini ke upstream. Kalau kebijakan perusahaan melarang DNS keluar ke pihak ketiga, pakai mode plain dengan forwarder ke resolver internal.

**Cache DNS.** Saat recursion menyala, BIND menyimpan jawaban dari upstream di cache. Halaman **DNS zones -> DNS cache** menampilkan hit ratio, isi cache, dan memori yang terpakai. Dari halaman itu kamu juga bisa melihat isi cache untuk satu nama, menghapus cache, dan membatasi ukuran serta TTL cache.

- **Statistik** diambil dari statistics-channel BIND (`bind_stats_url`), sama seperti dashboard DNS.
- **Lookup** memakai `dig +norecurse` ke `bind_local_addr` (default `127.0.0.1`), jadi paket `bind9-dnsutils` harus terpasang.
- **Flush** memakai `rndc` dan langsung berlaku tanpa deploy.
- **Pengaturan ukuran dan TTL** baru berlaku setelah Deploy.

Dari CLI:

```bash
sudo diddy cache-stats
sudo diddy cache-lookup www.example.com A
sudo diddy cache-flush www.example.com           # satu nama
sudo diddy cache-flush example.com --tree        # nama beserta semua turunannya
sudo diddy cache-flush                           # seluruh cache
```

| Gejala | Solusi |
|---|---|
| Domain sudah dipindah ke IP baru, client masih dapat IP lama | Flush nama itu (*Flush a name*), atau pakai `--tree` untuk seluruh domain |
| Hit ratio rendah dan *Evicted (cache full)* terus naik | Cache terlalu kecil. Naikkan *Max cache size* (mis. `1G` atau `50%`), lalu Deploy |
| Domain yang baru dibuat masih NXDOMAIN | Jawaban negatif masih tersimpan di cache. Flush nama itu, atau turunkan *Max negative cache TTL* |
| Statistik cache tidak tersedia | Statistics-channel BIND belum aktif (Deploy sekali), atau recursion mati |
| Lookup gagal `dig tidak ditemukan` | `sudo apt install bind9-dnsutils` |

### 3.7 Deploy sukses, tapi DNS tidak menjawab

```bash
grep diddy /etc/bind/named.conf.local      # include harus ada
named-checkconf -z                            # validasi semua config + zona
rndc status
journalctl -u named -n 50 --no-pager
dig @127.0.0.1 corp.local SOA
```

| Gejala | Solusi |
|---|---|
| `REFUSED` dari client lain | Default BIND hanya melayani query yang diizinkan. Untuk zona authoritative biasanya tidak masalah, tapi untuk recursion tambahkan `allow-recursion { 10.0.0.0/8; };` di `named.conf.options` |
| `rndc: connect failed` | `rndc-confgen -a`, lalu `systemctl restart named` |
| Zona tidak muncul | Include belum ada di `named.conf.local`. Tambahkan, lalu `rndc reload` |
| Port 53 bentrok (`address in use`) | Matikan DNS lain. Kalau `systemd-resolved` ikut listen di semua IP, set `DNSStubListener=no` di `/etc/systemd/resolved.conf` lalu restart |
| Serial tidak naik | Normal. Serial hanya naik kalau isi zona berubah |

### 3.8 Client tidak dapat IP DHCP

Cek berurutan:

1. Network di IPAM sudah *Serve DHCP* dan punya range. Tanpa range, Kea hanya melayani reservasi.
2. Perubahan sudah di-deploy.
3. `dhcp_interfaces` benar dan interface itu punya IP di subnet yang sama, atau request datang lewat relay.
4. Untuk VLAN lain: router/L3 switch sudah `ip helper-address <IP-Diddy>`, dan network relay-nya terdaftar di IPAM dengan DHCP aktif.
5. Port 67/udp tidak diblok firewall.
6. Tidak ada DHCP server lain di segmen yang sama.

Pantau secara live:

```bash
journalctl -u kea-dhcp4-server -f
tcpdump -ni ens19 port 67 or port 68
```

| Pesan Kea | Artinya |
|---|---|
| `DHCP4_PACKET_NAK_0001` / `no subnet selected` | Request masuk dari subnet yang tidak terdaftar/aktif DHCP di Diddy (cek alamat relay/giaddr) |
| `ALLOC_ENGINE_V4_ALLOC_FAIL` | Pool habis. Perbesar range |
| `DHCPSRV_MYSQL_...` error | Masalah koneksi ke DB lease, lihat 3.7 |

### 3.9 Kea dan database lease MySQL

| Gejala | Solusi |
|---|---|
| Kea gagal start, error MySQL | Tes login: `mysql -h 127.0.0.1 -u kea -p kea`. Samakan password dengan `kea_db_password`, lalu Deploy ulang |
| `schema version mismatch` setelah upgrade Kea | `kea-admin db-upgrade mysql -u kea -p <pass> -n kea` |
| `kea-admin db-init` gagal: `SUPER privilege ... binary logging is enabled` | MySQL 8 tidak mengizinkan user biasa membuat trigger/function saat binlog aktif. Minta DBA menjalankan sekali `SET GLOBAL log_bin_trust_function_creators = 1;` lalu ulangi installer tanpa variabel. Kalau tidak diizinkan DBA, pakai `KEA_LEASE_BACKEND=memfile FORCE_RECONFIG=1` |
| `kea-admin db-init` gagal: `Access denied` | User Kea belum punya hak di database lease: `GRANT ALL PRIVILEGES ON \`<db>\`.* TO '<user>'@'<ip-server-ddi>';` |
| Dashboard: *Kea lease DB unreachable* | Kea tetap melayani (`on-fail: serve-retry-continue`), tapi lease tidak tercatat. Perbaiki MySQL secepatnya |
| Halaman DHCP tidak menampilkan lease | Cek `kea_lease_backend = mysql` dan kredensial `kea_db_*` di config |

Cek lease langsung:

```bash
mysql -u root kea -e "SELECT INET_NTOA(address) ip, HEX(hwaddr) mac, hostname, expire, state FROM lease4 ORDER BY expire DESC LIMIT 20;"
```

### 3.10 Reload Kea gagal lewat control socket

Di laporan deploy muncul `control socket failed ... fallback`. Diddy lalu menjalankan `systemctl restart kea-dhcp4-server`.

- Pastikan `/run/kea/` ada dan Kea berjalan: `ls -l /run/kea/`.
- Pastikan `kea_socket` di config sama dengan `socket-name` di `/etc/kea/kea-dhcp4.conf`.
- Kea 2.7+ hanya menerima socket di dalam `/run/kea/`.

### 3.11 DDNS (client DHCP tidak muncul di DNS)

Cara kerjanya: Diddy membaca lease aktif, lalu menulis blok dinamis di antara penanda `BEGIN DHCP dynamic records` dan `END DHCP dynamic records` di dalam zone file. Bagian statis tidak disentuh, serial dinaikkan, zona di-reload. Default refresh tiap 60 detik.

| Gejala | Solusi |
|---|---|
| Client tidak muncul di DNS | Cek network sudah *Serve DHCP* dan *DDNS* aktif, dan domain-nya punya zona di Diddy. Klik **Refresh DDNS now** di halaman DHCP untuk mencoba langsung |
| Sebagian client tidak muncul | Client tidak mengirim hostname (opsi 12). Lease tanpa hostname dilewati |
| Nama client berubah dari aslinya | Hostname dibersihkan jadi satu label DNS yang valid, misal `iPhone Rina` menjadi `iphone-rina` |
| Nama client tertimpa | Ada host object atau record manual dengan nama yang sama. Record statis selalu menang dan record dinamis dilewati |
| Record dinamis tidak hilang setelah client pergi | Record baru hilang setelah lease expired di Kea, bukan saat perangkat dimatikan |
| Zona tidak pernah berubah | Zona belum pernah di-deploy, atau `ddns_refresh_interval = 0`. Cek `journalctl -u diddy -f` |
| Serial naik terus | Normal untuk zona DDNS. Serial hanya naik saat daftar lease berubah |

Jangan mengaktifkan `allow-update` di BIND untuk zona yang dikelola Diddy. Penulis zona harus satu, yaitu Diddy.

### 3.12 IP map dan ping sweep

| Gejala | Penjelasan |
|---|---|
| *IP map is available for IPv4 networks of /20 or smaller* | Batasan desain. Pecah network besar menjadi child network |
| *Discovery supports ... /22 or smaller* | Batasan desain untuk menjaga waktu scan |
| Ping sweep tidak menemukan apa pun | ICMP diblok firewall/ACL, atau container LXC unprivileged tidak boleh raw socket. Tes `ping <ip>` dari server |
| Titik merah di sel IP map | Konflik: fixed address di dalam range DHCP, MAC lease beda dengan MAC host, atau lease di luar range |

### 3.13 Error saat input data

| Pesan | Solusi |
|---|---|
| `No DNS zone found for ...` | Buat zona forward dulu, atau matikan *Create DNS records* pada host |
| `DHCP-enabled networks cannot overlap` | Hanya satu level network yang boleh DHCP aktif (misal /24, bukan /16 induknya) |
| `... is not inside a DHCP-enabled network` | Reservasi DHCP butuh network dengan DHCP aktif |
| `Turn on DHCP first` saat aktifkan DDNS | DDNS hanya untuk network yang lease-nya dilayani server ini |
| `No DNS zone covers '<domain>'` | Buat zona untuk domain DDNS tersebut lebih dulu |
| `MAC ... is already reserved` | Satu MAC hanya boleh satu reservasi per subnet |
| `CNAME ... must be the only record` | Aturan DNS: nama CNAME tidak boleh punya record lain |
| `Read-only account` | Login dengan user admin |

### 3.14 Mengembalikan kondisi darurat

Kalau hasil deploy mengganggu layanan:

```bash
# kembalikan config Kea sebelum deploy terakhir
cp /etc/kea/kea-dhcp4.conf.diddy.bak /etc/kea/kea-dhcp4.conf
systemctl restart kea-dhcp4-server

# atau kembalikan config Kea bawaan paket
cp /etc/kea/kea-dhcp4.conf.orig /etc/kea/kea-dhcp4.conf
systemctl restart kea-dhcp4-server

# nonaktifkan semua zona Diddy di BIND sementara
sed -i '\#diddy/named.conf.diddy#s#^#//#' /etc/bind/named.conf.local
rndc reload
```

Setelah masalah diperbaiki di UI, hapus tanda `//` pada include lalu Deploy ulang.

### 3.15 Data yang dilampirkan saat minta bantuan

```bash
cp /var/log/diddy-install.log installer.log
journalctl -u diddy -n 200 --no-pager > diddy.log
journalctl -u kea-dhcp4-server -n 200 --no-pager > kea.log
journalctl -u named -n 200 --no-pager > named.log
named-checkconf -z > bind-check.txt 2>&1
kea-dhcp4 -t /etc/kea/kea-dhcp4.conf > kea-check.txt 2>&1
```

Hapus password dari `diddy.conf` dan `kea-dhcp4.conf` sebelum membagikannya.

---

## Lampiran: referensi `diddy.conf`

| Parameter | Default | Keterangan |
|---|---|---|
| `listen` / `port` | `0.0.0.0` / `8080` | Alamat web UI |
| `data_dir` | `/var/lib/diddy` | Secret key dan password awal |
| `dry_run` | `false` | `true` = tulis file tanpa reload service |
| `default_ns` / `default_admin_email` | | Default SOA zona baru |
| `db_backend` | `mysql` | `mysql` atau `sqlite` |
| `mysql_host/port/user/password/database` | | Koneksi DB Diddy |
| `table_prefix` | kosong | Prefix tabel Diddy. Jangan diubah setelah ada data, kecuali tabel ikut di-rename |
| `ddns_ttl` | `60` | TTL record DDNS dari lease DHCP |
| `ddns_refresh_interval` | `60` | Interval refresh zona DDNS dalam detik, `0` = matikan |
| `kea_lease_backend` | `mysql` | `mysql` atau `memfile` |
| `kea_db_host/port/name/user/password` | | Koneksi DB lease Kea |
| `kea_lease_file` | `/var/lib/kea/kea-leases4.csv` | Hanya untuk `memfile` |
| `bind_dir` | `/etc/bind/diddy` | Lokasi output BIND |
| `dns_reload_cmd` | `rndc reload` | Perintah reload DNS |
| `dns_service` / `dhcp_service` | `named` / `kea-dhcp4-server` | Nama service untuk status di dashboard |
| `kea_conf` | `/etc/kea/kea-dhcp4.conf` | File config Kea yang ditulis |
| `kea_socket` | `/run/kea/kea4-ctrl-socket` | Control socket Kea |
| `kea_reload_fallback_cmd` | `systemctl restart kea-dhcp4-server` | Dipakai bila socket gagal |
| `dhcp_interfaces` | `*` | Interface DHCP, pisahkan dengan koma |

Setiap perubahan config butuh `systemctl restart diddy`.
