"""
ReyMarket - Backend (Tahap 1: API produk, Tahap 2: akun & login, Tahap 3: admin, Tahap 4: bug report + Gmail, Tahap 5: jual produk & keranjang, Tahap 6: pesanan & pengiriman)

Cara jalan di Termux:
    pip install flask
    python app.py
Lalu buka di browser HP:  http://127.0.0.1:5000
"""

import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
from datetime import timedelta
from email.message import EmailMessage
from functools import wraps

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "reymarket.db")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")    # file HTML/CSS/JS frontend
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")   # gambar produk (nanti)

# static_folder=None: kita atur sendiri file mana yang boleh dibuka,
# supaya app.py dan reymarket.db TIDAK bisa diunduh lewat browser.
app = Flask(__name__, static_folder=None)


def load_secret_key():
    """Kunci rahasia untuk menandatangani cookie login.
    Dibuat otomatis sekali, disimpan di secret.key (JANGAN dibagikan)."""
    path = os.path.join(BASE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    with open(path) as f:
        return f.read().strip()


app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,     # cookie tidak bisa dibaca JavaScript
    SESSION_COOKIE_SAMESITE="Lax",    # cookie tidak ikut dari situs lain
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,   # batas total request: 4 MB
)


# ------------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------------
def get_db():
    """Satu koneksi database per request."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# Data awal = produk yang tadinya ditulis manual di welcome.html
SEED_PRODUCTS = [
    ("Produk 1", 10000, "Deskripsi produk pertama.", "gambar1.jpg"),
    ("Produk 2", 15000, "Deskripsi produk kedua.", "gambar2.jpg"),
    ("rehan", 20000, "Deskripsi produk ketiga.", "gambar3.jpg"),
    ("Produk 4", 25000, "Deskripsi produk keempat.", "gambar4.jpg"),
    ("Produk 5", 30000, "Deskripsi produk kelima.", "gambar5.jpg"),
    ("Produk 6", 35000, "Deskripsi produk keenam.", "gambar6.jpg"),
]


def init_db():
    """Buat tabel kalau belum ada, isi produk awal kalau masih kosong."""
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            price       INTEGER NOT NULL CHECK (price >= 0),
            description TEXT    NOT NULL DEFAULT '',
            image       TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
            email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    NOT NULL,
            description TEXT    NOT NULL,
            screenshot  TEXT    NOT NULL DEFAULT '',
            status      TEXT    NOT NULL DEFAULT 'baru',
            user_id     INTEGER,
            created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Migrasi (aman untuk database lama): kolom pemilik produk
    kolom = [r[1] for r in db.execute("PRAGMA table_info(products)")]
    if "user_id" not in kolom:
        db.execute("ALTER TABLE products ADD COLUMN user_id INTEGER")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS uploads (
            name       TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cart_items (
            user_id    INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity   INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 99),
            added_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, product_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id     INTEGER NOT NULL,
            seller_id    INTEGER,
            status       TEXT    NOT NULL DEFAULT 'menunggu',
            subtotal     INTEGER NOT NULL,
            ongkir       INTEGER NOT NULL,
            total        INTEGER NOT NULL,
            courier      TEXT    NOT NULL,
            ship_name    TEXT    NOT NULL,
            ship_phone   TEXT    NOT NULL,
            ship_address TEXT    NOT NULL,
            note         TEXT    NOT NULL DEFAULT '',
            resi         TEXT,
            shipped_at   INTEGER,
            created_at   INTEGER NOT NULL,
            updated_at   INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   INTEGER NOT NULL,
            product_id INTEGER,
            name       TEXT    NOT NULL,
            price      INTEGER NOT NULL,
            image      TEXT    NOT NULL DEFAULT '',
            quantity   INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS order_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   INTEGER NOT NULL,
            title      TEXT    NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders (buyer_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_orders_seller ON orders (seller_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_order ON order_items (order_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_events_order ON order_events (order_id)")
    jumlah = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if jumlah == 0:
        db.executemany(
            "INSERT INTO products (name, price, description, image) "
            "VALUES (?, ?, ?, ?)",
            SEED_PRODUCTS,
        )
    db.commit()
    db.close()


# Produk selalu diambil bersama nama penjualnya
SQL_PRODUK = (
    "SELECT p.*, u.username AS seller FROM products p "
    "LEFT JOIN users u ON u.id = p.user_id"
)


def product_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "price": row["price"],            # angka rupiah, contoh: 10000
        "description": row["description"],
        "image": row["image"],            # nama file di folder uploads
        "seller_id": row["user_id"],      # None = produk bawaan toko
        "seller": row["seller"] or "ReyMarket",
    }


# ------------------------------------------------------------------
# API PRODUK
# ------------------------------------------------------------------
@app.get("/api/products")
def list_products():
    """Daftar produk.
    /api/products?q=kata   -> cari nama/deskripsi
    /api/products?mine=1   -> hanya produk milikku (wajib login)"""
    q = request.args.get("q", "").strip()
    kondisi, nilai = [], []

    if request.args.get("mine") == "1":
        user = current_user()
        if user is None:
            return error("Silakan login dulu.", 401)
        kondisi.append("p.user_id = ?")
        nilai.append(user["id"])

    if q:
        like = f"%{q}%"
        kondisi.append("(p.name LIKE ? OR p.description LIKE ?)")
        nilai += [like, like]

    sql = SQL_PRODUK
    if kondisi:
        sql += " WHERE " + " AND ".join(kondisi)
    rows = get_db().execute(sql + " ORDER BY p.id", nilai).fetchall()
    return jsonify([product_to_dict(r) for r in rows])


@app.get("/api/products/<int:product_id>")
def get_product(product_id):
    row = ambil_produk(product_id)
    if row is None:
        return jsonify({"error": "Produk tidak ditemukan."}), 404
    return jsonify(product_to_dict(row))


# ------------------------------------------------------------------
# API AKUN (Sign Up / Sign In / Sign Out)
# ------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
HASH_METHOD = "pbkdf2:sha256"   # tersedia di semua Python, termasuk Termux

# Hash palsu: dipakai agar waktu respons sama walau email tidak terdaftar
DUMMY_HASH = generate_password_hash("dummy-password", method=HASH_METHOD)

# Pembatas percobaan login gagal (disimpan di memori, reset saat server restart)
MAX_GAGAL = 5
JENDELA_DETIK = 300
gagal_login = {}


def user_to_dict(row):
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def json_body():
    """Ambil JSON dari request. Hanya menerima Content-Type: application/json
    (ini juga melindungi dari form palsu situs lain / CSRF)."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def error(message, status=400, field=None):
    body = {"error": message}
    if field:
        body["field"] = field
    return jsonify(body), status


