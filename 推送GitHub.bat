@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================================
echo   同步到 GitHub : KieranFinn/multidoc-ocr-compare
echo ==========================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 git, 请先安装 Git for Windows
  goto end
)

if "%GITHUB_TOKEN%"=="" (
  echo [错误] 环境变量 GITHUB_TOKEN 未设置, 无法推送
  goto end
)

echo [1/4] 扫描改动 ...
git add -A
git diff --cached --quiet
if not errorlevel 1 (
  echo       没有需要提交的改动, 仓库已是同步状态。
  goto end
)
echo       发现改动。
echo.

echo [2/4] 改动清单 :
git diff --cached --stat
echo.

set "MSG=%~1"
if "%MSG%"=="" set /p MSG=请输入提交说明 (直接回车用默认):
if "%MSG%"=="" set "MSG=chore: sync working copy"
echo.

echo [3/4] 提交中 ...
git commit -q -m "%MSG%"
if errorlevel 1 (
  echo [错误] 提交失败, 请查看上面的报错
  goto end
)
echo       已提交: %MSG%
echo.

echo [4/4] 推送到 origin/main ...
git -c credential.helper="!f() { echo username=x-access-token; echo password=$GITHUB_TOKEN; }; f" push origin main
if errorlevel 1 (
  echo.
  echo [错误] 推送失败。常见原因: token 过期 / 无 repo 写权限 / 网络问题
  goto end
)

echo.
echo [完成] 已同步到 https://github.com/KieranFinn/multidoc-ocr-compare

:end
echo.
if "%~1"=="" pause
