"""Local settings, encrypted secrets and bounded aggregate statistics."""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet

LOGGER = logging.getLogger(__name__)


class PanelStore:
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise RuntimeError("Panel state directory must be private (0700)")
        key_path = directory / "encryption.key"
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
        if key_path.is_symlink() or key_path.stat().st_mode & 0o077:
            raise RuntimeError("Panel encryption key must be private (0600)")
        self.cipher = Fernet(key_path.read_bytes())
        self.path = directory / "settings.sqlite3"
        if self.path.is_symlink():
            raise RuntimeError("Invalid panel database path")
        self.credentials: dict[str, str] = {}
        self.flags: dict[str, bool] = {}
        self.options: dict[str, str] = {}
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS credentials(name TEXT PRIMARY KEY, value BLOB);
                CREATE TABLE IF NOT EXISTS options(name TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, value INTEGER);
                CREATE TABLE IF NOT EXISTS flags(name TEXT PRIMARY KEY, enabled INTEGER);
                CREATE TABLE IF NOT EXISTS metrics(
                    name TEXT PRIMARY KEY, requests INTEGER, ok INTEGER,
                    errors INTEGER, empty INTEGER, limited INTEGER, seconds REAL
                );
            """)
            for name, value in db.execute("SELECT name,value FROM credentials"):
                self.credentials[name] = self.cipher.decrypt(value).decode()
            self.flags = dict(db.execute("SELECT name,enabled FROM flags"))
            self.options = dict(db.execute("SELECT name,value FROM options"))
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def update(self, name: str, env: str | None, enabled: bool,
               credential: str | None, remove: bool) -> None:
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO flags VALUES (?,?)", (name, int(enabled)))
            if env and remove:
                db.execute("DELETE FROM credentials WHERE name=?", (env,))
            elif env and credential is not None:
                db.execute("INSERT OR REPLACE INTO credentials VALUES (?,?)",
                           (env, self.cipher.encrypt(credential.encode())))
        self.flags[name] = enabled
        if env and remove:
            self.credentials.pop(env, None)
        elif env and credential is not None:
            self.credentials[env] = credential

    def set_options(self, values: dict[str, str | None]) -> None:
        with closing(self.connect()) as db, db:
            for name, value in values.items():
                if value is None:
                    db.execute("DELETE FROM options WHERE name=?", (name,))
                else:
                    db.execute("INSERT OR REPLACE INTO options VALUES (?,?)", (name, value))
        for name, value in values.items():
            if value is None:
                self.options.pop(name, None)
            else:
                self.options[name] = value

    def cache_result(self, hit: bool) -> None:
        try:
            with closing(self.connect()) as db, db:
                db.execute("""
                    INSERT INTO counters VALUES (?,1)
                    ON CONFLICT(name) DO UPDATE SET value=value+1
                """, ("cache_hits" if hit else "cache_misses",))
        except sqlite3.Error:
            LOGGER.warning("Panel counter write failed")

    def counters(self):
        with closing(self.connect()) as db:
            return dict(db.execute("SELECT name,value FROM counters"))

    def record(self, name: str, outcome: str, elapsed: float) -> None:
        try:
            with closing(self.connect()) as db, db:
                db.execute("""
                    INSERT INTO metrics VALUES (?,1,?,?,?,?,?)
                    ON CONFLICT(name) DO UPDATE SET requests=requests+1,
                    ok=ok+excluded.ok, errors=errors+excluded.errors,
                    empty=empty+excluded.empty, limited=limited+excluded.limited,
                    seconds=seconds+excluded.seconds
                """, (name, int(outcome == "ok"), int(outcome not in {"ok", "empty"}),
                      int(outcome == "empty"), int(outcome == "HTTP 429"), elapsed))
        except sqlite3.Error:
            LOGGER.warning("Panel metric write failed")

    def metrics(self):
        with closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM metrics ORDER BY name")]
