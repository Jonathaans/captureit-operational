# Laporan penutupan event

Paket lengkap ini melanjutkan versi kendaraan ketik. Perubahan terbaru bahan opsional dan transportasi dijelaskan dalam `UPDATE-BAHAN-TRANSPORTASI.md`. Notifikasi, mapping kendaraan, export Excel acuan, dan dialog logout dari pembaruan sebelumnya tetap tersedia.

## Cara menggunakan

1. **PIC yang ditugaskan** membuka detail event → **Penutupan**. Hak mengisi mengikuti penugasan PIC pada event tersebut, termasuk bila role utama akunnya Crew.
2. Isi tanggal/jam mulai dan selesai aktual dalam WIB serta kesesuaian layanan. Pemakaian ribbon, frame, lensa lenticular, magnet, dan keychain semuanya opsional. Rekap cetak/gagal juga boleh kosong. Jika mencatat ribbon, tambah baris setiap memakai roll berikutnya dan isi sisa kapasitas awal/akhir.
3. Catat kendala dan penanganannya, lalu pekerjaan yang masih harus ditindaklanjuti beserta penanggung jawab dan targetnya. Bagian kendala boleh kosong bila tidak ada.
4. Tambahkan foto setup atau pelaksanaan bila diperlukan. Maksimal delapan JPG/PNG per event; browser memperkecil gambar menjadi maksimal 500 KB per foto sebelum dikirim. Foto dan data baru tersimpan di server setelah **Simpan draft** atau **Kirim untuk review** berhasil.
5. **Simpan draft** boleh dilakukan sebelum semua isian lengkap. **Kirim untuk review** memerlukan waktu aktual dan hasil layanan. Semua bahan boleh kosong. Jika satu angka pada roll ribbon diisi, lengkapi angka awal/akhir roll tersebut sebelum mengirim. Waktu selesai aktual tidak boleh berada di masa depan. Laporan mendukung event yang selesai lewat tengah malam.
6. **Coordinator yang ditugaskan atau Administrator** membuka laporan, kemudian memilih **Terima laporan** atau **Minta revisi**. Catatan wajib jika meminta revisi. PIC mendapat notifikasi dan dapat memperbaiki laporan; draft revisi tetap berstatus Perlu revisi sampai dikirim ulang.
7. Setelah diterima, klik **Tandai clear**, kemudian isi penilaian Crew/PIC pada tab Performance seperti sebelumnya. Penerimaan laporan tidak langsung menandai event clear.

Event, jadwal, tim, kendaraan, serta driver ditampilkan otomatis. Angka ribbon dari versi lama menjadi isian awal untuk laporan baru. Input pemakaian bahan sekarang hanya berada di Penutupan. Waktu aktual tetap diisi PIC. Nama event, jadwal, tim, dan kendaraan disalin saat laporan dikirim agar coordinator melihat konteks saat pengiriman.

## Aturan penyimpanan dan akses

- Laporan yang dikirim terkunci sampai coordinator meminta revisi. Laporan diterima dan laporan event yang sudah clear/dibatalkan tidak dapat diedit.
- Penambahan/penghapusan anggota tim terkunci selama laporan berstatus Menunggu review atau Diterima. Koreksi tim dilakukan pada tahap revisi sebelum laporan diterima. Pengaturan skill tetap mengikuti hak akses yang sudah ada.
- Setiap aksi laporan mencatat akun, waktu, versi, dan catatan review. Server menolak penyimpanan dari versi lama untuk mencegah laporan tertimpa saat dibuka pada dua tab.
- Foto disimpan privat dalam database SQLite dan hanya dapat dibuka oleh akun yang berhak membaca event. Backup database mencakup foto penutupan. Penghapusan event melalui utilitas reset juga menghapus laporan, riwayat, dan foto terkait.
- Draft belum disimpan tetap tersedia saat berpindah tab dalam sesi halaman yang sama; muat ulang halaman atau logout menghapus draft lokal tersebut. Gunakan **Simpan draft** sebelum meninggalkan aplikasi.
- Event lama yang sudah clear tetap mempertahankan evaluasinya dan tidak diwajibkan membuat laporan secara retroaktif. Event berstatus Terjadwal harus melalui laporan diterima sebelum Clear.
- Notifikasi mengarahkan PIC ke laporan yang belum lengkap/perlu revisi, dan coordinator ke laporan menunggu review atau siap Clear. Laporan yang belum selesai tetap diingatkan meski event lebih dari 30 hari yang lalu.

