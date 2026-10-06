RECORDCOUNTDOWN STUDIO v2.14 - SINGLE STRIP PRINT + VIDEO 9:16

V2.14 SINGLE STRIP CLIENT REVISION
- Template baru: "Prenagen BA - 1 Strip Video 9:16" memakai overlay 600x1800.
- JPG digital tetap 600x1800 tanpa bidang hitam.
- MP4 khusus template ini dibuat 1080x1920: strip berada di tengah, sisi kiri-kanan hitam.
- Settings > Printer Foto kini memiliki Layout cetak:
  1) 1 strip di kiri — untuk DNP 2inch cut ON;
  2) 2 strip — untuk DNP 2inch cut ON;
  3) Full 4R — untuk DNP 2inch cut OFF.
- Mode 1 strip mencetak photostrip di sisi kiri media 4x6 dan membiarkan sisi kanan putih,
  sehingga hasil potong tidak zoom dan hanya menjadi satu strip.
- Tombol Preview Layout di Settings menampilkan halaman 4x6 sebelum test print.

====================================================

RECORDCOUNTDOWN STUDIO v2.13 - FLEXIBLE CAMERA SOURCE

V2.13 FLEXIBLE CAMERA SOURCE
- Settings kini memiliki Sumber sesi: Auto, DirectShow, atau Browser MediaRecorder.
- Jika Logitech C920 muncul di Capture tetapi tidak di DirectShow, pilih Mode Browser.
- Browser merekam satu video WebM dari kamera yang dipilih, mengunggahnya ke aplikasi,
  lalu aplikasi mengekstrak setiap countdown dan membuat JPG/MP4 dengan template yang sama.
- DirectShow tetap menjadi mode yang disarankan untuk EOS Webcam Utility dan workflow FFmpeg.

V2.12.1 CAMERA LOCK FIX
- Saat berpindah dari Capture ke Settings, browser preview dilepas otomatis.
- Refresh kamera juga melepas stream browser sebelum FFmpeg memindai DirectShow.
- Ini mencegah Logitech C920 tersembunyi dari daftar DirectShow karena sedang dipakai preview browser.
====================================================

V2.11 PREVIEW OVERLAY PER SESI
------------------------------
Layar Self Service sekarang menampilkan canvas template selama sesi berjalan.
Preview kamera langsung ditempatkan di slot sesi aktif, lalu setelah countdown
selesai frame pose tersebut dibekukan pada slotnya. Sesi berikutnya muncul di
slot berikutnya, sementara overlay PNG selalu berada di lapisan paling atas.
Setelah rendering selesai, canvas sementara diganti dengan JPG final yang
memakai frame video sebenarnya. Preview ini hanya membantu framing dan tidak
mengubah file foto/video final.

Untuk template portrait/strip, preview diberi frame gold yang lebih tegas.
Countdown dipindahkan ke area kosong di samping strip, sedangkan tulisan besar
MEMPROSES disembunyikan dari atas foto agar tidak menutupi slot.
Frame preview portrait dibuat lebih tinggi sehingga template terlihat lebih
besar. Live frame sekarang di-preload lalu ditukar secara double-buffer agar
canvas tidak berkedip saat stream MJPEG berganti frame.

Reset sesi mengosongkan semua slot preview. Rotation dan mirror sesi tetap
mengikuti pengaturan Camera Transform yang dikunci saat tombol Mulai Foto
ditekan.

V2.12 CAMERA ENUMERATION DAN PREVIEW
------------------------------------
Daftar kamera DirectShow sekarang diparsing dari output FFmpeg dengan lebih
aman. Kamera lama yang sudah tidak tersedia tidak lagi dihitung sebagai kamera
terdeteksi. Preview browser mencoba ulang kamera UVC tanpa batas FPS jika
driver menolak kombinasi constraint awal. File 02_DIAGNOSE_CAMERA.bat juga
memiliki capture test 3 detik; pesan error setelah list_options sendiri adalah
keluaran normal dari FFmpeg.


RECORDCOUNTDOWN STUDIO v2.10 - HD VIDEO + VIDEO PREVIEW
=======================================================

