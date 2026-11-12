"""LOCAL DEMO ONLY: a tiny stand-in for mysql.connector backed by SQLite, so the
site can run on a laptop without the real database. Never used in production."""
import datetime
import os
import re
import sqlite3

DB = os.environ.get('DEMO_SQLITE', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'demo.db'))
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime.datetime, lambda d: d.isoformat(sep=' '))


class Error(Exception):
    pass


def _tr(sql):
    sql = re.sub(r'\bINT\s+AUTO_INCREMENT\s+PRIMARY\s+KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT', sql, flags=re.I)
    sql = re.sub(r'\bNOW\(\)', "datetime('now')", sql, flags=re.I)
    sql = re.sub(r'ON DUPLICATE KEY UPDATE.*$', '', sql, flags=re.I | re.S)
    return sql.replace('%s', '?')


class Cursor:
    def __init__(self, c, dictionary):
        self.c, self.d = c, dictionary

    def execute(self, sql, params=()):
        self.c.execute(_tr(sql), tuple(params or ()))

    @property
    def description(self):
        return self.c.description

    @property
    def lastrowid(self):
        return self.c.lastrowid

    @property
    def rowcount(self):
        return self.c.rowcount

    def fetchall(self):
        rows = self.c.fetchall()
        if self.d and self.c.description:
            cols = [x[0] for x in self.c.description]
            return [dict(zip(cols, r)) for r in rows]
        return rows

    def fetchone(self):
        r = self.c.fetchone()
        if r is not None and self.d:
            return dict(zip([x[0] for x in self.c.description], r))
        return r

    def close(self):
        self.c.close()


class Conn:
    def __init__(self):
        self.x = sqlite3.connect(DB, timeout=15)

    def cursor(self, dictionary=False, **k):
        return Cursor(self.x.cursor(), dictionary)

    def commit(self):
        self.x.commit()

    def rollback(self):
        self.x.rollback()

    def close(self):
        self.x.close()


def connect(**cfg):
    return Conn()
