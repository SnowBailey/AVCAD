@echo off
REM ============================================================
REM  AVCAD Windows 一键打包脚本
REM  - 产出 dist\AVCAD\AVCAD.exe（PyInstaller 目录模式，启动快）
REM  - 若本机装了 Inno Setup 6：继续产出 dist\AVCAD-Setup-1.0.0.exe 安装程序
REM  - 没装 Inno Setup：退化为 dist\AVCAD-1.0.0-Windows-Portable.zip 绿色版
REM
REM  用法：在本机 Windows 上，进入项目根目录，双击本脚本
REM        （或 cmd 里执行 packaging\windows\build.bat）
REM ============================================================
setlocal enabledelayedexpansion

cd /d "%~dp0..\.."
set "ROOT=%CD%"
set "VER=1.0.0"
set "PYEXE=python"

echo.
echo ==== [1/5] 准备虚拟环境 ====
if not exist ".venv-win" (
    %PYEXE% -m venv .venv-win
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败，请确认已安装 Python 3.10 ~ 3.13 并加入 PATH
        pause & exit /b 1
    )
)
call ".venv-win\Scripts\activate.bat"

echo ==== [2/5] 安装依赖 ====
python -m pip install --upgrade pip
python -m pip install -r "packaging\windows\requirements.txt"
if errorlevel 1 ( echo [错误] 依赖安装失败 & pause & exit /b 1 )

echo ==== [3/5] 生成图标 ====
if not exist "packaging\AVCAD.ico" python "packaging\make_icon.py"

echo ==== [4/5] PyInstaller 打包 ====
rmdir /s /q build dist\AVCAD 2>nul
python -m PyInstaller --noconfirm --clean --windowed ^
  --name AVCAD ^
  --icon "packaging\AVCAD.ico" ^
  --paths "%ROOT%" ^
  --add-data "%ROOT%\avcad\ui\static;avcad\ui\static" ^
  --add-data "%ROOT%\avcad\data;avcad\data" ^
  --add-data "%ROOT%\avcad\config;avcad\config" ^
  --hidden-import scripts.check_overlap ^
  --hidden-import openpyxl ^
  --hidden-import yaml ^
  --hidden-import ezdxf ^
  --collect-submodules encodings ^
  --exclude-module pytest ^
  "packaging\avcad_app.py"
if errorlevel 1 ( echo [错误] 打包失败 & pause & exit /b 1 )
if not exist "dist\AVCAD\AVCAD.exe" ( echo [错误] 未找到 dist\AVCAD\AVCAD.exe & pause & exit /b 1 )

echo ==== [5/5] 制作安装程序 ====
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if defined ISCC (
    echo 找到 Inno Setup：%ISCC%
    "%ISCC%" "packaging\windows\AVCAD.iss"
    if errorlevel 1 ( echo [警告] Inno Setup 编译失败，改为打绿色版 ZIP & set "ISCC=" )
)

if not defined ISCC (
    echo 未找到 Inno Setup，生成绿色版 ZIP（解压后运行 AVCAD\AVCAD.exe）
    powershell -NoProfile -Command "Compress-Archive -Path 'dist\AVCAD\*' -DestinationPath 'dist\AVCAD-%VER%-Windows-Portable.zip' -Force"
)

echo.
echo ==== 完成 ====
dir /b dist
echo.
pause
