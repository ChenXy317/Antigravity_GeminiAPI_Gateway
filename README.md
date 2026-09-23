# Gemini Gateway

把本机 **Google Antigravity** 的订阅与使用额度，接到外部开发工具里的本地全栈网关。

客户端使用 OpenAI Chat Completions、Responses 或 Anthropic Messages 哪种形态均可；上游自动直连 Antigravity 的 CloudCode 端点，直接扣除 Antigravity 账号的周期额度，无需单独购买 Google AI Studio API Key。

```
 OpenAI SDK / Cursor ────►  /v1/chat/completions ─┐
 Responses 客户端     ────►  /v1/responses       ─┼─► IR ──► daily-cloudcode-pa.googleapis.com
 Claude Code         ────►  /v1/messages        ─┘          (Antigravity 账号凭据)
```

默认监听：`127.0.0.1:8789` · MIT License

---

## 解决的问题

| 场景 | 网关如何处理 |
|---|---|
| 代码中调用 Gemini 3.8 / 3.1 Pro，不想申请或付费 API Key | 将 Base URL 指向 `http://127.0.0.1:8789/v1`，直接使用 Antigravity 额度 |
| Claude Code 仅支持 Anthropic 协议 | 请求发送至 `/v1/messages`，网关自动转换为 Gemini 上游格式并回转 Messages 响应流 |
| 想用 Antigravity 账号里的 `claude-sonnet-4-6` / `claude-opus-4-6-thinking` | 直接指定对应的 model id 调用即可 |
| 登录态与 Token 过期管理 | 自动读取 Windows 凭证管理器中的 `gemini:antigravity`，后台自动刷新续期 |
| 实时查看周额度与重置倒计时 | 管理控制台直观展示周限额与 5 小时滚动配额剩余百分比及重置时间 |

---

## 快速开始

需要 Python 3.11+，且本机已安装并登录过 Google Antigravity。

**Windows**：
双击 `start.bat`，或双击 `入口.url` 打开 Web 控制台。

**macOS / Linux**：
```bash
chmod +x start.sh
./start.sh
```

**手动启动**：
```bash
pip install -r requirements.txt
cp config.example.json config.json
python -m uvicorn app.main:app --host 127.0.0.1 --port 8789
```

打开 `http://127.0.0.1:8789/`：
1. 确认左侧面板 Antigravity 显示「已登录」，且周额度进度条正常显示；
2. 点击「连通测试」验证模型响应；
3. 将外部客户端的 Base URL 配置为 `http://127.0.0.1:8789/v1`。

---

## 客户端接入方式

### 1. OpenAI SDK (Python)
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8789/v1",
    api_key="sk-local",
)

response = client.chat.completions.create(
    model="gemini-3.8-flash-high",
    messages=[{"role": "user", "content": "你好！"}],
)
print(response.choices[0].message.content)
```

### 2. Claude Code
```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8789
export ANTHROPIC_AUTH_TOKEN=sk-local
claude
```

### 3. Cursor
* **OpenAI API Key**: `sk-local`
* **Base URL**: `http://127.0.0.1:8789/v1`
* **Model**: `gemini-3.8-flash-high` 或 `claude-sonnet-4-6`

### 4. cURL
```bash
curl http://127.0.0.1:8789/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-local" \
  -d '{"model":"gemini-3.8-flash-high","messages":[{"role":"user","content":"你好"}]}'
```

---

## 支持的模型列表

网关内置并支持动态同步 Antigravity 提供的模型：
* `gemini-3.8-flash-high` / `medium` / `low`
* `gemini-3.1-pro-high` / `low`
* `gemini-pro-agent`
* `gemini-3.7-flash-high`
* `gemini-2.5-pro`
* `claude-sonnet-4-6` (Thinking)
* `claude-opus-4-6-thinking`

可以在控制台点击「从上游同步」拉取当前账号可用的最新模型池。

---

## 鉴权说明

| 场景 | 鉴权要求 |
|---|---|
| 设置了 `server.local_api_key`（默认 `sk-local`） | 必须携带匹配的 `Authorization: Bearer` 或 `x-api-key` |
| `local_api_key` 为空且仅监听本机（127.0.0.1） | 本机环境免密放行 |
| 绑定到非本机（`0.0.0.0` / 局域网 IP） | 必须显式设置 `local_api_key`，否则触发安全锁定拒绝访问 |

---

## 目录结构

```
app/
  ├── antigravity_auth.py  # 凭据接管与 OAuth 自动刷新
  ├── quota.py             # 周额度与套餐用量解析
  ├── ir.py                # 协议统一中间表示
  ├── transform.py         # IR ↔ Google internal 端点格式映射
  ├── stream.py            # SSE 流转码器
  ├── upstream.py          # 上游 HTTP/SSE 客户端通信
  ├── server.py            # FastAPI 路由与管理 API
  └── main.py              # 服务入口与中间件
static/                    # Web 管理控制台 (Alpine.js + Tailwind CSS)
tests/                     # 单元与集成测试
```