def terlalu_banyak_gagal(kunci):
    sekarang = time.time()
    percobaan = [t for t in gagal_login.get(kunci, []) if sekarang - t < JENDELA_DETIK]
    gagal_login[kunci] = percobaan
    return len(percobaan) >= MAX_GAGAL


@app.post("/api/register")
def register():
    data = json_body()
    username = str(data.get("username", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not USERNAME_RE.match(username):
        return error("Nama pengguna 3-20 karakter: huruf, angka, atau _.", field="username")
    if len(email) > 254 or not EMAIL_RE.match(email):
        return error("Format email belum benar.", field="email")
    if len(password) < 8:
        return error("Password minimal 8 karakter.", field="password")
    if len(password) > 128:
        return error("Password terlalu panjang (maksimal 128).", field="password")

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        return error("Nama pengguna sudah dipakai.", 409, "username")
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return error("Email sudah terdaftar.", 409, "email")

    try:
        # role SELALU 'user'. Admin tidak bisa dibuat lewat Sign Up.
        cur = db.execute(
            "INSERT INTO users (username, email, password_hash, role) "
            "VALUES (?, ?, ?, 'user')",
            (username, email, generate_password_hash(password, method=HASH_METHOD)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return error("Nama pengguna atau email sudah dipakai.", 409)

    user = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"user": user_to_dict(user)}), 201


@app.post("/api/login")
def login():
    data = json_body()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    kunci = (request.remote_addr, email)
    if terlalu_banyak_gagal(kunci):
        return error("Terlalu banyak percobaan. Coba lagi beberapa menit lagi.", 429)

    user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    hash_dicek = user["password_hash"] if user else DUMMY_HASH
    cocok = check_password_hash(hash_dicek, password)

    if user is None or not cocok:
        gagal_login.setdefault(kunci, []).append(time.time())
        # Pesan sengaja sama, supaya orang tidak bisa menebak email mana yang terdaftar
        return error("Email atau password salah.", 401)

    gagal_login.pop(kunci, None)
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"user": user_to_dict(user)})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    """Siapa yang sedang login? {"user": null} kalau belum login."""
    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({"user": None})
    user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:  # akun sudah dihapus
        session.clear()
        return jsonify({"user": None})
    return jsonify({"user": user_to_dict(user)})


# ------------------------------------------------------------------
# PRODUK: user biasa kelola produk MILIKNYA, admin boleh kelola semua
# ------------------------------------------------------------------
NAMA_GAMBAR_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}\.(jpg|png|webp)$")
MAX_PRODUK_USER = 30          # batas produk per user biasa
MAX_UPLOAD = 20               # batas upload gambar per user
JENDELA_UPLOAD = 600          # per 10 menit
upload_user = {}


def current_user():
    """User yang sedang login, dibaca dari database (bukan dari cookie),
    jadi kalau role diubah, efeknya langsung terasa."""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def login_required(fn):
    @wraps(fn)
    def pembungkus(*args, **kwargs):
        if current_user() is None:
            return error("Silakan login dulu.", 401)
        return fn(*args, **kwargs)
    return pembungkus


