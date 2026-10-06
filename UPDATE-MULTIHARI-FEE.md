# Update: Sync multi-hari, mapping crew per hari, pembatasan fee

## Sync Google Calendar
- Event all-day: tanggal akhir Google bersifat eksklusif; kini dikurangi 1 hari (sebelumnya event bertambah 1 hari).
- Pesan gagal sync kini menampilkan kode HTTP dan alasan dari Google beserta petunjuk (invalid_grant, API belum aktif, ID kalender salah). Juga dicetak ke log server.
- Jika `invalid_grant`: OAuth consent screen berstatus Testing membuat refresh token kedaluwarsa tiap 7 hari. Ubah ke In production, lalu buat ulang GOOGLE_REFRESH_TOKEN di .env.
- Event lama yang sudah tersimpan dari sync sebelumnya tidak dikoreksi otomatis (sync hanya mengambil event yang belum berakhir). Ubah manual jika perlu.

## Mapping crew per hari (event > 24 jam dan melewati lebih dari satu tanggal)
- Form "Jadwalkan staff" menampilkan pilihan hari. Per penugasan ada "Atur hari kerja".
- Absensi per hari, hanya pada hari yang dipetakan. Hari yang sudah ada absensinya tidak dapat dilepas.
- Bentrok jadwal dihitung per hari yang sama-sama dikerjakan.
- Payroll = (fee dasar + skill + uang makan) x jumlah hari check-out; baru muncul setelah semua hari selesai.
- Jika tanggal event berubah di Calendar, mapping hari di luar rentang dibersihkan otomatis.
- Event 1 hari, event malam, dan penugasan lama tidak berubah.

## Fee
- Admin Finance, Sales Staff (dan kode admin_sales jika dibuat) tidak dapat membuka payroll freelance, ekspor payroll, tarif fee/skill, maupun payroll in-house. Dikunci di kode, tidak bisa diberikan lewat Configure.
- Tetap bisa: slip gaji sendiri, absensi in-house, uang jalan.

## Tes
`python -m unittest discover -s tests` dan `node --test tests/*.cjs`. Tes baru: tests/test_multiday.py, tests/test_multiday_ui.cjs.

## Koreksi absensi yang terlewat
- Izin baru `attendance.correct`: hanya Event Coordinator, Head Operations, dan Administrator. PIC dan crew tidak bisa.
- Tombol "Koreksi absensi" muncul pada hari yang sudah/sedang berlangsung dan belum selesai (Belum absen, check-in tanpa check-out, atau ditandai Tidak hadir). Event 1 hari dan multi-hari sama-sama didukung.
- Pilihan: Hadir (isi jam masuk dan pulang) atau Tidak hadir. Alasan wajib (min. 5 karakter).
- Aturan: tidak bisa mengoreksi absensi sendiri; tidak bisa hari yang belum berlangsung; tidak bisa absensi yang sudah lengkap dengan foto dan lokasi; tidak bisa jika sudah masuk batch payroll; tidak bisa pada event yang sudah di-clear atau dibatalkan. Check-in crew yang sudah tercatat (berfoto) tidak ditimpa.
- Hasil koreksi berlabel "Dikoreksi pengelola" beserta alasannya, dan tercatat di audit log (nilai sebelum dan sesudah).
- Hari yang sudah lewat tanpa absensi ditandai "Perlu tindakan", dan ada banner jumlah crew yang belum terselesaikan.
- Event multi-hari tidak dapat di-clear selama masih ada hari yang belum terselesaikan. Event 1 hari tetap seperti sebelumnya.
