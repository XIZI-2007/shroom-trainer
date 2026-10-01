@echo off
rem 注意：本文件必须存成 GBK/ANSI 编码（中文 Windows 默认代码页），配合下面的 chcp 936。
rem   另外 GBK 编不出 U+26A0 等符号，bat 里不要用。
chcp 936 >nul
title 备份到 GitHub（XIZI-2007/shroom-trainer）
cd /d "%~dp0"

set GIT=C:\Program Files\Git\cmd\git.exe

rem 本文件被复制到别处时自动回到项目目录
if not exist ".git" (
  if exist "C:\Users\杨李乐\WorkBuddy\2026-09-22-18-31-26\shroom-trainer\.git" (
    cd /d "C:\Users\杨李乐\WorkBuddy\2026-09-22-18-31-26\shroom-trainer"
  ) else (
    echo.
    echo [错误] 本目录不是 git 仓库，也找不到项目目录。
    echo         本文件要和 shroom-trainer 项目放在一起运行。
    echo.
    pause
    exit /b 1
  )
)

if not exist "%GIT%" (
  echo.
  echo [错误] 找不到系统 Git：
  echo     %GIT%
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo   备份到 GitHub 私有仓库
echo   https://github.com/XIZI-2007/shroom-trainer
echo ============================================================
echo.

echo [1/3] 检查改动...
"%GIT%" add -A
"%GIT%" diff --cached --quiet
if errorlevel 1 goto :haschange
echo       没有需要提交的改动，直接检查远端。
goto :push

:haschange
echo       本次要提交的改动：
echo.
"%GIT%" --no-pager diff --cached --stat
echo.
echo [2/3] 提交...
"%GIT%" commit -q -m "chore: 备份（%date% %time%）"
if errorlevel 1 (
  echo       [失败] 提交出错，请看上面的信息。
  pause
  exit /b 1
)
echo       已提交
echo.
goto :push

:push
echo [3/3] 推送到 GitHub ...
echo.
echo       提示：第一次运行会弹出 GitHub 登录窗口，
echo             选 Browser 登录一次，以后就不用再登了。
echo.
"%GIT%" push origin main
if errorlevel 1 (
  echo.
  echo [失败] 推送没成功。常见原因：
  echo    1) 登录窗口没完成授权（重新双击本文件再试）
  echo    2) 网络连不上 github.com（稍后再试）
  echo.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   备份完成
echo   https://github.com/XIZI-2007/shroom-trainer
echo ============================================================
echo.
pause
exit /b 0