Checklist barang kembali tetap menggunakan CRM. Koordinasi/konfirmasi crew tetap melalui grup WhatsApp atau aplikasi komunikasi yang sudah dipakai, tanpa tambahan konfirmasi crew dalam aplikasi ini.

## Pengaruh pada export Excel

Sheet **Events** tetap menggunakan 21 kolom dan urutan file acuan. Tidak ada kolom baru.

- Sebelum laporan diterima, export mempertahankan angka Ribbon Awal/Akhir historis dari versi lama bila tersedia. Event baru tanpa laporan diterima menampilkan kolom ribbon kosong.
- Setelah laporan diterima, **Ribbon Awal** adalah jumlah sisa awal semua roll, **Ribbon Akhir** adalah jumlah sisa akhir semua roll, dan **Total Penggunaan** adalah selisih kedua total tersebut. Rumus Excel tetap `Ribbon Awal − Ribbon Akhir`.
- Contoh dua roll: roll 1 `60 → 0`, roll 2 `400 → 300`. Export menghasilkan awal **460**, akhir **300**, pemakaian **160**. Notes mencantumkan rincian per roll, jumlah cetak/gagal, kendala, penanganan, dan tindak lanjut. Notes dari Logistik tetap disertakan.
- Bahan yang tidak diisi tetap kosong; angka nol yang sengaja diisi tetap nol. Frame, lensa lenticular, magnet, dan keychain yang diisi dicantumkan dalam Notes. Format tanggal, nomor telepon sebagai teks, status event, serta cakupan coordinator tetap seperti versi sebelumnya.

## Memasang pembaruan

1. Hentikan aplikasi dan cadangkan database serta folder upload yang sudah digunakan.
2. Salin kode dalam paket ini ke folder instalasi yang sama. Pastikan file baru `closing_reports.py`, `static/closing-ui.js`, dan `static/closing.css` ikut disalin. Pertahankan `.env`, database, volume Docker, serta folder upload lama.
3. Untuk Docker, jalankan `docker compose up -d --build captureit-ops` dari folder/project Compose lama. Untuk Python, jalankan kembali `python3 server.py`. Tabel laporan, foto, dan riwayat dibuat otomatis saat startup; tidak ada paket runtime tambahan.
4. Muat ulang halaman browser. **Tidak perlu menjalankan reset data** dan jangan menghapus volume database.

## Verifikasi

- **43 pengujian backend lulus**: alur aplikasi lama serta laporan draft, kirim, revisi, penerimaan, Clear, role/penugasan, versi bersamaan, foto privat, rollback isian gagal, notifikasi, dan export beberapa roll.
- Pemeriksaan upgrade mempertahankan 13 akun, 5 event, 14 penugasan, dan 3 evaluasi pada database contoh lama; integritas dan foreign key SQLite valid.
- Sintaks Python/JavaScript dan hasil render template form diperiksa, termasuk PIC, review baca saja, revisi, tombol Clear, angka nol, perhitungan multi-roll, konteks tersimpan, dan escaping HTML.
- Uji interaksi browser desktop/mobile, kamera/pemilihan foto, dan tampilan visual belum dijalankan karena executable browser tidak tersedia di lingkungan pengujian. Saat mencoba paket, jalankan alur PIC → review → revisi → diterima → Clear dan buka foto menggunakan akun yang sesuai.

Jalankan ulang pengujian dari folder aplikasi:

```bash
python3 -m unittest discover -s tests -q
```