V2.10 VIDEO HD
--------------
Template Editor sekarang memiliki pilihan kualitas video:
- Standard: ukuran canvas template dan file lebih kecil;
- HD: render 1,5× canvas, CRF 17, encoder medium;
- HD+: render 2× canvas, CRF 15, encoder slow.

Nilai default template lama dibaca sebagai HD. Kualitas foto dan overlay tetap
mengikuti canvas foto. Untuk memperoleh detail HD dari kamera, isi Input
resolution dengan 1920x1080 jika perangkat kamera mendukungnya.


RECORDCOUNTDOWN STUDIO v2.9 - VIDEO PREVIEW + PRINTER WINDOWS
==============================================================

V2.9 PREVIEW VIDEO DAN PRINTER WINDOWS
--------------------------------------
Panel hasil Self Service sekarang menampilkan pemutar video langsung di bawah
QR. Video memakai file MP4 hasil sesi, poster memakai foto final, dan kontrol
play tersedia tanpa membuka tab baru. Tautan "Lihat Video" tetap tersedia
untuk membuka file penuh.

Menu Settings kini menyebut Printer Foto/Printer Windows. Semua printer yang
terdaftar di Windows dapat dipilih, termasuk DNP dan Epson. DNP tetap memakai
aturan 4R/strip dan 2inch cut. Epson memakai pengaturan media, orientasi,
kualitas, dan borderless dari driver Epson. Klik Test Print setelah memilih
printer untuk memastikan ukuran kertas dan antrean Windows sudah benar.


RECORDCOUNTDOWN STUDIO v2.8 - CAPTURE IT SELF SERVICE
======================================================

V2.8 TAMPILAN SELF SERVICE CAPTURE IT
------------------------------------
Layar Self Service sekarang memakai logo Capture It Photobooth yang diberikan
pengguna dan warna hitam, emas, putih dari logo. Preview kamera tetap besar,
countdown tampil di atas preview, dan setiap foto mempunyai kartu progres.
Tombol Mulai Foto, Cetak Foto, Scan QR, Simpan Foto, dan Foto Lagi tetap ada.
Pada layar sempit panel QR dan print ditampilkan sebelum preview hasil agar
mudah ditemukan. Alur perekaman, rotasi kamera, hasil, QR, dan driver printer
tidak diubah oleh pembaruan desain ini.

Lihat UPGRADE_V2_8.txt untuk cara memasang tanpa kehilangan template dan
pengaturan yang sudah disimpan. URL Self Service: http://127.0.0.1:5050/kiosk


RECORDCOUNTDOWN STUDIO v2.7 - KOREKSI FFmpeg/WEB PREVIEW
=======================================================

V2.7 ORIENTASI SEBELUM DAN SESUDAH START SAMA
-----------------------------------------------
Pada screenshot setup EOS Webcam Utility pengguna, frame browser tegak ketika
Rotation 0 derajat, sementara frame DirectShow/FFmpeg yang muncul saat sesi
berjalan terbalik 180 derajat. Koreksi sumber FFmpeg sekarang terpisah dari
Rotation tampilan.

Pengaturan yang sesuai dengan contoh pengguna:
- Settings > Camera Transform > Rotation correction = 0 derajat.
- Settings > Koreksi sumber FFmpeg = 180 derajat (EOS Webcam Utility).
- Mirror Horizontal dan Mirror Vertical = OFF.

Preview awal menerapkan Rotation tampilan. Preview FFmpeg menerapkan Rotation
tampilan + Koreksi sumber FFmpeg. Video take, snapshot, JPG final, dan MP4 final
menerapkan koreksi yang sama satu kali, sehingga hasilnya mengikuti preview
awal. Record 3s Test juga memakai pengaturan koreksi tersebut.

Jika memakai kamera lain yang sumber browser dan FFmpeg sudah sama, atur
Koreksi sumber FFmpeg ke 0 derajat. File settings.json versi lama boleh disalin
untuk mempertahankan printer, folder sinkronisasi, dan pengaturan lain; EOS
Webcam Utility otomatis memilih koreksi 180 derajat bila pengaturan baru ini
belum ada. Atur Rotation tampilan ke 0 derajat jika preview awal sudah tegak.


