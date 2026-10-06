# Pusat notifikasi dan push perangkat

Paket lengkap, 5 Oktober 2026 (WIB), melanjutkan paket histori gaji/jadwal.

## Yang sudah tersedia

Notifikasi baru disimpan per akun di SQLite. Pengiriman push mempunyai antrean
tersendiri, sehingga kegagalan Firebase tidak menggagalkan penerbitan slip,
penugasan, atau pengiriman brief. Notifikasi dalam aplikasi langsung dapat
digunakan tanpa Firebase. Push perangkat perlu diaktifkan dengan konfigurasi di
bawah; paket tidak berisi akun Firebase, kunci privat, atau APK.

| Peristiwa | Penerima dan perilaku |
|---|---|
| Slip In-house terbit | Pemilik payout, setelah Finance mencatat transfer. Menyimpan draft/perubahan gaji tidak menerbitkan notifikasi slip. |
| Slip Crew/PIC terbit | Setiap pemilik penugasan dalam batch yang ditransfer, satu pemberitahuan per orang per batch. Export saja belum memicu slip. |
| Penugasan baru/dihapus | Orang yang ditugaskan atau dibatalkan. Orang yang dihapus menerima pemberitahuan pembatalan, tanpa akses kembali ke rincian event. |
| Tanggal/jam/lokasi/nama/status event berubah dari Calendar | Crew/PIC dan coordinator terkait; pembatalan Calendar yang hanya berisi ID tetap mempertahankan rincian jadwal terakhir. |
| Transportasi berubah | Tim event menerima pemberitahuan ketika rincian transportasi benar-benar berubah. Loading tetap tidak memblokir penjadwalan manusia. |
| Brief baru/diperbarui | Designer terpilih. Tanpa assignee, masuk ke akun aktif dengan izin baca/update desain dan akses event, termasuk Admin dengan izin tersebut. |
| Revisi desain | Designer terkait. Catatan revisi terlihat pada event; mengirim status/catatan identik tidak mengirim ulang. |
| Desain menunggu review/disetujui | Coordinator; PIC juga diberi tahu ketika disetujui. Link hasil desain opsional, harus HTTPS. Link yang disetujui tersedia pada ringkasan event untuk tim yang berhak melihatnya. |
| Pengingat event besok | Crew/PIC terkait, berdasarkan tanggal WIB. Menggunakan jadwal terbaru. |
| Uang jalan, penutupan, kesiapan tim/transportasi/desain/alat, konflik, evaluasi | Pengingat yang sudah ada diteruskan ke antrean sesuai penerima dan hak aksesnya. Pengingat yang sudah tidak relevan tidak dikirim lagi. |

Peristiwa slip, penugasan, Calendar, transportasi, dan desain dicatat pada
transaksi yang sama dengan perubahan sumbernya. Worker memeriksa antrean dan
pengingat operasional kira-kira setiap 15 detik saat server berjalan. Lonceng
tetap diperbarui setiap menit saat halaman aktif; push di halaman aktif juga
memperbarui lonceng. Pengingat operasional mewakili keadaan terkini, bukan audit
setiap status antara dua pemeriksaan. Riwayat perubahan tetap ada di audit log.

## Penggunaan

**Notifikasi:** tekan lonceng → **Pengaturan & push**. Pilih **Aktifkan notifikasi**
pada perangkat yang akan digunakan. Permintaan izin browser baru muncul setelah
tombol ditekan. Pilih **Tes perangkat ini**, kemudian **Muat ulang status**.
Status *Diterima layanan push* berarti FCM menerima pesan; itu belum membuktikan
pesan tampil di HP atau sudah dibaca. Menonaktifkan kategori hanya menghentikan
push kategori itu; catatannya tetap dapat dibaca dalam aplikasi.

Jam tenang memakai WIB. Pengingat biasa ditunda sampai jam tenang selesai.
Perubahan/pembatalan jadwal mendesak dan tes yang diminta pengguna dapat lewat
jam tenang. Mematikan kategori tetap menghentikan push kategorinya. Tes perangkat
merupakan permintaan eksplisit dan tetap bisa dikirim meskipun kategori
operasional dimatikan. Tes dibatasi sekali per menit per akun.

**Desain:** buka event → **Design**, isi brief, pilih designer atau antrean tim,
pilih tenggat opsional → **Kirim brief**. Membuka/mengetik form belum mengirim
notifikasi. Pengiriman isi yang sama tidak menggandakan pemberitahuan. Jika
orang lain mengubah brief lebih dulu, server meminta muat ulang agar perubahan
baru tidak tertimpa. Tahap desain, catatan revisi, dan tautan hasil disimpan
dengan formulir terpisah. Form ini belum memiliki penyimpanan draft perangkat;
kirim brief sebelum menutupnya.

