# Pembaruan jadwal Crew/PIC dan draft otomatis — 30 September 2026

Paket lengkap ini melanjutkan versi bahan dan transportasi. Dua prioritas tambahan: mencegah penugasan yang bentrok dan memulihkan isian laporan penutupan.

**Catatan versi 1 Oktober 2026:** aturan penjadwalan dalam dokumen ini sudah diganti oleh `UPDATE-HISTORI-GAJI-JADWAL.md`. Hari sama/jam beririsan kini berupa peringatan yang dapat dikonfirmasi, tanpa loading atau batas perjalanan. Penjelasan draft penutupan di bawah tetap berlaku.

## Pemeriksaan jadwal Crew/PIC

Pada tab **Tim**, pilih akun dan isi **Jeda perjalanan (menit)**. Nilai awal 60 menit; dapat diubah dari 0 sampai 720 menit sesuai kebutuhan lokasi. Ini merupakan minimum yang ditentukan coordinator, bukan perhitungan rute atau lalu lintas.

| Kondisi | Perilaku aplikasi |
| --- | --- |
| Jadwal tersedia | Penugasan dapat disimpan langsung. |
| Jam tugas bertumpang tindih | Penugasan diblokir. Pilih staff lain atau sesuaikan jadwal. |
| Jeda antar-event kurang dari minimum | Tampilkan event, lokasi, jam, dan selisih waktunya. Coordinator perlu mencentang bahwa waktu perjalanan sudah diperiksa. |
| Jadwal/lokasi berubah setelah pengecekan | Server memeriksa ulang; persetujuan atas rincian lama tidak berlaku untuk peringatan baru. |

Waktu tugas dimulai dari jam loading bila lebih awal daripada jam event, sampai jam selesai event. Jadwal loading event dianggap berlaku bagi tim event tersebut. Perkiraan kembali kendaraan tidak memperpanjang waktu tugas Crew/PIC. Jika dua penugasan memakai minimum perjalanan berbeda, nilai terbesar dipakai.

Pemeriksaan mencakup penugasan Crew maupun PIC orang yang sama, termasuk event lintas tanggal dan zona waktu. Event dibatalkan dan penugasan berstatus **Tidak hadir** tidak memblokir penugasan lain. Pemeriksaan dilakukan kembali dalam transaksi ketika menyimpan, sehingga dua coordinator tidak dapat membuat penugasan tumpang tindih secara bersamaan.

Bila jadwal event yang sudah memiliki tim berubah dan menimbulkan tumpang tindih, peringatan muncul pada tab Tim. Lonceng notifikasi juga menampilkan bentrok untuk event mendatang dalam rentang notifikasi 30 hari. Crew/PIC hanya melihat bentrok miliknya; pengelola penugasan dapat melihat bentrok tim. Perubahan jadwal tidak menghapus penugasan lama secara otomatis. Konfirmasi kepada tim tetap dapat dilakukan melalui WhatsApp seperti alur sebelumnya.

## Draft laporan penutupan otomatis

Isian PIC pada tab **Penutupan** disimpan otomatis di browser/perangkat yang dipakai. Draft dipisahkan menurut instalasi aplikasi, akun, dan event. Menutup tab atau keluar akun tidak menghapus draft perangkat; login kembali dengan akun yang sama untuk memulihkannya.

- Teks, waktu, bahan, dan rincian ribbon disimpan saat input berubah. Angka nol dan bahan kosong tetap dibedakan.
- Foto baru diperkecil, lalu disimpan di penyimpanan browser terpisah. Tunggu hingga status penyimpanan selesai sebelum menutup halaman.
- Status di atas form membedakan **tersimpan di perangkat**, **offline**, dan **tersimpan di server**. Jika penyimpanan browser penuh/dibatasi atau foto belum tersimpan, aplikasi menampilkan kegagalan tersebut.
- **Simpan draft ke server** menyimpan laporan agar tersedia di perangkat lain. **Kirim untuk review** tetap merupakan tindakan PIC; aplikasi tidak mengirim laporan otomatis.
- Jika koneksi gagal saat menyimpan ke server, isian perangkat tetap tersedia. Bila server sudah menerima perubahan tetapi respons tidak sampai, versi server yang lebih baru akan memicu pilihan pemulihan, bukan dikirim ulang otomatis.
- Jika versi server berubah, aplikasi menampilkan isi draft perangkat untuk diperiksa. PIC dapat memilih **Pulihkan draft ke form** atau **Gunakan versi server**. Laporan yang sedang direview/diterima tetap terkunci.
- Bila tab browser lain memperbarui draft, muncul pilihan isian yang ingin dilanjutkan. Selesainya permintaan simpan di satu tab tidak menghapus draft yang lebih baru di tab lain.
- Foto draft yang tidak tersedia ditandai dan perlu ditambahkan ulang atau dihapus sebelum laporan disimpan; foto tidak dibuang diam-diam.
- Logout menunggu foto selesai diproses/disimpan. Jika draft gagal tersimpan atau pilihan antar-tab belum diselesaikan, dialog meminta penyelesaian draft dahulu agar isian tidak hilang.

Draft perangkat mengikuti browser, profil, dan alamat aplikasi yang sama. Menghapus data situs, memakai mode privat, atau berganti perangkat dapat menghilangkan akses ke draft tersebut. Simpan ke server untuk penggunaan lintas perangkat. Paket ini belum menyediakan pemuatan seluruh aplikasi saat offline: jika halaman sudah ditutup, koneksi diperlukan untuk membuka aplikasi/login kembali, kemudian draft dipulihkan. Draft Transportasi & Detail tetap sebatas sesi halaman seperti versi sebelumnya.

Semua pemakaian bahan tetap opsional dan hanya di Penutupan. Export masih mengikuti template Events 21 kolom; checklist barang kembali tetap mengikuti alur CRM yang sudah dipakai.

## Memasang pembaruan

1. Hentikan aplikasi dan cadangkan database serta folder upload lama.
2. Salin seluruh isi paket ke instalasi lama, termasuk modul baru `staffing.py`, `schema.sql`, dan seluruh berkas `static`. Pertahankan `.env`, database, folder upload, dan volume Docker.
3. Jalankan kembali server. Untuk Docker, jalankan `docker compose up -d --build captureit-ops` dari folder/project Compose lama. Migrasi menambahkan kolom jeda perjalanan dan penanda persetujuan pada penugasan, serta identitas penyimpanan draft. Akun, penugasan, laporan, dan konfigurasi tetap digunakan.
4. Lakukan hard refresh browser. **Tidak perlu reset database.** Python tetap tanpa dependensi pihak ketiga; Node hanya diperlukan bila ingin menjalankan tes JavaScript.

## Verifikasi

- 54 tes backend: alur lama, pemeriksaan bentrok/ACL, loading, perjalanan, tengah malam, perubahan lokasi, transaksi bersamaan, notifikasi, dan migrasi idempoten.
- 14 tes JavaScript: pemulihan setelah halaman baru, pemisahan akun/instalasi, foto, kegagalan penyimpanan, offline, konflik versi/tab, pemulihan eksplisit, retensi draft saat koneksi gagal/logout, dan respons pengecekan staff yang terlambat.
- Pemeriksaan regresi form transportasi dan bahan, termasuk satu kali simpan Luxio, tetap dijalankan.
- Tes JavaScript memakai lingkungan DOM/penyimpanan tiruan di Node. Interaksi dan tampilan langsung pada browser desktop/HP belum diuji karena executable browser tidak tersedia di lingkungan ini.

```bash
python3 -m unittest discover -s tests -q
node --test tests/test_drafts.cjs
```
