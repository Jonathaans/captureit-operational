# Capture It Ops — QA sebelum VPS

Tanggal: 30 September 2026. Kandidat rilis: QA pra-VPS, lanjutan paket jadwal/draft.

**Arsip hasil QA versi sebelumnya.** Rilis 1 Oktober menambahkan histori gaji dan peringatan jadwal; rilis 5 Oktober 2026 menambahkan pusat notifikasi tersimpan, brief desain, dan integrasi push. Hasil terbaru: **113 tes backend + 55 tes JavaScript lulus**, dengan FCM disimulasikan. Lihat `UPDATE-NOTIFIKASI-PUSH.md` dan `UPDATE-HISTORI-GAJI-JADWAL.md`. Aturan loading/jeda dan penolakan bentrok tim di dokumen lama ini tidak lagi berlaku. Penghalang go-live dan checklist VPS/perangkat nyata tetap berlaku; push Firebase nyata juga harus diuji di staging.

## Keputusan rilis

**GO untuk staging terbatas. NO-GO untuk go-live publik saat ini.**

75 tes backend dan 31 tes JavaScript lulus, tanpa tes dilewati. Temuan pada sesi,
otorisasi, validasi nominal, export CSV, dan tanggal WIB sudah diperbaiki lalu
diuji ulang. Ini bukti terhadap skenario yang dijalankan, bukan jaminan aplikasi
bebas bug atau sertifikasi keamanan.

Penghalang go-live: lapisan HTTP masih menggunakan `http.server` bawaan Python,
belum ada bukti deployment HTTPS/rate-limit di VPS, serta belum ada UAT pada
browser/HP nyata. Jangan menganggap reverse proxy saja menyelesaikan seluruh
risiko server produksi. Penggantian lapisan server produksi perlu pekerjaan dan
pengujian tersendiri; arsitektur aplikasi tidak dirombak dalam patch QA ini.

## Lingkungan dan metode

- Database SQLite sementara dengan data sintetis; tidak mengubah database operasional pengguna.
- Tes backend melalui HTTP lokal nyata dan tes fungsi/database; Python 3.12.
- Tes JavaScript memakai Node, VM dan DOM tiruan. Data render berasal dari API lokal nyata untuk 14 role.
- Seluruh tes JavaScript dijalankan dengan zona sistem UTC. 17 tes render/tanggal diulang dengan zona America/Los_Angeles dan lulus.
- Tidak ada browser terpasang yang dapat dijalankan. Unduhan Chromium gagal, sehingga tidak ada klaim uji visual, klik browser end-to-end, kamera, atau GPS perangkat nyata.
- Tidak ada deployment, pengiriman WhatsApp, koneksi Google Calendar live, maupun transfer uang sungguhan.

## Ringkasan hasil yang dijalankan

| Kelompok pengujian | Jumlah tes | Hasil | Cakupan utama |
|---|---:|---|---|
| `tests/test_workflows.py` | 20 | Lulus | Role, profil/KTP, Calendar mock, uang jalan, gudang, desain, absensi, fee, KPI, payroll dua jenis |
| `tests/test_operations_features.py` | 11 | Lulus | Notifikasi, transportasi, konflik kendaraan, export template, logout |
| `tests/test_closing_reports.py` | 12 | Lulus | Bahan opsional, laporan/revisi/review/clear, foto privat, versi dan export |
| `tests/test_staffing.py` | 11 | Lulus | Bentrok penugasan, loading, jeda perjalanan, jadwal lintas hari, konkurensi dan migrasi |
| `tests/test_full_qa.py` | 21 | Lulus | Batas input, sesi/role, privasi, CSV, WIB, backup, pembacaan paralel dan alur utuh |
| `tests/test_drafts.cjs` | 14 | Lulus | Draft teks/foto, reload/login, akun terpisah, bentrok tab/versi, kegagalan penyimpanan, logout |
| `tests/test_frontend.cjs` | 17 | Lulus | Render halaman/tab untuk 14 role, kalender WIB, escaping nama event dan notifikasi |
| **Total tes unik** | **106** | **Lulus** | **75 Python + 31 JavaScript; bukan persentase code coverage** |

