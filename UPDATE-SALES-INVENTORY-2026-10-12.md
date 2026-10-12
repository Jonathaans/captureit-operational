# Update Sales Pipeline, Inventory, dan Surat Jalan

Tanggal: 2026-10-12

## Ringkasan

Update ini menambahkan fondasi scale-up aplikasi operasional ke area Sales, Inventory, Asset, dan Surat Jalan.

Fitur utama:

- Sales Pipeline berbentuk board kartu yang bisa digeser.
- Tahap default: Leads, Quote, Proforma Invoice, Invoice, Surat Perintah Kerja.
- Multi pipeline, sehingga perusahaan bisa membuat lebih dari satu alur sales.
- Sales Staff hanya melihat dan mengubah lead miliknya sendiri.
- Administrator, Head Operations, Head Finance, dan Finance dapat melihat pipeline sesuai permission role.
- Dashboard Admin Sales untuk ringkasan jumlah lead, nilai estimasi, dan posisi pipeline.
- Master Inventory dan Asset.
- Scan keluar, masuk, kembali, dan adjustment barang.
- Surat Jalan yang bisa dibuat dari Event atau SPK.
- Surat Jalan berisi daftar alat yang akan dibawa ke event dan terhubung ke scan inventory.
- Dashboard inventory menampilkan stok rendah dan riwayat scan.

## Role dan Hak Akses

Default role baru:

- Administrator: semua akses.
- Sales Staff: melihat dan mengelola lead miliknya sendiri, serta melihat Surat Jalan.
- Head Operations: melihat semua sales, dashboard sales, inventory, scan, dan surat jalan.
- Head Finance: melihat semua sales dan dashboard sales.
- Finance: melihat semua sales.
- Warehouse Head: mengelola inventory, scan, dan surat jalan.
- Warehouse Staff: scan inventory dan melihat surat jalan.

Jika role pernah di-custom lewat menu Configure, cek kembali permission baru ini:

- `sales.read_own`
- `sales.read_all`
- `sales.manage_own`
- `sales.manage_all`
- `sales.pipeline.manage`
- `sales.dashboard`
- `inventory.read`
- `inventory.manage`
- `inventory.scan`
- `delivery_orders.read`
- `delivery_orders.manage`

## Catatan Data

Schema baru otomatis dibuat saat aplikasi start. Tidak perlu menjalankan migrate manual.

Tabel baru:

- `sales_pipelines`
- `sales_stages`
- `sales_leads`
- `sales_documents`
- `inventory_locations`
- `inventory_items`
- `inventory_movements`
- `delivery_orders`
- `delivery_order_items`

## Deploy ke VPS

Jalankan dari terminal VPS sebagai root:

```bash
cd /var/www/captureit-operational
bash deploy/backup-vps.sh
git pull --ff-only origin main
docker compose -p captureit-operational config --quiet
docker compose -p captureit-operational up -d --build --no-deps captureit-ops
docker compose -p captureit-operational logs --tail=40 captureit-ops
```

Jangan menjalankan `docker compose down -v` karena dapat menghapus volume data.

## Test

Sudah diverifikasi:

- `python3 -m py_compile server.py sales_inventory.py reset_operational_data.py`
- `node --check static/app.js`
- `node --check static/sales-inventory-ui.js`
- 184 Python tests non-production
- 71 frontend tests

Catatan: `tests/test_production.py` tidak dijalankan di environment lokal ini karena package `waitress` tidak tersedia di luar Docker. Production image tetap meng-install `waitress==3.0.2` dari `requirements-server.txt`.

