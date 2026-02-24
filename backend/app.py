from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
import time

try:
    # Works when launched as `uvicorn backend.app:app` from repo root.
    from backend.dr1_demo_db import ensure_dr1_demo_db
except ImportError:
    # Works when launched from inside `backend/` via `fastapi dev`.
    from dr1_demo_db import ensure_dr1_demo_db
app = FastAPI(
    title="Team2 Backend",
    openapi_url=f"/openapi.json",
    docs_url=f"/docs",
    redoc_url=f"/redoc",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "frontend" / "assets"
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

DR1_DB_PATH = REPO_ROOT / "database" / "dr1_demo.db"

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "monotream")
_admin_sessions: set[str] = set()

@app.on_event("startup")
async def _startup():
    # Creates + seeds the demo DB locally if missing/empty.
    ensure_dr1_demo_db(REPO_ROOT)


def _is_admin(request: Request) -> bool:
    token = request.cookies.get("admin_session")
    return bool(token) and token in _admin_sessions
	 
@app.get("/")
async def root():
    landing_path = REPO_ROOT / "frontend" / "landing.html"
    if not landing_path.exists():
        raise HTTPException(status_code=404, detail="landing.html not found")
    return FileResponse(landing_path, media_type="text/html")


@app.get("/dashboard")
async def dashboard():
    mockup_path = REPO_ROOT / "frontend" / "mockup.html"
    if not mockup_path.exists():
        raise HTTPException(status_code=404, detail="mockup.html not found")
    return FileResponse(mockup_path, media_type="text/html")


@app.get("/about")
async def about():
    about_path = REPO_ROOT / "frontend" / "about.html"
    if not about_path.exists():
        raise HTTPException(status_code=404, detail="about.html not found")
    return FileResponse(about_path, media_type="text/html")


@app.post("/admin/login")
async def admin_login(payload: dict = Body(...)):
    password = str(payload.get("password", ""))
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid admin password")

    token = secrets.token_urlsafe(32)
    _admin_sessions.add(token)

    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "admin_session",
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 8,
    )
    return resp