RECORDCOUNTDOWN STUDIO v2.6 - ORIENTATION LOCK (RIWAYAT)
================================================

V2.6 ROTATION PREVIEW + OUTPUT KONSISTEN
-----------------------------------------
Versi ini memperbaiki perpindahan orientasi saat tombol Mulai Foto ditekan.
Nilai Rotation yang terlihat pada preview kini dikunci saat sesi dimulai dan
dipakai oleh seluruh sesi sampai selesai.

Alur orientasi v2.6:
- preview idle dari browser memakai Camera Transform di UI;
- live preview sesi dari FFmpeg tetap berupa frame mentah dan memakai Camera
  Transform UI yang sama, sehingga tidak kembali ke 0 derajat saat berganti;
- video take menerapkan transform tersebut tepat satu kali saat ekstraksi;
- snapshot, JPG final, MP4 final, dan hot-folder berasal dari take yang sudah
  memakai orientasi yang sama;
- nilai rotasi dikirim dan disimpan secara sinkron ketika Start Session/Mulai
  Foto ditekan, sehingga autosave yang terlambat tidak memakai nilai lama.

Rotation 0/90/180/270 dan Mirror Horizontal/Vertical tetap didukung. Setelah
upgrade, lakukan Camera Test 3 detik satu kali untuk memilih orientasi yang
benar. Orientasi itu akan tetap sama sebelum, selama, dan sesudah sesi.


RECORDCOUNTDOWN STUDIO v2.5 - FULL 4R / STRIP SWITCH
=====================================================

V2.5 FORMAT PRINT EKSPLISIT
---------------------------
Mode Auto dihapus agar format tidak berubah berdasarkan ukuran file.

Di Settings > DNP Print tersedia sakelar:
- OFF = Full 4R (4x6), seluruh gambar dicetak tanpa crop.
  DNP Printing Preferences: Paper Size (6x4), 2inch cut = Disable.
- ON = dua strip 2x6 dalam satu media 4x6.
  DNP Printing Preferences: Paper Size (6x4), 2inch cut = Enable.

Tombol "Buka DNP Printing Preferences" membuka pengaturan DS-RX1 langsung
dari aplikasi. Opsi 2inch cut merupakan pengaturan privat driver DNP, sehingga
harus dibuat sama dengan sakelar format di aplikasi.

Perbaikan tambahan:
- StartDoc yang mengembalikan None pada driver DS-RX1 tidak lagi dianggap error;
- fallback PowerShell memakai Rectangle integer sehingga kompatibel dengan
  overload DrawImage pada Windows PowerShell 5.1;
- Full 4R mengikuti orientasi driver dan memenuhi seluruh printable area;
- pemilihan mode yang tidak cocok dengan rasio template menghasilkan pesan
  yang jelas agar layout 4x6 tidak salah dicetak sebagai strip.


RECORDCOUNTDOWN STUDIO v2.4 - DNP FULL-BLEED + 2x6 DUPLEX
==========================================================

V2.4 PERBAIKAN SKALA PRINT
--------------------------
Versi ini memperbaiki hasil DS-RX1 yang hanya mengisi sebagian kertas:
- jalur utama sekarang memakai ukuran piksel printable area dari driver DNP;
- PowerShell fallback mengonversi 1/100 inch ke DPI/piksel secara eksplisit;
- Auto DNP memilih Full Bleed untuk layout biasa;
- output strip 600x1800 otomatis digandakan menjadi dua strip 2x6 pada 4x6;
- portrait/landscape driver diikuti otomatis tanpa mengubah orientasi desain;
- Test Print Auto menampilkan dua strip dan harus memenuhi seluruh lembar.

Setting yang direkomendasikan:
1. Settings > DNP Print > pilih DS-RX1.
2. Pilih "Auto DNP - Full Bleed / 2x6 Duplex".
3. Klik Test Print DNP.
4. Jangan gunakan "Tanpa crop" jika ingin kertas terisi penuh, karena mode
   tersebut memang mempertahankan seluruh gambar dan dapat memberi bidang putih.


