"""Database helper with MySQL and PostgreSQL (Supabase) support + in-memory fallback."""
import os
import re
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv()

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

def _to_postgres_sql(sql):
    s = sql
    if "ON DUPLICATE KEY UPDATE" in s:
        if "daily_bookings" in s:
            s = s.split("ON DUPLICATE KEY UPDATE")[0] + "ON CONFLICT (stay_date) DO UPDATE SET rooms_booked = EXCLUDED.rooms_booked, price = EXCLUDED.price"
        elif "price_recommendations" in s:
            s = s.split("ON DUPLICATE KEY UPDATE")[0] + "ON CONFLICT (stay_date) DO UPDATE SET recommended_price = EXCLUDED.recommended_price, occupancy_pct = EXCLUDED.occupancy_pct, expected_revenue = EXCLUDED.expected_revenue"
        elif "users" in s:
            s = s.split("ON DUPLICATE KEY UPDATE")[0] + "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash"
    s = re.sub(r"DATE_FORMAT\s*\(\s*stay_date\s*,\s*'%Y-%m-%d'\s*\)", "TO_CHAR(stay_date, 'YYYY-MM-DD')", s, flags=re.IGNORECASE)
    s = re.sub(r"DATE_FORMAT\s*\(\s*created_at\s*,\s*'%Y-%m-%d\s+%H:%i'\s*\)", "TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI')", s, flags=re.IGNORECASE)
    return s

def _get_pg_connection():
    import psycopg2
    import psycopg2.extras
    if DATABASE_URL:
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

    # In-memory fallback
    sql_upper = sql.strip().upper()
    if sql_upper.startswith("SELECT * FROM USERS"):
        uname = params[0] if params else "admin"
        if uname in _USERS:
            return [_USERS[uname]] if fetch else None
        return [{"id": 1, "username": uname, "password_hash": generate_password_hash("admin123"), "role": "owner"}] if fetch else None

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
            _USERS[params[0]] = {"id": len(_USERS) + 1, "username": params[0], "password_hash": params[1], "role": "owner"}
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
    if DB_PASSWORD and str(DB_PASSWORD).lower() not in ("none", "", "skip", "false"):
        try:
            run("INSERT INTO owner_feedback (category, rating, comment, author) VALUES (%s, %s, %s, %s)",
                (category, int(rating), comment.strip(), author))
        except Exception:
            pass
    return item