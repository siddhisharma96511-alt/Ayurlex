"""Check your MySQL set-up in 5 seconds:  python check_db.py"""
from dotenv import load_dotenv
load_dotenv()
from db import store

print("Connecting to MySQL ...")
if not store.init():
    print("\nFAILED:", store.error)
    print("\nCommon fixes:\n  1. Is MySQL running? (Windows: open Services or XAMPP and start MySQL)"
          "\n  2. Is DB_PASSWORD in your .env file correct? (XAMPP default password is empty)"
          "\n  3. Did you run: pip install -r requirements.txt ?")
    raise SystemExit(1)
uid = store.new_user("check-user")
cid = store.create_chat(uid, "Database check")
store.add_message(cid, "user", "Is ashwagandha safe in pregnancy?")
found = store.search(uid, "pregnancy")
store.run("DELETE FROM users WHERE id=%s", (uid,))
print("OK: tables exist, saving works, history search works" if found else "Saved, but search returned nothing - please report this.")
print("Full-text search:", "on" if store.fulltext else "off (LIKE search is used instead, that is fine)")
