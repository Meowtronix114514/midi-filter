@echo off
chcp 936 >nul
title MIDI CC 监控台
echo ============================================
echo   MIDI CC 监控台（桌面端）
echo   实时监控 CC / 手动调整 CC / 动态增删监控项
echo ============================================
echo.
D:\midi-filter\venv\Scripts\pythonw.exe D:\midi-filter\cc_monitor_gui.py --block-cc7
