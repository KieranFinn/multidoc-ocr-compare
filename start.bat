@echo off
cd /d %~dp0
python webapp.py
if errorlevel 1 pause