def admin_required(fn):
    @wraps(fn)
    def pembungkus(*args, **kwargs):
        user = current_user()
        if user is None:
            return error("Silakan login dulu.", 401)
        if user["role"] != "admin":
            return error("Hanya admin yang boleh melakukan ini.", 403)
        return fn(*args, **kwargs)
    return pembungkus


def ambil_produk(product_id):
    return get_db().execute(SQL_PRODUK + " WHERE p.id = ?", (product_id,)).fetchone()


def boleh_kelola(user, produk):
    """Admin boleh semua. User biasa hanya produk yang dia miliki."""
    if user["role"] == "admin":
        return True
    return produk["user_id"] is not None and produk["user_id"] == user["id"]


def baca_produk(data):
    """Validasi data produk. Return (nilai, None) atau (None, response_error)."""
    name = str(data.get("name", "")).strip()
    description = str(data.get("description", "")).strip()
    image = str(data.get("image", "")).strip()
    price = data.get("price")

    if not name or len(name) > 100:
        return None, error("Nama produk wajib diisi (maksimal 100 karakter).", field="name")
    if isinstance(price, bool) or not isinstance(price, int) or price < 0 or price > 2_000_000_000:
        return None, error("Harga harus angka bulat 0 atau lebih.", field="price")
    if len(description) > 1000:
        return None, error("Deskripsi maksimal 1000 karakter.", field="description")
    if image and not NAMA_GAMBAR_RE.match(image):
        return None, error("Nama file gambar tidak valid.", field="image")

    return (name, price, description, image), None


def gambar_boleh(user, image, produk_lama=None):
    """Gambar hanya boleh: kosong, tidak berubah, atau hasil upload user itu sendiri.
    (Mencegah user memakai/mencuri gambar milik orang lain.)"""
    if not image:
        return True
    if produk_lama is not None and produk_lama["image"] == image:
        return True
    row = get_db().execute(
        "SELECT user_id FROM uploads WHERE name = ?", (image,)
    ).fetchone()
    return row is not None and row["user_id"] == user["id"]


def hapus_file_gambar(nama):
    """Hapus file gambar dari uploads kalau tidak dipakai produk lain."""
    if not nama or not NAMA_GAMBAR_RE.match(nama):
        return
    db = get_db()
    dipakai = db.execute(
        "SELECT (SELECT COUNT(*) FROM products WHERE image = ?) "
        "     + (SELECT COUNT(*) FROM order_items WHERE image = ?)",
        (nama, nama),
    ).fetchone()[0]
    if dipakai == 0:
        try:
            os.remove(os.path.join(UPLOAD_DIR, nama))
        except OSError:
            pass
        db.execute("DELETE FROM uploads WHERE name = ?", (nama,))
        db.commit()


def deteksi_tipe_gambar(header):
    """Tentukan tipe dari ISI file (bukan dari nama file yang bisa dipalsukan)."""
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


@app.post("/api/products")
@login_required
def tambah_produk():
    user = current_user()
    nilai, gagal = baca_produk(json_body())
    if gagal:
        return gagal

    db = get_db()
    if user["role"] != "admin":
        jumlah = db.execute(
            "SELECT COUNT(*) FROM products WHERE user_id = ?", (user["id"],)
        ).fetchone()[0]
        if jumlah >= MAX_PRODUK_USER:
            return error(f"Batas {MAX_PRODUK_USER} produk per akun sudah tercapai.", 400)
    if not gambar_boleh(user, nilai[3]):
        return error("Gambar tidak valid. Upload ulang gambarnya.", field="image")

    # pemilik SELALU orang yang login (tidak bisa diatur dari data kiriman)
    cur = db.execute(
        "INSERT INTO products (name, price, description, image, user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        nilai + (user["id"],),
    )
    db.commit()
    return jsonify(product_to_dict(ambil_produk(cur.lastrowid))), 201


@app.put("/api/products/<int:product_id>")
@login_required
def ubah_produk(product_id):
    user = current_user()
    lama = ambil_produk(product_id)
    if lama is None:
        return error("Produk tidak ditemukan.", 404)
    if not boleh_kelola(user, lama):
        return error("Kamu hanya boleh mengubah produk milikmu sendiri.", 403)

    nilai, gagal = baca_produk(json_body())
    if gagal:
        return gagal
    if not gambar_boleh(user, nilai[3], lama):
        return error("Gambar tidak valid. Upload ulang gambarnya.", field="image")

    db = get_db()
    db.execute(
        "UPDATE products SET name = ?, price = ?, description = ?, image = ? WHERE id = ?",
        nilai + (product_id,),
    )
    db.commit()
    if lama["image"] != nilai[3]:
        hapus_file_gambar(lama["image"])   # gambar lama yang sudah tidak dipakai
    return jsonify(product_to_dict(ambil_produk(product_id)))


