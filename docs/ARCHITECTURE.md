# Arsitektur Diddy

*Your DDI Friend*

(c) 2026 kripermana, lisensi MIT. Dokumen ini untuk Diddy 2.1.0.

Diddy adalah package Python `diddy/` yang mengelola BIND9 (DNS), ISC Kea (DHCPv4), dan dnsdist (upstream DoT/DoH) dari satu database MySQL atau SQLite. Database adalah sumber kebenaran; file config service selalu dihasilkan dari database, divalidasi, lalu baru ditulis.

## Peta modul

| Modul | Tanggung jawab |
|---|---|
| `__main__.py` | CLI: `serve`, `deploy`, `drift`, `reset-password`, `migrate-sqlite`, `version` |
| `web.py` | Flask app factory: registrasi blueprint, error handler, header keamanan, UI statis |
| `wsgi.py` | Objek `app` untuk server WSGI lain (`waitress-serve diddy.wsgi:app`) |
| `worker.py` | Thread latar: refresh DDNS dan pemeriksaan drift, jadwal masing-masing |
| `config.py` | Nilai default dan pembacaan `/etc/diddy/diddy.conf` |
| `version.py` | `VERSION`, `AUTHOR` |
| `core/errors.py` | `ApiError`, diterjemahkan jadi respons JSON |
| `core/util.py` | Validasi input (FQDN, IP, MAC), `run()` untuk perintah sistem, util waktu |
| `core/log.py` | Logger `diddy`, aman dipakai dari thread latar |
| `core/runtime.py` | State bersama: waktu start, hasil DDNS dan drift terakhir |
| `db/connection.py` | Koneksi MySQL/SQLite, `q()` baca, `x()` tulis, `state_get/set` |
| `db/tables.py` | Daftar tabel dan prefix tabel |
| `db/schema.py` | DDL, penambahan kolom otomatis, inisialisasi, migrasi SQLite ke MySQL |
| `auth.py` | Login sesi dan HTTP Basic, decorator `@auth`, role admin/read-only |
| `audit.py` | `audit()` dan `changed()` (audit log + tanda perubahan tertunda) |
| `users.py` | Endpoint manajemen user |
| `ipam/networks.py` | Network bertingkat, utilisasi, IP bebas, reverse zone otomatis |
| `ipam/routes.py` | Endpoint network, IP map, next available, ping sweep |
| `dhcp/leases.py` | Membaca lease aktif dari Kea (MySQL `lease4` atau CSV) |
| `dhcp/ranges.py` | Validasi DHCP range |
| `dhcp/kea.py` | Render `kea-dhcp4.conf`, perintah ke control socket Kea |
| `dhcp/routes.py` | Endpoint range dan lease |
| `dns/zones.py` | Zona, record, validasi, render zone file dan `named.conf.diddy` |
| `dns/ddns.py` | Record A/PTR dari lease, disisipkan ke blok dinamis zone file |
| `dns/resolver.py` | Recursion, ACL, forwarder, conditional forwarder, config dnsdist |
| `dns/routes.py` | Endpoint zona, record, resolver, forwarder, refresh DDNS |
| `hosts.py` | Host object: DNS + PTR + reservasi DHCP dalam satu objek, import CSV |
| `deploy/drift.py` | Salinan file yang ditulis Diddy, deteksi dan pemulihan drift |
| `deploy/pipeline.py` | `run_deploy()`: render, validasi, tulis, reload |
| `deploy/routes.py` | Endpoint deploy, preview, drift |
| `metrics.py` | Collector statistik BIND/Kea per menit, pengolahan counter menjadi grafik |
| `dashboards.py` | Endpoint statistik dan layout dashboard per user |
| `system.py` | Dashboard, health, system information, pencarian, export, audit log |
| `static/` | Web UI (HTML, CSS, JavaScript tanpa build step); `charts.js` berisi grafik SVG tanpa dependency |

## Lapisan dan aturan import

Import hanya boleh mengarah ke lapisan yang sama atau di bawahnya. Aturan ini yang mencegah circular import.

```
5  Entry point       __main__  web  wsgi  worker
4  HTTP              ipam.routes  dhcp.routes  dns.routes  deploy.routes  hosts  users  system
3  Logika domain     dhcp.leases -> dns.zones -> ipam.networks -> dhcp.kea / dns.resolver
                     deploy.drift -> dns.ddns -> deploy.pipeline
2  Layanan bersama   audit  auth
1  Data              db.tables  db.connection  db.schema
0  Dasar             version  config  core.*
```

Tiga aturan yang perlu dijaga:

1. Modul `routes` hanya berisi endpoint. Logika bisnis ada di modul domain, supaya bisa dipanggil dari CLI dan thread latar tanpa HTTP.
2. Modul domain tidak memakai `request` atau `session`. Satu-satunya ketergantungan Flask di sana adalah `g.user` lewat `audit()`, yang tersedia di app context mana pun.
3. Konstanta format file yang dipakai lebih dari satu modul ditaruh di modul pemilik formatnya. Contoh: penanda blok DDNS ada di `dns/zones.py`, bukan di `dns/ddns.py`.

## Alur deploy

```
database ──render──> zone file, named.conf.diddy, options, kea-dhcp4.conf, dnsdist.conf
                      │
                      ├─ staging di /etc/bind/diddy/.staging dan /etc/kea (lolos AppArmor)
                      ├─ named-checkzone, named-checkconf, kea-dhcp4 -t, dnsdist --check-config
                      │     gagal satu = berhenti, tidak ada file live yang disentuh
                      ├─ tulis file live + simpan salinan untuk deteksi drift
                      └─ reload: dnsdist lebih dulu, lalu BIND, lalu Kea (control socket, fallback restart)
```

Refresh DDNS memakai jalur terpisah: hanya blok dinamis di zone file yang ditulis ulang, sehingga perubahan yang masih tertunda di UI tidak ikut terdorong. Refresh juga menolak menulis ke zone file yang sedang drift.

## Menambah fitur baru

Contoh menambah fitur "DHCP option 150 untuk IP phone":

1. Kolom baru: tambahkan ke DDL di `db/schema.py` untuk SQLite dan MySQL, lalu ke `ensure_columns()` supaya instalasi lama ikut mendapat kolomnya.
2. Validasi dan logika: di modul domainnya, di sini `ipam/networks.py` (`validate_network`) dan `dhcp/kea.py` (`render_kea`).
3. Endpoint: bila butuh endpoint baru, tambahkan ke `ipam/routes.py` atau buat modul routes baru dengan `Blueprint`, lalu daftarkan di `web.py` pada `BLUEPRINTS`.
4. UI: `static/app.js`.
5. Test: tambahkan skenario di `tests/smoke_test.py`, lalu jalankan `python3 tests/smoke_test.py`.

## Pengujian

```bash
python3 tests/smoke_test.py                 # SQLite, dry run, direktori sementara
python3 tests/smoke_test.py -v              # tampilkan setiap langkah
TEST_MYSQL="127.0.0.1:user:pass:db:ddi_" python3 tests/smoke_test.py
python3 -m pyflakes diddy                 # cek import dan nama yang tidak terdefinisi
```

Test memakai direktori sementara dan `dry_run = true`, jadi aman dijalankan di server produksi. Bila `named-checkzone`, `kea-dhcp4`, atau `dnsdist` terpasang, validasi aslinya ikut dijalankan.
