"""Database helper with MySQL and PostgreSQL (Supabase) support + in-memory fallback."""
import os
import re
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# In-memory store when database integration is not needed or DB is not running
_USERS = {
    "admin": {
        "id": 1,
        "username": "admin",
        "password_hash": generate_password_hash("admin123"),
        "role": "owner"
    }
}
_BOOKINGS = []

try:
    import pandas as pd
    if os.path.exists("data.csv"):
        _df = pd.read_csv("data.csv").tail(10)
        for _, r in _df.iterrows():
            _BOOKINGS.append({
                "date": str(r["date"]),
                "rooms_booked": int(r["bookings"]),
                "price": int(r["price"]),
                "revenue": int(r["bookings"] * r["price"])
            })
        _BOOKINGS.reverse()
except Exception:
    pass

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432 if ("supabase" in DB_HOST.lower() or os.getenv("DB_TYPE") == "postgres") else 3306))
DB_USER = os.getenv("DB_USER", "postgres" if "supabase" in DB_HOST.lower() else "root")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "postgres" if "supabase" in DB_HOST.lower() else "eco_pricing")
DB_TYPE = os.getenv("DB_TYPE", "postgres" if ("supabase" in DB_HOST.lower() or DB_PORT in (5432, 6543) or (DATABASE_URL and "postgres" in DATABASE_URL)) else "mysql")

# Supabase REST API credentials
SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")

