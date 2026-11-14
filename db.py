import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

class Database:
    def __init__(self,path:Path):
        self.path=path; path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS articles(
            url TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT,source TEXT,
            published_at TEXT NOT NULL,category TEXT,summary TEXT,why TEXT,
            published INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,started TEXT NOT NULL,finished TEXT,
            status TEXT NOT NULL,details TEXT)""")
    def connect(self):
        c=sqlite3.connect(self.path,timeout=20); c.row_factory=sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=20000"); return c
    def add(self,items):
        now=datetime.now(timezone.utc).isoformat(); n=0
        with self.connect() as c:
            for x in items:
                cur=c.execute("INSERT OR IGNORE INTO articles(url,title,description,source,published_at,created_at) VALUES(?,?,?,?,?,?)",
                    (x["url"],x["title"][:500],x.get("description","")[:3000],x.get("source","")[:200],x["published_at"],now))
                n+=cur.rowcount
        return n
    def pending(self,hours):
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat()
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM articles WHERE published=0 AND published_at>=? ORDER BY published_at DESC LIMIT 100",(cutoff,))]
    def annotate(self,items):
        with self.connect() as c:
            for x in items:
                c.execute("UPDATE articles SET category=?,summary=?,why=? WHERE url=?",
                    (x["category"],x["summary"],x["why"],x["url"]))
    def mark(self,urls):
        with self.connect() as c: c.executemany("UPDATE articles SET published=1 WHERE url=?",[(u,) for u in urls])
    def start(self):
        with self.connect() as c:
            return c.execute("INSERT INTO runs(started,status) VALUES(?, 'running')",(datetime.now(timezone.utc).isoformat(),)).lastrowid
    def finish(self,i,status,details=""):
        with self.connect() as c: c.execute("UPDATE runs SET finished=?,status=?,details=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),status,details[:1500],i))
    def status(self):
        with self.connect() as c:
            counts=c.execute("SELECT COUNT(*),SUM(published) FROM articles").fetchone()
            last=c.execute("SELECT started,status,details FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            return {"total":counts[0],"published":counts[1] or 0,"pending":counts[0]-(counts[1] or 0),"last":dict(last) if last else None}