RECORDCOUNTDOWN STUDIO v2.3 - DNP PRINT FIX
============================================

V2.3 PERBAIKAN PRINT DNP
------------------------
Alur foto/video tetap sama seperti versi sebelumnya. Perubahan versi ini
difokuskan pada printer DNP:
- deteksi printer Windows lebih kuat dan otomatis memilih DS-RX1/DNP;
- nama printer lama dicocokkan kembali meskipun nama driver sedikit berbeda;
- jalur utama memakai Windows PrintDocument;
- jika gagal, aplikasi otomatis mencoba PyWin32 GDI;
- tersedia tombol Test Print DNP di Settings;
- setelah Test Print berhasil, fitur Print otomatis diaktifkan dan disimpan;
- layar hasil memeriksa kembali koneksi printer sebelum mengaktifkan tombol;
- error print dicatat di data\print_jobs.log.

Cara setup:
1. Pastikan DS-RX1 terlihat dan berstatus Idle di Windows Printers.
2. Atur paper size dan orientation melalui DNP Printer Properties.
3. Buka Settings > DNP Print lalu klik Refresh.
4. Pastikan DS-RX1 terpilih, lalu klik Test Print DNP.
5. Jika halaman uji keluar, tombol Print pada layar hasil sudah aktif.

Jika printer belum terdeteksi setelah update, tutup aplikasi lalu jalankan
00_INSTALL.bat dan restart Windows Print Spooler atau PC.


RECORDCOUNTDOWN STUDIO v2.2 - DRIVE DESKTOP SYNC + CUSTOM QR
==============================================================

V2.2 GOOGLE DRIVE DESKTOP + UPLOAD QR/BARCODE
------------------------------------------------
Versi ini tidak memakai Google Drive API dan tidak meminta login Google.

Alur yang direkomendasikan:
1. Buat dua folder di Google Drive Desktop, misalnya:
   G:\My Drive\RecordCountdown\Photos
   G:\My Drive\RecordCountdown\Videos
2. Buka Settings > Google Drive Desktop Auto-Sync.
3. Masukkan kedua alamat folder lokal tersebut, aktifkan copy otomatis,
   tekan Test Folder, lalu Save Settings.
4. Share folder Google Drive yang ingin dibuka pengunjung dan buat gambar
   QR/barcode untuk link folder tersebut menggunakan alat pilihan Anda.
5. Di Settings > QR Hasil Foto, pilih "Upload QR/Barcode Google Drive",
   pilih file gambarnya, lalu klik Upload / Ganti QR.

Setelah setiap sesi:
- JPG lokal disimpan di data\outputs\photos\;
- MP4 lokal disimpan di data\outputs\videos\;
- JPG dan MP4 disalin ke folder Google Drive Desktop yang berbeda;
- Google Drive Desktop melakukan sinkronisasi cloud;
- QR/barcode unggahan tampil pada layar hasil;
- tombol Print tampil langsung pada layar hasil.

QR/barcode dapat diganti kapan saja dari Settings tanpa mengubah aplikasi.
Karena QR menunjuk ke sebuah folder bersama, gunakan folder berbeda untuk
setiap event jika hasil antar-event tidak boleh tercampur.

Untuk DNP, aktifkan DNP Print, pilih printer Windows, atur jumlah copy dan
Photo fit, lalu Save Settings. Tombol Print akan aktif pada layar hasil.


RECORDCOUNTDOWN STUDIO v2.1 - QR DOWNLOAD + DNP PRINT
======================================================

V2.1 QR DOWNLOAD + DNP PRINT
----------------------------
Setelah semua sesi dan render selesai, mode self-service sekarang menampilkan:
- QR yang dapat dipindai untuk mengambil hasil foto;
- tombol Print untuk printer DNP melalui driver Windows;
- tombol simpan foto, lihat video, dan foto lagi.

