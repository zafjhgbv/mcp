#  MCP 服务器

一个 MCP（Model Context Protocol）服务器，支持负载均衡和高并发访问。

## 核心功能

### 三大工具
- **深度研究** - 基于 AI 生成详细研究报告，支持多维度关键词搜索、DuckDuckGo 并行搜索、自动报告生成
- **网页抓取** - 智能提取网页核心内容和链接，自动移除广告和噪音
- **问候服务** - 基础交互和连接测试

### 负载均衡
- 支持多实例部署（默认 3 个服务器）
- 轮询分发请求，提高并发处理能力
- 自动日志管理和进程监控

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置环境
编辑 `.env` 文件，设置 OpenAI API 密钥和站点：
```env
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=your_openai_base_url
```

### 3. 启动服务

**单机模式：**
```bash
python mcp_server.py --port 8000
```

**负载均衡模式：**
```bash
python run_cluster.py
# 或双击 start_cluster.bat (Windows)
```

服务将在 `http://127.0.0.1:8000/sse/` 启动



## 项目结构

```
├── mcp_server.py          # MCP 服务器主程序
├── load_balancer.py       # 负载均衡器
├── run_cluster.py         # 集群管理器
├── .env                   # 环境配置
└── requirements.txt       # 依赖清单
```

