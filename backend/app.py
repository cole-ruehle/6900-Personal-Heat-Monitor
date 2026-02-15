from fastapi import FastAPI, Form
import sqlite3
from datetime import datetime
import time
app = FastAPI(
    title="Team2 Backend",
    openapi_url=f"/openapi.json",
    docs_url=f"/docs",
    redoc_url=f"/redoc",
)
	 
@app.get("/")
async def root():
    return "hey there"

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