# Diddy REST API

Base URL `http://<server>:8080/api/v1`. Semua body berformat JSON. Dokumen ini untuk Diddy 2.2.4.

## Autentikasi

Pakai **HTTP Basic** di setiap request. Tidak perlu login atau token.

```bash
curl -u admin:PASSWORD http://ddi01:8080/api/v1/me
```

| Role | Hak |
|---|---|
| `admin` | Semua method |
| `readonly` | Hanya `GET`; method lain dijawab `403` |

Verifikasi password disimpan sementara (5 menit) di memori, jadi hanya request pertama yang lambat. Ganti password atau hapus user langsung berlaku.

## Format respons

| Status | Arti |
|---|---|
| `200` | Berhasil |
| `201` | Objek dibuat, respons berisi objek termasuk `id` |
| `400` | Input tidak valid; `{"error": "pesan"}` menjelaskan penyebabnya |
| `401` | Username atau password salah |
| `403` | User read-only mencoba mengubah data |
| `404` | Objek tidak ada |
| `409` | Bentrok dengan data yang sudah ada (IP, network, zona, forwarder duplikat) |
| `422` | Deploy berhenti karena validasi BIND/Kea/dnsdist gagal; tidak ada file yang diubah |

Semua perubahan data hanya tersimpan di database dan menyalakan tanda *pending*. Service baru berubah setelah `POST /deploy`.

## Endpoint

| Method | Path | Fungsi |
|---|---|---|
| GET | `/me` | User saat ini, versi, status pending |
| POST | `/login`, `/logout` | Sesi browser (klien API tidak perlu) |
| GET | `/health` | Status service, database, drift, DDNS, disk |
| GET | `/system` | Versi software, OS, path config |
| GET | `/dashboard` | Ringkasan dan utilisasi network |
| GET | `/search?q=` | Cari IP, nama, MAC, network |
| GET | `/audit?limit=` | Audit log |
| GET | `/metrics/dns?range=1h\|6h\|24h\|7d` | Statistik DNS: query/detik, request per jam, total, rcode, qtype, utilisasi |
| GET | `/metrics/dhcp?range=` | Statistik DHCP: lease aktif, pool per network, paket Kea |
| GET, PUT, DELETE | `/dashboard/layout?board=overview\|dns\|dhcp` | Layout widget milik user yang login (read-only juga boleh menyimpan) |
| GET, POST | `/networks` | Daftar / buat network |
| GET, PUT, DELETE | `/networks/{id}` | Detail / ubah / hapus network |
| GET | `/networks/{id}/ipmap` | Status setiap IP (IPv4, maks /20) |
| GET | `/networks/{id}/next_available?num=` | IP bebas berikutnya |
| POST | `/networks/{id}/discover` | Ping sweep (maks /22) |
| GET, POST | `/ranges?network_id=` | Daftar / buat DHCP range |
| PUT, DELETE | `/ranges/{id}` | Ubah / hapus DHCP range |
| GET | `/leases` | Lease DHCP aktif |
| GET, POST | `/hosts` | Daftar / buat host (A/AAAA + PTR + reservasi DHCP) |
| GET, PUT, DELETE | `/hosts/{id}` | Detail / ubah / hapus host |
| POST | `/import/hosts` | Import host dari CSV |
| GET, POST | `/zones` | Daftar / buat zona |
| GET, PUT, DELETE | `/zones/{id}` | Detail / ubah / hapus zona |
| GET | `/zones/{id}/records` | Semua record: manual, dari host, dan dari DDNS |
| POST | `/records` | Buat record |
| PUT, DELETE | `/records/{id}` | Ubah / hapus record |
| GET, PUT | `/dns-settings` | Setting resolver, forwarder, upstream terenkripsi |
| GET, POST | `/forwarders` | Daftar / buat conditional forwarder |
| PUT, DELETE | `/forwarders/{id}` | Ubah / hapus conditional forwarder |
| GET | `/dns-cache?range=1h\|6h\|24h\|7d` | Cache DNS BIND: statistik saat ini, hit ratio historis, pengaturan cache |
| PUT | `/dns-cache/settings` | Ubah `max_cache_size` (`512M`, `2G`, `50%`, `unlimited`, kosong = default), `max_cache_ttl`, `max_ncache_ttl` (detik, `null` = default). Berlaku setelah deploy |
| POST | `/dns-cache/flush` | Hapus cache sekarang: body `{}` = semua, `{"name": "example.com"}` = satu nama, tambah `"tree": true` untuk nama beserta turunannya |
| GET | `/dns-cache/lookup?name=&type=` | Isi cache untuk satu nama tanpa resolusi baru (butuh `dig`); IP otomatis jadi lookup PTR |
| POST | `/ddns/refresh` | Refresh DDNS sekarang |
| GET | `/deploy/preview` | Isi file yang akan ditulis |
| POST | `/deploy` | Validasi dan terapkan ke service |
| GET | `/drift` | File service yang berubah di luar Diddy |
| POST | `/drift/repair` | Kembalikan file yang drift |
| GET, POST | `/users` | Daftar / buat user |
| PUT, DELETE | `/users/{id}` | Ganti password atau role / hapus user |
| GET | `/export/{networks,hosts,records,leases}.csv` | Export CSV |

