"""
ReyMarket - Backend (Tahap 1: API produk, Tahap 2: akun & login)

Cara jalan di Termux:
    pip install flask
    python app.py
Lalu buka di browser HP:  http://127.0.0.1:5000
"""

import os
import re
import secrets
import sqlite3
import time
from datetime import timedelta

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
    jumlah = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if jumlah == 0:
        db.executemany(
            "INSERT INTO products (name, price, description, image) "
            "VALUES (?, ?, ?, ?)",
            SEED_PRODUCTS,
        )
    db.commit()
    db.close()


def product_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "price": row["price"],            # angka rupiah, contoh: 10000
        "description": row["description"],
        "image": row["image"],            # nama file di folder uploads
    }


# ------------------------------------------------------------------
# API PRODUK
# ------------------------------------------------------------------
@app.get("/api/products")
def list_products():
    """Daftar produk. Opsional: /api/products?q=kata  (cari nama/deskripsi)"""
    q = request.args.get("q", "").strip()
    db = get_db()

    if q:
        like = f"%{q}%"
        rows = db.execute(
            "SELECT * FROM products "
            "WHERE name LIKE ? OR description LIKE ? ORDER BY id",
            (like, like),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM products ORDER BY id").fetchall()

    return jsonify([product_to_dict(r) for r in rows])


@app.get("/api/products/<int:product_id>")
def get_product(product_id):
    row = get_db().execute(
        "SELECT * FROM products WHERE id = ?", (product_id,)
    ).fetchone()

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
