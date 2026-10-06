# Pembaruan: riwayat gaji dan peringatan jadwal

Tanggal rilis: 1 Oktober 2026 (WIB). Paket ini lengkap dan melanjutkan ZIP QA pra-VPS sebelumnya.

## Riwayat fee Crew/PIC

Buka **Tarif & Skill**, pilih akun, isi fee, tanggal berlaku, dan alasan perubahan
(opsional), lalu simpan. Tombol **Riwayat** tersedia pada setiap baris akun.

Riwayat menampilkan nominal sebelumnya → nominal baru, selisih, tanggal berlaku,
alasan, pengubah, serta waktu pencatatan dalam WIB. Angka turun, naik, maupun nol
tetap tercatat. Koreksi pada tanggal yang sama menambah catatan baru; versi yang
digantikan ditandai **Dikoreksi**. Permintaan identik untuk nominal dan tanggal
yang sama tidak menambah catatan duplikat. Untuk mengganti alasan tanpa perubahan
angka, saat ini tidak ada fitur edit catatan lama.

Tarif bertanggal masa depan terlihat sebagai **Terjadwal** dan belum mengganti
tarif aktif hari ini. Urutan daftar adalah urutan pencatatan, sehingga perubahan
berlaku surut tidak menyembunyikan urutan tindakan sebenarnya.

Nilai penugasan yang sudah memiliki snapshot serta payroll yang sudah diekspor
tidak dihitung ulang karena perubahan tarif. Penugasan baru tanpa tarif disimpan
dengan snapshot nol. Penugasan sangat lama yang belum memiliki snapshot masih
mengikuti mekanisme legacy: tarif akun yang berlaku pada tanggal event. Tinjau
tarif legacy tersebut sebelum export, terutama jika memasukkan koreksi mundur.

## Riwayat gaji In-house

Buka **Payroll In-house**. Sunting nominal akun yang perlu diubah, pilih
**Berlaku mulai**, isi alasan opsional, kemudian **Simpan perubahan gaji**.
Hanya akun yang disunting yang dikirim. Tombol **Riwayat** membuka daftar
perubahan per orang; tidak ada menu gaji baru pada direktori crew.

Tarif aktif mengikuti tanggal hari ini. Pratinjau/draft payroll baru memakai
tarif yang berlaku pada **tanggal mulai periode payroll**, tanpa prorata otomatis.
Contoh: gaji baru berlaku 15 Februari, maka periode mulai 1 Februari masih
memakai tarif lama; periode mulai 1 Maret memakai tarif baru. Penyesuaian dalam
periode dapat dicatat Finance pada tunjangan/potongan sesuai keputusan internal.

Mengubah gaji tidak otomatis mengubah draft yang sudah disimpan. Draft yang
belum dibayar mengambil perhitungan terbaru hanya setelah Finance menyimpan
ulang payroll. Payout dan slip yang sudah dicatat ditransfer tetap memakai
snapshot lama, termasuk jika ada koreksi tanggal berlaku surut.

Untuk mengoreksi tarif yang dijadwalkan menjadi sama dengan tarif aktif hari
ini, sunting kembali angka akun tersebut dan pilih tanggal berlaku tarif yang
dikoreksi. Ini tetap dapat dicatat meskipun angka akhirnya sama dengan tarif
aktif yang terlihat.

## Akses dan data lama

| Jenis riwayat | Izin yang diperlukan | Role bawaan |
|---|---|---|
| Fee Crew/PIC | `fees.manage` | Administrator, Head Operations, Head Finance |
| Gaji In-house | `inhouse_payroll.manage` | Administrator, Finance, Head Finance |

Administrator dapat menyesuaikan izin role melalui Configure. Endpoint riwayat
memeriksa izin server, termasuk ketika URL dipanggil langsung. Coordinator,
Crew/PIC, dan Admin Finance tidak otomatis mendapat akses histori gaji. Modal
riwayat dibersihkan saat ditutup/logout; respons terlambat dari permintaan lama
tidak ditampilkan pada akun berikutnya.

Migrasi otomatis membuat saldo awal dari data yang benar-benar masih tersedia:

- Fee: nominal dan tanggal berlaku dari setiap baris tarif lama.
- Gaji In-house: nilai terakhir, pengubah/waktu yang tersedia, dengan tanggal berlaku **tidak diketahui**.
- Saldo awal diberi label **Saldo awal migrasi**; nilai sebelum catatan itu tidak dikarang. Audit lama tetap ada, tetapi riwayat lengkap yang dahulu tertimpa tidak direkonstruksi.
- Saldo gaji migrasi dipakai sebagai fallback periode tanpa tarif bertanggal untuk mempertahankan perilaku sebelumnya. Finance perlu meninjau nominal untuk periode historis itu.
- Initialization berulang tidak menggandakan saldo awal. Reset operasional tetap menyimpan riwayat kompensasi.

Tabel riwayat bersifat append-only dengan penjagaan database terhadap update dan
delete. Identitas/nama pengubah disimpan saat perubahan dicatat; perubahan nama
akun kemudian tidak mengganti nama dalam riwayat lama. Nominal dan riwayat
ditulis dalam transaksi yang sama, sehingga kegagalan satu baris membatalkan
seluruh batch gaji tersebut.

## Penjadwalan tim

