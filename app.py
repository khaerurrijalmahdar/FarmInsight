import os
import math
from datetime import datetime, date, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash


# =========================
# App config
# =========================
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "ganti-secret-acak-yang-panjang")

DB_URL = os.getenv("DATABASE_URL", "sqlite:///farm.db")
app.config["SQLALCHEMY_DATABASE_URI"] = DB_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# (opsional) bantu SQLite di environment multi-thread (tetap 1 writer at a time)
if DB_URL.startswith("sqlite"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"check_same_thread": False}}

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


# =========================
# Models
# =========================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="admin")

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(200), nullable=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)  # Telur / Ikan Nila / Umum
    default_unit = db.Column(db.String(20), default="unit")


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tgl = db.Column(db.Date, nullable=False, default=date.today)
    tipe = db.Column(db.String(3), nullable=False)  # IN / OUT
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    deskripsi = db.Column(db.String(200), default="")
    qty = db.Column(db.Float, default=0.0)          # Telur: rak, Nila: kg
    unit = db.Column(db.String(20), default="unit")
    unit_price = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)

    product = db.relationship("Product")


class Pond(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)

    # kolam bulat
    shape = db.Column(db.String(20), default="circular")  # circular
    diameter_m = db.Column(db.Float, default=3.0)
    water_depth_m = db.Column(db.Float, default=1.0)

    # kapasitas berbasis volume (editable)
    stocking_rate_fish_per_m3 = db.Column(db.Float, default=150.0)   # ekor / m³
    biomass_capacity_kg_per_m3 = db.Column(db.Float, default=10.0)   # kg / m³

    def volume_m3(self) -> float:
        r = (self.diameter_m or 0) / 2.0
        h = (self.water_depth_m or 0)
        return math.pi * (r ** 2) * h

    def capacity_fish_count(self) -> int:
        return int(round(self.volume_m3() * (self.stocking_rate_fish_per_m3 or 0)))

    def capacity_biomass_kg(self) -> float:
        return float(self.volume_m3() * (self.biomass_capacity_kg_per_m3 or 0))


class FishEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pond_id = db.Column(db.Integer, db.ForeignKey("pond.id"), nullable=False)
    tgl = db.Column(db.Integer, nullable=False, default=lambda: int(date.today().strftime("%Y%m%d")))
    # STOCK / HARVEST / MORTALITY
    event_type = db.Column(db.String(12), nullable=False)
    count = db.Column(db.Integer, default=0)        # ekor
    weight_kg = db.Column(db.Float, default=0.0)    # kg (opsional)
    note = db.Column(db.String(200), default="")

    pond = db.relationship("Pond")

    @property
    def tgl_date(self) -> date:
        s = str(self.tgl)
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


class Flock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, default="Flok 1")
    start_date = db.Column(db.Date, default=date.today)
    initial_count = db.Column(db.Integer, default=0)


class ChickenDailyLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    flock_id = db.Column(db.Integer, db.ForeignKey("flock.id"), nullable=False)
    tgl = db.Column(db.Date, nullable=False, default=date.today)
    eggs_count = db.Column(db.Integer, default=0)   # butir
    dead_count = db.Column(db.Integer, default=0)
    note = db.Column(db.String(200), default="")

    flock = db.relationship("Flock")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# =========================
# Helpers
# =========================
def parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None

def parse_int(s, default=0):
    try:
        return int(float(s))
    except Exception:
        return default

def parse_float(s, default=0.0):
    try:
        return float(s)
    except Exception:
        return default

def get_setting(key: str, default: str):
    s = Setting.query.filter_by(key=key).first()
    return s.value if s else default

def set_setting(key: str, value: str):
    s = Setting.query.filter_by(key=key).first()
    if not s:
        s = Setting(key=key, value=value)
        db.session.add(s)
    else:
        s.value = value