Settings > QR Hasil Foto:
- pilih QR foto otomatis per output atau link tetap/custom;
- ganti URL dan teks QR kapan saja;
- aktifkan akses LAN agar QR dapat dibuka dari HP;
- atur durasi layar hasil 15-300 detik.

Settings > DNP Print:
- refresh lalu pilih printer DNP yang terpasang di Windows;
- pilih jumlah copy;
- Contain mencetak seluruh layout, Cover mengisi media dengan kemungkinan crop.

Ukuran kertas dan orientasi tetap diatur melalui Windows DNP Printer Properties.
Jalankan kembali 00_INSTALL.bat saat upgrade agar dependency QR dan print
Windows ikut terpasang.

Menu Templates juga memiliki tombol Delete Template. Penghapusan meminta
konfirmasi, tidak diizinkan untuk sample template, dan diblokir saat template
sedang digunakan oleh sesi aktif.


RECORDCOUNTDOWN STUDIO v2.0 - SELF-SERVICE KIOSK
=================================================

V2.0 SELF-SERVICE KIOSK
-----------------------
Mode khusus tamu tersedia di:
http://127.0.0.1:5050/kiosk

Fitur mode self-service:
- live preview kamera berukuran besar;
- countdown tampil langsung di atas live preview;
- progres otomatis mengikuti jumlah sesi pada template;
- hasil foto dan video tampil setelah seluruh sesi selesai;
- tombol Foto Lagi, Simpan Foto, dan Lihat Video;
- otomatis kembali ke layar awal setelah 25 detik;
- tampilan responsif dan tombol besar untuk layar sentuh;
- menu operator tidak terlihat oleh tamu.

Cara cepat:
1. Jalankan 01_START.bat.
2. Jalankan 03_OPEN_SELF_SERVICE.bat atau klik Self Service.
3. Tekan tombol fullscreen pada UI kiosk.
4. Untuk kembali ke panel operator, tekan Ctrl + Shift + A.

Mode operator tetap tersedia di:
http://127.0.0.1:5050/


RECORDCOUNTDOWN STUDIO v1.9 - FULL CANVAS TEMPLATE EDITOR
==========================================================

V1.9 FULL CANVAS TEMPLATE EDITOR
--------------------------------
Template Editor sekarang memakai workspace lebar seperti editor layout:
- Fit All selalu menampilkan seluruh overlay tanpa terpotong;
- Fit Width memperbesar overlay sesuai lebar kanvas dan menyediakan scroll;
- 100% menampilkan ukuran pixel asli;
- tombol minus/plus dan slider mengatur zoom 1% sampai 200%;
- Ctrl + mouse wheel dapat dipakai untuk zoom;
- kanvas memiliki scroll horizontal dan vertikal saat diperbesar;
- panel properti dibuat lebih lebar agar angka koordinat tidak terpotong;
- tampilan otomatis menyesuaikan ukuran jendela browser.

Zoom hanya mengubah tampilan editor. Ukuran canvas, posisi slot, final MP4,
dan final JPG tidak ikut berubah.

RECORDCOUNTDOWN STUDIO v1.8 - TEMPLATE SLOT ADJUSTER
=====================================================

V1.8 TEMPLATE SLOT ADJUSTER
---------------------------
Di Template Editor, setiap slot sekarang dapat disesuaikan langsung:
- klik slot untuk memilih;
- drag kotak untuk memindahkan slot;
- tarik delapan handle kuning untuk mengubah ukuran;
- edit X, Y, Width, dan Height untuk nilai yang presisi;
- tombol panah menggeser 1 pixel;
- Shift + tombol panah menggeser 10 pixel;
- Center X / Center Y meratakan slot ke tengah kanvas.

Posisi dan ukuran selalu dibatasi di dalam kanvas. Klik Save setelah selesai.
Koordinat yang tersimpan dipakai oleh final MP4 dan final JPG.

RECORDCOUNTDOWN STUDIO v1.7 - SMOOTHER 30 FPS CAPTURE
======================================================

RECORDCOUNTDOWN STUDIO v1.2 - CAMERA TRANSFORM
============================================

RECORDCOUNTDOWN STUDIO v1.1 - SYNC START
=======================================

