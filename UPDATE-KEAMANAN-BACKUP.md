# Update: keamanan login, backup harian, notifikasi sync gagal, event manual

## 1. Pembatasan login
- 5 password salah untuk satu email dalam 15 menit mengunci login email itu selama 15 menit. Selama terkunci, password yang benar pun ditolak (HTTP 429, pesan "Coba lagi dalam N menit").
- Email yang tidak terdaftar dihitung sama, sehingga respons tidak membocorkan akun mana yang ada.
- Satu alamat IP yang gagal 30 kali (ke akun mana pun) dalam 15 menit juga dikunci.
- Login berhasil menghapus hitungan gagal untuk email itu. Saat kunci pertama kali aktif, tercatat di audit log (`login_locked`).
- Kunci hanya sementara. Tidak ada akun yang terkunci permanen.
- Atur lewat `.env` (opsional): `LOGIN_MAX_FAILURES=5`, `LOGIN_IP_MAX_FAILURES=30`, `LOGIN_LOCK_MINUTES=15`.
- Di belakang reverse proxy (nginx/Cloudflare) tambahkan `TRUST_PROXY=1`, kalau tidak semua pengguna terlihat berasal dari IP proxy. Pastikan proxy menambahkan `X-Forwarded-For`.

## 2. Backup
`backup.py` membuat satu file `backups/ops-backup-TANGGAL-JAM.zip` berisi salinan database yang konsisten (aman saat server berjalan), seluruh folder foto/dokumen privat, dan manifest berisi checksum dan jumlah data. Setelah dibuat, backup langsung diperiksa seperti saat restore; backup yang tidak bisa dipulihkan dihapus dan skrip berhenti dengan pesan error.

| Perintah | Fungsi |
|---|---|
| `python backup.py` | buat backup sekarang (Windows: klik `04_BACKUP.bat`) |
| `python backup.py list` | daftar backup |
| `python backup.py verify [FILE]` | buktikan backup bisa dipulihkan (terbaru jika FILE kosong) |
| `python backup.py restore FILE --yes` | pulihkan (Windows: `06_RESTORE_BACKUP.bat`). **Matikan server dulu.** Database lama disimpan sebagai `ops.sqlite3.before-restore-...`; file foto yang lebih baru tidak ditimpa. |

**Jadwal harian (Windows):** klik `05_JADWALKAN_BACKUP_HARIAN.bat`, pilih jam (komputer harus menyala pada jam itu). Hasil dicatat di `backups\backup.log`. Linux/VPS: `0 2 * * * cd /path/captureit-ops && python3 backup.py`. Docker: jalankan lewat `docker compose exec -T captureit-ops python backup.py` dengan `BACKUP_DIR=/data/backups` di `.env`.

Pengaturan `.env` (opsional): `BACKUP_DIR` (sebaiknya disk lain), `BACKUP_COPY_DIR` (salinan kedua, mis. folder Google Drive/flashdisk), `BACKUP_KEEP_DAYS=14` (backup lebih lama dihapus; 3 terbaru selalu disimpan).

**Penting:** backup di disk yang sama dengan database tidak melindungi dari disk rusak/hilang. Isi `BACKUP_COPY_DIR` atau pindahkan folder `backups` ke tempat lain secara berkala, dan uji restore sesekali.

## 3. Notifikasi sync Google gagal
- Saat sync (manual atau otomatis) gagal, semua Administrator aktif menerima notifikasi berisi alasan dari Google. Maksimal satu notifikasi per hari.
- Saat sync berhasil lagi setelah gagal, ada satu notifikasi "pulih".
- Koordinator dan crew tidak menerima notifikasi ini.

## 4. Event manual (tanpa Google Calendar)
- Di halaman Jadwal Event ada panel **+ Buat event manual** (untuk yang punya hak menjadwalkan: Event Coordinator, Head Operations, Administrator). Isi judul, waktu mulai dan selesai (maks. 14 hari), lokasi, dan kode project (kosong = kode sementara OPS-...).
- Event manual diberi label "Manual" dan tidak pernah diubah oleh sync Calendar. Event lebih dari 24 jam otomatis multi-hari, jadi mapping crew per hari dan koreksi absensi bisa diuji. Tanggal di masa lalu boleh, untuk menguji koreksi absensi.
- **Hanya Administrator** yang dapat menghapus event manual (tombol di panel event), dan hanya jika belum masuk batch payroll. Event dari Calendar tidak dapat dihapus lewat fitur ini.
- Event uji coba ikut dihitung di laporan dan payroll seperti event biasa. Hapus setelah selesai menguji.

## Tes
`python -m unittest discover -s tests` dan `node --test tests/*.cjs`. Tes baru: `tests/test_security_backup.py` (20 tes), `tests/test_multiday_ui.cjs`.