# =========================
# Init command
# =========================
@app.cli.command("init-db")
def init_db():
    db.create_all()

    # Seed products
    seeds = [("Telur", "rak"), ("Ikan Nila", "kg"), ("Umum", "unit")]
    for name, unit in seeds:
        if not Product.query.filter_by(name=name).first():
            db.session.add(Product(name=name, default_unit=unit))

    # Default setting: 30 butir / rak
    if not Setting.query.filter_by(key="EGGS_PER_RACK").first():
        db.session.add(Setting(key="EGGS_PER_RACK", value="30"))

    # Seed admin
    if not User.query.filter_by(username="admin").first():
        u = User(username="admin")
        u.set_password("admin123")  # ganti setelah login
        db.session.add(u)

    # Seed 6 kolam bulat kalau belum ada
    if Pond.query.count() == 0:
        for i in range(1, 7):
            db.session.add(Pond(
                name=f"Kolam {i}",
                shape="circular",
                diameter_m=3.0,
                water_depth_m=1.0,
                stocking_rate_fish_per_m3=150.0,
                biomass_capacity_kg_per_m3=10.0
            ))

    # Seed 1 flok default
    if Flock.query.count() == 0:
        db.session.add(Flock(name="Flok 1", initial_count=0))

    db.session.commit()
    print("✅ DB siap. Login: admin / admin123")