@app.post("/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get("admin_session")
    if token:
        _admin_sessions.discard(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("admin_session")
    return resp


@app.get("/admin")
async def admin(request: Request):
    if not _is_admin(request):
        return RedirectResponse(url="/", status_code=303)

    admin_path = REPO_ROOT / "frontend" / "admin.html"
    if not admin_path.exists():
        raise HTTPException(status_code=404, detail="admin.html not found")
    return FileResponse(admin_path, media_type="text/html")

@app.get("/health")
async def health():
    return "Im Up"

@app.get("/heat_index")
async def heat_index_func(t: float, rh: float):
    """
    Calculate the heat index given temperature and humidity.
    The formula used is the Rothfusz regression.
    """
    temperature = t
    humidity = rh
    hi = (
        -42.379
        + 2.04901523 * temperature
        + 10.14333127 * humidity
        - 0.22475541 * temperature * humidity
        - 6.83783e-3 * temperature ** 2
        - 5.481717e-2 * humidity ** 2
        + 1.22874e-3 * temperature ** 2 * humidity
        + 8.5282e-4 * temperature * humidity ** 2
        - 1.99e-6 * temperature ** 2 * humidity ** 2
    )
    return round(hi)


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _parse_sqlite_utc_ts(ts: str) -> datetime:
    # ts format: "YYYY-MM-DD HH:MM:SS" (stored as UTC in our demo DB)
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _dt_to_sqlite_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _connect_dr1() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DR1_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@app.get("/api/dr1/locations")
async def dr1_locations():
    """
    Latest reading per device with lat/lon/temp for map markers.
    """
    ensure_dr1_demo_db(REPO_ROOT)
    conn = _connect_dr1()
    try:
        rows = conn.execute(
            """
            SELECT r.*
            FROM readings r
            JOIN (
              SELECT device_id, MAX(timestamp) AS max_ts
              FROM readings
              GROUP BY device_id
            ) latest
              ON latest.device_id = r.device_id AND latest.max_ts = r.timestamp
            WHERE r.lat IS NOT NULL
              AND r.lon IS NOT NULL
              AND r.temp_c IS NOT NULL
              AND r.humidity_rh IS NOT NULL
            ORDER BY r.device_id ASC;
            """
        ).fetchall()

        out: list[dict] = []
        for r in rows:
            temp_c = float(r["temp_c"])
            rh = float(r["humidity_rh"])
            temp_f = _c_to_f(temp_c)
            heat_index_f = float(await heat_index_func(temp_f, rh))
            out.append(
                {
                    "device_id": r["device_id"],
                    "label": r["device_id"],
                    "lat": r["lat"],
                    "lon": r["lon"],
                    "timestamp": r["timestamp"],
                    "temp_c": temp_c,
                    "temp_f": round(temp_f, 1),
                    "humidity_rh": round(rh, 1),
                    "heat_index_f": heat_index_f,
                }
            )
        return out
    finally:
        conn.close()


@app.get("/api/dr1/devices/{device_id}/series")
async def dr1_device_series(device_id: str, limit: int = 180):
    """
    Time-series readings for plots (ascending by timestamp).
    """
    ensure_dr1_demo_db(REPO_ROOT)
    conn = _connect_dr1()
    try:
        rows = conn.execute(
            """
            SELECT timestamp, temp_c, humidity_rh
            FROM readings
            WHERE device_id = ?
              AND temp_c IS NOT NULL
              AND humidity_rh IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?;
            """,
            (device_id, int(limit)),
        ).fetchall()

        # reverse to ascending time for charting
        out: list[dict] = []
        for r in reversed(rows):
            temp_c = float(r["temp_c"])
            rh = float(r["humidity_rh"])
            temp_f = _c_to_f(temp_c)
            heat_index_f = float(await heat_index_func(temp_f, rh))
            out.append(
                {
                    "timestamp": r["timestamp"],
                    "temp_c": temp_c,
                    "temp_f": round(temp_f, 2),
                    "humidity_rh": round(rh, 2),
                    "heat_index_f": heat_index_f,
                }
            )
        return out
    finally:
        conn.close()


@app.get("/api/dr1/areas")
async def dr1_areas(window_minutes: int = 10):
    """
    Area-level aggregate over a recent window of readings.
    Returns timestamp_ms for local-time rendering in the frontend.
    """
    ensure_dr1_demo_db(REPO_ROOT)
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=int(window_minutes))
    cutoff_sql = _dt_to_sqlite_utc(cutoff)

    conn = _connect_dr1()
    try:
        rows = conn.execute(
            """
            SELECT
              a.area_id,
              a.name,
              a.min_lon,
              a.min_lat,
              a.max_lon,
              a.max_lat,
              COUNT(r.id) AS n_readings,
              AVG(r.temp_c) AS avg_temp_c,
              AVG(r.humidity_rh) AS avg_rh,
              MAX(r.timestamp) AS max_ts
            FROM areas a
            LEFT JOIN readings r
              ON r.lat BETWEEN a.min_lat AND a.max_lat
             AND r.lon BETWEEN a.min_lon AND a.max_lon
             AND r.timestamp >= ?
             AND r.temp_c IS NOT NULL
             AND r.humidity_rh IS NOT NULL
            GROUP BY a.area_id
            ORDER BY a.area_id ASC;
            """,
            (cutoff_sql,),
        ).fetchall()

        out: list[dict] = []
        for r in rows:
            n = int(r["n_readings"] or 0)
            avg_temp_c = float(r["avg_temp_c"]) if r["avg_temp_c"] is not None else None
            avg_rh = float(r["avg_rh"]) if r["avg_rh"] is not None else None

            if avg_temp_c is not None:
                avg_temp_f = _c_to_f(avg_temp_c)
            else:
                avg_temp_f = None

            if avg_temp_f is not None and avg_rh is not None:
                heat_index_f = float(await heat_index_func(avg_temp_f, avg_rh))
            else:
                heat_index_f = None

            max_ts = r["max_ts"]
            if max_ts:
                ts_dt = _parse_sqlite_utc_ts(str(max_ts))
                ts_ms = int(ts_dt.timestamp() * 1000)
            else:
                ts_ms = None

            min_lat = float(r["min_lat"])
            max_lat = float(r["max_lat"])
            min_lon = float(r["min_lon"])
            max_lon = float(r["max_lon"])

            out.append(
                {
                    "area_id": r["area_id"],
                    "name": r["name"],
                    "bounds": {
                        "min_lat": min_lat,
                        "min_lon": min_lon,
                        "max_lat": max_lat,
                        "max_lon": max_lon,
                    },
                    "center": {"lat": (min_lat + max_lat) / 2, "lon": (min_lon + max_lon) / 2},
                    "window_minutes": int(window_minutes),
                    "n_readings": n,
                    "avg_temp_c": round(avg_temp_c, 3) if avg_temp_c is not None else None,
                    "avg_temp_f": round(avg_temp_f, 2) if avg_temp_f is not None else None,
                    "avg_humidity_rh": round(avg_rh, 2) if avg_rh is not None else None,
                    "avg_heat_index_f": heat_index_f,
                    "timestamp_ms": ts_ms,
                    "timestamp_utc": str(max_ts) if max_ts else None,
                }
            )

        return out
    finally:
        conn.close()


@app.get("/api/dr1/areas/{area_id}/series")
async def dr1_area_series(area_id: str, bucket_minutes: int = 5, hours: int = 3):
    """
    Time-series aggregates for a selected area.
    Returns bucket timestamp_ms for local-time rendering.
    """
    ensure_dr1_demo_db(REPO_ROOT)
    bucket_minutes = max(1, int(bucket_minutes))
    hours = max(1, int(hours))
    bucket_seconds = bucket_minutes * 60

    conn = _connect_dr1()
    try:
        area = conn.execute(
            """
            SELECT area_id, min_lon, min_lat, max_lon, max_lat, name
            FROM areas
            WHERE area_id = ?;
            """,
            (area_id,),
        ).fetchone()
        if not area:
            raise HTTPException(status_code=404, detail="Unknown area_id")

        cutoff = datetime.now(tz=UTC) - timedelta(hours=hours)
        cutoff_sql = _dt_to_sqlite_utc(cutoff)

        rows = conn.execute(
            f"""
            SELECT
              (CAST(strftime('%s', r.timestamp) AS INTEGER) / {bucket_seconds}) * {bucket_seconds} AS bucket_s,
              COUNT(r.id) AS n_readings,
              AVG(r.temp_c) AS avg_temp_c,
              AVG(r.humidity_rh) AS avg_rh,
              MAX(r.timestamp) AS max_ts
            FROM readings r
            WHERE r.timestamp >= ?
              AND r.lat BETWEEN ? AND ?
              AND r.lon BETWEEN ? AND ?
              AND r.temp_c IS NOT NULL
              AND r.humidity_rh IS NOT NULL
            GROUP BY bucket_s
            ORDER BY bucket_s ASC;
            """,
            (
                cutoff_sql,
                float(area["min_lat"]),
                float(area["max_lat"]),
                float(area["min_lon"]),
                float(area["max_lon"]),
            ),
        ).fetchall()

        out: list[dict] = []
        for r in rows:
            avg_temp_c = float(r["avg_temp_c"]) if r["avg_temp_c"] is not None else None
            avg_rh = float(r["avg_rh"]) if r["avg_rh"] is not None else None
            avg_temp_f = _c_to_f(avg_temp_c) if avg_temp_c is not None else None
            heat_index_f = float(await heat_index_func(avg_temp_f, avg_rh)) if (avg_temp_f is not None and avg_rh is not None) else None
            bucket_ms = int(r["bucket_s"]) * 1000 if r["bucket_s"] is not None else None
            out.append(
                {
                    "bucket_minutes": bucket_minutes,
                    "timestamp_ms": bucket_ms,
                    "n_readings": int(r["n_readings"] or 0),
                    "avg_temp_f": round(avg_temp_f, 2) if avg_temp_f is not None else None,
                    "avg_humidity_rh": round(avg_rh, 2) if avg_rh is not None else None,
                    "avg_heat_index_f": heat_index_f,
                    "timestamp_utc": str(r["max_ts"]) if r["max_ts"] else None,
                }
            )

        return {
            "area_id": area["area_id"],
            "name": area["name"],
            "hours": hours,
            "bucket_minutes": bucket_minutes,
            "series": out,
        }
    finally:
        conn.close()


@app.get("/api/dr1/campus/summary")
async def dr1_campus_summary(window_minutes: int = 10):
    """
    Campus-wide summary derived from area aggregates.
    """
    areas = await dr1_areas(window_minutes=window_minutes)
    areas_with_data = [a for a in areas if (a.get("n_readings") or 0) > 0 and a.get("avg_temp_f") is not None]

    if not areas_with_data:
        return {
            "window_minutes": int(window_minutes),
            "n_readings_total": 0,
            "avg_temp_f": None,
            "hottest_area": None,
            "coolest_area": None,
            "timestamp_ms": None,
        }

    n_total = sum(int(a["n_readings"]) for a in areas_with_data)
    avg_temp_f = (
        sum(float(a["avg_temp_f"]) * int(a["n_readings"]) for a in areas_with_data) / max(n_total, 1)
    )
    hottest = max(areas_with_data, key=lambda a: float(a["avg_temp_f"]))
    coolest = min(areas_with_data, key=lambda a: float(a["avg_temp_f"]))
    timestamp_ms = max(a["timestamp_ms"] for a in areas_with_data if a.get("timestamp_ms") is not None)

    return {
        "window_minutes": int(window_minutes),
        "n_readings_total": n_total,
        "avg_temp_f": round(avg_temp_f, 2),
        "hottest_area": {"area_id": hottest["area_id"], "name": hottest["name"], "avg_temp_f": hottest["avg_temp_f"]},
        "coolest_area": {"area_id": coolest["area_id"], "name": coolest["name"], "avg_temp_f": coolest["avg_temp_f"]},
        "timestamp_ms": timestamp_ms,
    }

RHT_DB = "rht_log.db"  # you'd obvi change this
@app.get("/heat_index2")
async def heat_index_function2(rh: float, t: float, history: int | None): 
    heat_index = round(await heat_index_func(t, rh)) 
    conn = sqlite3.connect(RHT_DB) 
    c = conn.cursor() 
    c.execute('''
    CREATE TABLE IF NOT EXISTS rht_table (
        rh REAL,
        t REAL,
        heat_index REAL,
        ts INTEGER
    );
    ''')

    now = int(time.time_ns()) // 1_000_000
    c.execute(
        '''INSERT INTO rht_table (rh, t, heat_index, ts) VALUES (?, ?, ?, ?);''',
        (rh, t, heat_index, now)
    )

    if history is not None:
        cutoff = now - history * 1000
        prev_data = c.execute(
            '''
            SELECT rh, t, heat_index, ts
            FROM rht_table
            WHERE ts >= ?
            ORDER BY ts DESC;
            ''',
            (cutoff,)
        ).fetchall()
    else:
        prev_data = c.execute(
            '''
            SELECT rh, t, heat_index, ts
            FROM rht_table
            ORDER BY ts DESC;
            '''
        ).fetchall()
    conn.commit() 
    conn.close()
    outs = ""
    for t in prev_data:
        outs += f"rh: {t[0]} t: {t[1]} heat_index: {t[2]}!"
    return outs

LUX_DB = "lux_log.db"

@app.post("/lux_logger")
async def log_lux(
    lux: float = Form(0.0),
    battery_voltage: float = Form(0.0, alias="bat"),
    kerberos: str = Form("")
):
    """
    Log lux and battery voltage measurements.
    Kerberos can be used for authentication (optional).
    """
    conn = sqlite3.connect(LUX_DB)
    c = conn.cursor()
    
    c.execute('''
    CREATE TABLE IF NOT EXISTS lux_table (
        lux REAL,
        battery_voltage REAL,
        kerberos TEXT,
        ts INTEGER
    );
    ''')
    
    now = int(time.time_ns()) // 1_000_000
    c.execute(
        '''INSERT INTO lux_table (lux, battery_voltage, kerberos, ts) VALUES (?, ?, ?, ?);''',
        (lux, battery_voltage, kerberos, now)
    )
    
    conn.commit()
    conn.close()
    
    return {
        "status": "logged",
        "lux": lux,
        "battery_voltage": battery_voltage,
        "timestamp": now
    }

@app.get("/get_lux")
async def get_lux(kerberos: str, time_minutes: int):
    """
    Retrieve lux and battery voltage data from the last time_minutes minutes.
    Requires proper kerberos for authentication.
    GET endpoint does not create or update db tables.
    """
    conn = sqlite3.connect(LUX_DB)
    c = conn.cursor()
    
    c.execute('''
    SELECT name FROM sqlite_master 
    WHERE type='table' AND name='lux_table';
    ''')
    table_exists = c.fetchone()
    
    if not table_exists:
        conn.close()
        return f"No data found. Table does not exist yet."
    
    now = int(time.time_ns()) // 1_000_000
    cutoff = now - time_minutes * 60 * 1000
    
    c.execute('''
    SELECT ts, lux, battery_voltage
    FROM lux_table
    WHERE ts >= ? AND kerberos = ?
    ORDER BY ts DESC;
    ''',
    (cutoff, kerberos))
    
    prev_data = c.fetchall()
    conn.close()
    
    if not prev_data:
        return f"No data found for kerberos '{kerberos}' in the last {time_minutes} minutes."
    
    outs = ""
    for row in prev_data:
        ts, lux_val, battery_voltage = row
        readable_time = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
        outs += f"Time: {readable_time} ({ts}), Lux: {lux_val}, Battery Voltage: {battery_voltage}\n"
    
    return outs