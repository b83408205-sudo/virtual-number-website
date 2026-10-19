import os
import sqlite3
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
DB_PATH = os.environ.get("DATABASE_PATH", "store.db")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country TEXT NOT NULL, phone TEXT NOT NULL UNIQUE,
            price REAL NOT NULL, available INTEGER NOT NULL DEFAULT 1
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number_id INTEGER NOT NULL, customer_name TEXT NOT NULL,
            customer_contact TEXT NOT NULL, payment_method TEXT NOT NULL,
            receipt_file TEXT, status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY(number_id) REFERENCES numbers(id)
        )""")

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/")
def index():
    with db() as con:
        numbers = con.execute("SELECT * FROM numbers WHERE available=1 ORDER BY id DESC").fetchall()
    return render_template("index.html", numbers=numbers)

@app.route("/order/<int:number_id>", methods=["GET", "POST"])
def order(number_id):
    with db() as con:
        number = con.execute("SELECT * FROM numbers WHERE id=? AND available=1", (number_id,)).fetchone()
    if not number:
        abort(404)
    if request.method == "POST":
        name = request.form.get("customer_name", "").strip()
        contact = request.form.get("customer_contact", "").strip()
        method = request.form.get("payment_method", "")
        receipt = request.files.get("receipt")
        if not name or not contact or method not in ("JazzCash", "Easypaisa"):
            flash("تمام معلومات درست درج کریں۔")
            return render_template("order.html", number=number)
        receipt_name = None
        if receipt and receipt.filename:
            if not allowed_file(receipt.filename):
                flash("رسید JPG, PNG, WEBP یا PDF میں اپلوڈ کریں۔")
                return render_template("order.html", number=number)
            receipt_name = secrets.token_hex(12) + "_" + secure_filename(receipt.filename)
            receipt.save(os.path.join(app.config["UPLOAD_FOLDER"], receipt_name))
        with db() as con:
            cur = con.execute("""INSERT INTO orders
                (number_id, customer_name, customer_contact, payment_method, receipt_file, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'Pending', ?)""",
                (number_id, name, contact, method, receipt_name, datetime.utcnow().isoformat(timespec="seconds")))
            order_id = cur.lastrowid
        return render_template("order_done.html", order_id=order_id)
    return render_template("order.html", number=number)

@app.route("/admin", methods=["GET"])
@admin_required
def admin():
    with db() as con:
        numbers = con.execute("SELECT * FROM numbers ORDER BY id DESC").fetchall()
        orders = con.execute("""SELECT orders.*, numbers.country, numbers.phone, numbers.price
            FROM orders JOIN numbers ON orders.number_id=numbers.id ORDER BY orders.id DESC""").fetchall()
    return render_template("admin.html", numbers=numbers, orders=orders)

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            flash("ہوسٹنگ میں ADMIN_PASSWORD environment variable سیٹ کریں۔")
        elif secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["admin"] = True
            return redirect(url_for("admin"))
        else:
            flash("پاس ورڈ درست نہیں۔")
    return render_template("login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

@app.route("/admin/add-number", methods=["POST"])
@admin_required
def add_number():
    country = request.form.get("country", "").strip()
    phone = request.form.get("phone", "").strip()
    try:
        price = float(request.form.get("price", ""))
        if price < 0: raise ValueError()
    except ValueError:
        flash("قیمت درست درج کریں۔")
        return redirect(url_for("admin"))
    if not country or not phone:
        flash("ملک اور نمبر دونوں درج کریں۔")
        return redirect(url_for("admin"))
    try:
        with db() as con:
            con.execute("INSERT INTO numbers(country, phone, price, available) VALUES (?, ?, ?, 1)",
                        (country, phone, price))
        flash("نمبر شامل ہو گیا۔")
    except sqlite3.IntegrityError:
        flash("یہ نمبر پہلے سے موجود ہے۔")
    return redirect(url_for("admin"))

@app.route("/admin/toggle-number/<int:number_id>", methods=["POST"])
@admin_required
def toggle_number(number_id):
    with db() as con:
        con.execute("UPDATE numbers SET available=CASE available WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (number_id,))
    flash("نمبر کی دستیابی اپڈیٹ ہو گئی۔")
    return redirect(url_for("admin"))

@app.route("/admin/order/<int:order_id>/<status>", methods=["POST"])
@admin_required
def update_order(order_id, status):
    if status not in ("Approved", "Rejected"):
        abort(400)
    with db() as con:
        con.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    flash("آرڈر کی حالت اپڈیٹ ہو گئی۔")
    return redirect(url_for("admin"))

@app.route("/admin/receipt/<path:filename>")
@admin_required
def receipt(filename):
    # Only authenticated admins can access uploaded receipts.
    from flask import send_from_directory
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)

@app.errorhandler(413)
def too_large(_):
    return "فائل 5 MB سے چھوٹی ہونی چاہیے۔", 413

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
