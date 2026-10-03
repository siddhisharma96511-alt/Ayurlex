"""AyurLex web server (Flask). Run:  python app.py"""
import json
import os
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, render_template, request, session  # noqa: E402

from db import store  # noqa: E402
from pipeline import DATA, engine, llm_name  # noqa: E402

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "ayurlex-dev-secret-change-me")
app.permanent_session_lifetime = timedelta(days=365)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", JSON_AS_ASCII=False)

_hits = defaultdict(deque)       # simple per-IP rate limit so nobody burns your API quota
_last_db_try = [0.0]


def rate_limited(limit=40, window=60):
    q, now = _hits[request.remote_addr], time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def ensure_db():
    if not store.ok and time.time() - _last_db_try[0] > 30:     # MySQL started after the app? try again
        _last_db_try[0] = time.time()
        store.init()


def user_id():
    """Each browser is a guest user (cookie). If the saved id no longer exists in MySQL, make a new one."""
    ensure_db()
    if not store.ok:
        return None
    uid = session.get("uid")
    if uid and store.run("SELECT id FROM users WHERE id=%s", (uid,), True):
        return uid
    uid = store.new_user("guest-" + uuid.uuid4().hex[:10])
    if uid:
        session.permanent = True
        session["uid"] = uid
    return uid


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/status")
def status():
    ensure_db()
    return jsonify(app="AyurLex", db=store.status(), llm=llm_name(), docs=len(engine.docs), passages=len(engine.chunks),
                   usage=store.stats() if store.ok else {})


@app.get("/api/metrics")
def metrics():
    path = os.path.join(DATA, "eval_report.json")
    return jsonify(json.load(open(path)) if os.path.exists(path) else {})


@app.get("/api/docs")
def docs():
    return jsonify(engine.docs)


@app.post("/api/chat")
def chat():
    if rate_limited():
        return jsonify(error="Too many questions. Please wait a minute."), 429
    d = request.get_json(silent=True) or {}
    q = (d.get("query") or "").strip()
    if not q:
        return jsonify(error="Type a question first."), 400
    if len(q) > 600:
        return jsonify(error="Please keep the question under 600 characters."), 400
    res = engine.answer(q, d.get("lang") or "English", d.get("context") or {})

    chat_id, message_id = d.get("chat_id"), None
    uid = user_id()
    if uid:
        if chat_id and not store.owns(uid, chat_id):
            chat_id = None
        if not chat_id:
            chat_id = store.create_chat(uid, q)
        if chat_id:
            store.add_message(chat_id, "user", q)
            meta = {k: v for k, v in res.items() if k not in ("answer", "sources")}
            message_id = store.add_message(chat_id, "bot", res["answer"], res["intent"], res["sources"], meta)
    res.update(chat_id=chat_id, message_id=message_id, history=bool(chat_id))
    return jsonify(res)


@app.get("/api/chats")
def chats():
    uid = user_id()
    rows = store.list_chats(uid) if uid else []
    return jsonify([{"id": r["id"], "title": r["title"],
                     "when": r["updated_at"].strftime("%d %b %H:%M") if r["updated_at"] else ""} for r in rows])


@app.get("/api/chats/<int:cid>")
def chat_detail(cid):
    uid = user_id()
    rows = store.get_chat(uid, cid) if uid else None
    return jsonify(rows) if rows is not None else (jsonify(error="Conversation not found."), 404)


@app.delete("/api/chats/<int:cid>")
def chat_delete(cid):
    uid = user_id()
    return jsonify(ok=bool(uid and store.delete_chat(uid, cid)))


@app.get("/api/search")
def search():
    uid = user_id()
    q = (request.args.get("q") or "").strip()[:100]
    return jsonify(store.search(uid, q) if uid and q else [])


@app.post("/api/feedback")
def feedback():
    d = request.get_json(silent=True) or {}
    uid = user_id()
    rating = 1 if d.get("rating") == 1 else -1
    ok = bool(uid and d.get("message_id") and store.set_feedback(uid, int(d["message_id"]), rating))
    return jsonify(ok=ok)


@app.errorhandler(500)
def oops(_):
    return jsonify(error="Something went wrong on the server. Check the terminal for details."), 500


def boot():
    engine.load()
    store.init()


if __name__ == "__main__":
    boot()
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "5000"))
    print(f"\n  AyurLex is running: http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)
