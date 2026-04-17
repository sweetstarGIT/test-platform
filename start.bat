@echo off
echo ============================================
echo  Test Platform + Cloudflare Tunnel
echo ============================================
echo.
echo URL: https://test-platform.sweetstar.cloud
echo.

REM Start test-platform in new window
start "Test Platform" python -m uvicorn app.main:app --host 0.0.0.0 --port 8080

REM Wait for service to start
timeout /t 3 /nobreak >nul

REM Start Cloudflare Tunnel
echo Starting Cloudflare Tunnel...
cloudflared tunnel run 45dbe875-8463-4c75-94ba-5d62e4b6c17e
