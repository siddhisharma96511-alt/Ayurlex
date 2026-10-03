"""AyurLex engine: understands a question, finds trusted sources, answers with citations.

Pipeline (each step is small and explainable):
  1. Language detection   - Unicode script check (English by default)
  2. Normalisation        - glossary turns Hindi/Hinglish terms into English
  3. Entity detection     - typo-tolerant herb matching (difflib)
  4. Intent classifier    - TF-IDF + Logistic Regression (scikit-learn), trained at start-up
  5. Hybrid retrieval     - BM25 + character n-gram TF-IDF, boosted by herb and intent
  6. Safety layer         - dose refusal, emergency / pregnancy / child / interaction notices
  7. Answer               - LLM (Gemini/OpenAI) grounded on sources, or extractive fallback
  8. Guardrails           - reject LLM output that contains doses or bad citations
"""
import difflib
import glob
import json
import math
import os
import re
import time
from collections import Counter

import numpy as np
import requests
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline, make_union

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

INTENT_LABELS = {
    "patent": "Patents",
    "traditional_knowledge": "Traditional knowledge",
    "licensing": "Licensing and drug law",
    "geo_trademark": "GI, trademark and plant rights",
    "medicine_info": "Medicine information",
    "dosing": "Dosing question",
    "out_of_scope": "Outside my scope",
}
LEGAL = {"patent", "traditional_knowledge", "licensing", "geo_trademark"}
MEDICAL = {"medicine_info", "dosing"}

SUGGESTIONS = [
    "Can I patent a herbal formulation based on a classical Ayurvedic text?",
    "What is TKDL and how does it stop bio-piracy?",
    "Is giloy safe for the liver?",
    "Which licence do I need to manufacture Ayurvedic medicines?",
]

STOP = set("""a an the of to in for is are was were be been can could i my me you your do does did how what which who whom when
where why it its this that these those and or on with as at by from about should will would shall may might am if so than then also
into like just please tell explain want know give need there any get have has had not no yes there here some more most very much
many""".split())

# Everyday words that are fine to appear in a question even if our sources never use them.
COMMON = """side effect effects benefit benefits safe safety good bad take taking taken use used using uses daily day days night
morning work works help helps cure treat treatment treating problem problems risk risks cause causes causing avoid allowed allow
healthy health people person women woman men man old older young adult adults baby babies child children kid kids drug drugs
medicine medicines medication tablet tablets pill pills capsule capsules powder juice tea oil herb herbs herbal natural
ayurveda ayurvedic remedy remedies product products brand company make made making buy sell selling online india indian law legal
rule rules need needs needed required require permission approval apply application file filing register registration
protect protection protected own owner right rights allowed possible reason rejected refused revoked valid long time years year
month months week weeks ever always never often sometimes combine mix together both either whether during before after while
over under between without within again still already really actually always best better worse common commonly general
generally normally usually regular regularly different difference same similar example check find found look looking know
knowledge information detail details tradition traditional traditionally""".split()

SAFETY_RULES = [
    ("emergency", r"overdos|poison|can.?t breathe|cannot breathe|chest pain|unconscious|seizure|severe allergic|swelling of (the )?(face|throat)|vomiting blood|lethal|swallowed too",
     "If this may be an emergency, call 112 (India) or go to the nearest hospital now."),
    ("pregnancy", r"pregnan|breastfeed|breast feed|lactat|nursing mother|trying to conceive|गर्भ",
     "Pregnancy or breastfeeding: use herbal products only on the advice of a doctor or registered practitioner."),
    ("children", r"\bchild\b|\bchildren\b|\bkids?\b|\binfants?\b|\bbab(y|ies)\b|\btoddlers?\b",
     "Children: do not give herbal products without advice from a doctor or registered practitioner."),
    ("medication", r"warfarin|blood thinner|insulin|thyroxine|prescription|antibiotic|other medicines?|with (my )?medicines?|taking medicines?",
     "Taking other medicines? Herbs can interact with them. Ask your doctor or pharmacist before combining."),
    ("surgery", r"surgery|operation|anaesthesia|anesthesia",
     "Before surgery: tell your surgeon about every herbal product and ask when to stop."),
]

