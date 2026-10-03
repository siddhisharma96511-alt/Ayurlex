"""Measure how well AyurLex works. Run:  python evaluate.py
Writes data/eval_report.json, which the web app shows in its "How it works" panel."""
import json, os, time
from datetime import datetime
from sklearn.metrics import f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from pipeline import engine, DOSE_RE, DATA

engine.load()
tests = json.load(open(os.path.join(DATA, "eval_set.json"), encoding="utf-8"))

# 1) intent classifier: 5-fold cross-validation on the training examples
cv = StratifiedKFold(5, shuffle=True, random_state=7)
pred = cross_val_predict(engine.intent, engine.train_X, engine.train_y, cv=cv)
cv_acc = sum(p == t for p, t in zip(pred, engine.train_y)) / len(pred)
cv_f1 = f1_score(engine.train_y, pred, average="macro")

# 2) unseen test questions: intent, retrieval, behaviour
intent_ok = hit1 = hit3 = n_ret = beh_ok = leaks = 0
fails, total_ms = [], 0
for t in tests:
    a = engine.analyze(t["q"])
    out = engine.answer(t["q"], use_llm=False)
    total_ms += out["ms"]
    shown = "out_of_scope" if a["scope_refused"] else a["route"]
    if shown == t["intent"]:
        intent_ok += 1
    else:
        fails.append(f"INTENT  {t['q']!r}: expected {t['intent']}, got {shown}")
    if t["docs"] and t.get("mode") != "dose_policy":
        n_ret += 1
        ids = [h["id"] for h in a["hits"][:3]]
        if ids and ids[0] in t["docs"]: hit1 += 1
        else: fails.append(f"HIT@1   {t['q']!r}: expected {t['docs']}, got {ids}")
        if any(i in t["docs"] for i in ids): hit3 += 1
    expected = t.get("mode")
    good = (out["mode"] == expected) if expected else out["mode"] in ("extractive", "llm", "dose_policy")
    if good: beh_ok += 1
    else: fails.append(f"BEHAVE  {t['q']!r}: expected {expected or 'answer'}, got {out['mode']}")
    if DOSE_RE.search(out["answer"]): leaks += 1

n = len(tests)
rep = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "train_examples": len(engine.train_y), "test_questions": n,
       "documents": len(engine.docs), "passages": len(engine.chunks),
       "intent_cv_accuracy": round(cv_acc, 3), "intent_cv_macro_f1": round(cv_f1, 3),
       "intent_test_accuracy": round(intent_ok / n, 3), "retrieval_hit_at_1": round(hit1 / n_ret, 3),
       "retrieval_hit_at_3": round(hit3 / n_ret, 3), "behaviour_accuracy": round(beh_ok / n, 3),
       "dose_leaks": leaks, "avg_response_ms": int(total_ms / n)}
json.dump(rep, open(os.path.join(DATA, "eval_report.json"), "w"), indent=1)
print(json.dumps(rep, indent=1))
print("\nFailures:" if fails else "\nNo failures.")
for f in fails: print(" -", f)
