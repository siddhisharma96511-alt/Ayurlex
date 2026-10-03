"""MySQL storage: users, chats, messages (with search) and feedback.
The database and tables are created automatically on start-up. If MySQL is not reachable the app still answers
questions; it just cannot save history (the header shows "History off")."""
import json
import os

try:
    import mysql.connector
except ImportError:          # driver not installed
    mysql = None


def _like(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class Store:
    def __init__(self):
        self.ok, self.error, self.fulltext = False, None, False
        self.cfg = dict(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "3306")),
                        user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""))
        self.name = os.getenv("DB_NAME", "ayurlex")

    # ---- connection helpers
    def _connect(self, with_db=True):
        if mysql is None:
            raise RuntimeError("mysql-connector-python is not installed (pip install -r requirements.txt)")
        cfg = dict(self.cfg, charset="utf8mb4", connection_timeout=5)
        if with_db:
            cfg["database"] = self.name
        return mysql.connector.connect(**cfg)

    def run(self, sql, args=(), fetch=False):
        """Run one statement. Returns rows (fetch=True) or the last inserted id. None means the database failed."""
        try:
            con = self._connect()
            try:
                cur = con.cursor(dictionary=True)
                cur.execute(sql, args)
                res = cur.fetchall() if fetch else cur.lastrowid
                con.commit()
                cur.close()
                self.ok, self.error = True, None
                return res
            finally:
                con.close()
        except Exception as e:
            self.ok, self.error = False, f"{type(e).__name__}: {e}"
            print("[DB]", self.error)
            return None

    # ---- set-up
    def init(self):
        try:
            con = self._connect(with_db=False)
            cur = con.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{self.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            con.commit()
            cur.close()
            con.close()
            con = self._connect()
            cur = con.cursor()
            for ddl in SCHEMA:
                cur.execute(ddl)
            try:                                   # FULLTEXT is optional: search still works with LIKE if it fails
                cur.execute("SHOW INDEX FROM messages WHERE Key_name='ft_content'")
                if not cur.fetchall():
                    cur.execute("ALTER TABLE messages ADD FULLTEXT KEY ft_content (content)")
                self.fulltext = True
            except Exception as e:
                print("[DB] FULLTEXT index skipped:", e)
            con.commit()
            cur.close()
            con.close()
            self.ok, self.error = True, None
            print(f"[DB] Connected to MySQL database '{self.name}' (fulltext search: {'on' if self.fulltext else 'off'})")
        except Exception as e:
            self.ok, self.error = False, f"{type(e).__name__}: {e}"
            print("[DB] MySQL not available, history is off:", self.error)
        return self.ok

    def status(self):
        return {"ok": self.ok, "error": self.error}

    # ---- users and chats
    def new_user(self, username):
        return self.run("INSERT INTO users (username) VALUES (%s)", (username,))

    def create_chat(self, uid, title):
        return self.run("INSERT INTO chats (user_id, title) VALUES (%s, %s)", (uid, title[:140]))

    def owns(self, uid, cid):
        rows = self.run("SELECT id FROM chats WHERE id=%s AND user_id=%s", (cid, uid), True)
        return bool(rows)

    def add_message(self, chat_id, role, content, intent=None, sources=None, trace=None):
        mid = self.run("INSERT INTO messages (chat_id, role, content, intent, sources, trace) VALUES (%s,%s,%s,%s,%s,%s)",
                       (chat_id, role, content, intent, json.dumps(sources or [], ensure_ascii=False),
                        json.dumps(trace or {}, ensure_ascii=False)))
        self.run("UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=%s", (chat_id,))
        return mid

    def list_chats(self, uid, limit=50):
        return self.run("SELECT id, title, updated_at FROM chats WHERE user_id=%s ORDER BY updated_at DESC, id DESC LIMIT %s",
                        (uid, limit), True) or []

    def get_chat(self, uid, cid):
        if not self.owns(uid, cid):
            return None
        rows = self.run("""SELECT m.id, m.role, m.content, m.intent, m.sources, m.trace, f.rating
                           FROM messages m LEFT JOIN feedback f ON f.message_id = m.id
                           WHERE m.chat_id=%s ORDER BY m.id""", (cid,), True) or []
        for r in rows:
            r["sources"] = json.loads(r["sources"]) if r["sources"] else []
            r["trace"] = json.loads(r["trace"]) if r["trace"] else {}
        return rows

    def delete_chat(self, uid, cid):
        if not self.owns(uid, cid):
            return False
        self.run("DELETE FROM chats WHERE id=%s AND user_id=%s", (cid, uid))
        return True

    # ---- search across everything this user has asked or been answered
    def search(self, uid, text, limit=30):
        text = text.strip()
        if not text:
            return []
        like = _like(text)
        if self.fulltext and len(text) >= 3:
            where = "(MATCH(m.content) AGAINST (%s IN NATURAL LANGUAGE MODE) OR m.content LIKE %s OR c.title LIKE %s)"
            args = (like, uid, text, like, like, limit)
        else:
            where = "(m.content LIKE %s OR c.title LIKE %s)"
            args = (like, uid, like, like, limit)
        # rows whose message text matches come first, so the snippet shows the real match
        rows = self.run(f"""SELECT m.id, m.chat_id, c.title, m.role, m.content, m.created_at, (m.content LIKE %s) AS hit
                            FROM messages m JOIN chats c ON c.id = m.chat_id
                            WHERE c.user_id=%s AND {where} ORDER BY hit DESC, m.id DESC LIMIT %s""", args, True) or []
        out, seen = [], set()
        for r in rows:
            if r["chat_id"] in seen:
                continue                                   # one result per conversation
            seen.add(r["chat_id"])
            c, i = r["content"], r["content"].lower().find(text.lower())
            start = max(0, i - 40) if i >= 0 else 0
            out.append({"chat_id": r["chat_id"], "title": r["title"], "role": r["role"],
                        "snippet": ("..." if start else "") + c[start:start + 140] + ("..." if len(c) > start + 140 else ""),
                        "when": r["created_at"].strftime("%d %b %H:%M") if r["created_at"] else ""})
        return out

    # ---- feedback and usage numbers
    def set_feedback(self, uid, message_id, rating):
        rows = self.run("""SELECT m.id FROM messages m JOIN chats c ON c.id=m.chat_id
                           WHERE m.id=%s AND c.user_id=%s AND m.role='bot'""", (message_id, uid), True)
        if not rows:
            return False
        self.run("INSERT INTO feedback (message_id, rating) VALUES (%s,%s) ON DUPLICATE KEY UPDATE rating=%s",
                 (message_id, rating, rating))
        return True

    def stats(self):
        r = self.run("""SELECT (SELECT COUNT(*) FROM chats) AS chats, (SELECT COUNT(*) FROM messages WHERE role='user') AS questions,
                        (SELECT COUNT(*) FROM feedback WHERE rating=1) AS helpful,
                        (SELECT COUNT(*) FROM feedback WHERE rating=-1) AS not_helpful""", (), True)
        return r[0] if r else {}


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(60) NOT NULL UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS chats (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        title VARCHAR(140),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_chats_user (user_id, updated_at),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS messages (
        id INT AUTO_INCREMENT PRIMARY KEY,
        chat_id INT NOT NULL,
        role VARCHAR(8) NOT NULL,
        content TEXT NOT NULL,
        intent VARCHAR(40),
        sources LONGTEXT,
        trace LONGTEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_messages_chat (chat_id),
        FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS feedback (
        id INT AUTO_INCREMENT PRIMARY KEY,
        message_id INT NOT NULL UNIQUE,
        rating TINYINT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]

store = Store()
