# AyurLex setup guide (SIH26045 - IP-SAKTI)

AI assistant for Ayurveda IP law, drug rules and herb safety. Answers always come with sources.

## You need
- Python 3.10 or newer (Windows: tick "Add Python to PATH" in the installer)
- MySQL running on your computer (MySQL 8, or XAMPP/MariaDB). Only needed for chat history.
- Internet for the first install. Nothing else is downloaded (no big AI models).
- Optional: a free Gemini API key for fluent AI answers and translation.

## Fastest way
- Windows: double-click `run.bat`
- Mac/Linux: `./run.sh`

It creates a virtual environment, installs packages, copies `.env`, measures the model and starts the app.
Then open **http://127.0.0.1:5000**

## Step by step
1. Open a terminal in the `AyurLex` folder.
2. `python -m venv venv`, then activate it:
   Windows `venv\Scripts\activate` | Mac/Linux `source venv/bin/activate`
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env`. Set `DB_PASSWORD` (XAMPP: leave empty). Add `GEMINI_API_KEY` if you have one.
5. `python check_db.py` - must print OK (it creates the database and tables for you).
6. `python evaluate.py` - prints the quality numbers shown in the app's "How it works" panel.
7. `python app.py`, then open http://127.0.0.1:5000

## What the header shows
- "AI: offline answers (no API key)": works, quotes the best source passages. Add a key for written answers and translation.
- "History: saved": MySQL is connected. "History: off": MySQL is not reachable; hover for the reason. Questions still work.

## Using it
- Pick the answer language at the top (English is the default). You can type in Hindi or other languages.
- Search your past questions with the box in the left sidebar.
- Open "How I answered" under any answer to see each pipeline step.

## Adding documents
Put `.txt` files in `data/docs/`:
```
TITLE: Name of the Act or document
SOURCE: https://official-link
TYPE: law            (law, medicine or safety)
TOPIC: patent        (patent, traditional_knowledge, licensing, geo_trademark, medicine_info, dosing)
HERB: ashwagandha    (only for herb files; must match data/glossary.json)

First paragraph.

Second paragraph.
```
Restart the app. To add a herb also add its names to `data/glossary.json`.
To teach the classifier, add example questions to `data/intents.json`, then run `python evaluate.py`.

## Troubleshooting
- `check_db.py` fails: start MySQL, and check `DB_PASSWORD` in `.env`.
- `pip install` fails on mysql-connector: update pip (`python -m pip install --upgrade pip`).
- Page looks plain: Bootstrap loads from a CDN; the page still works without internet.
- Port 5000 busy: set `PORT=5001` in `.env`.

## Important before you present
- The 23 documents are my short summaries, written for this prototype. **Check them against the official texts** (indiacode.nic.in, ipindia.gov.in, tkdl.res.in, nbaindia.org, ayush.gov.in) and replace them. Citation accuracy is your strongest point with judges.
- The herb safety notes were not reviewed by a doctor or Ayurvedic practitioner. Have one review them before you claim medical correctness.
- AyurLex never gives doses, on purpose. Say so in the pitch: it is a safety feature.
- The quality numbers come from a small test set (49 questions) used while building. Present them as a development check.

## Demo script
1. "Can I patent a herbal formulation based on a classical Ayurvedic text?" (Section 3(p), cited)
2. "Is giloy safe for the liver?" then "what about pregnancy?" (remembers the herb, adds a notice)
3. "How much ashwagandha should I take?" (refuses doses, shows safety points)
4. "Is ginger safe in pregnancy?" (admits it has no source)
5. "क्या मेरे हर्बल उत्पाद को GI टैग मिल सकता है?" (Hindi)
6. Search "giloy" in the sidebar, open "How it works", show the measured quality.
