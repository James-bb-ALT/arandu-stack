@echo off
cd /d "%~dp0"
"..\Arandu NLU\.venv-ai-lab-313\Scripts\python.exe" arandu_stack\app\server.py --local --data .local-data
pause
