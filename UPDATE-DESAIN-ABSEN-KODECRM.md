# Update: menu Kode CRM, akses desain per designer, absen sesuai tanggal, keterlambatan In-house

## 1. Menu "Kode CRM"
- Daftar "Event Calendar · menunggu kode CRM" dipindah dari halaman Jadwal Event ke menu sendiri **Kode CRM** (di grup Operasional, dengan lencana jumlah antrean). Ada kolom pencarian judul/lokasi/kode, filter bulan, dan tombol Sync Calendar.
- Halaman Jadwal Event kini hanya menampilkan satu baris pengingat jumlah event yang menunggu, dengan tombol ke menu Kode CRM.

## 2. Desain: Head Design dan akses per designer
- Peran baru **Head Design** (pilih di Configure → Akun). Izin baru: `design.read_all` (lihat semua kartu) dan `design.assign` (kirim brief dan pilih designer).
- Yang dapat mengirim brief dan memilih designer: **Head Design, Administrator, Head Operations, Event Coordinator** (Coordinator hanya untuk event yang ia tangani, seperti sebelumnya). **Team Design tidak bisa**.
- **Team Design hanya melihat kartu yang ditugaskan kepadanya**: papan desain, hitungan di dashboard, tab Design pada event, pengingat, dan notifikasi. Mengubah status kartu designer lain ditolak (403). Head Design, Admin, Head Operations, dan Coordinator tetap melihat semua kartu.
- Brief tanpa designer terpilih ("Belum dipilih") hanya terlihat dan diberitahukan ke Head Design/Admin. **Kartu lama yang belum punya designer tidak terlihat oleh staff sampai Head menugaskannya.**
- Daftar event (Jadwal Event) tetap menampilkan semua event untuk Team Design seperti sebelumnya; hanya lencana status desain yang disembunyikan untuk event yang bukan miliknya.

## 3. Absen crew sesuai tanggal event
- Check-in hanya pada tanggal event (WIB). Event 1 hari kini ikut dibatasi (sebelumnya hanya multi-hari). Di luar tanggal, ditolak dengan pesan berisi tanggal event, dan tombol diganti keterangan "Check-in dibuka pada ...".
- Check-out boleh pada tanggal event dan **sehari setelahnya** (shift yang selesai lewat tengah malam). Setelah itu, hubungi Coordinator untuk koreksi absensi.
- Event multi-hari: check-out tanpa menyebut hari akan menutup hari yang masih terbuka (mis. kemarin malam).

## 4. Keterlambatan dan pulang awal In-house
- **Jam kerja** diatur di Configure → tab **Jam kerja**: jam masuk (default 09:00), jam pulang (default 18:00), toleransi terlambat dan toleransi pulang awal (default 0 menit).
- Saat absen, server menghitung menit terlambat (check-in) dan menit pulang lebih awal (check-out), dibulatkan ke bawah per menit penuh, lalu menyimpannya bersama jadwal yang berlaku saat itu. Mengubah jam kerja nanti tidak mengubah riwayat.
- Karyawan langsung melihat pesan setelah absen, misalnya "Terlambat 60 menit (jadwal 09:00)", serta lencana di halaman Absensi In-house dan riwayatnya.
- Notifikasi dikirim ke akun yang berhak melihat absensi In-house (`attendance.inhouse.read_all`, mis. Head Operations, Admin Finance, Head Finance, Administrator), bukan ke karyawan itu sendiri. Satu notifikasi per kejadian.
- **Export (Excel dan CSV)** kini memuat kolom: Jadwal masuk, Terlambat (menit), Jadwal pulang, Pulang lebih awal (menit), dan Keterangan (mis. "Terlambat 65 menit · Pulang lebih awal 59 menit"). Catatan lama sebelum fitur ini dibiarkan kosong, tidak diisi angka rekaan.
- Belum ada kalender hari libur atau akhir pekan: absen di hari apa pun dibandingkan dengan jam kerja yang sama.

## Tes
`python -m unittest discover -s tests` dan `node --test tests/*.cjs`. Tes baru: `tests/test_design_attendance.py` (19 tes), `tests/test_attendance_ui.cjs`.
Dua tes lama (`test_assignment_is_persisted_and_removed_staff_receives_private_notice`, `test_pagination_read_all_and_device_data_privacy`) tergantung tanggal sistem dan juga gagal pada zip asli.
