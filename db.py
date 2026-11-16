import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS articles(
                url TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT, source TEXT,
                published_at TEXT NOT NULL, category TEXT, summary TEXT, why TEXT,
                published INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                image_url TEXT, title_ru TEXT)""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(articles)")}
            if "image_url" not in columns:
                connection.execute("ALTER TABLE articles ADD COLUMN image_url TEXT")
            if "title_ru" not in columns:
                connection.execute("ALTER TABLE articles ADD COLUMN title_ru TEXT")
            connection.execute("""CREATE TABLE IF NOT EXISTS runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT NOT NULL,
                finished TEXT, status TEXT NOT NULL, details TEXT)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS daily_batches(
                day TEXT PRIMARY KEY, prepared_at TEXT NOT NULL, status TEXT NOT NULL,
                details TEXT)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS daily_queue(
                id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, slot INTEGER NOT NULL,
                article_url TEXT NOT NULL, image_url TEXT NOT NULL, caption TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', sent_at TEXT, error TEXT,
                UNIQUE(day, slot), UNIQUE(day, article_url))""")

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    def add(self, items):
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        with self.connect() as connection:
            for item in items:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO articles(
                        url,title,description,source,published_at,created_at,image_url
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (item["url"], item["title"][:500], item.get("description", "")[:3000],
                     item.get("source", "")[:200], item["published_at"], now,
                     item.get("image_url")),
                )
                inserted += cursor.rowcount
                if item.get("image_url"):
                    connection.execute(
                        "UPDATE articles SET image_url=? WHERE url=? AND (image_url IS NULL OR image_url='')",
                        (item["image_url"], item["url"]),
                    )
        return inserted

    def pending(self, hours):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT * FROM articles
                   WHERE published=0 AND published_at>=?
                   ORDER BY published_at DESC LIMIT 150""", (cutoff,)
            )]

    def annotate(self, items):
        with self.connect() as connection:
            for item in items:
                connection.execute(
                    """UPDATE articles SET category=?,summary=?,why=?,title_ru=?,image_url=COALESCE(NULLIF(image_url,''),?)
                       WHERE url=?""",
                    (item["category"], item["summary"], item["why"],
                     item.get("title_ru"), item.get("image_url"), item["url"]),
                )

    def mark(self, urls):
        with self.connect() as connection:
            connection.executemany("UPDATE articles SET published=1 WHERE url=?", [(url,) for url in urls])

    def has_prepared_day(self, day):
        with self.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM daily_batches WHERE day=?", (day,)
            ).fetchone() is not None

    def enqueue_day(self, day, items, details=""):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO daily_batches(day,prepared_at,status,details) VALUES(?,?,?,?)",
                (day, now, "ready" if items else "empty", details[:1000]),
            )
            for slot, item in enumerate(items, start=1):
                connection.execute(
                    """INSERT OR IGNORE INTO daily_queue(
                        day,slot,article_url,image_url,caption,status
                    ) VALUES(?,?,?,?,?,'pending')""",
                    (day, slot, item["url"], item["image_url"], item["caption"]),
                )

    def next_queued(self, day):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM daily_queue WHERE day=? AND status='pending'
                   ORDER BY slot LIMIT 1""", (day,)
            ).fetchone()
            return dict(row) if row else None

    def queue_sent(self, queue_id):
        with self.connect() as connection:
            connection.execute(
                "UPDATE daily_queue SET status='sent',sent_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), queue_id),
            )

    def queue_failed(self, queue_id, error):
        with self.connect() as connection:
            connection.execute(
                "UPDATE daily_queue SET status='failed',error=? WHERE id=?",
                (str(error)[:500], queue_id),
            )

    def queue_counts(self, day):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status,COUNT(*) AS n FROM daily_queue WHERE day=? GROUP BY status",
                (day,),
            ).fetchall()
            return {row["status"]: row["n"] for row in rows}

    def start(self):
        with self.connect() as connection:
            return connection.execute(
                "INSERT INTO runs(started,status) VALUES(?, 'running')",
                (datetime.now(timezone.utc).isoformat(),),
            ).lastrowid

    def finish(self, run_id, status, details=""):
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET finished=?,status=?,details=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), status, details[:1500], run_id),
            )

    def status(self):
        with self.connect() as connection:
            counts = connection.execute(
                "SELECT COUNT(*),SUM(published) FROM articles"
            ).fetchone()
            last = connection.execute(
                "SELECT started,status,details FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return {
                "total": counts[0], "published": counts[1] or 0,
                "pending": counts[0] - (counts[1] or 0),
                "last": dict(last) if last else None,
            }
