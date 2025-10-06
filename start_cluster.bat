@echo off
echo ===========================================
echo    深度研究 MCP 服务器负载均衡集群
echo ===========================================
echo.

REM 检查是否在正确的目录
if not exist "mcp_server.py" (
    echo 错误: 请在 fastmcp-server-feature-load-balancing 目录下运行此脚本
    pause
    exit /b 1
)

REM 检查Python环境
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误: 未找到 Python，请确保 Python 已安装并添加到 PATH
    pause
    exit /b 1
)

echo 正在启动深度研究 MCP 服务器集群...
echo.
echo 集群配置:
echo - 负载均衡器: http://127.0.0.1:8000
echo - 后端服务器: 8001, 8002, 8003
echo - 日志目录: logs/
echo.

REM 启动集群
python run_cluster.py

echo.
echo 集群已停止。
pause