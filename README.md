# Gemini Gateway

本地 AI 协议网关服务。读取本机已登录的 Google Antigravity 账号凭据，将其订阅配额与模型能力转换为标准的 OpenAI (`/v1/chat/completions`)、Anthropic (`/v1/messages`) 与 Responses (`/v1/responses`) 协议端点，供外部开发工具（如 Cursor、Claude Code、OpenAI SDK 等）调用。

---

## 架构原理

```
┌──────────────────────────────────────────────┐
│ 外部客户端: Cursor / Claude Code / OpenAI SDK │
└──────────────────────┬───────────────────────┘
                       │ OpenAI / Anthropic 协议请求
                       ▼
┌──────────────────────────────────────────────┐
│ Gemini Gateway (默认监听 127.0.0.1:8789)      │
│ ├─ 协议转码器 (中间表示 IR 与 SSE 流式转换)    │
│ ├─ 本地凭据管理 (读取 Windows 凭据管理器)     │
│ ├─ 模型智能别名路由 (gpt-4o / o1 / claude 等) │
│ └─ Web 管理控制台 (状态监控 / 调试 / 日志)    │
└──────────────────────┬───────────────────────┘
                       │ 直连内部 CloudCode 端点
                       ▼
┌──────────────────────────────────────────────┐
│ Google CloudCode 端点 (扣除 Antigravity 配额) │
└──────────────────────────────────────────────┘
```

---

## 功能特性

* **OpenAI 兼容优先**：专为 OpenAI API 标准深度优化，支持 Chat Completions 流式（SSE）与非流式调用，完整保留 `reasoning_content`（思考链）与函数调用（Tools / Functions）。
* **纯净透传，用户控制**：
  * **System 提示词保真**：100% 忠实于客户端传入的 System 指令，不注入任何额外人设或默认覆写词；未传 System 消息时不携带额外字段。
  * **全参数无损映射**：完整透传 `temperature`、`top_p`、`top_k`、`max_tokens`、`stop`、`presence_penalty`、`frequency_penalty`、`seed`、`response_format`（JSON 模式）与 `thinking_budget`（思考预算），未传参数完全交由底层模型默认处理。
* **智能模型别名路由**：内置主流别名映射机制，客户端请求 `gpt-4o`、`claude-3-7-sonnet`、`o1` 等名称时自动路由至对应的 Gemini / Claude 模型，无需修改客户端默认配置。
* **多协议兼容**：除标准 OpenAI 格式外，同时兼容 Anthropic Messages 与 Responses 协议格式。
* **凭据接管与自愈**：自动读取本机 Antigravity 会话凭据（Windows 凭据管理器 `gemini:antigravity`），后台优先同步本机续期状态，支持多级容错与重载自愈。
* **配额监控**：直观展示当前周配额百分比与 5 小时滚动配额剩余量及重置时间。
* **Web 控制台**：基于 Alpine.js + Tailwind CSS 构建，支持深浅色模式切换，内置连通性测试、在线 Playground 调试与请求日志。

---

## 快速开始

### 前置要求

* Python 3.11+
* 本机已安装并成功登录 Google Antigravity

### 安装与启动

1. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```

2. **启动服务**：
   * **Windows**：双击运行 `start.bat`，或在命令行中执行：
     ```powershell
     python -m uvicorn app.main:app --host 127.0.0.1 --port 8789
     ```
   * **macOS / Linux**：
     ```bash
     chmod +x start.sh
     ./start.sh
     ```

3. **访问控制台**：
   在浏览器中打开 `http://127.0.0.1:8789` 查看网关状态与配额。

---

## 客户端接入方式

### 1. Cursor IDE

* **OpenAI API Key**: `sk-local`（或网关配置的 `local_api_key`）
* **Base URL**: `http://127.0.0.1:8789/v1`
* **Model**: `gemini-3.8-flash-high`、`gpt-4o`、`gemini-3.1-pro-high` 或 `claude-sonnet-4-6`

### 2. Python OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8789/v1",
    api_key="sk-local",
)

# 流式调用示例（支持思考链）
response = client.chat.completions.create(
    model="gemini-3.8-flash-high",
    messages=[
        {"role": "system", "content": "你是一个严谨的助手。"},
        {"role": "user", "content": "你好，请简单介绍一下你自己。"},
    ],
    temperature=0.7,
    stream=True,
)

for chunk in response:
    # 提取思考链（若模型输出思考过程）
    reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
    if reasoning:
        print(reasoning, end="", flush=True)

    # 提取正文内容
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

### 3. cURL

```bash
curl http://127.0.0.1:8789/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-local" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "你好"}],
    "temperature": 0.7
  }'
```

### 4. Claude Code

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8789
export ANTHROPIC_AUTH_TOKEN=sk-local
claude
```

---

## 模型与别名映射

网关内置模型别名映射，方便直接使用预设模型名称的各类客户端：

| 请求模型别名 | 实际映射模型 | 说明 |
|---|---|---|
| `gpt-4o` / `gpt-4` / `gpt-4-turbo` | `gemini-3.8-flash-high` | 主力高性能模型 |
| `gpt-4o-mini` / `gpt-3.5-turbo` | `gemini-3.8-flash-medium` | 轻量快速模型 |
| `o1` / `o1-preview` | `gemini-3.1-pro-high` | 深度推理模型 |
| `o3-mini` / `o1-mini` | `gemini-3.8-flash-high` | 快速推理模型 |
| `claude-3-5-sonnet` / `claude-3-7-sonnet` | `claude-sonnet-4-6` | Claude 思考模型 |
| `claude-3-opus` | `claude-opus-4-6-thinking` | Claude 旗舰思考模型 |

如需自定义映射关系，可在 `config.json` 的 `model_aliases` 字段中配置。若传入的原生模型 ID 已在列表中（如 `gemini-3.8-flash-high`），将优先精确匹配原生模型。

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
    "strip_base_persona": false,
    "allow_unknown_models": true
  }
}
```

| 参数项 | 说明 | 默认值 |
|---|---|---|
| `host` | 服务监听 IP，非本机回环绑定时强制要求设置 `local_api_key` | `127.0.0.1` |
| `port` | 服务监听端口 | `8789` |
| `local_api_key` | 客户端外接入站鉴权 Key | `sk-local` |
| `admin_api_key` | 管理接口鉴权 Key，留空则沿用 `local_api_key` | `""` |
| `default_model` | 未指定模型时的默认模型 | `gemini-3.8-flash-high` |
| `strip_base_persona` | 是否在系统提示词末尾追加指令覆写文本（建议保持 `false` 以保障提示词纯净） | `false` |
| `allow_unknown_models` | 是否允许透传未在模型列表中的模型标识 | `true` |

---

## 安全注意事项

1. 网关设计用于个人本地开发环境，默认绑定 `127.0.0.1` 回环地址。
2. 若将监听地址变更为 `0.0.0.0` 或局域网 IP，网关将强制要求配置有效的 `local_api_key`；未配置时将触发安全锁定并拒绝访问。