DOSE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|µg|g|gm|gms|grams?|ml|millilit\w+|tablets?|tabs?|capsules?|caps|tsp|tbsp|teaspoons?|tablespoons?|drops?|spoons?)\b"
    r"|\b(?:once|twice|thrice)\s+(?:a\s+|per\s+)?(?:day|daily)\b", re.I)

LEGAL_WORDS = re.compile(r"patent|trademark|trade mark|licen[cs]e|\bgi\b|geographical|tkdl|biopiracy|bio-piracy|\blaw\b|\bact\b|copyright|dispute|revoked|litigation", re.I)
MEDICAL_WORDS = re.compile(r"side effect|safe|dose|dosage|benefit|pregnan|interaction|allerg|liver|kidney|diabet|used for|cautions?", re.I)

DOSE_WORDS = re.compile(r"\bdoses?\b|dosage|how much\b|how many\b|\bquantity\b|\bmg\b|per day|times a day|too much|lethal|overdose|maximum safe|\bspoons?\b|\bgrams?\b", re.I)

NOTICE_MED = "General information, not medical advice. Check with a doctor or registered Ayurvedic practitioner before using any herbal product."
NOTICE_LAW = "General information, not legal advice. Check the current official text or an IP attorney before you act."

SCRIPTS = [("hi", 0x0900, 0x097F), ("bn", 0x0980, 0x09FF), ("pa", 0x0A00, 0x0A7F), ("gu", 0x0A80, 0x0AFF),
           ("ta", 0x0B80, 0x0BFF), ("te", 0x0C00, 0x0C7F), ("kn", 0x0C80, 0x0CFF), ("ml", 0x0D00, 0x0D7F)]
SCRIPT_LANG = {"en": "English", "hi": "Hindi", "bn": "Bengali", "pa": "Punjabi", "gu": "Gujarati",
               "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam"}


# ---------------------------------------------------------------- text helpers
def detect_script(text):
    """Return a language code from the writing script. Latin text is English."""
    counts = Counter()
    for ch in text:
        o = ord(ch)
        for code, lo, hi in SCRIPTS:
            if lo <= o <= hi:
                counts[code] += 1
    return counts.most_common(1)[0][0] if sum(counts.values()) >= 2 else "en"


def stem(w):
    for suf in ("ing", "es", "ed", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def tokens(text):
    return [stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]


# ---------------------------------------------------------------- retrieval
class BM25:
    """Classic BM25 ranking, written out so it is easy to explain."""

    def __init__(self, docs, k1=1.4, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=float)
        self.avg = float(self.len.mean()) or 1.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query_tokens):
        s = np.zeros(len(self.tf))
        for t in set(query_tokens):
            if t not in self.idf:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(t, 0)
                if f:
                    norm = 1 - self.b + self.b * self.len[i] / self.avg
                    s[i] += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * norm)
        return s


def load_chunks():
    """Each .txt file = header lines, blank line, paragraphs. Each paragraph becomes one searchable chunk."""
    chunks = []
    for path in sorted(glob.glob(os.path.join(DATA, "docs", "*.txt"))):
        raw = open(path, encoding="utf-8").read().strip()
        head, _, body = raw.partition("\n\n")
        meta = {}
        for line in head.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().upper()] = v.strip()
        doc_id = os.path.splitext(os.path.basename(path))[0]
        paras = [p.strip() for p in body.split("\n\n") if p.strip()]
        for i, p in enumerate(paras):
            chunks.append({
                "id": doc_id, "title": meta.get("TITLE", doc_id), "source": meta.get("SOURCE", ""),
                "type": meta.get("TYPE", "law"), "herb": meta.get("HERB", ""), "topic": meta.get("TOPIC", ""), "text": p, "para": i,
                "index_text": f'{meta.get("TITLE", "")}. {meta.get("HERB", "")} {p}',
            })
    return chunks


