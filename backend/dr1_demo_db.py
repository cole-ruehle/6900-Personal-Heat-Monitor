from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class AreaSeed:
    area_id: str
    name: str
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    temp_c_offset: float
    rh_offset: float


def _utc_sqlite_ts(dt: datetime) -> str:
    # Match SQLite datetime('now') default format: YYYY-MM-DD HH:MM:SS
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _ensure_schema(conn: sqlite3.Connection, schema_sql_path: Path) -> None:
    sql = schema_sql_path.read_text(encoding="utf-8")
    conn.executescript(sql)


def _table_has_rows(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1;").fetchone()
    return row is not None


def ensure_dr1_demo_db(repo_root: Path) -> Path:
    """
    Creates and seeds a DR1 demo database at `database/dr1_demo.db` if it does not exist
    (or exists but is empty). Returns the db path.
    """
    db_dir = repo_root / "database"
    db_path = db_dir / "dr1_demo.db"
    schema_path = db_dir / "dr1_schema.sql"

    db_dir.mkdir(parents=True, exist_ok=True)
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")

    conn = _connect(db_path)
    try:
        _ensure_schema(conn, schema_path)

        _ensure_areas_seeded(conn)

        # Seed only if we have sufficient demo readings (idempotent-ish).
        device_count = conn.execute("SELECT COUNT(*) FROM devices;").fetchone()[0]
        reading_count = conn.execute("SELECT COUNT(*) FROM readings;").fetchone()[0]
        if device_count >= 30 and reading_count >= 30 * 60:
            return db_path

        _reset_demo_data(conn)
        _seed_demo_rows(conn)
        conn.commit()
        return db_path
    finally:
        conn.close()


def _ensure_areas_seeded(conn: sqlite3.Connection) -> None:
    # Coordinates are (min_lon, min_lat, max_lon, max_lat)
    areas: list[AreaSeed] = [
        AreaSeed(
            area_id="kendallEastGateway",
            name="Kendall Square / MIT East Gateway",
            min_lon=-71.0928,
            min_lat=42.3610,
            max_lon=-71.0856,
            max_lat=42.3662,
            temp_c_offset=1.2,
            rh_offset=-2.0,
        ),
        AreaSeed(
            area_id="mainCampusCore",
            name="Main Campus Core (Killian / Infinite Corridor)",
            min_lon=-71.0959,
            min_lat=42.3566,
            max_lon=-71.0892,
            max_lat=42.3609,
            temp_c_offset=0.3,
            rh_offset=0.0,
        ),
        AreaSeed(
            area_id="stataVassarNorth",
            name="Stata / Vassar Street North",
            min_lon=-71.0938,
            min_lat=42.3619,
            max_lon=-71.0887,
            max_lat=42.3657,
            temp_c_offset=0.9,
            rh_offset=-1.0,
        ),
        AreaSeed(
            area_id="sloanMediaLabAmes",
            name="Sloan / Media Lab / Ames Street",
            min_lon=-71.0912,
            min_lat=42.3573,
            max_lon=-71.0866,
            max_lat=42.3614,
            temp_c_offset=0.6,
            rh_offset=0.8,
        ),
        AreaSeed(
            area_id="westCampusKresgeSimmons",
            name="West Campus (Kresge / Simmons)",
            min_lon=-71.1038,
            min_lat=42.3548,
            max_lon=-71.0957,
            max_lat=42.3606,
            temp_c_offset=-0.4,
            rh_offset=1.5,
        ),
        AreaSeed(
            area_id="nwAlbanyAthletics",
            name="NW Campus / Albany / Athletics corridor",
            min_lon=-71.1093,
            min_lat=42.3602,
            max_lon=-71.1007,
            max_lat=42.3673,
            temp_c_offset=-0.2,
            rh_offset=1.0,
        ),
    ]

    conn.executemany(
        """
        INSERT OR IGNORE INTO areas (area_id, name, min_lon, min_lat, max_lon, max_lat)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        [
            (a.area_id, a.name, a.min_lon, a.min_lat, a.max_lon, a.max_lat)
            for a in areas
        ],
    )


def _reset_demo_data(conn: sqlite3.Connection) -> None:
    # Wipe demo fact tables; keep areas.
    # Order matters due to foreign keys.
    conn.execute("DELETE FROM device_status;")
    conn.execute("DELETE FROM surveys;")
    conn.execute("DELETE FROM readings;")
    conn.execute("DELETE FROM devices;")
    conn.execute("DELETE FROM users;")


def _seed_demo_rows(conn: sqlite3.Connection) -> None:
    """
    Seed a richer demo:
    - 1 user
    - ~60 devices distributed across areas
    - 12 hours of readings every 2 minutes per device
    - Device motion: bounded random walk inside assigned area bbox
    """
    user_id = "u_demo_tester"
    conn.execute(
        """
        INSERT OR IGNORE INTO users (user_id, first_name, last_name, email, role)
        VALUES (?, ?, ?, ?, ?);
        """,
        (user_id, "Demo", "Tester", "demo.tester@example.com", "tester"),
    )

    areas_rows = conn.execute(
        """
        SELECT area_id, name, min_lon, min_lat, max_lon, max_lat
        FROM areas
        ORDER BY area_id ASC;
        """
    ).fetchall()
    if not areas_rows:
        raise RuntimeError("areas table is empty; expected seeded areas")

    areas: list[AreaSeed] = []
    # default offsets if not present (should not happen)
    offsets = {
        "kendallEastGateway": (1.2, -2.0),
        "mainCampusCore": (0.3, 0.0),
        "stataVassarNorth": (0.9, -1.0),
        "sloanMediaLabAmes": (0.6, 0.8),
        "westCampusKresgeSimmons": (-0.4, 1.5),
        "nwAlbanyAthletics": (-0.2, 1.0),
    }
    for r in areas_rows:
        t_off, rh_off = offsets.get(r["area_id"], (0.0, 0.0))
        areas.append(
            AreaSeed(
                area_id=r["area_id"],
                name=r["name"],
                min_lon=float(r["min_lon"]),
                min_lat=float(r["min_lat"]),
                max_lon=float(r["max_lon"]),
                max_lat=float(r["max_lat"]),
                temp_c_offset=float(t_off),
                rh_offset=float(rh_off),
            )
        )

    rng = random.Random(6900)

    devices_per_area = 10
    device_ids: list[tuple[str, AreaSeed]] = []
    for a in areas:
        for i in range(devices_per_area):
            device_id = f"d_demo_{a.area_id}_{i:02d}"
            device_ids.append((device_id, a))
            conn.execute(
                """
                INSERT INTO devices (device_id, user_id, status, version)
                VALUES (?, ?, 'active', '1');
                """,
                (device_id, user_id),
            )

    now = datetime.now(tz=UTC)
    start = now - timedelta(hours=12)
    step = timedelta(minutes=2)
    points = int((now - start) / step) + 1

    # Initialize device positions inside area bboxes.
    pos: dict[str, tuple[float, float]] = {}
    for device_id, a in device_ids:
        lat = rng.uniform(a.min_lat, a.max_lat)
        lon = rng.uniform(a.min_lon, a.max_lon)
        pos[device_id] = (lat, lon)

    for idx in range(points):
        ts = start + idx * step
        phase = (idx / max(points - 1, 1)) * 2 * math.pi

        for d_i, (device_id, a) in enumerate(device_ids):
            # Random walk step, clamped to bbox.
            lat, lon = pos[device_id]
            lat += rng.uniform(-0.00010, 0.00010)
            lon += rng.uniform(-0.00014, 0.00014)
            lat = min(max(lat, a.min_lat), a.max_lat)
            lon = min(max(lon, a.min_lon), a.max_lon)
            pos[device_id] = (lat, lon)

            # Smooth signal + per-area offset + slight per-device phase shift.
            device_phase = phase + (d_i % 9) * 0.15
            base_temp_c = 26.5 + a.temp_c_offset
            base_rh = 54.0 + a.rh_offset
            temp_c = base_temp_c + 1.8 * math.sin(device_phase) + 0.4 * math.sin(device_phase * 0.4)
            rh = base_rh + 7.0 * math.cos(device_phase) + 2.0 * math.sin(device_phase * 0.3)
            rh = min(max(rh, 20.0), 95.0)

            conn.execute(
                """
                INSERT INTO readings (
                  device_id, timestamp, lat, lon, location_accuracy,
                  temp_c, humidity_rh, lux, uv_index, accel_x, accel_y, accel_z
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    device_id,
                    _utc_sqlite_ts(ts),
                    float(f"{lat:.6f}"),
                    float(f"{lon:.6f}"),
                    10.0,
                    float(f"{temp_c:.3f}"),
                    float(f"{rh:.3f}"),
                    140.0 + 80.0 * math.sin(device_phase),
                    1.5 + 1.2 * math.sin(device_phase * 0.8),
                    0.02 * math.sin(device_phase),
                    0.02 * math.cos(device_phase),
                    0.98,
                ),
            )