@app.delete("/api/products/<int:product_id>")
@login_required
def hapus_produk(product_id):
    user = current_user()
    lama = ambil_produk(product_id)
    if lama is None:
        return error("Produk tidak ditemukan.", 404)
    if not boleh_kelola(user, lama):
        return error("Kamu hanya boleh menghapus produk milikmu sendiri.", 403)

    db = get_db()
    db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    db.execute("DELETE FROM cart_items WHERE product_id = ?", (product_id,))
    db.commit()
    hapus_file_gambar(lama["image"])
    return jsonify({"ok": True})


@app.post("/api/upload")
@login_required
def upload_gambar():
    """Upload gambar produk (jpg/png/webp, maksimal 3 MB). Return nama file."""
    user = current_user()
    sekarang = time.time()
    riwayat = [t for t in upload_user.get(user["id"], []) if sekarang - t < JENDELA_UPLOAD]
    if len(riwayat) >= MAX_UPLOAD:
        return error("Terlalu banyak upload. Coba lagi beberapa menit lagi.", 429)

    file = request.files.get("file")
    if file is None or file.filename == "":
        return error("Pilih file gambar dulu.")

    isi = file.read()
    if len(isi) > 3 * 1024 * 1024:
        return error("File terlalu besar (maksimal 3 MB).", 413)
    tipe = deteksi_tipe_gambar(isi[:16])
    if tipe is None:
        return error("File harus gambar JPG, PNG, atau WEBP.")

    nama = secrets.token_hex(12) + "." + tipe   # nama acak, aman
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(UPLOAD_DIR, nama), "wb") as f:
        f.write(isi)

    db = get_db()
    db.execute("INSERT INTO uploads (name, user_id) VALUES (?, ?)", (nama, user["id"]))
    db.commit()
    riwayat.append(sekarang)
    upload_user[user["id"]] = riwayat
    return jsonify({"image": nama}), 201


# ------------------------------------------------------------------
# KERANJANG: setiap user hanya melihat & mengubah keranjangnya sendiri
# ------------------------------------------------------------------
def ringkasan_keranjang(user_id):
    rows = get_db().execute(
        "SELECT c.product_id, c.quantity, p.name, p.price, p.image, "
        "       p.user_id AS seller_id, u.username AS seller "
        "FROM cart_items c "
        "JOIN products p ON p.id = c.product_id "
        "LEFT JOIN users u ON u.id = p.user_id "
        "WHERE c.user_id = ? ORDER BY c.added_at, c.product_id",
        (user_id,),
    ).fetchall()
    items = [
        {
            "product_id": r["product_id"],
            "name": r["name"],
            "price": r["price"],
            "image": r["image"],
            "seller": r["seller"] or "ReyMarket",
            "seller_id": r["seller_id"],
            "quantity": r["quantity"],
            "subtotal": r["price"] * r["quantity"],
        }
        for r in rows
    ]
    return {
        "items": items,
        "count": sum(i["quantity"] for i in items),
        "total": sum(i["subtotal"] for i in items),
    }


def baca_jumlah(nilai, bawaan=None):
    if nilai is None:
        return bawaan
    if isinstance(nilai, bool) or not isinstance(nilai, int) or not 1 <= nilai <= 99:
        return False
    return nilai


@app.get("/api/cart")
@login_required
def lihat_keranjang():
    return jsonify(ringkasan_keranjang(current_user()["id"]))


@app.post("/api/cart")
@login_required
def tambah_keranjang():
    user = current_user()
    data = json_body()
    product_id = data.get("product_id")
    jumlah = baca_jumlah(data.get("quantity"), 1)

    if isinstance(product_id, bool) or not isinstance(product_id, int):
        return error("Produk tidak valid.")
    if jumlah is False:
        return error("Jumlah harus antara 1 dan 99.", field="quantity")

    produk = ambil_produk(product_id)
    if produk is None:
        return error("Produk tidak ditemukan.", 404)
    if produk["user_id"] == user["id"]:
        return error("Ini produk milikmu sendiri, tidak bisa dimasukkan ke keranjang.")

    db = get_db()
    db.execute(
        "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, product_id) "
        "DO UPDATE SET quantity = MIN(99, quantity + excluded.quantity)",
        (user["id"], product_id, jumlah),
    )
    db.commit()
    return jsonify(ringkasan_keranjang(user["id"])), 201


@app.patch("/api/cart/<int:product_id>")
@login_required
def ubah_keranjang(product_id):
    user = current_user()
    jumlah = baca_jumlah(json_body().get("quantity"))
    if jumlah is None or jumlah is False:
        return error("Jumlah harus antara 1 dan 99.", field="quantity")

    db = get_db()
    cur = db.execute(
        "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ?",
        (jumlah, user["id"], product_id),
    )
    db.commit()
    if cur.rowcount == 0:
        return error("Produk tidak ada di keranjang.", 404)
    return jsonify(ringkasan_keranjang(user["id"]))


@app.delete("/api/cart/<int:product_id>")
@login_required
def hapus_dari_keranjang(product_id):
    user = current_user()
    db = get_db()
    db.execute(
        "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
        (user["id"], product_id),
    )
    db.commit()
    return jsonify(ringkasan_keranjang(user["id"]))