**Tautan notifikasi:** membuka slip yang terkait, tab event, atau pusat
notifikasi untuk penugasan yang sudah dihapus. Tautan dari push tetap meminta
login dan memeriksa penerima/hak akses. Status dibaca bukan konfirmasi kesediaan
bertugas. Konfirmasi/percakapan tetap melalui WhatsApp; tidak ada pesan WhatsApp
otomatis yang dikirim oleh fitur ini.

## Aktivasi di VPS

1. Backup database, `.env`, file privat, dan aset branding. Hentikan aplikasi,
   ganti kode dengan paket lengkap, lalu jalankan kembali. Migrasi berjalan
   otomatis dan tidak menghapus data lama. Pertahankan volume/lokasi data lama.
2. Siapkan domain HTTPS yang mengarah ke aplikasi, pada root domain/subdomain
   (contoh `https://ops.perusahaan.id`, tanpa subpath). Gunakan `COOKIE_SECURE=1`.
   Persyaratan staging/produksi pada `QA-PRA-VPS.md` tetap berlaku.
3. Di Firebase, buat/pilih proyek milik perusahaan dan daftarkan aplikasi Web.
   Salin konfigurasi Web dan buat Web Push certificate/VAPID key pada Cloud
   Messaging. Pastikan Cloud Messaging API proyek aktif. Semua konfigurasi Web,
   VAPID, service account, dan aplikasi Android nanti harus merujuk proyek yang
   sama.
4. Buat kredensial service account untuk server dengan izin mengirim FCM
   (`cloudmessaging.messages.create`; role yang sesuai adalah Firebase Cloud
   Messaging API Admin). Simpan JSON privat di luar folder `static`, di luar
   source repository/ZIP, dan jangan masukkan ke browser atau aplikasi Android.
   Batasi pembacaan file kepada administrator dan proses aplikasi.
5. Tambahkan variabel berikut ke `.env` yang sudah ada. Jangan mengganti seluruh
   `.env` instalasi lama dengan contoh baru.

```dotenv
PUSH_ENABLED=true
OPS_PUBLIC_ORIGIN=https://ops.perusahaan.id
FIREBASE_PROJECT_ID=proyek-perusahaan
FIREBASE_WEB_API_KEY=konfigurasi-apiKey-web
FIREBASE_MESSAGING_SENDER_ID=konfigurasi-messagingSenderId
FIREBASE_WEB_APP_ID=konfigurasi-appId-web
FIREBASE_WEB_VAPID_KEY=public-key-Web-Push
FIREBASE_SERVICE_ACCOUNT_FILE=/lokasi-privat/firebase-service-account.json
```

Konfigurasi Web/VAPID publik memang dibaca browser. File JSON service account
berisi kunci privat dan hanya dibaca server. Halaman pengaturan tidak menampilkan
token perangkat, hash sesi, path kredensial, atau kunci privat.

**Python langsung:** jalankan `python3 -m pip install -r requirements-push.txt`
dalam virtual environment aplikasi, lalu restart proses server. Tanpa dependensi
ini, aplikasi tetap berjalan dengan pusat notifikasi; push dinyatakan belum
aktif. Modul pengiriman menggunakan Firebase Admin SDK 7.7.0; SDK Web 12.19.0
dimuat dari CDN Google setelah pengguna mengaktifkan push. Keduanya menggunakan
API registration-token yang masih didukung, meskipun Firebase sudah menyediakan
API Firebase Installation ID baru. Pembaruan SDK berikutnya perlu diuji sebelum
dipakai.

