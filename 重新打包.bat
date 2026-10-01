@echo off
rem 注意：本文件必须存成 GBK/ANSI 编码（中文 Windows 的默认代码页），配合下面的 chcp 936。
rem   曾用 UTF-8 + chcp 65001，结果个别中文 echo 行被 cmd 拆错，冒出
rem   "'xx' is not recognized as an internal or external command"，功能不受影响但很难看。
rem   另外 GBK 编不出 U+26A0 等符号，bat 里不要用，只用 ASCII 符号加中文。
chcp 936 >nul
title ShroomTrainer 打包（打包 / 更新桌面 / 清理 / 可选验证）
cd /d "%~dp0"

rem 让 Python 的输出用 GBK，与上面的 chcp 936 对齐
rem   （外层环境若设了 PYTHONUTF8 / PYTHONIOENCODING 也不会干扰本窗口）
set PYTHONIOENCODING=gbk

set PY=C:\Users\杨李乐\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set PYI=C:\Users\杨李乐\.workbuddy\binaries\python\envs\default\Scripts\pyinstaller.exe
set DESK=%USERPROFILE%\Desktop\ShroomTrainer.exe

echo ============================================================
echo   打包 ShroomTrainer.exe
echo ============================================================
echo.

rem ---------- 0) 环境自检：早报错，省得白等一分钟 ----------
if not exist "ShroomTrainer.spec" (
  echo [错误] 本目录下找不到 ShroomTrainer.spec
  echo         本文件必须和 .spec 与 src 放在同一个目录（shroom-trainer）。
  echo         不要把它单独复制到桌面或别处运行。
  echo.
  pause
  exit /b 1
)
if not exist "src\trainer_gui.py" (
  echo [错误] 找不到 src\trainer_gui.py，项目目录不完整。
  echo.
  pause
  exit /b 1
)
if not exist "%PYI%" (
  echo [错误] 找不到打包工具：
  echo     %PYI%
  echo.
  pause
  exit /b 1
)

echo [1/5] 先做语法检查（有错就不浪费打包时间）...
"%PY%" -m py_compile src\trainer_gui.py src\trainer_core.py
if errorlevel 1 (
  echo.
  echo [失败] 源码里有语法错误，请按上面的提示修好再来。
  echo.
  pause
  exit /b 1
)
echo       语法 OK
echo.

echo [2/5] 正在打包（约 1 分钟，请稍等）...
rem 用 spec 打包：datas 里已经写好 agent.js 和 i18n_cards.json，
rem 别改成命令行 --add-data 手写（漏一个就会打出没汉化或读不到牌库的包）
"%PYI%" --noconfirm --clean ShroomTrainer.spec --distpath dist --workpath build
if errorlevel 1 goto :err
if not exist "dist\ShroomTrainer.exe" goto :err
echo       打包完成
echo.

rem ---------- 3) 更新桌面副本并逐字节校验 ----------
echo [3/5] 复制到桌面并校验...
copy /y "dist\ShroomTrainer.exe" "%DESK%" >nul
fc /b "dist\ShroomTrainer.exe" "%DESK%" >nul
if errorlevel 1 (
  echo       [注意] 桌面副本与 dist 不完全一致，请手动确认。
) else (
  echo       桌面副本已更新（逐字节一致）
)
echo.

rem ---------- 4) 清理中间产物 ----------
echo [4/5] 清理中间产物...
if exist build rmdir /s /q build
if exist src\__pycache__ rmdir /s /q src\__pycache__
if exist tools\__pycache__ rmdir /s /q tools\__pycache__
echo       已清理 build 与 __pycache__
echo.

rem ---------- 5) 可选：成品冒烟验证 ----------
echo [5/5] 可选验证
echo       冒烟测试会真的启动一次 exe、点几下、截图比对，约 1 分半。
echo       注意：它开头会强制关闭正在运行的 ShroomTrainer.exe（面板）。
echo.
set RUNSMOKE=
set /p RUNSMOKE=      现在跑一次验证吗? 输入 y 再回车就跑，直接回车跳过 [y/N]: 
if /i not "%RUNSMOKE%"=="y" (
  echo       已跳过验证。
  goto :done
)
echo.
set SHROOM_THEME=light
"%PY%" -u tools\exe_smoke.py
if errorlevel 1 (
  echo.
  echo       [失败] 冒烟测试没通过，上面的 FAIL 项需要看一下。
) else (
  echo.
  echo       冒烟测试通过。
)

:done
echo.
echo ============================================================
echo   完成。新程序：
echo     %~dp0dist\ShroomTrainer.exe
echo     桌面副本 %DESK%
echo ============================================================
echo.
pause
exit /b 0

:err
echo.
echo [失败] 打包出错，请把上面的错误信息发出来。
echo.
pause
exit /b 1
