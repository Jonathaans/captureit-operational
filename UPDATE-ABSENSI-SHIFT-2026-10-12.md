# Absensi website, shift karyawan, dan detail Board Design

## Cara menggunakan

1. Buka website melalui **HTTPS** di Chrome, Edge, atau Safari. Izinkan kamera dan lokasi. Pada laptop, aktifkan layanan lokasi dan Wi-Fi; pada HP, aktifkan lokasi presisi.
2. Pilih **Absensi In-house → Absen masuk**. Ambil foto, periksa hasil, lalu tekan **Kirim Check-in**. Gunakan **Absen pulang** setelah selesai bekerja.
3. Tombol **Ambil ulang**, **Batal**, dan tutup kamera kembali berfungsi. Kamera dilepas setelah dialog ditutup. Jika kamera browser tidak tersedia, tersedia **Coba kamera lagi** dan **Kamera / foto perangkat**. Pilihan perangkat menerima file foto dan menormalisasinya ke JPEG; foto tersebut tidak dijamin berasal dari pengambilan langsung. Lokasi tetap wajib, maksimal ketidakakuratan 500 m, dan waktu absensi tetap ditentukan server.
4. **Administrator / Head Operations → Absensi In-house → Jadwal shift karyawan**: pilih karyawan, nama shift, tanggal awal–akhir, jam masuk–pulang, dan toleransi. Setiap hari dalam rentang mendapat shift yang sama; ubah tanggal tertentu untuk rotasi. Tersedia satu shift per karyawan per tanggal mulai. Tanpa jadwal khusus, jam reguler dari **Configure → Jam kerja** berlaku.
5. Contoh shift **22:00–06:00** berakhir keesokan hari. Absen pulang menutup shift yang masih terbuka. Jika baru absen masuk setelah tengah malam, pilih tanggal mulai shift yang sesuai. Server hanya menerima tanggal hari ini atau shift malam kemarin yang belum berakhir. Selesaikan absen pulang sebelum membuka shift berikutnya.
6. Klik/tap bagian mana pun pada kartu **Board Design** untuk langsung membuka detail event pada tab **Design**. Enter/Space juga berfungsi. Status dapat diubah melalui form detail pada HP, atau drag-and-drop pada laptop.

## Hak akses

- Default: Administrator dan Head Operations mengatur shift (`attendance.inhouse.schedule`). Izin ini dapat disesuaikan Administrator melalui matriks role; role kustom yang sudah ada tidak direset.
- Rekap/foto tim tetap mengikuti `attendance.inhouse.read_all`. Izin mengatur shift saja tidak memberikan akses foto/rekap absensi tim.
- Karyawan In-house hanya mengirim absensi untuk akunnya sendiri. Crew/PIC tetap memakai absensi event sesuai penugasan dan tanggal event.
- Team Design tetap hanya mendapat kartu/brief yang ditugaskan kepadanya. Head Design dan role pengelola mengikuti hak akses sebelumnya.
- Perubahan role berlaku pada permintaan berikutnya; navigasi kembali ke Dashboard jika halaman lama tidak lagi tersedia.

## Penyimpanan dan pembaruan

- Startup menambahkan tabel `inhouse_shifts` serta kolom snapshot ke `inhouse_attendance` secara otomatis. Tidak ada penghapusan atau penggantian riwayat absensi.
- Jam masuk, jam pulang, tanggal selesai, nama shift, dan toleransi dibekukan saat check-in. Mengubah jam reguler sesudahnya tidak mengubah check-out atau hasil ekspor catatan tersebut.
- Jadwal yang absensinya sudah dimulai tidak dapat diedit/reset. Penyimpanan rentang bersifat atomik dan menolak shift khusus yang bertumpuk dengan shift khusus pada tanggal sebelahnya.
- Export Excel/CSV menambahkan kolom **Shift** dan **Jadwal selesai (WIB)**. Riwayat akun yang kemudian dinonaktifkan/berganti role tetap ikut dalam ekspor periode terkait.
- `Dockerfile` menyertakan modul baru `attendance_shifts.py`. Alur hapus akun melindungi akun yang memiliki jadwal shift; reset data operasional juga membersihkan jadwal shift.

Sesudah PR digabung, gunakan alur update instalasi yang sudah ada di `deploy/VPS-OPERATIONS.md` dan pertahankan project/volume Compose yang sama. Untuk instalasi `/var/www/captureit-operational`:

```bash
cd /var/www/captureit-operational
bash deploy/backup-vps.sh
git pull --ff-only origin main
docker compose -p captureit-operational config --quiet
docker compose -p captureit-operational up -d --build --no-deps captureit-ops
docker compose -p captureit-operational logs --tail=40 captureit-ops
```

## Validasi

- `python -m unittest discover -s tests`: 193 tes lulus, termasuk matriks 15 role, privasi foto, perubahan role/ACL, shift lintas tanggal, duplikasi absen, snapshot jadwal/toleransi, dan ekspor.
- `python tests/run_wsgi_suite.py`: 193 tes yang sama lulus melalui transport Waitress yang dipakai deployment.
- `node --test tests/*.cjs`: 71 tes UI lulus.
- `node scripts/verify_attendance_browser.cjs`: pengujian browser Chromium dengan layar 1365×900 dan emulasi HP 390×844 lulus. Menguji form shift, check-in/out dengan kamera sintetis dan lokasi simulasi, ambil ulang, penutupan kamera, pilihan foto perangkat, klik/tap kartu desain, keyboard, dan lebar tampilan.
- Tes browser menggunakan database sementara, memerlukan paket `playwright` dan Chromium. `CHROMIUM_PATH` dapat menunjuk browser lokal; `PYTHON` dapat memilih interpreter; `QA_SCREENSHOTS` opsional untuk hasil tangkapan layar.
- Perangkat fisik dan website produksi belum diuji dari lingkungan ini. HTTPS dan izin kamera/lokasi tetap diperlukan; kode tidak dapat mengaktifkan izin perangkat yang ditolak pengguna.