RECORDCOUNTDOWN STUDIO v1
=========================

TUJUAN
------
Satu engine template-driven untuk:
- overlay PNG apa pun
- jumlah slot apa pun
- jumlah take otomatis dari mapping slot
- satu take boleh dipakai di banyak slot
- 1 kamera lewat DirectShow (mis. EOS Webcam Utility)
- countdown + video recording
- snapshot JPG di akhir setiap take
- final MP4 dan final JPG dari template yang sama
- optional copy snapshot ke dslrBooth Hot Folder

ALUR DEFAULT 3 SESI
-------------------
- Sesi 1: live preview + countdown sambil merekam tepat 5 detik.
  Frame terakhir menjadi foto sesi 1 dan masuk ke layout/baris 1.
- Ganti pose.
- Sesi 2: proses yang sama untuk layout/baris 2.
- Ganti pose.
- Sesi 3: proses yang sama untuk layout/baris 3.
- Final JPG memakai frame terakhir ketiga video.
- Final MP4 berdurasi 5 detik; ketiga video diputar bersamaan di slotnya.

Ini LOCAL WEB APP.
Browser hanya UI. Semua file, camera capture, FFmpeg, template, dan output
tetap berjalan di PC Windows lokal. Tidak memerlukan internet setelah instalasi.


INSTALASI
---------
1. Extract folder menjadi:
   C:\RecordCountdownStudio\

2. FFmpeg harus sudah tersedia:
   ffmpeg -version

3. Install Python bila belum ada.
   Windows winget:
   winget install Python.Python.3.12

4. Jalankan:
   00_INSTALL.bat

5. Jalankan:
   01_START.bat

6. Browser:
   http://127.0.0.1:5050


PERTAMA KALI
------------
Settings:
- Refresh Camera Devices
- pilih "EOS Webcam Utility" atau virtual webcam/device yang benar
- Input Resolution dikosongkan dulu agar device memakai default
- Input FPS = 0 agar device memakai default
- Save Settings
- Record 3s Test

Jika test camera berhasil, buka Capture dan jalankan sample template.


TEMPLATE ENGINE
---------------
Upload PNG transparan dari menu Templates.

RecordCountdown mencoba mendeteksi area alpha/transparan besar.
Area yang berada dalam baris yang sama otomatis memakai sourceTake yang sama.

Contoh 6 slot:
[ Take 1 ][ Take 1 ]
[ Take 2 ][ Take 2 ]
[ Take 3 ][ Take 3 ]

=> jumlah take otomatis = 3.

Mapping dapat diubah per slot.

Jika slot tidak terdeteksi:
- klik + Add Slot
- drag rectangle pada preview overlay

Jika hasil auto-detect kurang pas:
- klik kotak slot pada preview;
- drag kotak untuk memindahkannya;
- tarik handle kuning untuk resize;
- atau isi X/Y/Width/Height di panel Posisi Slot Terpilih;
- klik Save.

PNG overlay tetap berada PALING ATAS saat render.
Jadi lubang transparan berbentuk lingkaran, rounded, blob, heart, dsb tetap
dapat menjadi mask. Rectangle slot hanya menentukan area video di belakangnya.


TAKE COUNT
----------
Tidak ada takeCount permanen di engine.

Engine menghitung:
max(sourceTake)

Contoh:
[1,1,2,2,3,3] -> 3 take
[1,2,3,4] -> 4 take
[1,1,1,1,1,1] -> 1 take
[1,1,2,2,3,3,4,4] -> 4 take


EOS WEBCAM UTILITY / SATU KAMERA
-------------------------------
Pada mode ini RecordCountdown memegang camera feed melalui DirectShow.
Jangan biarkan dslrBooth juga mengontrol DSLR yang sama secara langsung.

Jika ingin dslrBooth tetap dipakai untuk print:
- RecordCountdown mengambil snapshot JPG di akhir tiap take.
- aktifkan Hot Folder di Settings.
- arahkan ke folder input/data yang dipantau workflow dslrBooth Anda.

