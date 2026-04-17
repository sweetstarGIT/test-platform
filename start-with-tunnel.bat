@echo off
echo ============================================
echo  Test Platform + Cloudflare Tunnel
echo ============================================
echo.
echo URL: https://test-platform.sweetstar.cloud
echo.

REM 启动 test-platform（后台新窗口）
start "Test Platform" python -m uvicorn app.main:app --host 0.0.0.0 --port 8080

REM 等待服务启动
timeout /t 3 /nobreak >nul

REM 启动 Cloudflare Tunnel
echo Starting Cloudflare Tunnel...
cloudflared tunnel --config %USERPROFILE%\.cloudflared\config.yml run 45dbe875-8463-4c75-94ba-5d62e4b6c17e