**Docker Compose:** image memasang dependensi push. Letakkan service account di
VPS, lalu tambahkan `FIREBASE_SERVICE_ACCOUNT_HOST_FILE=/path/absolut/file.json`
ke `.env`. File harus dapat dibaca UID 10001 container tanpa memberi akses publik.
Override memasangnya read-only ke `/run/secrets/firebase-service-account.json`.

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.push.yml up -d --build
```

Perubahan `.env`/service account memerlukan restart/recreate proses. Setelah
konfigurasi selesai, login di browser/HP, aktifkan izin, dan kirim tes melalui
aplikasi. Jangan menjalankan reset data untuk mengaktifkan push.

## Web, iPhone, dan Android

Web membutuhkan HTTPS, browser yang mendukung Push API, serta izin notifikasi.
Manifest dan service worker sudah disediakan. Pada iPhone/iPad yang mendukung
Web Push (iOS/iPadOS 16.4+), tambahkan aplikasi ke Home Screen dan buka dari ikon
tersebut sebelum mengaktifkan notifikasi. Dukungan instalasi/ikon tetap perlu
diverifikasi pada perangkat yang digunakan tim. Tidak ada cache offline untuk
data API, gaji, atau foto.

Untuk APK Android nantinya, tambahkan Firebase Messaging SDK, izin notifikasi
Android, channel `captureit_updates`, penanganan token berubah, dan pembukaan
tautan notifikasi. Backend sudah menerima `platform: "android"`; APK belum
dibuat oleh patch ini. Pemanggil native memakai autentikasi sesi dan CSRF yang
sama dengan aplikasi, lalu mendaftarkan token melalui `POST /api/push/register`:

```json
{"token":"token-dari-SDK","device_key":"64-karakter-hex-acak-per-instalasi","platform":"android","label":"HP kerja saya"}
```

Client Android perlu membuat `device_key` secara kriptografis, menyimpannya
secara lokal, mendaftarkan ulang token saat berganti, dan menghapus notifikasi
lokal saat logout. Pesan Android membawa ID notifikasi; ambil rincian melalui
`GET /api/notifications/item?notice=ID` setelah login. Maksimal 10 perangkat aktif
per akun. Pendaftaran di browser/perangkat yang sama mengalihkan tujuan ke sesi
dan akun terbaru; antrean untuk pemilik lama dibatalkan.

## Pengiriman dan privasi

- Pesan FCM tidak berisi nominal gaji, rekening, referensi bank, isi brief, atau
  nama event. Notifikasi layar kunci menggunakan teks umum. Rincian dibaca dari
  server setelah pemeriksaan akun dan hak akses.
- Web service worker memeriksa sesi aktif sebelum menampilkan pesan. Logout
  membatalkan antrean perangkat sesi tersebut, menutup notifikasi lokal, dan
  mengabaikan respons lama. Sesi yang habis/akun nonaktif tidak menerima kiriman
  baru. Android dapat saja menampilkan pesan umum yang sudah terlanjur diterima
  OS; rincian tetap tidak dapat dibuka oleh akun lain.
- Antrean dicoba ulang maksimal 6 kali dengan jeda bertambah, hingga 1 jam.
  Push berumur maksimal 48 jam; catatan inbox tidak ikut dihapus. Token yang
  dinyatakan tidak terdaftar oleh FCM dinonaktifkan. Kesalahan menyimpan kode
  jenis error saja, tanpa menyalin token/kredensial ke log.
- Worker memakai lease agar dua worker tidak mengambil antrean yang sama.
  Kegagalan tepat setelah provider menerima pesan masih dapat menghasilkan
  percobaan ulang; ID/tag notifikasi yang sama digunakan untuk mengurangi
  duplikasi tampilan. Pengiriman FCM/OS bukan jaminan tepat sekali atau selalu
  seketika.
- Migrasi menetapkan pengingat lama sebagai saldo awal tanpa push massal.
  Slip/brief historis sebelum patch tidak dikirim ulang. Pengingat yang selesai
  disembunyikan dari inbox aktif, namun catatan yang sudah disimpan tetap ada di
  database. Reset operasional menghapus notifikasi/antrean/perangkat, sambil
  mempertahankan preferensi akun dan histori gaji.

## Verifikasi

**113 tes backend dan 55 tes JavaScript lulus (168 total).** Migrasi dari ZIP
histori gaji/jadwal sebelumnya diperiksa pada database sintetis: nilai baris
tabel lama tetap sama, initialisasi berulang tidak menggandakan notifikasi,
tidak ada push untuk saldo awal pengingat, dan integritas/foreign key bersih.

Jalankan dari folder aplikasi:

```bash
python3 -m unittest discover -s tests -v
node --test --test-reporter=tap tests/test_drafts.cjs tests/test_frontend.cjs tests/test_schedule_history_ui.cjs tests/test_push_ui.cjs
```

Pengujian mencakup alur lama, penerima slip/brief, akses akun, transaksi atomik,
idempotensi, perubahan Calendar, retry/lease worker, logout/pergantian akun,
jam tenang, token invalid, pagination, tautan tujuan, serta respons terlambat
pada service worker. Uji JavaScript memakai VM/DOM tiruan; transport FCM memakai
simulasi. **Koneksi Firebase nyata, SDK terpasang di VPS, build Docker, tampilan
browser/HP, dan push nyata belum diuji karena kredensial/domain/perangkat belum
disediakan.** Tuntaskan uji tersebut di staging sebelum pemakaian operasional.

Rujukan integrasi resmi:

- https://firebase.google.com/docs/cloud-messaging/web/get-started
- https://firebase.google.com/docs/cloud-messaging/web/receive-messages
- https://firebase.google.com/docs/cloud-messaging/send/admin-sdk
- https://firebase.google.com/support/release-notes/js
- https://firebase.google.com/support/release-notes/admin/python
- https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/
