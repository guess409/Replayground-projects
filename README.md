# ReyMarket

## Struktur
- app.py          -> backend (Flask + SQLite)
- public/         -> semua file frontend (HTML) - edit di Acode
- uploads/        -> gambar produk (gambar1.jpg, dst)
- reymarket.db    -> database, dibuat otomatis saat app.py pertama dijalankan

## Jalankan di Termux
    cd ~/Shoping
    pip install flask
    python app.py

Buka di browser HP: http://127.0.0.1:5000
Berhenti: tekan CTRL + C di Termux.

## Akun (Tahap 2)
- Sign Up / Sign In / Sign Out sudah memakai backend (/api/register, /api/login, /api/logout, /api/me).
- Password disimpan sebagai hash, bukan teks asli.
- secret.key dibuat otomatis (kunci cookie login). JANGAN dibagikan atau di-upload.
- Kalau secret.key dihapus, semua user otomatis ter-logout (akun tetap aman).
