PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY DEFAULT ('u_' || lower(hex(randomblob(16)))),
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL COLLATE NOCASE,
  role TEXT NOT NULL CHECK (role IN ('admin', 'tester')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY DEFAULT ('d_' || lower(hex(randomblob(16)))),
  user_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
  version TEXT NOT NULL DEFAULT '1',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  deleted_at TEXT,
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS device_status (
  id INTEGER PRIMARY KEY,
  device_id TEXT NOT NULL,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  battery_level INTEGER NOT NULL CHECK (battery_level BETWEEN 0 AND 100),
  is_charging INTEGER NOT NULL DEFAULT 0 CHECK (is_charging IN (0,1)),
  last_charged TEXT,
  FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY,
  device_id TEXT NOT NULL,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  lat REAL,
  lon REAL,
  location_accuracy REAL,
  temp_c REAL,
  humidity_rh REAL,
  lux REAL,
  uv_index REAL,
  accel_x REAL,
  accel_y REAL,
  accel_z REAL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS surveys (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  device_id TEXT,
  heat_sensation INTEGER NOT NULL CHECK (heat_sensation BETWEEN 0 AND 10),
  comfort_level TEXT CHECK (comfort_level IN ('comfortable','neutral','uncomfortable')),
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
  FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS areas (
  area_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  min_lon REAL NOT NULL,
  min_lat REAL NOT NULL,
  max_lon REAL NOT NULL,
  max_lat REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