Catatan:
JPG berasal dari webcam stream, bukan file foto full-resolution DSLR.


OUTPUT
------
data\outputs\photos\
- recordcountdown_<session>.jpg

data\outputs\videos\
- recordcountdown_<session>.mp4

data\sessions\
- take_01.mp4
- snapshot_01.jpg
- take_02.mp4
- snapshot_02.jpg
- dst.


FLEXIBLE TEMPLATE
-----------------
Setiap template menyimpan:
- canvas width/height
- overlay
- countdown
- pre-session delay
- gap antar take
- FPS
- output duration
- fit cover/contain
- repeat/none
- slot coordinate
- sourceTake untuk setiap slot

Engine tidak perlu diedit ketika brand/layout berubah.


CATATAN
-------
v1 sengaja fokus pada fondasi:
template manager + camera capture + countdown + snapshot + compose.

Fitur berikutnya dapat ditambahkan tanpa mengganti format template:
- sharing QR/WhatsApp
- retake per take
- animated countdown skin
- multi-output (Story/Reels/Square/Print)
- admin PIN
- automatic upload
- printer queue


V1.1 SYNC START
---------------
Perubahan penting:
- Kamera TIDAK dibuka ulang untuk Take 1, 2, 3.
- Satu DirectShow / EOS Webcam Utility stream dibuka sepanjang session.
- Default camera warmup = 3 detik sebelum Take 1.
- Setiap countdown ditandai memakai media clock FFmpeg.
- Setelah session, take dipotong dari satu continuous source.
- Semua take kemudian di-reset ke timestamp 00:00 saat final compose.

Ini mengatasi kasus:
Take 1 terlihat freeze/diam beberapa detik sedangkan Take 2 dan Take 3
sudah bergerak dari awal.

Setting:
Settings > Camera warmup sebelum Take 1

Mulai dari 3 detik.
Jika EOS Webcam Utility masih freeze pada Take 1, naikkan ke 4-5 detik.

Final MP4:
Take 1 / Take 2 / Take 3 semuanya mulai dari frame awal masing-masing pada
timeline output 00:00 secara bersamaan.


V1.2 CAMERA TRANSFORM
---------------------
Settings sekarang mempunyai:
- Rotation 0 / 90 / 180 / 270
- Mirror Horizontal
- Mirror Vertical

Untuk setup yang menghasilkan video upside-down:
Rotation = 180
Mirror Horizontal = OFF
Mirror Vertical = OFF

Transform global dilakukan sebelum clip/take dibuat.
Jadi final MP4 dan snapshot JPG akan konsisten.


V1.3 (HISTORICAL)
-----------------
1. Live Preview ditambahkan di halaman Capture.
   - memakai browser camera preview (getUserMedia)
   - berguna untuk framing sebelum session
   - otomatis dihentikan saat Start Session agar tidak bentrok dengan FFmpeg

2. Logic sample template diperbaiki:
   - cameraWarmupSeconds = 3 detik
   - preSessionDelay = 0 detik
   - countdown = 5 detik
   - snapshotLead = 0.05 detik

Artinya sekarang alurnya:
- klik Start Session
- backend membuka EOS Webcam / DirectShow
- warmup 3 detik
- Take 1 countdown 5 detik sambil merekam video dari detik pertama countdown
- frame akhir countdown dijadikan image/foto
- jeda antar take
- Take 2
- Take 3
- render final

Jadi TIDAK ADA lagi delay tambahan 3 detik kedua sebelum take pertama.
Sebelumnya warmup 3 detik + preSessionDelay 3 detik membuat flow terasa salah.

CATATAN
-------
Preview browser dan backend capture adalah dua hal berbeda:
- Preview dipakai untuk framing
- Backend FFmpeg dipakai untuk hasil final
- Start Session otomatis mematikan preview browser agar tidak rebutan kamera


V1.4 LIVE CAPTURE PREVIEW + CAPTURE AFTER 1
-------------------------------------------
Perubahan utama:
- Browser preview untuk framing tetap tersedia sebelum sesi.
- Saat Start Session ditekan, tampilan otomatis beralih ke live preview dari
  proses FFmpeg yang sama dengan rekaman. Kamera tetap hanya dibuka satu kali.