@app.delete("/api/cart")
@login_required
def kosongkan_keranjang():
    user = current_user()
    db = get_db()
    db.execute("DELETE FROM cart_items WHERE user_id = ?", (user["id"],))
    db.commit()
    return jsonify(ringkasan_keranjang(user["id"]))


# ------------------------------------------------------------------
# PESANAN & PENGIRIMAN  (SIMULASI - tidak ada pembayaran / kurir sungguhan)
# ------------------------------------------------------------------
# "detik" = lama setiap tahap perjalanan paket. Ubah angka ini kalau mau
# simulasinya lebih lambat atau lebih cepat.
KURIR = {
    "hemat":   {"nama": "ReyHemat",   "ongkir": 8000,  "detik": 60},
    "reguler": {"nama": "ReyReguler", "ongkir": 15000, "detik": 30},
    "express": {"nama": "ReyExpress", "ongkir": 25000, "detik": 10},
}
TAHAP_PENGIRIMAN = [
    "Paket tiba di hub sortir",
    "Paket dalam perjalanan ke alamat tujuan",
    "Kurir tiba di alamat tujuan",
]
HP_RE = re.compile(r"^\+?[0-9]{8,15}$")
SQL_ORDER = (
    "SELECT o.*, b.username AS buyer, s.username AS seller FROM orders o "
    "JOIN users b ON b.id = o.buyer_id "
    "LEFT JOIN users s ON s.id = o.seller_id"
)


def kini():
    return int(time.time())


def tambah_event(db, order_id, title, waktu=None):
    db.execute(
        "INSERT INTO order_events (order_id, title, created_at) VALUES (?, ?, ?)",
        (order_id, title, waktu if waktu is not None else kini()),
    )


def info_pengiriman(o, sekarang):
    """Posisi paket dihitung dari waktu kirim (tidak perlu proses di latar belakang)."""
    if o["shipped_at"] is None:
        return None
    detik = KURIR[o["courier"]]["detik"]
    jumlah = len(TAHAP_PENGIRIMAN)
    tahap = min(jumlah, max(0, (sekarang - o["shipped_at"]) // detik))
    steps = [
        {"time": o["shipped_at"] + (i + 1) * detik, "title": TAHAP_PENGIRIMAN[i]}
        for i in range(tahap)
    ]
    next_in = None
    if tahap < jumlah:
        next_in = o["shipped_at"] + (tahap + 1) * detik - sekarang
    return {
        "tahap": tahap,
        "total_tahap": jumlah,
        "steps": steps,
        "next_in": next_in,
        "tiba": tahap >= jumlah,
    }


def aksi_tersedia(user, o, info):
    """Aksi apa yang boleh dilakukan user ini pada pesanan ini (sumber kebenaran server)."""
    pembeli = o["buyer_id"] == user["id"]
    penjual = user["role"] == "admin" or (
        o["seller_id"] is not None and o["seller_id"] == user["id"]
    )
    st = o["status"]
    aksi = []
    if penjual and st == "menunggu":
        aksi.append("proses")
    if penjual and st == "diproses":
        aksi.append("kirim")
    if pembeli and st == "dikirim" and info and info["tiba"]:
        aksi.append("terima")
    if (pembeli and st == "menunggu") or (penjual and st in ("menunggu", "diproses")):
        aksi.append("batal")
    return aksi


def boleh_lihat_order(user, o):
    return (
        user["role"] == "admin"
        or o["buyer_id"] == user["id"]
        or (o["seller_id"] is not None and o["seller_id"] == user["id"])
    )


def order_ringkas(o, items):
    pertama = items[0] if items else None
    return {
        "id": o["id"],
        "status": o["status"],
        "total": o["total"],
        "buyer": o["buyer"],
        "seller": o["seller"] or "ReyMarket",
        "courier": KURIR[o["courier"]]["nama"],
        "resi": o["resi"],
        "created_at": o["created_at"],
        "item_count": sum(i["quantity"] for i in items),
        "first_item": pertama["name"] if pertama else "",
        "first_image": pertama["image"] if pertama else "",
        "more_items": max(0, len(items) - 1),
    }


@app.get("/api/couriers")
def daftar_kurir():
    return jsonify([
        {"id": k, "nama": v["nama"], "ongkir": v["ongkir"],
         "estimasi_detik": v["detik"] * len(TAHAP_PENGIRIMAN)}
        for k, v in KURIR.items()
    ])


@app.post("/api/checkout")
@login_required
def checkout():
    """Ubah isi keranjang menjadi pesanan. Satu pesanan per penjual."""
    user = current_user()
    data = json_body()
    name = str(data.get("name", "")).strip()
    phone = re.sub(r"[\s\-]", "", str(data.get("phone", "")))
    address = str(data.get("address", "")).strip()
    note = str(data.get("note", "")).strip()
    courier = data.get("courier")

    if len(name) < 2 or len(name) > 60:
        return error("Nama penerima 2-60 karakter.", field="name")
    if not HP_RE.match(phone):
        return error("Nomor HP 8-15 angka (boleh diawali +).", field="phone")
    if len(address) < 10 or len(address) > 300:
        return error("Alamat lengkap 10-300 karakter.", field="address")
    if len(note) > 200:
        return error("Catatan maksimal 200 karakter.", field="note")
    if courier not in KURIR:
        return error("Pilih kurir pengiriman.", field="courier")

    db = get_db()
    rows = db.execute(
        "SELECT c.product_id, c.quantity, p.name, p.price, p.image, p.user_id AS seller_id "
        "FROM cart_items c JOIN products p ON p.id = c.product_id "
        "WHERE c.user_id = ? ORDER BY c.added_at, c.product_id",
        (user["id"],),
    ).fetchall()
    rows = [r for r in rows if r["seller_id"] != user["id"]]   # produk sendiri tidak ikut
    if not rows:
        return error("Keranjang kosong.")

    kelompok = {}   # seller_id (None = toko) -> daftar barang
    for r in rows:
        kelompok.setdefault(r["seller_id"], []).append(r)

    sekarang = kini()
    ids = []
    try:
        for seller_id, barang in kelompok.items():
            subtotal = sum(b["price"] * b["quantity"] for b in barang)
            ongkir = KURIR[courier]["ongkir"]
            cur = db.execute(
                "INSERT INTO orders (buyer_id, seller_id, status, subtotal, ongkir, total, "
                "courier, ship_name, ship_phone, ship_address, note, created_at, updated_at) "
                "VALUES (?, ?, 'menunggu', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user["id"], seller_id, subtotal, ongkir, subtotal + ongkir, courier,
                 name, phone, address, note, sekarang, sekarang),
            )
            order_id = cur.lastrowid
            db.executemany(
                "INSERT INTO order_items (order_id, product_id, name, price, image, quantity) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(order_id, b["product_id"], b["name"], b["price"], b["image"], b["quantity"])
                 for b in barang],
            )
            tambah_event(db, order_id, "Pesanan dibuat, menunggu konfirmasi penjual", sekarang)
            ids.append(order_id)
        db.execute("DELETE FROM cart_items WHERE user_id = ?", (user["id"],))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({"orders": ids}), 201


