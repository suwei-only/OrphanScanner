@echo off
rem OrphanScanner 测试入口: 双击运行全部单元测试
cd /d "%~dp0"
python -m unittest discover -s tests -v
pause