Pemeriksaan tambahan di luar hitungan 106:

- Kompilasi semua modul Python dan pemeriksaan sintaks seluruh JS/CJS: lulus.
- Upgrade database sintetis dari paket sebelumnya, lalu initialization dua kali: seluruh isi 45 tabel tetap sama; pemeriksaan integritas dan foreign key bersih.
- Bootstrap konfigurasi non-demo: tepat satu administrator, tanpa event, akun demo, atau sesi demo.
- Compose YAML dapat dibaca; port aplikasi terikat loopback, volume `/data` persisten, Dockerfile memakai user non-root. Ini pemeriksaan statis, bukan hasil build Docker.
- Backup/restore lokal: SQLite backup dan salinan file profil privat, kemudian integritas/relasi/sesi/akses file diperiksa. Tidak mewakili restore VPS penuh atau backup saat ada penulisan aktif.
- 30 request bootstrap dengan 6 worker berhasil. Ini smoke test konkurensi ringan, bukan load/stress test kapasitas produksi.

## Temuan dan perbaikan

Prioritas berikut adalah penilaian QA internal, bukan skor CVSS.

| ID | Prioritas | Temuan | Perbaikan dan bukti |
|---|---|---|---|
| QA-01 | Tinggi | Reset password tidak membatalkan sesi lama; sesi akun yang dinonaktifkan dapat hidup kembali setelah reaktivasi | Reset/deaktivasi menghapus sesi; tes login lama ditolak setelah kedua alur |
| QA-02 | Tinggi | Perubahan role dapat meninggalkan klasifikasi Freelancer/In-house dan akses laporan lama yang tidak konsisten | Klasifikasi/departemen diperbarui langsung, akses baca event/laporan/foto mengikuti izin terkini; tes pergantian role dan PIC lama |
| QA-03 | Tinggi | Beberapa nominal menerima boolean/pecahan atau angka terlalu besar; input payroll yang salah dapat tidak ditolak dengan rapi | Parser rupiah bilangan bulat dengan batas, validasi daftar dan akun aktif In-house, transaksi atomik; tes negatif lintas endpoint |
| QA-04 | Sedang | Nama/event yang diawali rumus dapat masuk CSV sebagai formula spreadsheet | Nilai CSV dinetralkan menjadi teks; diuji pada export payroll dan absensi In-house |
| QA-05 | Sedang | Form lintas situs berjenis `text/plain` dapat memanggil login | POST JSON diwajibkan; permintaan sederhana lintas situs ditolak 415, CSRF pada aksi terautentikasi tetap diuji |
| QA-06 | Sedang | Bootstrap administrator dan jalur CLI belum konsisten dengan aturan password/klasifikasi | Password baru 12–1024 karakter, validasi email, klasifikasi akun CLI langsung benar; akun demo lokal tetap untuk demo saja |
| QA-07 | Sedang | Crew dapat menandai diri tidak hadir lewat API tanpa hak pengelola | Aksi `absent` wajib `attendance.manage`; check-in/out sendiri tetap sesuai izin |
| QA-08 | Sedang | Tanggal dashboard, payroll dan filter bulan dapat mengikuti UTC/zona perangkat, bukan WIB | Backend dan UI memakai WIB; diuji lintas tengah malam, pergantian bulan/tahun dan awal minggu payroll |
| QA-09 | Sedang | Proteksi embedding halaman belum eksplisit | Ditambah X-Frame-Options DENY dan CSP dasar untuk frame/base/object; tidak diklaim sebagai CSP anti-XSS lengkap |
| QA-10 | Rendah | Respons login dikirim sebelum transaksi sesi selesai commit | Commit sesi sebelum respons sukses, serta pembatasan panjang input login; diuji keberadaan sesi segera setelah respons |