@app.get("/api/orders")
@login_required
def daftar_pesanan():
    """?as=buyer (pembelianku, bawaan)  atau  ?as=seller (pesanan masuk untuk produkku)."""
    user = current_user()
    db = get_db()
    if request.args.get("as") == "seller":
        if user["role"] == "admin":
            rows = db.execute(SQL_ORDER + " ORDER BY o.id DESC LIMIT 200").fetchall()
        else:
            rows = db.execute(
                SQL_ORDER + " WHERE o.seller_id = ? ORDER BY o.id DESC LIMIT 200",
                (user["id"],),
            ).fetchall()
    else:
        rows = db.execute(
            SQL_ORDER + " WHERE o.buyer_id = ? ORDER BY o.id DESC LIMIT 200",
            (user["id"],),
        ).fetchall()

    per_order = {}
    if rows:
        ids = [r["id"] for r in rows]
        tanda = ",".join("?" * len(ids))
        for it in db.execute(
            f"SELECT * FROM order_items WHERE order_id IN ({tanda}) ORDER BY id", ids
        ):
            per_order.setdefault(it["order_id"], []).append(it)
    return jsonify([order_ringkas(r, per_order.get(r["id"], [])) for r in rows])


def detail_pesanan(user, o):
    db = get_db()
    sekarang = kini()
    items = db.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (o["id"],)
    ).fetchall()
    info = info_pengiriman(o, sekarang)

    timeline = [
        {"time": e["created_at"], "title": e["title"]}
        for e in db.execute(
            "SELECT * FROM order_events WHERE order_id = ? ORDER BY id", (o["id"],)
        )
    ]
    if info:
        timeline += info["steps"]
    timeline.sort(key=lambda t: t["time"])   # sort stabil: urutan asli tetap terjaga

    hasil = order_ringkas(o, items)
    hasil.update({
        "buyer_id": o["buyer_id"],
        "subtotal": o["subtotal"],
        "ongkir": o["ongkir"],
        "ship_name": o["ship_name"],
        "ship_phone": o["ship_phone"],
        "ship_address": o["ship_address"],
        "note": o["note"],
        "items": [
            {"product_id": i["product_id"], "name": i["name"], "price": i["price"],
             "image": i["image"], "quantity": i["quantity"],
             "subtotal": i["price"] * i["quantity"]}
            for i in items
        ],
        "timeline": timeline,
        "tracking": None if info is None else {
            "tahap": info["tahap"], "total_tahap": info["total_tahap"],
            "next_in": info["next_in"], "tiba": info["tiba"],
        },
        "actions": aksi_tersedia(user, o, info),
        "server_time": sekarang,
    })
    return hasil


