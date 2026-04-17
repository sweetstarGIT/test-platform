# 创建启动脚本
@"
@echo off
echo ============================================
echo  Test Platform + Cloudflare Tunnel
echo ============================================
echo.
echo 固定域名: https://test-platform.sweetstar.cloud
echo.

:: 启动 test-platform（后台）
start "Test Platform" python -m uvicorn app.main:app --host 0.0.0.0 --port 8080

:: 等待服务启动
timeout /t 3 /nobreak >nul

:: 启动 Cloudflare Tunnel
echo 启动 Cloudflare Tunnel...
cloudflared tunnel --config %USERPROFILE%\.cloudflared\config.yml run 45dbe875-8463-4c75-94ba-5d62e4b6c17e
"@ | Out-File -Encoding UTF8 start-with-tunnel.bat