@echo off
cd /d %~dp0
if not exist venv ( python -m venv venv )
call venv\Scripts\activate
pip install -r requirements.txt
if not exist .env ( copy .env.example .env )
python evaluate.py
python app.py
pause
