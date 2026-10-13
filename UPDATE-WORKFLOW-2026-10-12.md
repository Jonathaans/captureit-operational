# Sales, inventory, dan perbaikan operasional

## Alur yang tersedia

1. **Sales Pipeline**: buat beberapa pipeline, tambah lead, geser kartu di desktop atau lewat pegangan kartu di HP. Tahap juga bisa dipilih melalui detail lead. Sales Staff hanya mendapat lead dan dokumen miliknya; pemeriksaan berlaku di server, termasuk URL cetak dan surat jalan.
2. **Quote → Proforma Invoice → Invoice → SPK**: dokumen berikutnya dibuat dari dokumen sumber yang sudah diterbitkan untuk lead yang sama. Item, jumlah, hari, harga, diskon, dan pajak nominal tersalin. Total dihitung server. Nomor otomatis tersedia; draft bisa diedit, isi dokumen terbit dikunci, status pembayaran dicatat pada invoice/proforma. Tombol **Cetak** mendukung Save as PDF dari browser.
3. **SPK → event → Surat Jalan**: jadwalkan event dari SPK dengan tanggal/jam WIB, lalu pilih alat dan kuantitas untuk Surat Jalan. Event yang sudah ada juga bisa ditautkan. SPK, lead, event, dan daftar alat tetap terhubung. Rilis SJ mengunci daftar dan memesan stok agar tidak dialokasikan berlebihan.
4. **Inventory & asset**: inventory memakai SKU/barcode dan kuantitas. Asset memakai kode unik setiap unit dan serial number opsional. Tersedia label QR, scanner USB/keyboard, kamera, dan scan dari foto. Scan keluar/kembali wajib memilih SJ yang sudah dirilis dan barang yang tercantum; pengembalian tidak bisa melebihi barang yang keluar. Scan berulang dengan request yang sama tidak menggandakan stok.
5. **Dashboard Sales**: pipeline, jumlah/nilai lead, per sales, nilai SPK, invoice terbit/lunas/belum lunas, dan jatuh tempo. **Dashboard Inventory**: stok, stok minimum, asset per unit, barang belum kembali, dan riwayat transaksi.

Pembuatan dokumen dan perubahan status tidak mengirim email ke customer. Pajak diisi sebagai nominal sesuai dokumen bisnis; tidak ada perhitungan tarif pajak otomatis.

## Empat perbaikan operasional

- PIC dapat menyimpan draft sebelum event selesai dan mengirim report setelah jam selesai event, tanpa menunggu status Clear. Report lama yang tertinggal pada event sudah Clear dapat dilengkapi oleh PIC. Laporan diterima tetap terkunci.
- Setelah coordinator menerima report dan absensi selesai, penilaian/finish dapat menyelesaikan event dalam satu transaksi; tidak perlu Clear terlebih dahulu untuk membuka form penilaian.
- Calendar menjadi sumber tanggal/judul/lokasi. Jam operasional dimasukkan manual oleh Event Coordinator. Sync berikutnya tidak menimpa jadwal yang sudah diisi. Event yang baru mendapat kode tetapi belum diisi jamnya ditandai **Isi jam manual**, dan belum dapat menerima mapping crew.
- Crew/PIC dengan absensi masuk, pulang, tidak hadir, atau riwayat koreksi tidak dapat dihapus dari jadwal. Event manual yang memiliki absensi juga tidak dapat dihapus. Penguncian berlaku untuk single-day dan multi-day.
- **Kode CRM** tampil sebagai kalender bulanan; klik event untuk mengisi kode/jam atau membuka detail event. Kartu pada **Board Design** tetap membuka detail event dan brief desain.

## Akses

