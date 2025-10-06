#!/bin/bash

echo "==========================================="
echo "   深度研究 MCP 服务器负载均衡集群"
echo "==========================================="
echo

# 检查是否在正确的目录
if [ ! -f "mcp_server.py" ]; then
    echo "错误: 请在 fastmcp-server-feature-load-balancing 目录下运行此脚本"
    exit 1
fi

# 检查Python环境
if ! command -v python &> /dev/null; then
    echo "错误: 未找到 Python，请确保 Python 已安装"
    exit 1
fi

echo "正在启动深度研究 MCP 服务器集群..."
echo
echo "集群配置:"
echo "- 负载均衡器: http://127.0.0.1:8000"
echo "- 后端服务器: 8001, 8002, 8003"
echo "- 日志目录: logs/"
echo

# 设置信号处理
trap 'echo; echo "正在停止集群..."; exit 0' INT

# 启动集群
python run_cluster.py

echo
echo "集群已停止。"