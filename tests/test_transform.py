"""协议与请求转换测试。"""
from app.transform import to_ir, ir_to_gemini, gemini_to_ir_response, upstream_resp_to_inbound


def test_chat_to_gemini_transform():
    body = {
        "model": "gemini-3.8-flash-high",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ],
        "temperature": 0.7,
        "max_tokens": 100,
    }
    ir = to_ir("chat", body)
    gemini_req = ir_to_gemini(ir, project="aicode-consumers")

    assert gemini_req["project"] == "aicode-consumers"
    assert gemini_req["model"] == "gemini-3.8-flash-high"
    assert "systemInstruction" in gemini_req["request"]
    assert gemini_req["request"]["systemInstruction"]["parts"][0]["text"] == "You are a helpful assistant."
    assert gemini_req["request"]["contents"][0]["role"] == "user"
    assert gemini_req["request"]["contents"][0]["parts"][0]["text"] == "Hello!"
    assert gemini_req["request"]["generationConfig"]["temperature"] == 0.7
    assert gemini_req["request"]["generationConfig"]["maxOutputTokens"] == 100


def test_gemini_to_ir_response():
    sample_resp = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "Hello world!"},
                            {"thought": "internal thinking"}
                        ]
                    },
                    "finishReason": "STOP"
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15
            },
            "modelVersion": "gemini-3.8-flash",
            "responseId": "resp-123"
        }
    }
    ir_resp = gemini_to_ir_response(sample_resp, fallback_model="gemini-3.8-flash-high")
    assert ir_resp.content == "Hello world!"
    assert ir_resp.reasoning == "internal thinking"
    assert ir_resp.finish_reason == "stop"
    assert ir_resp.usage["total_tokens"] == 15

    chat_out = upstream_resp_to_inbound(ir_resp, "chat")
    assert chat_out["object"] == "chat.completion"
    assert chat_out["choices"][0]["message"]["content"] == "Hello world!"
    assert chat_out["choices"][0]["message"]["reasoning_content"] == "internal thinking"

    anthropic_out = upstream_resp_to_inbound(ir_resp, "messages")
    assert anthropic_out["type"] == "message"
    assert any(c["type"] == "thinking" for c in anthropic_out["content"])
    assert any(c["type"] == "text" for c in anthropic_out["content"])


def test_gemini_to_ir_response_with_thought_signature():
    """验证包含 thoughtSignature 字段时正文正常提取。"""
    sample_resp = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "thoughtSignature": "EsmtAQrFrQEBaRR9E49M...",
                                "text": "测试通过。",
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    }
    ir_resp = gemini_to_ir_response(sample_resp, fallback_model="gemini-3.8-flash-high")
    assert ir_resp.content == "测试通过。"



def test_tool_call_name_mapping():
    # 模拟多轮对话：客户端返回工具执行结果时仅有 tool_call_id
    body = {
        "model": "gemini-3.8-flash-high",
        "messages": [
            {"role": "user", "content": "What is the weather in Tokyo?"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_tokyo_001",
                        "type": "function",
                        "function": {"name": "get_current_weather", "arguments": '{"city": "Tokyo"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_tokyo_001",
                "content": '{"temp": 22}',
            },
        ],
    }
    ir = to_ir("chat", body)
    gemini_req = ir_to_gemini(ir)
    contents = gemini_req["request"]["contents"]
    # 验证工具返回消息中的 name 正确映射为函数原名 get_current_weather 而非 call_tokyo_001
    tool_part = contents[-1]["parts"][0]
    assert "functionResponse" in tool_part
    assert tool_part["functionResponse"]["name"] == "get_current_weather"
    assert tool_part["functionResponse"]["response"] == {"temp": 22}


def test_gemini_thought_boolean_parsing():
    # 模拟实际 Gemini 返回中 thought 为 True 且思考文本在 text 字段里的场景
    sample_resp = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "深度思考中...", "thought": True},
                            {"text": "这是给用户的最终回答。"},
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "modelVersion": "gemini-3.8-flash",
        }
    }
    ir_resp = gemini_to_ir_response(sample_resp, fallback_model="gemini-3.8-flash-high")
    assert ir_resp.reasoning == "深度思考中..."
    assert ir_resp.content == "这是给用户的最终回答。"