| Role | Akses tambahan |
| --- | --- |
| Administrator | Semua modul dan pengaturan role |
| Admin Sales | Semua lead/dokumen/pipeline, dashboard sales, baca inventory, buat/rilis SJ |
| Sales Staff | Lead/dokumen sendiri dan baca SJ terkait lead sendiri |
| Kepala Gudang | Master inventory/asset, scan, stok, dan SJ |
| Staff Gudang | Baca inventory/SJ, scan keluar/kembali sesuai SJ |
| Event Coordinator | Kalender CRM, jam manual, mapping, review report dan finish |
| PIC | Report event yang ditugaskan dan absensi sendiri |

Role yang sebelumnya diubah manual di Configure mempertahankan ACL kustom. Administrator dapat memilih **Kembalikan ke default** untuk role tersebut bila ingin memakai matriks default terbaru. Role bawaan yang belum dikustomisasi diperbarui otomatis.

## Data lama dan penggunaan scan

Migrasi berjalan saat startup, menambahkan tabel/kolom/index tanpa mereset akun, event, absensi, payroll, atau stok. Sebelum deploy, skrip mencadangkan seluruh volume `/data` beserta upload dan SQLite WAL.

Asset lama yang masih berupa stok agregat perlu diberi kode melalui **+ Unit asset → Beri kode untuk stok lama**. Opsi ini tidak menambah stok; jumlah unit terdaftar dibatasi stok yang tersedia. Untuk asset baru, stok awal otomatis dibuatkan kode per unit.

Live camera memerlukan HTTPS dan izin kamera. Bila kamera belum tersedia, gunakan **Scan dari foto**, scanner USB, atau input kode. Hasil scan perlu dikonfirmasi bersama arah transaksi dan SJ sebelum disimpan.

## Deploy setelah merge

Log VPS sebelumnya menunjukkan `git pull --ff-only` gagal karena riwayat branch berbeda. Docker yang dijalankan setelah kegagalan itu membangun kode lama. Respons HTTP 200 belum membuktikan revisi terbaru telah terpasang.

Setelah PR pembaruan ini di-merge ke `main`, jalankan satu blok berikut di VPS:

```bash
bash -se <<'BASH'
cd /var/www/captureit-operational
git fetch origin main
git show origin/main:deploy/update-vps.sh > /tmp/captureit-update.sh
OPS_SOURCE_ROOT="$PWD" bash /tmp/captureit-update.sh
BASH
```

Skrip berhenti pada kegagalan, memverifikasi volume existing, membuat worktree bersih dari `origin/main`, menyalin `.env`, membangun image, menjalankan backup, lalu memperbarui hanya service `captureit-ops` dengan project Compose tetap `captureit-operational`. Branch dan perubahan lokal VPS tetap ada. Health check memverifikasi **SHA commit** yang sedang berjalan, bukan hanya HTTP 200. Hasil sukses menampilkan `Deploy OK`, `Revision`, dan lokasi folder rilis.

Gunakan folder rilis tersebut untuk perintah Compose/log berikutnya. Simpan perubahan `.env` di folder sumber awal agar ikut deployment berikutnya. Sesudah deploy, login ulang dan lakukan refresh halaman untuk memuat menu terbaru.

## Verifikasi dan batas kapasitas

```bash
python3 -m pip install -r requirements-server.txt
python3 tests/run_wsgi_suite.py
node --test tests/test_*.cjs
bash -n deploy/update-vps.sh
```

Tes backend mencakup role, isolasi antar-sales, rantai dokumen, stok/reservasi, race scan asset, report/finish, jadwal manual, dan absensi. Tes deploy memakai repository Git yang benar-benar divergen dengan Docker simulasi; tes tersebut tidak mengubah VPS. Browser smoke tersedia di `tests/browser_sales_server.py` dan `tests/browser_sales_smoke.cjs` untuk data sementara, desktop, serta viewport HP.

Arsitektur deployment masih satu aplikasi Waitress dengan SQLite dan volume persisten. Pengembangan modul ini belum merupakan uji beban skala besar atau migrasi ke banyak server. Profil kapasitas dan database perlu dievaluasi berdasarkan jumlah user/event/transaksi nyata sebelum menjalankan beberapa replica.
