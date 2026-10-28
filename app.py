import os, json, joblib, pandas as pd
from functools import wraps
from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from werkzeug.security import check_password_hash
from dotenv import load_dotenv
from generate_data import calendar
import pricing_engine as pe
from db import run, get_feedback, add_feedback, register_user, verify_user
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "eco-tourism-secret-key-2026-local")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_FILE = os.path.join(BASE_DIR, "model.pkl")
RESULTS_FILE = os.path.join(BASE_DIR, "results.json")
DATA_FILE = os.path.join(BASE_DIR, "data.csv")

if not os.path.exists(MODEL_FILE):
    print("model.pkl not found. Training model now...")
    if not os.path.exists(DATA_FILE):
        import numpy as np
        from generate_data import true_demand, ROOMS, SEASON
        rng = np.random.default_rng(42)
        df_cal = calendar(pd.date_range("2023-01-01", "2025-12-31"))
        df_cal["price"] = (3500 * (1 + .2*df_cal.is_weekend) * (1 + .1*(df_cal.month.map(SEASON) > 1.1)) * rng.uniform(.6, 2.2, len(df_cal))).clip(2000, 9000).round(-2)
        df_cal["bookings"] = np.minimum(ROOMS, rng.poisson(true_demand(df_cal, df_cal.price)))
        df_cal["occupancy"] = df_cal.bookings / ROOMS * 100
        df_cal["revenue"] = df_cal.bookings * df_cal.price
        df_cal.to_csv(DATA_FILE, index=False)
    res = pe.train()
    m_temp = joblib.load(MODEL_FILE)
    s_temp = pe.simulate(m_temp)
    json.dump(dict(models=res, simulation=s_temp), open(RESULTS_FILE, "w"), indent=1)

model = joblib.load(MODEL_FILE)

def owner_only(f):
    @wraps(f)
    def wrap(*a, **k):
        if session.get("role") != "owner":
            if request.path.startswith("/api/"): return jsonify(error="login required"), 401
            return redirect(url_for("login"))
        return f(*a, **k)
    return wrap

# ---------- authentication ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    portal = request.args.get("portal") or request.form.get("portal") or "owner"
    mode = request.args.get("mode") or request.form.get("action") or "signin"
    next_url = request.args.get("next") or request.form.get("next")

    if request.method == "POST":
        action = request.form.get("action", "signin")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = "customer" if portal == "customer" else request.form.get("role", "owner")

        if action == "signup":
            u, err = register_user(username, password, role=role)
            if u:
                session.clear()
                session["role"] = u["role"]
                session["user"] = u["username"]
                if next_url: return redirect(next_url)
                return redirect(url_for("book") if u["role"] == "customer" else url_for("home"))
            mode = "signup"
        else: # signin
            u, err = verify_user(username, password)
            if u:
                session.clear()
                session["role"] = u["role"]
                session["user"] = u["username"]
                if next_url: return redirect(next_url)
                return redirect(url_for("book") if u["role"] == "customer" else url_for("home"))
            mode = "signin"

    return render_template("login.html", error=err, mode=mode, portal=portal, next=next_url)

@app.route("/customer/login")
def customer_login():
    return redirect(url_for("login", portal="customer", next="/book"))

@app.route("/api/auth", methods=["POST"])
def api_auth():
    data = request.get_json(silent=True) or request.form or {}
    action = data.get("action", "signin")
    username = data.get("username", "").strip()
    password = data.get("password", "")
    portal = data.get("portal", "customer")
    role = "customer" if portal == "customer" else data.get("role", "customer")

    if action == "signup":
        user, err = register_user(username, password, role=role)
        if err:
            return jsonify(ok=False, error=err), 400
        session.clear()
        session["user"] = user["username"]
        session["role"] = user["role"]
        return jsonify(ok=True, user=user["username"], role=user["role"], message="Account created and signed in!")

    user, err = verify_user(username, password)
    if err:
        return jsonify(ok=False, error=err), 401
    session.clear()
    session["user"] = user["username"]
    session["role"] = user["role"]
    return jsonify(ok=True, user=user["username"], role=user["role"], message="Signed in successfully!")

@app.route("/logout")
def logout():
    next_url = request.args.get("next")
    session.clear()
    if next_url:
        return redirect(next_url)
    return redirect(url_for("login"))

# ---------- customer side (public: price only) ----------
@app.route("/book")
def book(): return render_template("customer.html")

@app.route("/api/public-price")
def public_price():
    try: d = pd.Timestamp(request.args["date"]).normalize()
    except Exception: return jsonify(error="Invalid date"), 400
    today = pd.Timestamp.today().normalize()
    if not (today <= d <= today + pd.Timedelta(days=180)): return jsonify(error="Choose a date within the next 180 days"), 400
    r = pe.recommend(model, calendar([d]))[0]
    return jsonify(date=r["date"], price_per_night=r["price"])

# ---------- owner side (login required) ----------
@app.route("/")
@owner_only
def home(): return render_template("index.html")

@app.route("/api/forecast")
@owner_only
def forecast():
    start = request.args.get("start", str(pd.Timestamp.today().date())); days = int(request.args.get("days", 60))
    rec = pe.recommend(model, calendar(pd.date_range(start, periods=days)))
    try:
        run("INSERT INTO price_recommendations (stay_date, recommended_price, occupancy_pct, expected_revenue) VALUES (%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE recommended_price=VALUES(recommended_price), occupancy_pct=VALUES(occupancy_pct), expected_revenue=VALUES(expected_revenue)",
            [(r["date"], r["price"], float(r["occupancy"]), r["revenue"]) for r in rec], many=True)
    except Exception as e: print("DB save failed:", e)
    return jsonify(rec)

@app.route("/api/whatif")
@owner_only
def whatif(): return jsonify(pe.whatif(model, request.args["date"], float(request.args["price"])))

@app.route("/api/results")
@owner_only
def results():
    if os.path.exists(RESULTS_FILE):
        return jsonify(json.load(open(RESULTS_FILE)))
    return jsonify(models=[], simulation={"dynamic": 0, "fixed": 0, "uplift_pct": 0})

@app.route("/api/bookings", methods=["GET", "POST"])
@owner_only
def bookings():
    if request.method == "POST":
        d = request.get_json(); rooms = int(d["rooms"]); price = int(d["price"])
        if not (0 <= rooms <= pe.ROOMS and 2000 <= price <= 9000): return jsonify(error="rooms 0-20, price 2000-9000"), 400
        run("INSERT INTO daily_bookings (stay_date, rooms_booked, price) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE rooms_booked=VALUES(rooms_booked), price=VALUES(price)", (d["date"], rooms, price))
        return jsonify(ok=True)
    return jsonify(run("SELECT DATE_FORMAT(stay_date,'%Y-%m-%d') AS date, rooms_booked, price, revenue FROM daily_bookings ORDER BY stay_date DESC LIMIT 10", fetch=True))

@app.route("/api/feedback", methods=["GET", "POST"])
@owner_only
def feedback():
    if request.method == "POST":
        d = request.get_json() or {}
        category = d.get("category", "General Feedback").strip()
        comment = d.get("comment", "").strip()
        try:
            rating = int(d.get("rating", 5))
            if not (1 <= rating <= 5): rating = 5
        except (ValueError, TypeError):
            rating = 5
        if not comment:
            return jsonify(error="Comment cannot be empty"), 400
        author = session.get("user", "owner")
        item = add_feedback(category, rating, comment, author=author)
        return jsonify(ok=True, feedback=item)
    return jsonify(get_feedback())

if __name__ == "__main__": app.run(debug=True)