Pengujian tambahan sebelum perbaikan memang menemukan kegagalan. Semua skenario
regresi tersebut lulus pada hasil akhir; kegagalan setup fixture selama penulisan
tes tidak dihitung sebagai bug aplikasi.

## Alur bisnis yang diverifikasi

Satu tes integrasi berurutan menjalankan Calendar intake sintetis → kode CRM →
tarif akun → penugasan PIC → Luxio + motor + Lalamove → status grup WhatsApp →
uang jalan → gudang siap/berangkat → check-in/check-out dengan payload foto/GPS
sintetis → laporan penutupan → diterima → alat kembali → event clear → evaluasi
100% → Excel event → export payroll → pencatatan transfer → slip pribadi.
Pencatatan transfer kedua ditolak. Foto/GPS dalam tes ini tidak berasal dari
perangkat atau kamera nyata.

Aturan yang tetap dipertahankan:

- Ribbon, frame, lensa lenticular, magnet dan keychain semuanya opsional; pemakaian dicatat hanya di laporan penutupan. Nilai kosong berbeda dengan nol.
- Mengisi nama kendaraan membuat/memakai kendaraan dalam satu penyimpanan. Motor masing-masing, Lalamove/kurir, dan transportasi lain dapat dikombinasikan.
- Checklist barang per item tetap di CRM; aplikasi hanya mencatat status kesiapan/pengembalian event.
- Koordinasi WhatsApp tetap melalui link/template dan status yang dicatat manusia; tidak ada bot pengiriman otomatis.
- Notifikasi bell adalah notifikasi di aplikasi, diperbarui berkala; bukan push notification Android.
- Export event mempertahankan 21 kolom template di paket, format tanggal/jam dan angka, batas data coordinator, serta data penutupan yang sudah diterima. Pembukaan visual di Microsoft Excel/LibreOffice belum dilakukan pada putaran QA ini.
- Draft penutupan tersimpan di perangkat/peramban yang sama, terpisah per akun/workspace/event. Bukan sinkronisasi lintas HP dan bukan jaminan aplikasi bisa dimuat sepenuhnya tanpa internet.
- Payroll mencatat konfirmasi pembayaran, tidak mengirim uang ke bank. Export payroll mengunci snapshot dan klaim penugasan; salah export perlu rekonsiliasi Finance, tidak dibuka otomatis.

## Pengujian yang belum dijalankan / batas rilis

| Area | Status | Yang harus dibuktikan sebelum go-live |
|---|---|---|
| Server HTTP produksi | Belum ditangani | Migrasi serving layer ke server aplikasi produksi, kemudian ulang QA |
| VPS/domain/HTTPS | Belum diuji | DNS, sertifikat valid, redirect HTTPS, secure cookie, firewall dan akses privat |
| Nginx/Docker | Contoh + inspeksi statis saja | Build/start/restart, permission volume, `nginx -t`, rate-limit login dan proxy upload |
| Browser desktop/Android | Belum diuji | Klik seluruh menu yang relevan, responsive layout, dialog logout, navigasi dan aksesibilitas dasar |
| Kamera/GPS | Belum diuji nyata | Izin diberikan/ditolak, akurasi buruk, timeout, perangkat tanpa kamera, upload melalui HTTPS |
| Google Calendar live | Mock saja | Token valid, calendar benar, sinkronisasi manual/berkala, token kedaluwarsa dan perubahan jadwal |
| Excel nyata | Struktur OOXML/CSV lulus | Buka file di Excel/LibreOffice, pastikan tidak ada peringatan repair dan tampilan sesuai |
| Backup VPS penuh | Lokal terbatas lulus | Restore database + seluruh direktori privat/branding dari snapshot yang konsisten |
| Beban, penetrasi, pemindaian malware | Tidak dilakukan | Uji sesuai perkiraan volume dan review keamanan deployment; validasi ekstensi/signature bukan antivirus |
| Shift In-house lewat tengah malam | Batas perilaku saat ini | Absensi berbasis tanggal WIB; jika shift lintas hari diperlukan, definisikan alurnya sebelum digunakan |
| APK Android/PWA/offline penuh | Di luar tugas QA | Belum dibuat atau diuji dalam rilis ini |

