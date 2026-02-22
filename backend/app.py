from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os
import secrets
import sqlite3
from datetime import datetime
import time
app = FastAPI(
    title="Team2 Backend",
    openapi_url=f"/openapi.json",
    docs_url=f"/docs",
    redoc_url=f"/redoc",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "frontend" / "assets"
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "monotream")
_admin_sessions: set[str] = set()


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