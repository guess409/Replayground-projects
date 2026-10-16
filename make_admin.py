"""
Jadikan sebuah akun sebagai admin (atau cabut kembali).
Admin TIDAK bisa dibuat lewat Sign Up, hanya lewat script ini di Termux.

Pakai (daftar dulu lewat Sign Up, lalu):
    python make_admin.py namapengguna            -> jadikan admin
    python make_admin.py namapengguna --cabut    -> kembalikan jadi user biasa
    python make_admin.py --daftar                -> lihat semua akun
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reymarket.db")


def main():
    if not os.path.exists(DB_PATH):
        print("Database belum ada. Jalankan 'python app.py' dulu sekali.")
        return

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    db = sqlite3.connect(DB_PATH)

    if args[0] == "--daftar":
        for uid, username, email, role in db.execute(
            "SELECT id, username, email, role FROM users ORDER BY id"
        ):
            print(f"{uid:>3}  {username:<20} {email:<30} {role}")
        return

    username = args[0]
    role = "user" if "--cabut" in args else "admin"

    cur = db.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
    db.commit()

    if cur.rowcount == 0:
        print(f"Akun '{username}' tidak ditemukan. Daftar dulu lewat Sign Up.")
    else:
        print(f"Berhasil: '{username}' sekarang berperan '{role}'.")


if __name__ == "__main__":
    main()