class Retriever:
    def __init__(self, chunks):
        self.chunks = chunks
        texts = [c["index_text"] for c in chunks]
        self.bm25 = BM25([tokens(t) for t in texts])
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.matrix = self.char.fit_transform(texts)

    def search(self, query, herb=None, intent=None, k=4):
        bm = self.bm25.scores(tokens(query))
        bm_n = bm / (bm + 4.0)                                    # squash to 0..1
        cos = (self.matrix @ self.char.transform([query]).T).toarray().ravel()
        fused = 0.5 * bm_n + 0.5 * cos
        for i, c in enumerate(self.chunks):
            if herb and c["type"] == "medicine" and c["herb"] != herb:
                fused[i] = -1.0                                    # never answer about the wrong herb
                continue
            if herb and intent in MEDICAL and c["herb"] == herb:
                fused[i] += 0.25                                   # the herb the person named
            if c["topic"] == intent:
                fused[i] += 0.10                                   # documents in the routed topic
            if intent in LEGAL and c["type"] == "medicine":
                fused[i] -= 0.25                                   # a law question should not get herb facts
        order = np.argsort(-fused)[:k]
        return [dict(self.chunks[i], score=round(float(fused[i]), 3), bm25=round(float(bm_n[i]), 3),
                     cos=round(float(cos[i]), 3)) for i in order if fused[i] > 0]


# ---------------------------------------------------------------- LLM
def llm_name():
    p = os.getenv("LLM_PROVIDER", "gemini").lower()
    if p == "openai" and os.getenv("OPENAI_API_KEY"):
        return "OpenAI " + os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if p == "gemini" and os.getenv("GEMINI_API_KEY"):
        return "Gemini " + os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return None


