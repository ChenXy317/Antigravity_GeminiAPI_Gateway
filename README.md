# Gemini Gateway

本地 AI 协议网关服务。读取本机已登录的 Google Antigravity 账号凭据，将其订阅配额与模型能力转换为标准的 OpenAI (`/v1/chat/completions`)、Anthropic (`/v1/messages`) 与 Responses (`/v1/responses`) 协议端点，供外部开发工具（如 Cursor、Claude Code、OpenAI SDK 等）调用。

---

## 架构原理

```
┌──────────────────────────────────────────────┐
│ 外部工具: Cursor / Claude Code / OpenAI SDK  │
└──────────────────────┬───────────────────────┘
                       │ OpenAI / Anthropic 协议请求
                       ▼
┌──────────────────────────────────────────────┐
│ Gemini Gateway (默认监听 127.0.0.1:8789)      │
│ ├─ 协议转码器 (IR 中间表示与 SSE 流转换)      │
│ ├─ 本地凭据管理 (自动读取 Windows 凭据管理器) │
│ └─ Web 管理控制台 (支持明暗色与主题色切换)     │
└──────────────────────┬───────────────────────┘
                       │ 直连内部 CloudCode 端点
                       ▼
┌──────────────────────────────────────────────┐
│ Google CloudCode 端点 (扣除 Antigravity 配额) │
└──────────────────────────────────────────────┘
```

---

## 主要功能

* **多协议互转**：支持以 OpenAI Chat Completions、Anthropic Messages 或 Responses 格式入站，自动转换为底层通信协议；流式输出完整保留 `reasoning_content`（思考链）与工具调用。
* **凭据自动接管**：自动读取本机 Antigravity 会话凭据（Windows 凭据管理器 `gemini:antigravity`），后台自动处理 Token 过期与刷新，无需手动复制 Key。
* **配额监控**：直观展示账号当前周配额百分比与 5 小时滚动配额剩余量及重置时间。
* **人设剥离配置**：可选自动覆盖底层预设的 "You are Gemini... built by Google" 系统人设，保证自定义提示词生效。
* **轻量 Web 控制台**：基于 Alpine.js + Tailwind CSS 构建，支持浅色 / 深色模式及 7 种主题强调色切换，内置连通性测试、在线调试与请求日志。

---

## 快速开始

### 前置要求
* Python 3.11+
* 本机已安装并成功登录 Google Antigravity

### 启动服务

**Windows**：
双击运行 `start.bat`，或在命令行中执行：
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8789
```

**macOS / Linux**：
```bash
chmod +x start.sh
./start.sh
```

启动完成后，在浏览器打开控制台：`http://127.0.0.1:8789`。

---

## 客户端接入方式

### 1. Cursor IDE
* **OpenAI API Key**: `sk-local`（或网关配置的 `local_api_key`）
* **Base URL**: `http://127.0.0.1:8789/v1`
* **Model**: `gemini-3.8-flash-high`、`gemini-3.1-pro-high` 或 `claude-sonnet-4-6`

### 2. Claude Code
在终端中设置环境变量后启动 Claude Code：
```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8789
export ANTHROPIC_AUTH_TOKEN=sk-local
claude
```

### 3. Python OpenAI SDK
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8789/v1",
    api_key="sk-local",
)

response = client.chat.completions.create(
    model="gemini-3.8-flash-high",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

### 4. cURL
```bash
curl http://127.0.0.1:8789/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-local" \
  -d '{
    "model": "gemini-3.8-flash-high",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

---

## 配置说明

配置文件为 `config.json`（初次使用可参考 `config.example.json`）：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8789,
    "local_api_key": "sk-local",
    "admin_api_key": "",
    "default_model": "gemini-3.8-flash-high",
    "upstream_base_url": "https://daily-cloudcode-pa.googleapis.com",
    "project": "aicode-consumers",
    "strip_base_persona": true,
    "allow_unknown_models": true
  }
}
```

| 参数项 | 说明 | 默认值 |
|---|---|---|
| `host` | 服务监听 IP，非本机绑定时必须设置 `local_api_key` | `127.0.0.1` |
| `port` | 服务监听端口 | `8789` |
| `local_api_key` | 客户端外接入站鉴权 Key | `sk-local` |
| `admin_api_key` | 管理接口鉴权 Key，留空则沿用 `local_api_key` | `""` |
| `default_model` | 未指定模型时的默认模型 | `gemini-3.8-flash-high` |
| `strip_base_persona` | 是否在请求时覆盖官方出厂默认人设 | `true` |

---

## 安全注意事项

1. 网关仅设计用于个人本地开发环境，默认绑定 `127.0.0.1` 回环地址。
2. 若将监听地址变更为 `0.0.0.0` 或局域网 IP，网关将强制要求配置 `local_api_key`，未配置时将触发安全锁定并拒绝访问。