def test_stream_thought_event_parsing():
    from app.stream import parse_gemini_event

    ev_data = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "流式思考片段", "thought": True},
                        {"text": "流式回答文本"},
                    ]
                }
            }
        ]
    }
    items = parse_gemini_event(ev_data)
    thinking_items = [it for it in items if it["kind"] == "thinking"]
    text_items = [it for it in items if it["kind"] == "text"]

    assert len(thinking_items) == 1
    assert thinking_items[0]["thinking"] == "流式思考片段"
    assert len(text_items) == 1
    assert text_items[0]["text"] == "流式回答文本"


def test_admin_key_mask_in_config():
    from app.config import ServerConfig, server_public_dict, apply_incoming_server

    cfg = ServerConfig(local_api_key="sk-local", admin_api_key="secret-admin-pass")
    pub = server_public_dict(cfg)
    assert pub["admin_api_key"] == ""
    assert pub["admin_api_key_configured"] is True
    assert pub["admin_api_key_preview"] == "sec...ass"

    # 保存配置时留空或掩码不冲掉现有密钥
    patched = apply_incoming_server({"admin_api_key": ""}, cfg)
    assert patched["admin_api_key"] == "secret-admin-pass"
    patched_mask = apply_incoming_server({"admin_api_key": "***"}, cfg)
    assert patched_mask["admin_api_key"] == "secret-admin-pass"


import pytest

@pytest.mark.anyio
async def test_convert_stream_usage_sink():
    import json
    from app.stream import convert_stream

    # 模拟 Google 上游 SSE 事件
    ev = {
        "response": {
            "candidates": [{"content": {"parts": [{"text": "你好"}]}}],
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 6,
                "totalTokenCount": 18,
            },
        }
    }
    raw_sse = f"data: {json.dumps(ev)}\n\n".encode("utf-8")

    async def mock_stream():
        yield raw_sse

    usage_sink: dict[str, int] = {}
    chunks = []
    async for chunk in convert_stream(mock_stream(), inbound="chat", model="gemini-3.8-flash-high", usage_sink=usage_sink):
        chunks.append(chunk)

    assert len(chunks) > 0
    assert usage_sink.get("prompt_tokens") == 12
    assert usage_sink.get("completion_tokens") == 6
    assert usage_sink.get("total_tokens") == 18


def test_strip_base_persona_override():
    body = {
        "model": "gemini-3.8-flash-high",
        "messages": [
            {"role": "system", "content": "使用中文回答"},
            {"role": "user", "content": "你好"},
        ],
    }
    ir = to_ir("chat", body)

    # 验证 systemInstruction 严格等于用户输入，无额外指令注入
    req = ir_to_gemini(ir)
    sys_text = req["request"]["systemInstruction"]["parts"][0]["text"]
    assert sys_text == "使用中文回答"

    # 验证无 system 消息时不产生 systemInstruction 字段
    ir_no_sys = to_ir("chat", {"model": "gemini-3.8-flash-high", "messages": [{"role": "user", "content": "你好"}]})
    req_no_sys = ir_to_gemini(ir_no_sys)
    assert "systemInstruction" not in req_no_sys["request"]


def test_full_generation_config_passthrough():
    """验证用户传入的各项生成控制参数全量透传。"""
    body = {
        "model": "gemini-3.8-flash-high",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.4,
        "top_p": 0.85,
        "top_k": 40,
        "max_tokens": 1500,
        "stop": ["END", "STOP"],
        "presence_penalty": 0.5,
        "frequency_penalty": 0.2,
        "seed": 42,
        "response_format": {"type": "json_object"},
        "thinking_budget": 2048,
    }
    ir = to_ir("chat", body)
    req = ir_to_gemini(ir)
    cfg = req["request"]["generationConfig"]

    assert cfg["temperature"] == 0.4
    assert cfg["topP"] == 0.85
    assert cfg["topK"] == 40
    assert cfg["maxOutputTokens"] == 1500
    assert cfg["stopSequences"] == ["END", "STOP"]
    assert cfg["presencePenalty"] == 0.5
    assert cfg["frequencyPenalty"] == 0.2
    assert cfg["seed"] == 42
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["thinkingConfig"]["thinkingBudget"] == 2048




