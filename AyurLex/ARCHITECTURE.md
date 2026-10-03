# AyurLex architecture (for your PPT)

```
Question -> Language check -> Glossary (Hindi/Hinglish -> English) -> Herb detection (typo tolerant)
         -> Intent classifier (TF-IDF + Logistic Regression) -> Hybrid search (BM25 + char TF-IDF, herb/topic boost)
         -> Safety layer (dose refusal, emergency / pregnancy / child / interaction notices)
         -> Answer (Gemini/OpenAI from sources only, or quoted passages) -> Guardrails (no doses, valid citations)
         -> Cited answer + "How I answered" trace -> MySQL history (searchable)
```

## ML pieces (all scikit-learn / plain Python, explainable)
| Piece | Technique | Why |
|---|---|---|
| Intent classifier | TF-IDF (word + char) + Logistic Regression, herb names masked | Routes law vs medicine vs dosing vs out of scope |
| Retrieval | BM25 + character n-gram TF-IDF, fused | Keywords plus typo tolerance, runs offline in about 5 ms |
| Entity matching | Dictionary + difflib fuzzy match | "ashwaganda" still finds Ashwagandha |
| Rule + model routing | Regex rules override the model for legal words and dose words | Safety first |
| Hallucination control | Answer only from retrieved passages; abstain below a score; refuse unknown herbs | Never guesses |
| Guardrails | Regex check for doses, citation validation | LLM output is verified |

## Safety design
- Doses are never given. Dose questions get a fixed answer plus the herb's safety points.
- Unknown herbs, off-topic questions and weak matches are declined, not guessed.
- Emergency, pregnancy, children, medicine-interaction and surgery notices are added automatically.

## MySQL tables
users, chats, messages (FULLTEXT index, sources and pipeline trace stored as JSON text), feedback (helpful / not helpful).

## Files
app.py (Flask API) | pipeline.py (ML + RAG) | db.py (MySQL) | evaluate.py (metrics) | check_db.py | data/docs (knowledge base) | data/intents.json (training) | data/glossary.json | data/eval_set.json | templates/ static/ (UI)
