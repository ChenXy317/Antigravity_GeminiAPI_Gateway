#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -f "config.json" ] && [ -f "config.example.json" ]; then
    cp config.example.json config.json
    echo "[Gemini Gateway] 已从 config.example.json 创建 config.json"
fi

python3 -c "import fastapi, uvicorn, httpx, pydantic, anyio" >/dev/null 2>&1 || {
    echo "[Gemini Gateway] 正在安装依赖..."
    pip install -r requirements.txt
}

echo "[Gemini Gateway] 启动服务于 http://127.0.0.1:8789/"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8789