## Checklist UAT di staging

Gunakan akun dan data uji, bukan identitas/KTP asli. Catat penguji, perangkat,
tanggal, hasil, dan screenshot bila gagal. Kotak di bawah belum dinyatakan lulus.

- [ ] Admin: login, buat akun setiap role yang dipakai, ubah role, nonaktifkan/aktifkan, reset password, pastikan sesi lama ditolak.
- [ ] Admin terakhir tidak dapat dihapus perannya/dinonaktifkan sehingga semua akses pemulihan hilang.
- [ ] Crew/PIC tidak dapat membuka event lain, KTP orang lain, foto/dokumen privat yang bukan haknya, atau halaman Configure.
- [ ] Sinkronkan satu kalender uji; jadwalkan event berkode sementara lalu pasangkan kode CRM, ulang sync dan cek tidak duplikat.
- [ ] Buat bentrok crew/PIC dan kendaraan; pastikan save ditolak tanpa perubahan sebagian. Coba jeda perjalanan singkat dan konfirmasinya.
- [ ] Ketik `Luxio` lalu simpan satu kali; muat ulang. Ulangi dengan motor masing-masing + Lalamove tanpa kendaraan operasional.
- [ ] Buka notifikasi, tandai dibaca, muat ulang/login ulang; jumlah belum dibaca konsisten dan event terbuka sesuai hak akses.
- [ ] Buat/buka link grup WhatsApp di HP. Pastikan membuka link tidak dikira bukti pesan sudah terkirim.
- [ ] Jalankan uang jalan, unggah dokumen uji, unduh sebagai Finance; role lain tidak dapat membuka URL unduh langsung.
- [ ] Jalankan status gudang dan board desain. Periksa drag-drop desktop serta alternatif tombol di HP.
- [ ] Check-in/out dengan kamera dan GPS nyata; coba tolak izin, GPS tidak akurat, gagal jaringan, dan klik kirim berulang.
- [ ] PIC kirim laporan dengan semua bahan kosong; ulangi dengan nilai nol dan sebagian bahan terisi. Ribbon tidak muncul sebagai input operasional lain.
- [ ] Simpan draft dengan foto, refresh/login ulang, buka dua tab, simulasikan storage penuh dan jaringan putus; input tidak hilang diam-diam.
- [ ] Coordinator minta revisi, PIC kirim ulang, coordinator terima dan clear; laporan yang terkunci tidak dapat diedit.
- [ ] Dialog logout dapat dibatalkan/disetujui, tidak kehilangan draft yang belum aman, dan tombol Back tidak membuka data server setelah logout.
- [ ] Export event, payroll dan absensi; buka di Excel, cek 21 kolom event, filter coordinator, tanggal WIB, angka/nol/kosong dan total.
- [ ] Finance verifikasi fee/skill/uang makan dan payroll In-house; slip hanya terbit setelah transfer dicatat, pembayaran ulang ditolak.
- [ ] Uji lebar HP kecil dan besar: tidak ada tombol terpotong, overlap modal, scroll horizontal tak sengaja, atau keyboard menutup tombol simpan.
- [ ] Restart service/container; login, event, dokumen dan payroll masih tersedia. Restore backup pada staging terpisah dan ulang smoke test.
- [ ] Periksa respons login berlebihan menghasilkan 429 di proxy, port 8000 tidak publik, HTTP dialihkan ke HTTPS, cookie memiliki Secure/HttpOnly sesuai jenisnya.

## Persiapan VPS dan rollback