| Kondisi akun yang dipilih | Peringatan | Cara menyimpan |
|---|---|---|
| Tidak ada event lain pada hari yang sama | Normal | Simpan penugasan |
| Ada event lain pada hari yang sama, jam berbeda | Kuning | Tetap Jadwalkan |
| Jam pelaksanaan event beririsan | Merah | Tetap Jadwalkan |

Peringatan menampilkan event lain, kode project, lokasi, jam mulai/selesai, dan
peran orang tersebut. **Ganti Orang** mengosongkan pilihan staff. Jika peringatan
sudah tampil saat memilih staff, satu klik **Tetap Jadwalkan** langsung
mengonfirmasi sekaligus menyimpan; tidak ada checkbox atau dialog konfirmasi
kedua. Jika peringatan baru diketahui saat menekan Simpan, pengguna perlu
membacanya dahulu lalu menekan Tetap Jadwalkan.

- Pemeriksaan memakai akun/ID orang, bukan kesamaan teks nama.
- Loading, waktu kembali kendaraan, serta minimum jeda perjalanan tidak ikut menghitung peringatan tim. Field jeda perjalanan dihapus dari form penugasan.
- Aturan konflik kendaraan tetap berjalan terpisah; perubahan ini khusus penjadwalan manusia.
- Tanggal/jam dihitung dalam WIB. Event lewat tengah malam diperiksa pada setiap hari yang benar-benar ditempati; event yang berakhir tepat 00.00 tidak menempati hari berikutnya.
- Penugasan event yang dibatalkan atau kehadiran yang ditandai tidak hadir tidak menjadi peringatan. Beberapa peran pada event yang sama tidak dihitung sebagai beberapa event.
- Peringatan tetap terlihat di tab **Crew & Absensi** dan notifikasi sesuai cakupan akun. Tidak ada dashboard availability tambahan.
- Kedua tingkat peringatan dapat dikonfirmasi. Aturan lain tetap berlaku: hak akses, akun aktif, event dibatalkan/clear, serta tim yang sudah terkunci oleh laporan penutupan.

Server memeriksa ulang saat transaksi simpan. Jika ada coordinator lain mengubah
jadwal di antara preview dan save, peringatan baru ditampilkan untuk dikonfirmasi.
Klik ganda tidak membuat penugasan ganda. Konfirmasi menyimpan actor, waktu,
signature, dan snapshot rincian peringatan dalam assignment/audit log, tanpa
mengirim pesan WhatsApp otomatis.

## Hasil pengujian

| Suite | Tes lulus |
|---|---:|
| Alur aplikasi utama | 20 |
| Operasional/transportasi/export/notifikasi | 11 |
| Penutupan event | 12 |
| QA keamanan/input/sesi dan alur utuh | 21 |
| Penjadwalan sesuai aturan baru | 14 |
| Riwayat fee/gaji | 12 |
| **Backend** | **90** |
| Draft perangkat | 14 |
| Render halaman/tab 14 role dan WIB | 17 |
| Komponen konfirmasi jadwal dan riwayat | 12 |
| **JavaScript** | **43** |

Total **133 tes otomatis lulus**. JavaScript memakai VM/DOM tiruan dan data API
lokal, bukan browser visual atau perangkat nyata. Tes mencakup kenaikan/penurunan,
zero, koreksi berulang, tarif masa depan, tanggal efektif, pagination riwayat,
transaksi bersamaan, pembatasan akses, snapshot payroll, kuning/merah yang dapat
dikonfirmasi, loading fleksibel, lintas tengah malam, dan respons jaringan lama.

Migrasi tambahan memakai database sintetis dari ZIP QA sebelumnya: baris pada
44 tabel data lama tetap utuh (di luar tabel marker migrasi), 14 saldo awal
contoh diimpor sekali, namespace draft tidak berubah, integritas/foreign key
bersih, dan reset pada database sementara mempertahankan histori gaji.

Jalankan ulang dari folder aplikasi:

```bash
python3 -m unittest discover -s tests -v
node --test --test-reporter=tap tests/test_drafts.cjs tests/test_frontend.cjs tests/test_schedule_history_ui.cjs
```

## Upgrade dan batas pengujian

1. Hentikan aplikasi dan backup database, file privat, branding, serta `.env` sebelum upgrade. Jangan menghapus volume Docker atau menjalankan reset data.
2. Ganti kode menggunakan isi paket lengkap ini. Pertahankan `.env` dan lokasi database/file privat yang lama. Pastikan file baru `compensation.py` dan `static/compensation-ui.js` ikut tersalin.
3. Jalankan aplikasi; migrasi otomatis berjalan saat initialization. Pengguna Docker perlu rebuild image karena modul baru ditambahkan ke Dockerfile.
4. Coba perubahan gaji pada akun uji, koreksi tanggal sama, dan satu penugasan ganda. Pastikan tombol Riwayat serta Tetap Jadwalkan berfungsi sebelum memakai data nyata.

Uji browser/HP nyata, kamera/GPS, HTTPS VPS, build Docker di VPS, serta deployment
server produksi belum dijalankan pada putaran ini. Penghalang produksi dari
`QA-PRA-VPS.md` tetap berlaku; patch ini tidak mengganti server HTTP atau membuat
APK. Tampilan mempunyai aturan responsif, tetapi belum dinyatakan lolos UAT
visual pada perangkat. Paket ini siap untuk staging dan validasi operasional.