## Contoh

```bash
S=http://ddi01:8080/api/v1; A="-u admin:PASSWORD"; J="-H Content-Type:application/json"

# host baru di IP bebas berikutnya + reservasi DHCP
curl $A $J -d '{"fqdn":"srv01.corp.local","ip":"next:10.10.1.0/24","mac":"aa:bb:cc:dd:ee:ff","configure_dhcp":true}' $S/hosts

# 5 IP bebas
curl $A "$S/networks/2/next_available?num=5"

# record CNAME
curl $A $J -d '{"zone_id":1,"name":"www","type":"CNAME","value":"srv01.corp.local"}' $S/records

# terapkan ke BIND dan Kea
curl $A -X POST $S/deploy

# cache DNS: lihat isi cache untuk satu nama, lalu hapus nama itu beserta turunannya
curl $A "$S/dns-cache/lookup?name=www.example.com&type=A"
curl $A $J -d '{"name":"example.com","tree":true}' $S/dns-cache/flush
```

Nilai `ip` pada host bisa berupa IP biasa, `next:<cidr>`, `next:<id-network>`, atau `func:nextavailableip:<cidr>`.

Format `value` per tipe record:

| Tipe | Contoh `value` |
|---|---|
| A / AAAA | `10.10.1.30` / `2001:db8::30` |
| CNAME, NS, PTR | `target.corp.local` |
| MX | `10 mail.corp.local` |
| SRV | `0 100 389 dc01.corp.local` |
| TXT | `v=spf1 mx -all` |

## Menguji API

**Script otomatis**, hanya butuh Python 3, jalankan dari server atau laptop mana pun yang bisa menjangkau Diddy:

```bash
python3 tools/api_test.py --url http://ddi01:8080 -u admin -p 'PASSWORD'           # baca saja, aman
python3 tools/api_test.py --url http://ddi01:8080 -u admin -p 'PASSWORD' --write   # + buat/ubah/hapus
```

Mode `--write` memakai network `198.18.X.0/24` dan zona `apitest-X.invalid` (rentang dan domain khusus uji yang tidak mungkin dipakai di produksi), lalu menghapus semuanya. Deploy tidak pernah dipanggil, jadi BIND dan Kea tidak tersentuh. Banner *pending changes* akan muncul setelahnya walau isi database sudah kembali seperti semula.

**Postman / Insomnia / Bruno**: import `docs/Diddy.postman_collection.json`, isi variabel collection `baseUrl`, `username`, `password`, lalu jalankan Collection Runner. Folder 0 sampai 9 membuat, menguji, dan menghapus objek uji. Folder **Z** berisi operasi yang mengubah service (deploy, drift repair, ping sweep, ubah resolver), sengaja dipisah untuk dijalankan manual.

Dari command line dengan newman:

```bash
npm install -g newman
newman run docs/Diddy.postman_collection.json --env-var baseUrl=http://ddi01:8080 \
  --env-var username=admin --env-var password='PASSWORD' \
  --folder "0. Autentikasi" --folder "1. Sistem" --folder "2. IPAM" --folder "3. DHCP" --folder "4. DNS" \
  --folder "5. Hosts" --folder "6. Resolver" --folder "7. Deploy dan drift (baca saja)" \
  --folder "8. Users dan export" --folder "9. Bersih-bersih"
```
