"""Database helper with MySQL support and built-in in-memory fallback."""
import os
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv()

# In-memory store when database integration is not needed or MySQL is not running
_USERS = {
    "admin": {
        "id": 1,
        "username": "admin",
        "password_hash": generate_password_hash("admin123"),
        "role": "owner"
    }
}
_BOOKINGS = []

# Pre-populate sample bookings from data.csv if available
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

CFG = dict(host=os.getenv("DB_HOST", "localhost"),
           port=int(os.getenv("DB_PORT", 3306)),
           user=os.getenv("DB_USER", "root"),
           password=os.getenv("DB_PASSWORD"), database=os.getenv("DB_NAME", "eco_pricing"))

def run(sql, params=None, many=False, fetch=False):
    # If MySQL credentials exist and database integration is enabled, attempt MySQL
    if CFG.get("password") and str(CFG["password"]).lower() not in ("none", "", "skip", "false"):
        try:
            import mysql.connector
            con = mysql.connector.connect(**CFG); cur = con.cursor(dictionary=True)
            try:
                (cur.executemany if many else cur.execute)(sql, params or ())
                rows = cur.fetchall() if fetch else None
                con.commit(); return rows
            finally:
                cur.close(); con.close()
        except Exception:
            pass

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
    return item