def _supabase_request(endpoint, method="GET", json_data=None, params=None):
    """Executes a request against the Supabase PostgREST API."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        import requests
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation"
        }
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{endpoint}"
        if method == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=10)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=json_data, timeout=15)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers, params=params, timeout=10)
        else:
            return None
        if r.status_code in (200, 201):
            return r.json()
        print(f"[Supabase API Error {r.status_code}] {r.text}")
        return None
    except Exception as e:
        print(f"[Supabase API Connection Error] {e}")
        return None

def _to_postgres_sql(sql):
    s = sql
    if "ON DUPLICATE KEY UPDATE" in s:
        if "daily_bookings" in s:
            s = s.split("ON DUPLICATE KEY UPDATE")[0] + " ON CONFLICT (stay_date) DO UPDATE SET rooms_booked = EXCLUDED.rooms_booked, price = EXCLUDED.price"
        elif "price_recommendations" in s:
            s = s.split("ON DUPLICATE KEY UPDATE")[0] + " ON CONFLICT (stay_date) DO UPDATE SET recommended_price = EXCLUDED.recommended_price, occupancy_pct = EXCLUDED.occupancy_pct, expected_revenue = EXCLUDED.expected_revenue"
        elif "users" in s:
            s = s.split("ON DUPLICATE KEY UPDATE")[0] + " ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role"
    s = re.sub(r"DATE_FORMAT\s*\(\s*stay_date\s*,\s*'%Y-%m-%d'\s*\)", "TO_CHAR(stay_date, 'YYYY-MM-DD')", s, flags=re.IGNORECASE)
    s = re.sub(r"DATE_FORMAT\s*\(\s*created_at\s*,\s*'%Y-%m-%d\s+%H:%i'\s*\)", "TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI')", s, flags=re.IGNORECASE)
    return s

def _get_pg_connection():
    import psycopg2
    import psycopg2.extras
    if DATABASE_URL:
        if "sslmode" not in DATABASE_URL.lower():
            return psycopg2.connect(DATABASE_URL, sslmode="require")
        return psycopg2.connect(DATABASE_URL)
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME,
        sslmode="require" if "supabase" in DB_HOST.lower() else os.getenv("DB_SSLMODE", "prefer")
    )

def _get_mysql_connection():
    import mysql.connector
    cfg = dict(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
    return mysql.connector.connect(**cfg)

def run(sql, params=None, many=False, fetch=False):
    # Try PostgreSQL / Supabase if configured
    if DB_PASSWORD and str(DB_PASSWORD).lower() not in ("none", "", "skip", "false"):
        if DB_TYPE == "postgres":
            try:
                import psycopg2.extras
                con = _get_pg_connection()
                cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                try:
                    pg_sql = _to_postgres_sql(sql)
                    if many:
                        cur.executemany(pg_sql, params or ())
                    else:
                        cur.execute(pg_sql, params or ())
                    rows = cur.fetchall() if fetch else None
                    con.commit()
                    return [dict(r) for r in rows] if rows else rows
                finally:
                    cur.close()
                    con.close()
            except Exception as e:
                print(f"[Supabase/PG Error] {e} - falling back to local simulation")

        # Try MySQL
        elif DB_TYPE == "mysql":
            try:
                con = _get_mysql_connection()
                cur = con.cursor(dictionary=True)
                try:
                    (cur.executemany if many else cur.execute)(sql, params or ())
                    rows = cur.fetchall() if fetch else None
                    con.commit()
                    return rows
                finally:
                    cur.close()
                    con.close()
            except Exception as e:
                print(f"[MySQL Error] {e} - falling back to local simulation")

    # Try Supabase REST API if configured
    if SUPABASE_URL and SUPABASE_KEY:
        sql_upper = sql.strip().upper()
        try:
            if sql_upper.startswith("SELECT * FROM USERS"):
                uname = params[0] if params else None
                p = {"select": "*"}
                if uname:
                    p["username"] = f"eq.{uname}"
                rows = _supabase_request("users", params=p)
                if rows is not None:
                    return rows if fetch else None

            elif "FROM DAILY_BOOKINGS" in sql_upper:
                p = {"select": "stay_date,rooms_booked,price,revenue", "order": "stay_date.desc", "limit": "10"}
                rows = _supabase_request("daily_bookings", params=p)
                if rows is not None:
                    out = []
                    for r in rows:
                        out.append({
                            "date": str(r.get("stay_date")),
                            "rooms_booked": int(r.get("rooms_booked") or 0),
                            "price": int(r.get("price") or 0),
                            "revenue": int(r.get("revenue") or 0)
                        })
                    return out if fetch else None

            elif "INSERT INTO DAILY_BOOKINGS" in sql_upper:
                if many and params:
                    for i in range(0, len(params), 200):
                        chunk = params[i:i + 200]
                        body = [{"stay_date": str(r[0]), "rooms_booked": int(r[1]), "price": int(r[2])} for r in chunk]
                        _supabase_request("daily_bookings", method="POST", json_data=body)
                elif params:
                    body = [{"stay_date": str(params[0]), "rooms_booked": int(params[1]), "price": int(params[2])}]
                    _supabase_request("daily_bookings", method="POST", json_data=body)
                return None

            elif "INSERT INTO USERS" in sql_upper:
                if params:
                    role = params[2] if len(params) > 2 else "customer"
                    body = [{"username": str(params[0]), "password_hash": str(params[1]), "role": role}]
                    _supabase_request("users", method="POST", json_data=body)
                return None

            elif "INSERT INTO PRICE_RECOMMENDATIONS" in sql_upper:
                if many and params:
                    for i in range(0, len(params), 200):
                        chunk = params[i:i + 200]
                        body = [{"stay_date": str(r[0]), "recommended_price": int(r[1]), "occupancy_pct": float(r[2]), "expected_revenue": int(r[3])} for r in chunk]
                        _supabase_request("price_recommendations", method="POST", json_data=body)
                elif params:
                    body = [{"stay_date": str(params[0]), "recommended_price": int(params[1]), "occupancy_pct": float(params[2]), "expected_revenue": int(params[3])}]
                    _supabase_request("price_recommendations", method="POST", json_data=body)
                return None
        except Exception as e:
            print(f"[Supabase REST Error] {e}")

    # In-memory fallback
    sql_upper = sql.strip().upper()
    if sql_upper.startswith("SELECT * FROM USERS"):
        uname = params[0] if params else None
        if uname:
            if uname in _USERS:
                return [_USERS[uname]] if fetch else None
            return [] if fetch else None
        return list(_USERS.values()) if fetch else None

    if "FROM DAILY_BOOKINGS" in sql_upper:
        return list(_BOOKINGS[:10]) if fetch else None

    if "INSERT INTO DAILY_BOOKINGS" in sql_upper:
        if many and params:
            for r in params:
                _BOOKINGS.insert(0, {"date": str(r[0]), "rooms_booked": int(r[1]), "price": int(r[2]), "revenue": int(r[1]) * int(r[2])})
        elif params:
            _BOOKINGS.insert(0, {"date": str(params[0]), "rooms_booked": int(params[1]), "price": int(params[2]), "revenue": int(params[1]) * int(params[2])})
        return None

    if "INSERT INTO USERS" in sql_upper:
        if params:
            uname = params[0]
            phash = params[1]
            role = params[2] if len(params) > 2 else "customer"
            _USERS[uname] = {"id": len(_USERS) + 1, "username": uname, "password_hash": phash, "role": role}
        return None

    if "INSERT INTO PRICE_RECOMMENDATIONS" in sql_upper:
        return None

    return [] if fetch else None

# Feedback store
_FEEDBACK = [
    {
        "id": 1,
        "category": "Pricing Accuracy",
        "rating": 5,
        "comment": "The demand curve model smoothly adapted during the winter festival surge, capturing peak revenue without overpricing.",
        "created_at": "2026-10-10 14:32",
        "author": "admin"
    },
    {
        "id": 2,
        "category": "Demand Forecast",
        "rating": 4,
        "comment": "Weekend occupancy predictions are within 5% of actual walk-in bookings. Great reliability for staffing.",
        "created_at": "2026-10-12 09:15",
        "author": "admin"
    }
]

def get_feedback():
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            p = {"select": "id,category,rating,comment,author,created_at", "order": "id.desc", "limit": "20"}
            rows = _supabase_request("owner_feedback", params=p)
            if rows:
                out = []
                for r in rows:
                    c_at = str(r.get("created_at") or "")
                    if "T" in c_at:
                        c_at = c_at.replace("T", " ")[:16]
                    out.append({
                        "id": r.get("id"),
                        "category": r.get("category"),
                        "rating": r.get("rating"),
                        "comment": r.get("comment"),
                        "author": r.get("author"),
                        "created_at": c_at
                    })
                return out
        except Exception:
            pass

    if DB_PASSWORD and str(DB_PASSWORD).lower() not in ("none", "", "skip", "false"):
        try:
            date_fn = "TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI')" if DB_TYPE == "postgres" else "DATE_FORMAT(created_at, '%Y-%m-%d %H:%i')"
            rows = run(f"SELECT id, category, rating, comment, author, {date_fn} AS created_at FROM owner_feedback ORDER BY id DESC LIMIT 20", fetch=True)
            if rows: return rows
        except Exception:
            pass
    return list(_FEEDBACK)

def add_feedback(category, rating, comment, author="owner"):
    from datetime import datetime
    item = {
        "id": len(_FEEDBACK) + 1,
        "category": category,
        "rating": int(rating),
        "comment": comment.strip(),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "author": author
    }
    _FEEDBACK.insert(0, item)
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            _supabase_request("owner_feedback", method="POST", json_data=[{
                "category": category,
                "rating": int(rating),
                "comment": comment.strip(),
                "author": author
            }])
        except Exception:
            pass
    elif DB_PASSWORD and str(DB_PASSWORD).lower() not in ("none", "", "skip", "false"):
        try:
            run("INSERT INTO owner_feedback (category, rating, comment, author) VALUES (%s, %s, %s, %s)",
                (category, int(rating), comment.strip(), author))
        except Exception:
            pass
    return item

# ============================================================================
# User Authentication & Registration (PostgreSQL / Supabase / In-Memory)
# ============================================================================
_DB_INITIALIZED = False

def init_db():
    """Initializes schema tables in PostgreSQL / Supabase if connected."""
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    if DB_PASSWORD and str(DB_PASSWORD).lower() not in ("none", "", "skip", "false"):
        if DB_TYPE == "postgres":
            try:
                con = _get_pg_connection()
                cur = con.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(50) NOT NULL UNIQUE,
                        password_hash VARCHAR(255) NOT NULL,
                        role VARCHAR(20) NOT NULL DEFAULT 'customer'
                    );
                    CREATE TABLE IF NOT EXISTS daily_bookings (
                        stay_date DATE PRIMARY KEY,
                        rooms_booked INT NOT NULL CHECK (rooms_booked BETWEEN 0 AND 20),
                        price INT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS price_recommendations (
                        stay_date DATE PRIMARY KEY,
                        recommended_price INT NOT NULL,
                        occupancy_pct DECIMAL(5,1) NOT NULL,
                        expected_revenue INT NOT NULL,
                        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS owner_feedback (
                        id SERIAL PRIMARY KEY,
                        category VARCHAR(100) NOT NULL,
                        rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
                        comment TEXT NOT NULL,
                        author VARCHAR(50) NOT NULL DEFAULT 'owner',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                # Insert default admin user if not exists
                cur.execute("""
                    INSERT INTO users (username, password_hash, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (username) DO NOTHING;
                """, ("admin", generate_password_hash("admin123"), "owner"))
                con.commit()
                cur.close()
                con.close()
                _DB_INITIALIZED = True
            except Exception as e:
                print(f"[Supabase/PG Init Notice] {e}")

# Initialize on startup if DB is configured
init_db()

def get_user(username):
    """Retrieve user dictionary by username."""
    if not username:
        return None
    rows = run("SELECT * FROM users WHERE username=%s", (str(username).strip(),), fetch=True)
    if rows and len(rows) > 0:
        return rows[0]
    return None

def register_user(username, password, role="customer"):
    """
    Registers a new user into Supabase / PostgreSQL / MySQL / in-memory store.
    Returns (user_dict, None) on success, or (None, error_str) on failure.
    """
    uname = str(username or "").strip()
    if not uname:
        return None, "Username cannot be empty."
    if len(uname) < 3:
        return None, "Username must be at least 3 characters long."
    if not password or len(password) < 6:
        return None, "Password must be at least 6 characters long."
    
    if role not in ("owner", "customer"):
        role = "customer"

    existing = get_user(uname)
    if existing:
        return None, f"Username '{uname}' is already registered. Please sign in."

    pwd_hash = generate_password_hash(password)
    try:
        run("INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), role=VALUES(role)",
            (uname, pwd_hash, role))
        
        user = get_user(uname) or {"id": 1, "username": uname, "role": role}
        return user, None
    except Exception as e:
        return None, f"Registration failed: {str(e)}"

def verify_user(username, password):
    """
    Verifies user credentials for login.
    Returns (user_dict, None) on success, or (None, error_str) on failure.
    """
    uname = str(username or "").strip()
    if not uname or not password:
        return None, "Please provide both username and password."

    user = get_user(uname)
    if not user:
        return None, f"Account '{uname}' not found. Please sign up."

    if not check_password_hash(user["password_hash"], password):
        return None, "Incorrect password. Please try again."

    return user, None