def llm_call(prompt):
    name = llm_name()
    if not name:
        raise RuntimeError("no LLM key configured")
    if name.startswith("OpenAI"):
        r = requests.post("https://api.openai.com/v1/chat/completions", timeout=45,
                          headers={"Authorization": "Bearer " + os.getenv("OPENAI_API_KEY")},
                          json={"model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"), "temperature": 0.1,
                                "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout=45,
                      headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY")},
                      json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1}})
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self):
        self.ready = False

    # ---- start-up: build everything from the files in /data
    def load(self):
        gl = json.load(open(os.path.join(DATA, "glossary.json"), encoding="utf-8"))
        self.herbs = gl["herbs"]
        terms = sorted(gl["terms"].items(), key=lambda kv: -len(kv[0]))
        self.terms = [(re.compile(r"\b" + re.escape(k) + r"\b", re.I) if k.isascii() else k, v) for k, v in terms]
        self.chunks = load_chunks()
        self.ret = Retriever(self.chunks)
        self.min_conf = float(os.getenv("MIN_CONFIDENCE", "0.20"))
        self.strong = float(os.getenv("STRONG_CONFIDENCE", "0.50"))
        vocab = {t for c in self.chunks for t in tokens(c["index_text"])}
        vocab |= {stem(w) for w in COMMON}
        vocab |= {t for _, v in gl["terms"].items() for t in tokens(v)}
        vocab |= {t for syns in self.herbs.values() for s in syns for t in tokens(s)}
        self.vocab, self.vocab_list = vocab, sorted(vocab)
        self.intent_data = json.load(open(os.path.join(DATA, "intents.json"), encoding="utf-8"))
        self.intent = self._train_intent(self.intent_data)
        self.docs = []
        seen = set()
        for c in self.chunks:
            if c["id"] not in seen:
                seen.add(c["id"])
                self.docs.append({"id": c["id"], "title": c["title"], "source": c["source"], "type": c["type"]})
        self.ready = True
        print(f"[AyurLex] {len(self.docs)} documents, {len(self.chunks)} passages, "
              f"{sum(len(v) for v in self.intent_data.values())} intent examples | LLM: {llm_name() or 'off (extractive mode)'}")

    def _train_intent(self, data):
        X, y = [], []
        for label, examples in data.items():
            for e in examples:
                X.append(self.prep(self.to_english(e)))
                y.append(label)
        model = make_pipeline(
            make_union(TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True),
                       TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)),
            LogisticRegression(C=20, max_iter=3000, class_weight="balanced"))
        model.fit(X, y)
        self.train_X, self.train_y = X, y
        return model

    # ---- step 2: normalisation
    def to_english(self, text):
        for pat, val in self.terms:
            text = pat.sub(" " + val + " ", text) if not isinstance(pat, str) else text.replace(pat, " " + val + " ")
        return re.sub(r"\s+", " ", text).strip()

    # ---- step 3: entities
    def _herb_hit(self, syn, text):
        if not syn.isascii():
            return syn in text
        return re.search(r"(?<![a-z])" + re.escape(syn) + r"(?![a-z])", text) is not None

    def detect_herb(self, text):
        text = text.lower()
        for key, syns in self.herbs.items():
            if any(self._herb_hit(s, text) for s in syns):
                return key
        for w in re.findall(r"[a-z]{5,}", text):                  # typo tolerance: "ashwaganda", "tripla"
            for key, syns in self.herbs.items():
                latin = [s for s in syns if s.isascii() and " " not in s]
                if difflib.get_close_matches(w, latin, n=1, cutoff=0.84):
                    return key
        return None

    def mask_herbs(self, text):
        for syns in self.herbs.values():
            for s in syns:
                text = text.replace(s, " herbname ") if not s.isascii() else re.sub(
                    r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", " herbname ", text)
        return text

    def prep(self, text):
        return self.mask_herbs(text.lower())

    def unknown_words(self, text):
        out = []
        for w in re.findall(r"[a-z]{4,}", text.lower()):
            s = stem(w)
            if w in STOP or s in self.vocab:
                continue
            if difflib.get_close_matches(s, self.vocab_list, n=1, cutoff=0.85):
                continue
            out.append(w)
        return out

    # ---- step 4: intent
    def classify(self, text):
        x = self.prep(text)
        probs = self.intent.predict_proba([x])[0]
        classes = list(self.intent.classes_)
        best = int(np.argmax(probs))
        return classes[best], float(probs[best]), {c: round(float(p), 3) for c, p in zip(classes, probs)}

    # ---- analysis shared by answer() and evaluate.py
    def analyze(self, query, ctx=None, translated=None):
        ctx = ctx or {}
        script = detect_script(query)
        norm = translated or self.to_english(query)
        low = norm.lower()
        herb = self.detect_herb(query + " " + low)
        unknown = self.unknown_words(low)
        intent, p, probs = self.classify(low)
        followup = False
        if not herb and not unknown and ctx.get("herb") and intent in MEDICAL:
            herb, followup = ctx["herb"], True
        route = intent
        if intent == "out_of_scope" and p < 0.40:
            route = "medicine_info" if herb else max((c for c in probs if c != "out_of_scope"), key=probs.get)
        if route in MEDICAL and LEGAL_WORDS.search(low) and not MEDICAL_WORDS.search(low):
            legal_probs = {c: v for c, v in probs.items() if c in LEGAL}
            route = max(legal_probs, key=legal_probs.get)           # rule + model: a herb name alone does not make it a medicine question
        if route == "dosing" and not DOSE_WORDS.search(low):
            route = "medicine_info"                                  # model said dosing, but nobody asked for an amount
        elif route == "medicine_info" and DOSE_WORDS.search(low):
            route = "dosing"                                         # safety first: anything about amounts gets the fixed answer
        scope_refused = intent == "out_of_scope" and p >= 0.40 and not herb
        ascii_q = re.sub(r"[^\x00-\x7f]+", " ", norm)
        names = self.herb_title(herb) if herb else ""
        rq = (ascii_q + " " + names).strip()
        hits = [] if scope_refused else self.ret.search(rq, herb, route)
        return dict(script=script, norm=norm, herb=herb, followup=followup, unknown=unknown, intent=intent, p=p,
                    probs=probs, route=route, scope_refused=scope_refused, rq=rq, hits=hits)

    def herb_title(self, herb):
        for c in self.chunks:
            if c["herb"] == herb:
                return c["title"].replace(" - uses and safety", "")
        return herb

    # ---- step 6: safety
    def safety_flags(self, text, route):
        out = []
        for key, pattern, message in SAFETY_RULES:
            if key != "emergency" and route not in MEDICAL:
                continue
            if re.search(pattern, text.lower()):
                out.append({"type": key, "text": message})
        return out

    # ---- step 7: answers
    def extractive(self, used, max_words=60):
        """Quote the best passages in full (they are short). Cherry-picking sentences can drop safety warnings."""
        parts, words = [], 0
        for i, h in enumerate(used):
            if parts and words >= max_words:
                break
            parts.append(f"{h['text']} [{i + 1}]")
            words += len(h["text"].split())
        return "\n\n".join(parts), len(parts)

    def grounded_prompt(self, query, used, language):
        ctx = "\n\n".join(f"[{i + 1}] {h['title']}\n{h['text']}" for i, h in enumerate(used))
        return (
            "You are AyurLex, a careful assistant for Ayurveda intellectual-property law and medicine safety information in India.\n"
            "Rules:\n1. Use ONLY the numbered context. If it does not answer the question, say you could not find it.\n"
            "2. Cite every claim like [1] or [2].\n"
            "3. NEVER give doses, quantities, frequencies or treatment plans. Tell the person to follow the product label or a registered practitioner.\n"
            "4. For medicines, include the safety cautions that appear in the context.\n"
            "5. Keep it under 130 words. Plain sentences or short '- ' bullets. No headings.\n"
            f"6. Reply in {language}. Keep herb and law names in English as well.\n\nCONTEXT:\n{ctx}\n\nQUESTION: {query}")

    def answer(self, query, answer_lang="English", ctx=None, use_llm=True):
        t0 = time.time()
        query = query.strip()[:600]
        ctx = ctx or {}
        trace, llm = [], (llm_name() if use_llm else None)
        script = detect_script(query)
        translated = None
        if script != "en" and llm:
            try:
                translated = llm_call("Translate this question to English. Output only the translation.\n\n" + query)
            except Exception as e:
                trace.append({"step": "Translate", "detail": f"LLM translation failed ({type(e).__name__}); used glossary instead."})
        a = self.analyze(query, ctx, translated)
        if answer_lang == "auto":
            answer_lang = SCRIPT_LANG.get(a["script"], "English")
        flags = self.safety_flags(query + " " + a["norm"], a["route"])
        herb, route = a["herb"], a["route"]

        trace.append({"step": "Language", "detail": f"{SCRIPT_LANG.get(a['script'], 'English')} detected"
                      + (f"; understood as: “{a['norm'][:90]}”" if a["norm"].lower() != query.lower() else "")})
        trace.append({"step": "Intent", "detail": f"{INTENT_LABELS[a['intent']]} ({a['p']:.0%} sure)"
                      + (f"; routed to {INTENT_LABELS[route]}" if route != a["intent"] else "")})
        if herb:
            trace.append({"step": "Herb", "detail": self.herb_title(herb) + (" (from your previous question)" if a["followup"] else "")})
        elif a["unknown"] and route in MEDICAL:
            trace.append({"step": "Herb", "detail": "Unrecognised term: " + ", ".join(a["unknown"][:3])})

        shown = "out_of_scope" if a["scope_refused"] else route
        out = dict(intent=shown, intent_label=INTENT_LABELS[shown], intent_conf=round(a["p"], 3),
                   language=answer_lang, herb=herb, safety=flags, sources=[], notice="", note="", suggestions=[],
                   context={"herb": herb} if herb else {})

        def finish(text, mode, confidence=0.0, label=""):
            out.update(answer=text, mode=mode, confidence=round(confidence, 3), strength=label)
            trace.append({"step": "Answer", "detail": {"llm": "Written by " + (llm or "LLM") + " from the sources, then checked by guardrails",
                          "extractive": "Key sentences taken directly from the sources",
                          "dose_policy": "Fixed safety answer: this assistant never gives doses",
                          "scope": "Refused: outside Ayurveda IP and medicine safety",
                          "abstain": "Declined to guess: no reliable source found"}[mode]})
            out["trace"] = trace
            out["ms"] = int((time.time() - t0) * 1000)
            return out

        # outside scope
        if a["scope_refused"]:
            out["suggestions"] = SUGGESTIONS
            return finish("That is outside what I am built for. I answer questions about Ayurveda intellectual property "
                          "(patents, TKDL, GI tags, trademarks), drug licensing and rules, and safety information about common Ayurvedic herbs.",
                          "scope")

        # dosing: always a fixed, safe answer
        if route == "dosing":
            policy = next(c for c in self.chunks if c["id"] == "10_dosing_policy" and c["para"] == 0)
            used = [policy]
            text = ("I cannot give doses for Ayurvedic medicines. The right amount depends on the exact product, your age, weight, "
                    "health conditions, pregnancy and other medicines you take [1]. Follow the label of a licensed product, or ask a "
                    "registered Ayurvedic practitioner or doctor.")
            if herb:
                safety = next((c for c in self.chunks if c["herb"] == herb and c["text"].startswith("Safety and cautions")), None)
                if safety:
                    used.append(safety)
                    text += f"\n\nSafety points for {self.herb_title(herb).split(' (')[0]}: " + safety["text"].split(": ", 1)[1] + " [2]"
            out["sources"] = self.fmt_sources(used)
            out["notice"] = NOTICE_MED
            trace.append({"step": "Retrieve", "detail": "Dosing policy" + (" + herb safety passage" if herb else "")})
            return finish(text, "dose_policy", 1.0, "strong")

        # unknown herb: do not borrow another herb's facts
        if route == "medicine_info" and not herb and a["unknown"]:
            have = ", ".join(sorted(self.herb_title(h).split(" (")[0] for h in self.herbs))
            out["suggestions"] = SUGGESTIONS[2:3]
            return finish(f"I do not have a verified source about “{a['unknown'][0]}” yet, so I will not guess. "
                          f"Herbs I currently cover: {have}. For anything else, please ask a doctor or registered Ayurvedic practitioner.", "abstain")

        hits = a["hits"]
        top = hits[0]["score"] if hits else 0.0
        trace.append({"step": "Retrieve", "detail": ", ".join(f"{h['title'][:34]} ({h['score']:.2f})" for h in hits[:3]) or "nothing relevant"})
        if top < self.min_conf:
            out["suggestions"] = SUGGESTIONS
            return finish("I could not find a reliable answer to this in my indexed sources, so I will not guess. "
                          "Try rephrasing, or ask an IP attorney or a registered Ayurvedic practitioner.", "abstain", top, "weak")

        used = [h for h in hits if h["score"] >= max(self.min_conf, 0.75 * top)][:3]
        if herb and route == "medicine_info":                  # herb card: always the full intro AND the full safety passage
            used = sorted([c for c in self.chunks if c["herb"] == herb], key=lambda c: c["para"])
            for u in used:
                u["score"] = top
            if a["unknown"]:
                out["note"] = ("My sources do not cover \u201c" + ", ".join(a["unknown"][:3]) + "\u201d. Here is what they say about "
                               + self.herb_title(herb).split(" (")[0] + ".")
        out["notice"] = NOTICE_MED if route in MEDICAL else NOTICE_LAW
        label = "strong" if top >= self.strong else "moderate"
        text, mode = None, "extractive"
        if llm:
            try:
                cand = llm_call(self.grounded_prompt(query, used, answer_lang))
                cites = [int(n) for n in re.findall(r"\[(\d+)\]", cand)]
                if DOSE_RE.search(cand):
                    trace.append({"step": "Guardrail", "detail": "LLM answer contained a dose, so it was replaced by the source text"})
                elif not cites or max(cites) > len(used):
                    trace.append({"step": "Guardrail", "detail": "LLM answer had missing or invalid citations, so it was replaced"})
                else:
                    text, mode = cand, "llm"
                    trace.append({"step": "Guardrail", "detail": "No doses found; all citations valid"})
            except Exception as e:
                trace.append({"step": "LLM", "detail": f"Unavailable ({type(e).__name__}); using extractive answer"})
        if text is None:
            text, n_used = self.extractive(used)
            used = used[:n_used]
            if answer_lang != "English":
                out["note"] = f"Answers in {answer_lang} need an LLM API key. Showing the English source text."
        out["sources"] = self.fmt_sources(used)
        if flags:
            trace.append({"step": "Safety", "detail": ", ".join(f["type"] for f in flags) + " notice added"})
        return finish(text, mode, top, label)

    @staticmethod
    def fmt_sources(used):
        return [{"n": i + 1, "id": h["id"], "title": h["title"], "source": h["source"], "type": h["type"],
                 "snippet": h["text"][:300] + ("..." if len(h["text"]) > 300 else ""), "score": h.get("score", 1.0)}
                for i, h in enumerate(used)]


engine = Engine()