1. Gunakan staging/domain terpisah, akses terbatas, satu instance aplikasi. Jangan unggah database demo sebagai database operasional.
2. Instalasi baru memakai `.env.production.example`; isi admin unik dan password kuat. Upgrade mempertahankan `.env`, database, serta folder privat yang sudah ada. Jangan menimpa `.env` produksi dengan file contoh.
3. `DEMO_MODE=false` tidak menghapus akun demo yang terlanjur dibuat. Gunakan DB baru untuk instalasi baru; jika migrasi, inventarisasi akun dan nonaktifkan akun demo melalui admin sebelum penggunaan nyata.
4. Siapkan HTTPS, kebijakan akses/firewall dan proxy. Sesuaikan `deploy/nginx.conf.example`; contoh itu belum diuji di Nginx nyata. Verifikasi `nginx -t` sebelum reload. Jangan membuka port 8000 ke internet.
5. Periksa `docker compose config`, lalu build/start di staging. Tidak ada hasil build Docker dari lingkungan QA ini. `COOKIE_SECURE=1` berarti login melalui HTTP biasa tidak akan berfungsi sebagaimana mestinya.
6. Setelah bootstrap berhasil, hapus nilai password bootstrap dari `.env` dan jalankan `docker compose up -d --force-recreate` untuk menghilangkannya dari environment container. Jangan membagikan hasil `docker compose config` yang berisi secret.
7. Mulai dengan auto-sync Calendar nonaktif; tes manual kalender yang benar sebelum mengaktifkan interval. Pastikan waktu VPS tersinkronisasi.
8. Untuk upgrade: hentikan aplikasi agar tidak ada penulisan; backup konsisten SQLite beserta WAL/SHM bila masih ada, `attendance-photos`, `private-profile-uploads`, `cash-advance-documents`, `branding-assets`, dan direktori khusus lain yang dikonfigurasi. Foto penutupan berada di database. Lindungi juga salinan konfigurasi yang mengandung secret. Jangan hanya menyalin file `.sqlite3` ketika service masih menulis.
9. Simpan kode lama dan backup sebelum mengganti kode. Jalankan kandidat rilis dengan database salinan di staging dahulu; initialization melakukan migrasi otomatis. Tidak perlu menjalankan reset data.
10. Jika upgrade gagal: hentikan service, pulihkan kode lama bersama snapshot data/file yang cocok. Simpan salinan keadaan gagal untuk investigasi. Jangan menghapus volume Docker; jangan menjalankan `reset_operational_data.py` atau `docker compose down -v` sebagai cara upgrade/rollback.
11. Go-live hanya setelah penghalang produksi diselesaikan dan checklist UAT ditandatangani penanggung jawab operasional/Finance.

## Menjalankan ulang pengujian

Dari folder `captureit-ops`, dengan Python 3.12 dan Node modern terpasang:

```bash
python3 -m unittest discover -s tests -v
node --test --test-reporter=tap tests/test_drafts.cjs tests/test_frontend.cjs
python3 -m py_compile server.py operations.py closing_reports.py staffing.py reset_operational_data.py
```

Tes menggunakan database sementara; tidak memerlukan Google/WhatsApp live.
`frontend_fixtures.py` membuat data HTTP sintetis untuk tes Node. Tes Node dapat
diulang dengan `TZ=UTC` atau `TZ=America/Los_Angeles` pada Linux/macOS untuk
memastikan periode WIB tidak mengikuti zona perangkat. Tidak ada perubahan
dependency aplikasi runtime; suite Python memakai standard library.

## Acuan deployment

- [Dokumentasi resmi Python: http.server](https://docs.python.org/3/library/http.server.html) — modul ini tidak direkomendasikan untuk produksi.
- [Dokumentasi resmi Nginx: limit_req](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html) — dasar contoh pembatasan request login.
- [MDN: getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) — izin pengguna dan secure context untuk kamera.

Acuan diperiksa pada 30 September 2026. Tidak ada deployment eksternal atau
sertifikasi keamanan yang dilakukan sebagai bagian dari QA ini.