@app.get("/api/orders/<int:order_id>")
@login_required
def lihat_pesanan(order_id):
    user = current_user()
    o = get_db().execute(SQL_ORDER + " WHERE o.id = ?", (order_id,)).fetchone()
    # 404 juga untuk pesanan orang lain, agar nomor pesanan tidak bisa ditebak
    if o is None or not boleh_lihat_order(user, o):
        return error("Pesanan tidak ditemukan.", 404)
    return jsonify(detail_pesanan(user, o))


@app.post("/api/orders/<int:order_id>/status")
@login_required
def ubah_status_pesanan(order_id):
    user = current_user()
    db = get_db()
    o = db.execute(SQL_ORDER + " WHERE o.id = ?", (order_id,)).fetchone()
    if o is None or not boleh_lihat_order(user, o):
        return error("Pesanan tidak ditemukan.", 404)

    aksi = json_body().get("action")
    sekarang = kini()
    info = info_pengiriman(o, sekarang)
    if aksi not in aksi_tersedia(user, o, info):
        return error("Aksi ini tidak bisa dilakukan sekarang.", 400)

    pembeli = o["buyer_id"] == user["id"]
    if aksi == "proses":
        db.execute("UPDATE orders SET status='diproses', updated_at=? WHERE id=?", (sekarang, order_id))
        tambah_event(db, order_id, "Pesanan sedang disiapkan penjual", sekarang)
    elif aksi == "kirim":
        resi = "REY" + "".join(secrets.choice("0123456789") for _ in range(10))
        db.execute(
            "UPDATE orders SET status='dikirim', resi=?, shipped_at=?, updated_at=? WHERE id=?",
            (resi, sekarang, sekarang, order_id),
        )
        tambah_event(db, order_id,
                     f"Pesanan dikirim dengan {KURIR[o['courier']]['nama']} (resi {resi})", sekarang)
    elif aksi == "terima":
        db.execute("UPDATE orders SET status='selesai', updated_at=? WHERE id=?", (sekarang, order_id))
        tambah_event(db, order_id, "Pesanan diterima pembeli", sekarang)
    elif aksi == "batal":
        db.execute("UPDATE orders SET status='dibatalkan', updated_at=? WHERE id=?", (sekarang, order_id))
        tambah_event(db, order_id,
                     "Pesanan dibatalkan oleh pembeli" if pembeli else "Pesanan dibatalkan oleh penjual",
                     sekarang)
    db.commit()

    o = db.execute(SQL_ORDER + " WHERE o.id = ?", (order_id,)).fetchone()
    return jsonify(detail_pesanan(user, o))


# ------------------------------------------------------------------
# BUG REPORT: siapa saja boleh kirim, hanya admin yang boleh lihat/kelola
# ------------------------------------------------------------------
REPORT_DIR = os.path.join(BASE_DIR, "report_uploads")   # TIDAK dibuka publik
MAX_GAMBAR = 3 * 1024 * 1024
MAX_LAPORAN = 5            # per IP
JENDELA_LAPORAN = 600      # per 10 menit
laporan_ip = {}


def kirim_email_laporan(laporan, path_gambar):
    """Kirim notifikasi ke Gmail. Dijalankan di thread terpisah.
    Kalau gagal, laporan TETAP tersimpan di database.

    Butuh variabel lingkungan (diatur di Termux, BUKAN di file kode):
        GMAIL_USER          alamat Gmail pengirim
        GMAIL_APP_PASSWORD  Sandi Aplikasi 16 karakter dari Google
        REPORT_TO           (opsional) tujuan email, default = GMAIL_USER
    """
    user = os.environ.get("GMAIL_USER", "").strip()
    sandi = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    tujuan = os.environ.get("REPORT_TO", "").strip() or user
    if not user or not sandi:
        print("[email] GMAIL_USER / GMAIL_APP_PASSWORD belum diatur, email tidak dikirim.")
        return

    try:
        msg = EmailMessage()
        msg["Subject"] = f"[ReyMarket] Bug report #{laporan['id']}"
        msg["From"] = user
        msg["To"] = tujuan
        msg["Reply-To"] = laporan["email"]
        msg.set_content(
            f"Laporan bug baru #{laporan['id']}\n"
            f"Dari   : {laporan['email']}\n"
            f"Waktu  : {laporan['created_at']} (UTC)\n\n"
            f"{laporan['description']}\n\n"
            "Kelola laporan di Panel Admin ReyMarket."
        )
        if path_gambar and os.path.isfile(path_gambar):
            ext = path_gambar.rsplit(".", 1)[1]
            with open(path_gambar, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype="image",
                    subtype="jpeg" if ext == "jpg" else ext,
                    filename=f"bukti-{laporan['id']}.{ext}",
                )
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(user, sandi)
            server.send_message(msg)
        print(f"[email] Laporan #{laporan['id']} terkirim ke {tujuan}")
    except Exception as e:  # jaringan mati, sandi salah, dll.
        print(f"[email] Gagal kirim laporan #{laporan['id']}: {e}")


