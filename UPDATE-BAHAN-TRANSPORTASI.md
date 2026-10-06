# Pembaruan bahan dan transportasi — 30 September 2026

Paket aplikasi lengkap ini melanjutkan versi laporan penutupan. Data dan alur review/Clear yang sudah ada tetap digunakan.

## Pemakaian bahan hanya pada Penutupan

Tab **Transportasi & Detail** tidak lagi memiliki input ribbon. Semua bahan diisi oleh PIC pada tab **Penutupan**, di bagian **Pemakaian bahan**:

| Bahan | Cara mengisi | Wajib? |
| --- | --- | --- |
| Ribbon | Buka bagian Ribbon, tambah roll jika dipakai, isi sisa kapasitas cetak awal dan akhir | Opsional |
| Frame | Jumlah terpakai, dalam pcs | Opsional |
| Lensa lenticular | Jumlah terpakai, dalam pcs | Opsional |
| Magnet | Jumlah terpakai, dalam pcs | Opsional |
| Keychain | Jumlah terpakai, dalam pcs | Opsional |

Semua bahan boleh kosong saat mengirim laporan. Rekap cetak/gagal juga opsional dan ditempatkan dalam bagian yang dapat dibuka saat diperlukan. Angka nol disimpan sebagai nol; kolom kosong tetap kosong. Jika mulai mengisi satu roll ribbon, lengkapi kedua angka awal/akhir sebelum mengirim. Draft boleh menyimpan rincian yang belum lengkap.

Angka ribbon dari instalasi lama tetap tersimpan dan menjadi isian awal laporan baru, sehingga tidak perlu diketik ulang. Angka pada laporan yang sudah diterima tetap menjadi sumber export.

## Transportasi event

Menu **Transportasi Event** dan tab **Transportasi & Detail** mendukung pilihan yang bisa digabung:

| Pilihan | Isian |
| --- | --- |
| Kendaraan operasional | Ketik nama, misalnya Luxio. Driver/PIC perjalanan dan perkiraan kembali opsional. |
| Motor masing-masing | Cukup centang; tidak perlu membuat daftar motor atau mengisi driver. |
| Lalamove / kurir | Nama layanan, default Lalamove; detail pesanan/kiriman opsional. |
| Lainnya | Keterangan, misalnya taksi online atau kendaraan client. |

Contoh: centang **Motor masing-masing** dan **Lalamove / kurir** untuk tim yang berangkat memakai motor sementara alat dikirim kurir. Catatan perjalanan dapat dipakai untuk instruksi tambahan.

Pemeriksaan benturan jadwal berlaku pada unit kendaraan operasional. Motor masing-masing dan layanan kurir tidak dianggap sebagai satu armada bersama, sehingga event yang bersamaan tetap bisa memakai pilihan tersebut. Menghapus pilihan kendaraan operasional melepaskan booking unit itu.

Form memakai kartu transportasi, jadwal loading yang dapat dibuka bila diperlukan, dan bagian client/layanan terpisah. Daftar master armada berada dalam bagian **Kelola armada operasional** yang dapat dibuka pada halaman Transportasi Event.

## Penyimpanan satu kali

Ketik kendaraan → klik **Simpan perubahan**. Form mengirim satu permintaan simpan, menonaktifkan klik ganda selama proses, dan memakai nilai yang dikembalikan server. Setelah berhasil, status menjadi **Tersimpan** tanpa memuat ulang isi drawer. Nama baru otomatis tercatat sebagai armada.

Jika server menolak isian atau koneksi gagal, input tetap tampil bersama pesan kesalahan. Draft isian juga dipertahankan saat pindah tab pada sesi halaman yang sama. Draft yang belum disimpan tidak bertahan setelah reload/logout.

Gejala dua kali simpan pada browser pengguna belum dapat direproduksi langsung di lingkungan ini. Pengujian perubahan mencakup POST pertama yang langsung menyimpan kendaraan baru, respons server, pembacaan ulang melalui sesi baru, pencegahan klik ganda, dan input tetap ada setelah error.

## Export tetap mengikuti template

Sheet **Events** tetap memiliki 21 kolom acuan.

- **Vehicle Name** menampilkan gabungan transportasi, misalnya `Motor masing-masing + Lalamove` atau `Luxio + Motor masing-masing`.
- **Driver Name** boleh kosong. Vendor sewa kendaraan tetap mengikuti armada yang dipakai.
- Kolom ribbon mengambil jumlah seluruh roll dari laporan yang diterima. Jika ribbon tidak diisi, kolom ribbon kosong. Angka historis dari versi lama tetap tersedia sebelum ada laporan diterima yang menggantikannya.
- Pemakaian frame, lensa lenticular, magnet, dan keychain tercantum pada **Notes** setelah laporan diterima. Catatan perjalanan, detail kiriman, dan catatan event juga disertakan.

## Memasang paket

1. Hentikan aplikasi dan cadangkan database serta folder upload yang digunakan.
2. Salin seluruh kode dari paket ini ke folder instalasi yang sama, termasuk `server.py`, `operations.py`, `closing_reports.py`, `schema.sql`, dan seluruh file di `static`. Pertahankan `.env`, database, volume Docker, dan folder upload lama.
3. Jalankan kembali server. Untuk Docker: `docker compose up -d --build captureit-ops` dari folder/project Compose lama. Kolom transportasi ditambahkan otomatis saat startup; tidak ada paket runtime baru.
4. Lakukan hard refresh browser agar form baru dimuat. **Tidak perlu menjalankan reset database.**

## Hasil pengujian

- 43 tes backend lulus, mencakup alur lama, bahan kosong/nol, export, satu kali simpan kendaraan, transportasi gabungan, benturan armada, dan rollback form tidak valid.
- Tes logika JavaScript/form lulus untuk satu permintaan per simpan, pencegahan klik ganda, input tetap ada setelah error, draft antar-tab, bahan opsional, serta tampilan baca saja saat review.
- Upgrade diuji dari kode ZIP sebelumnya: 13 akun, 5 event, 14 penugasan, dan 3 evaluasi tetap ada. Mapping Luxio lama serta ribbon 400 → 102 tetap tersimpan dan muncul sebagai isian awal Penutupan. Integritas dan foreign key SQLite valid.
- Uji interaksi dan tampilan dalam browser desktop/mobile belum dijalankan karena executable browser tidak tersedia di lingkungan pengujian.

Untuk menjalankan tes backend:

```bash
python3 -m unittest discover -s tests -q
```