# =========================
# Auth routes
# =========================
@app.get("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.post("/login")
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    u = User.query.filter_by(username=username).first()
    if not u or not u.check_password(password):
        flash("Login gagal. Cek username/password.", "danger")
        return redirect(url_for("login"))
    login_user(u)
    return redirect(url_for("dashboard"))

@app.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# =========================
# Pages
# =========================
@app.get("/")
def home():
    return redirect(url_for("dashboard"))

@app.get("/dashboard")
@login_required
def dashboard():
    today = date.today()
    start_7d = today - timedelta(days=6)

    # totals
    total_in = db.session.query(db.func.coalesce(db.func.sum(Transaction.total), 0.0))\
        .filter(Transaction.tipe == "IN").scalar()
    total_out = db.session.query(db.func.coalesce(db.func.sum(Transaction.total), 0.0))\
        .filter(Transaction.tipe == "OUT").scalar()
    profit = (total_in or 0) - (total_out or 0)

    # eggs per rack
    eggs_per_rack = parse_int(get_setting("EGGS_PER_RACK", "30"), 30)

    telur = Product.query.filter_by(name="Telur").first()

    # total produksi telur (butir)
    eggs_produced = db.session.query(db.func.coalesce(db.func.sum(ChickenDailyLog.eggs_count), 0)).scalar()

    # total telur terjual (rak) dari transaksi IN
    racks_sold = 0.0
    if telur:
        racks_sold = db.session.query(db.func.coalesce(db.func.sum(Transaction.qty), 0.0))\
            .filter(Transaction.tipe == "IN", Transaction.product_id == telur.id).scalar()

    eggs_sold = int(round((racks_sold or 0) * eggs_per_rack))
    eggs_stock_raw = int((eggs_produced or 0) - eggs_sold)
    stock_warning = eggs_stock_raw < 0
    eggs_stock = max(0, eggs_stock_raw)

    stock_racks = eggs_stock // eggs_per_rack if eggs_per_rack > 0 else 0
    stock_eggs_rem = eggs_stock % eggs_per_rack if eggs_per_rack > 0 else eggs_stock

    # chicken current (flok pertama)
    flock = Flock.query.order_by(Flock.id.asc()).first()
    chicken_current_raw = 0
    if flock:
        dead_all = db.session.query(db.func.coalesce(db.func.sum(ChickenDailyLog.dead_count), 0))\
            .filter(ChickenDailyLog.flock_id == flock.id).scalar()
        chicken_current_raw = int((flock.initial_count or 0) - (dead_all or 0))
    chicken_warning = chicken_current_raw < 0
    chicken_current = max(0, chicken_current_raw)

    # ========= metrik harian & 7 hari (TELUR & AYAM) =========
    eggs_today = 0
    dead_today = 0
    eggs_7d = 0
    dead_7d = 0

    if flock:
        eggs_today = db.session.query(db.func.coalesce(db.func.sum(ChickenDailyLog.eggs_count), 0))\
            .filter(ChickenDailyLog.flock_id == flock.id, ChickenDailyLog.tgl == today).scalar()
        dead_today = db.session.query(db.func.coalesce(db.func.sum(ChickenDailyLog.dead_count), 0))\
            .filter(ChickenDailyLog.flock_id == flock.id, ChickenDailyLog.tgl == today).scalar()

        eggs_7d = db.session.query(db.func.coalesce(db.func.sum(ChickenDailyLog.eggs_count), 0))\
            .filter(ChickenDailyLog.flock_id == flock.id, ChickenDailyLog.tgl >= start_7d, ChickenDailyLog.tgl <= today).scalar()
        dead_7d = db.session.query(db.func.coalesce(db.func.sum(ChickenDailyLog.dead_count), 0))\
            .filter(ChickenDailyLog.flock_id == flock.id, ChickenDailyLog.tgl >= start_7d, ChickenDailyLog.tgl <= today).scalar()

    eggs_today = int(eggs_today or 0)
    dead_today = int(dead_today or 0)
    eggs_7d = int(eggs_7d or 0)
    dead_7d = int(dead_7d or 0)

    avg_eggs_7d = round(eggs_7d / 7.0, 1)

    # Hen-Day Egg Production (HDEP): (telur hari ini / jumlah ayam hari ini) * 100
    hen_day_pct = 0.0
    if chicken_current > 0:
        hen_day_pct = round((eggs_today / chicken_current) * 100.0, 1)
    hen_day_pct_clamped = max(0.0, min(hen_day_pct, 100.0))

    mortality_7d_pct = 0.0
    if chicken_current > 0:
        mortality_7d_pct = round((dead_7d / chicken_current) * 100.0, 2)

    # telur terjual hari ini (rak) dari transaksi IN
    racks_sold_today = 0.0
    if telur:
        racks_sold_today = db.session.query(db.func.coalesce(db.func.sum(Transaction.qty), 0.0))\
            .filter(Transaction.tipe == "IN", Transaction.product_id == telur.id, Transaction.tgl == today).scalar()
    racks_sold_today = float(racks_sold_today or 0.0)
    eggs_sold_today = int(round(racks_sold_today * eggs_per_rack))

    # ponds occupancy
    ponds = Pond.query.order_by(Pond.id.asc()).all()
    pond_cards = []
    for p in ponds:
        stocked = db.session.query(db.func.coalesce(db.func.sum(FishEvent.count), 0))\
            .filter(FishEvent.pond_id == p.id, FishEvent.event_type == "STOCK").scalar()
        harvested = db.session.query(db.func.coalesce(db.func.sum(FishEvent.count), 0))\
            .filter(FishEvent.pond_id == p.id, FishEvent.event_type == "HARVEST").scalar()
        dead = db.session.query(db.func.coalesce(db.func.sum(FishEvent.count), 0))\
            .filter(FishEvent.pond_id == p.id, FishEvent.event_type == "MORTALITY").scalar()
        current = int((stocked or 0) - (harvested or 0) - (dead or 0))

        cap_fish = p.capacity_fish_count()
        cap_kg = p.capacity_biomass_kg()
        vol = round(p.volume_m3(), 2)

        usage = 0 if cap_fish <= 0 else round(current / cap_fish * 100, 1)
        pond_cards.append({
            "pond": p,
            "current": current,
            "cap_fish": cap_fish,
            "cap_kg": round(cap_kg, 2),
            "vol": vol,
            "usage": usage
        })

    return render_template(
        "dashboard.html",
        total_in=total_in or 0,
        total_out=total_out or 0,
        profit=profit,

        eggs_per_rack=eggs_per_rack,
        eggs_produced=eggs_produced or 0,
        racks_sold=racks_sold or 0,
        eggs_sold=eggs_sold,
        eggs_stock=eggs_stock,
        eggs_stock_raw=eggs_stock_raw,
        stock_warning=stock_warning,
        stock_racks=stock_racks,
        stock_eggs_rem=stock_eggs_rem,

        chicken_current=chicken_current,
        chicken_current_raw=chicken_current_raw,
        chicken_warning=chicken_warning,

        eggs_today=eggs_today,
        dead_today=dead_today,
        eggs_7d=eggs_7d,
        dead_7d=dead_7d,
        avg_eggs_7d=avg_eggs_7d,
        hen_day_pct=hen_day_pct,
        hen_day_pct_clamped=hen_day_pct_clamped,
        mortality_7d_pct=mortality_7d_pct,
        racks_sold_today=racks_sold_today,
        eggs_sold_today=eggs_sold_today,

        pond_cards=pond_cards
    )


@app.get("/settings")
@login_required
def settings():
    eggs_per_rack = get_setting("EGGS_PER_RACK", "30")
    return render_template("settings.html", eggs_per_rack=eggs_per_rack)

@app.post("/settings")
@login_required
def settings_save():
    eggs_per_rack = request.form.get("eggs_per_rack", "30")
    eggs_per_rack_int = parse_int(eggs_per_rack, 30)
    if eggs_per_rack_int <= 0 or eggs_per_rack_int > 100:
        flash("Nilai butir per rak tidak valid (1..100).", "danger")
        return redirect(url_for("settings"))
    set_setting("EGGS_PER_RACK", str(eggs_per_rack_int))
    db.session.commit()
    flash("✅ Setting tersimpan.", "success")
    return redirect(url_for("settings"))


@app.get("/transactions")
@login_required
def transactions():
    products = Product.query.order_by(Product.name.asc()).all()
    q_type = request.args.get("tipe", "")
    q_product = request.args.get("product_id", "")

    query = Transaction.query
    if q_type in ("IN", "OUT"):
        query = query.filter(Transaction.tipe == q_type)
    if q_product.isdigit():
        query = query.filter(Transaction.product_id == int(q_product))

    rows = query.order_by(Transaction.tgl.desc(), Transaction.id.desc()).limit(300).all()
    return render_template("transactions.html", rows=rows, products=products, q_type=q_type, q_product=q_product)

@app.post("/transactions/add")
@login_required
def transactions_add():
    tgl = parse_date(request.form.get("tgl")) or date.today()
    tipe = request.form.get("tipe", "IN")
    product_id = parse_int(request.form.get("product_id"), 0)
    deskripsi = request.form.get("deskripsi", "")
    qty = parse_float(request.form.get("qty"), 0.0)
    unit = request.form.get("unit", "unit")
    unit_price = parse_float(request.form.get("unit_price"), 0.0)
    total = qty * unit_price

    if product_id <= 0:
        flash("Produk wajib dipilih.", "danger")
        return redirect(url_for("transactions"))

    db.session.add(Transaction(
        tgl=tgl, tipe=tipe, product_id=product_id, deskripsi=deskripsi,
        qty=qty, unit=unit, unit_price=unit_price, total=total
    ))
    db.session.commit()
    flash("✅ Transaksi tersimpan.", "success")
    return redirect(url_for("transactions"))


@app.get("/ponds")
@login_required
def ponds():
    rows = Pond.query.order_by(Pond.id.asc()).all()
    return render_template("ponds.html", rows=rows)

@app.get("/ponds/<int:pond_id>")
@login_required
def pond_detail(pond_id):
    p = db.session.get(Pond, pond_id)
    if not p:
        return "Not found", 404
    events = FishEvent.query.filter_by(pond_id=pond_id).order_by(FishEvent.tgl.desc(), FishEvent.id.desc()).all()
    return render_template("pond_detail.html", p=p, events=events)

@app.post("/ponds/<int:pond_id>/update")
@login_required
def pond_update(pond_id):
    p = db.session.get(Pond, pond_id)
    if not p:
        return "Not found", 404

    p.diameter_m = parse_float(request.form.get("diameter_m"), p.diameter_m)
    p.water_depth_m = parse_float(request.form.get("water_depth_m"), p.water_depth_m)
    p.stocking_rate_fish_per_m3 = parse_float(request.form.get("stocking_rate_fish_per_m3"), p.stocking_rate_fish_per_m3)
    p.biomass_capacity_kg_per_m3 = parse_float(request.form.get("biomass_capacity_kg_per_m3"), p.biomass_capacity_kg_per_m3)

    db.session.commit()
    flash("✅ Data kolam diperbarui.", "success")
    return redirect(url_for("pond_detail", pond_id=pond_id))

@app.post("/ponds/<int:pond_id>/event")
@login_required
def pond_add_event(pond_id):
    p = db.session.get(Pond, pond_id)
    if not p:
        return "Not found", 404

    tgl = parse_date(request.form.get("tgl")) or date.today()
    tgl_int = int(tgl.strftime("%Y%m%d"))
    event_type = request.form.get("event_type", "STOCK")
    count = parse_int(request.form.get("count"), 0)
    weight_kg = parse_float(request.form.get("weight_kg"), 0.0)
    note = request.form.get("note", "")

    db.session.add(FishEvent(
        pond_id=pond_id, tgl=tgl_int, event_type=event_type,
        count=count, weight_kg=weight_kg, note=note
    ))
    db.session.commit()
    flash("✅ Event ikan tersimpan.", "success")
    return redirect(url_for("pond_detail", pond_id=pond_id))


@app.get("/flocks")
@login_required
def flocks():
    rows = Flock.query.order_by(Flock.id.asc()).all()
    return render_template("flocks.html", rows=rows)

@app.post("/flocks/update/<int:flock_id>")
@login_required
def flocks_update(flock_id):
    f = db.session.get(Flock, flock_id)
    if not f:
        return "Not found", 404
    f.initial_count = parse_int(request.form.get("initial_count"), f.initial_count)
    db.session.commit()
    flash("✅ Jumlah awal flok diperbarui.", "success")
    return redirect(url_for("flocks"))

@app.get("/flocks/<int:flock_id>")
@login_required
def flock_detail(flock_id):
    f = db.session.get(Flock, flock_id)
    if not f:
        return "Not found", 404
    logs = ChickenDailyLog.query.filter_by(flock_id=flock_id).order_by(ChickenDailyLog.tgl.desc(), ChickenDailyLog.id.desc()).all()
    return render_template("flock_detail.html", f=f, logs=logs)

@app.post("/flocks/<int:flock_id>/log")
@login_required
def flock_add_log(flock_id):
    f = db.session.get(Flock, flock_id)
    if not f:
        return "Not found", 404

    tgl = parse_date(request.form.get("tgl")) or date.today()
    eggs = parse_int(request.form.get("eggs_count"), 0)
    dead = parse_int(request.form.get("dead_count"), 0)
    note = request.form.get("note", "")

    db.session.add(ChickenDailyLog(
        flock_id=flock_id, tgl=tgl, eggs_count=eggs, dead_count=dead, note=note
    ))
    db.session.commit()
    flash("✅ Log harian tersimpan.", "success")
    return redirect(url_for("flock_detail", flock_id=flock_id))


@app.get("/api/summary")
@login_required
def api_summary():
    total_in = db.session.query(db.func.coalesce(db.func.sum(Transaction.total), 0.0))\
        .filter(Transaction.tipe == "IN").scalar()
    total_out = db.session.query(db.func.coalesce(db.func.sum(Transaction.total), 0.0))\
        .filter(Transaction.tipe == "OUT").scalar()
    return jsonify({
        "income": float(total_in or 0),
        "expense": float(total_out or 0),
        "profit": float((total_in or 0) - (total_out or 0))
    })


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="127.0.0.1", port=5000, debug=False)