def laporan_to_dict(row):
    return {
        "id": row["id"],
        "email": row["email"],
        "description": row["description"],
        "has_screenshot": bool(row["screenshot"]),
        "status": row["status"],
        "username": row["username"],
        "created_at": row["created_at"],
    }


@app.post("/api/reports")
def kirim_laporan():
    ip = request.remote_addr
    sekarang = time.time()
    riwayat = [t for t in laporan_ip.get(ip, []) if sekarang - t < JENDELA_LAPORAN]
    if len(riwayat) >= MAX_LAPORAN:
        return error("Terlalu banyak laporan. Coba lagi beberapa menit lagi.", 429)

    email = request.form.get("email", "").strip().lower()
    deskripsi = request.form.get("description", "").strip()

    if len(email) > 254 or not EMAIL_RE.match(email):
        return error("Format email belum benar.", field="email")
    if len(deskripsi) < 10:
        return error("Deskripsi minimal 10 karakter.", field="description")
    if len(deskripsi) > 2000:
        return error("Deskripsi maksimal 2000 karakter.", field="description")

    nama_gambar = ""
    file = request.files.get("screenshot")
    if file is not None and file.filename:
        isi = file.read()
        if len(isi) > MAX_GAMBAR:
            return error("Gambar terlalu besar (maksimal 3 MB).", 413, "screenshot")
        tipe = deteksi_tipe_gambar(isi[:16])
        if tipe is None:
            return error("File harus gambar JPG, PNG, atau WEBP.", field="screenshot")
        os.makedirs(REPORT_DIR, exist_ok=True)
        nama_gambar = secrets.token_hex(12) + "." + tipe
        with open(os.path.join(REPORT_DIR, nama_gambar), "wb") as f:
            f.write(isi)

    db = get_db()
    cur = db.execute(
        "INSERT INTO reports (email, description, screenshot, user_id) VALUES (?, ?, ?, ?)",
        (email, deskripsi, nama_gambar, session.get("user_id")),
    )
    db.commit()
    row = db.execute(
        "SELECT r.*, NULL AS username FROM reports r WHERE r.id = ?", (cur.lastrowid,)
    ).fetchone()

    riwayat.append(sekarang)
    laporan_ip[ip] = riwayat

    path_gambar = os.path.join(REPORT_DIR, nama_gambar) if nama_gambar else ""
    threading.Thread(
        target=kirim_email_laporan,
        args=(laporan_to_dict(row), path_gambar),
        daemon=True,
    ).start()

    return jsonify({"ok": True}), 201


@app.get("/api/reports")
@admin_required
def daftar_laporan():
    rows = get_db().execute(
        "SELECT r.*, u.username FROM reports r "
        "LEFT JOIN users u ON u.id = r.user_id ORDER BY r.id DESC"
    ).fetchall()
    return jsonify([laporan_to_dict(r) for r in rows])


@app.patch("/api/reports/<int:report_id>")
@admin_required
def ubah_status_laporan(report_id):
    status = str(json_body().get("status", ""))
    if status not in ("baru", "selesai"):
        return error("Status harus 'baru' atau 'selesai'.", field="status")
    db = get_db()
    cur = db.execute("UPDATE reports SET status = ? WHERE id = ?", (status, report_id))
    db.commit()
    if cur.rowcount == 0:
        return error("Laporan tidak ditemukan.", 404)
    return jsonify({"ok": True, "status": status})


@app.delete("/api/reports/<int:report_id>")
@admin_required
def hapus_laporan(report_id):
    db = get_db()
    row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        return error("Laporan tidak ditemukan.", 404)
    db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    db.commit()
    if row["screenshot"] and NAMA_GAMBAR_RE.match(row["screenshot"]):
        try:
            os.remove(os.path.join(REPORT_DIR, row["screenshot"]))
        except OSError:
            pass
    return jsonify({"ok": True})


@app.get("/api/reports/<int:report_id>/screenshot")
@admin_required
def screenshot_laporan(report_id):
    row = get_db().execute(
        "SELECT screenshot FROM reports WHERE id = ?", (report_id,)
    ).fetchone()
    if row is None or not row["screenshot"]:
        return error("Gambar tidak ada.", 404)
    resp = send_from_directory(REPORT_DIR, row["screenshot"])
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.errorhandler(413)
def terlalu_besar(_error):
    return error("File terlalu besar (maksimal 3 MB).", 413)


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Endpoint tidak ditemukan."}), 404
    return "Halaman tidak ditemukan.", 404


# ------------------------------------------------------------------
# HALAMAN WEB (frontend)
# ------------------------------------------------------------------
@app.get("/")
def home():
    return send_from_directory(PUBLIC_DIR, "welcome.html")


@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.get("/<path:filename>")
def public_file(filename):
    # send_from_directory menolak path berbahaya seperti ../app.py
    return send_from_directory(PUBLIC_DIR, filename)


# ------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    init_db()
    # 127.0.0.1 = hanya bisa dibuka dari HP ini sendiri (aman)
    app.run(host="127.0.0.1", port=5000, debug=False)