- Rotation dan mirror diterapkan sebelum stream dibagi ke preview dan rekaman,
  sehingga preview sesi, MP4, snapshot, dan JPG final memakai orientasi sama.
- Countdown tampil di atas live preview: 5, 4, 3, 2, 1.
- Foto tidak lagi diambil saat angka 1 masih tampil. Setelah 1 selesai, engine
  menunggu captureDelay (default 0.25 detik) dan mengambil frame eksplisit dari
  timeline continuous recording.
- Setelah foto diambil, live preview tetap berjalan, muncul instruksi GANTI
  POSE, lalu countdown baru dimulai untuk foto berikutnya.


V1.5 RELIABLE LIVE PREVIEW + EXACT 5 SECONDS
--------------------------------------------
- Live preview sesi memakai respons JPEG pendek yang diperbarui berkala.
  Ini menghindari ikon gambar rusak/layar hitam dari multipart MJPEG pada
  kombinasi Windows dan Chrome tertentu.
- Setiap sesi/take sample direkam tepat 5.000 detik, bukan 5.25 detik.
- Foto setiap sesi diambil dari frame lengkap terakhir di dalam video 5 detik.
  Capture/flash sesudah countdown hanya indikator UI dan bukan bagian video.
- Sesi 1, 2, dan 3 tetap dipetakan ke layout/baris 1, 2, dan 3.
- Rotation default paket tetap 270 derajat karena itu yang membuat kamera
  contoh tampil tegak; preview sesi dan semua hasil memakai transform sama.

ROTATION UNTUK REKAMAN CONTOH
-----------------------------
Output contoh sebelumnya masih menyamping dengan Rotation 0 atau 180. Untuk
orientasi kamera pada contoh tersebut, nilai koreksi yang sesuai adalah:
- Rotation = 270
- Mirror Horizontal = OFF
- Mirror Vertical = OFF

Gunakan Settings > Record 3s Test untuk memastikan orientasi. Nilai yang tampil
benar pada test akan sama dengan live preview sesi dan file hasil.


V1.6 CAMERA ROTATION APPLY FIX
------------------------------
- Pilihan Rotation 0/90/180/270 sekarang tersimpan otomatis saat diubah.
- Record 3s Test selalu menyimpan nilai form terbaru sebelum membuka kamera.
- Video Camera Test langsung autoplay dan loop setelah selesai direkam.
- Status menampilkan rotation aktif dan filter FFmpeg yang benar-benar dipakai.
- Rotation yang sama digunakan oleh browser preview, Camera Test, live preview
  sesi, video take, final MP4, snapshot, dan final JPG.
- Backend menolak nilai rotation di luar 0/90/180/270 agar setting rusak tidak
  diam-diam dianggap 0 derajat.


V1.7 SMOOTHER 30 FPS CAPTURE
----------------------------
- Input FPS default EOS Webcam Utility dinaikkan dari Auto/0 ke 30.
- Sample template dan template baru memakai output 30 FPS (150 frame/5 detik).
- Live preview sesi dinaikkan dari 10 ke 20 FPS.
- Polling frame browser dipercepat agar preview 20 FPS dapat tampil penuh.
- Buffer DirectShow dan input queue diperbesar untuk mengurangi frame drop.
- Halaman Capture menampilkan FPS efektif, output FPS, preview FPS, dan persen
  frame duplikat dari FFmpeg secara real time.
- Indikator hijau berarti sumber kamera mendekati target. Indikator kuning
  berarti kamera masih memberikan FPS rendah atau banyak frame duplikat.

PENTING:
30 FPS output tidak dapat menciptakan detail gerak jika kamera hanya mengirim
10 FPS. Jika indikator tetap sekitar 10 FPS, tambah pencahayaan, atur shutter
kamera ke 1/50 atau 1/60, dan jalankan 02_DIAGNOSE_CAMERA.bat untuk memastikan
EOS Webcam Utility menawarkan mode 30 FPS.
