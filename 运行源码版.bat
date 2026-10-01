@echo off
rem 注意：本文件必须存成 GBK/ANSI 编码（中文 Windows 的默认代码页），配合下面的 chcp 936。
rem   曾用 UTF-8 + chcp 65001，个别中文 echo 行会被 cmd 拆错成"'xx' 不是内部或外部命令"。
rem   另外 GBK 编不出 U+26A0 等符号，bat 里不要用。
chcp 936 >nul
title ShroomTrainer 源码版（改完代码保存 → 关掉面板 → 再双击本文件）
cd /d "%~dp0"

rem 让 Python 的输出用 GBK，与上面的 chcp 936 对齐
rem   （外层环境若设了 PYTHONUTF8 / PYTHONIOENCODING 也不会干扰本窗口）
set PYTHONIOENCODING=gbk

rem 本文件被复制到桌面/别处时，自动回到项目目录（否则找不到 src\）
if not exist "src\trainer_gui.py" (
  if exist "C:\Users\杨李乐\WorkBuddy\2026-09-22-18-31-26\shroom-trainer\src\trainer_gui.py" (
    cd /d "C:\Users\杨李乐\WorkBuddy\2026-09-22-18-31-26\shroom-trainer"
  ) else (
    echo.
    echo [错误] 找不到项目目录 shroom-trainer\src\trainer_gui.py
    echo         本文件需要和项目放在一起，或项目被移动/改名了。
    echo.
    pause
    exit /b 1
  )
)

set PY=C:\Users\杨李乐\.workbuddy\binaries\python\envs\default\Scripts\python.exe

if not exist "%PY%" (
  echo.
  echo [错误] 找不到 Python 解释器：
  echo     %PY%
  echo.
  echo 请确认 WorkBuddy 的 Python 环境还在，或改用打包版 exe。
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo   正在启动【源码版】面板
echo   （读的是 src\ 目录里的最新代码，不需要重新打包）
echo ------------------------------------------------------------
echo   改了 trainer_gui.py 等源码并保存后：
echo     1. 关掉面板窗口
echo     2. 再双击本文件
echo   就能看到新效果。
echo.
echo   若弹出「已经在运行了」的提示，说明桌面上那个
echo   ShroomTrainer.exe 还开着，先把它关掉再双击本文件。
echo ============================================================
echo.

"%PY%" -u src\trainer_gui.py

echo.
echo ------------------------------------------------------------
echo   面板已关闭。
echo   若上方出现红字报错（Traceback / Error），说明代码里有问题，
echo   可以把这里的报错内容发给我。
echo ------------------------------------------------------------
pause
