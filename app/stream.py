"""Google SSE 流转换为 OpenAI Chat / Responses / Anthropic Messages 流。"""
from __future__ import annotations

import codecs
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .converter.common import sse_format
from .upstream import UpstreamHTTPError


def split_sse_buffer(buffer: str) -> tuple[list[str], str]:
    """切分并提取完整的 SSE data 行。"""
    text = buffer.replace("\r\n", "\n")
    parts = text.split("\n\n")
    remainder = parts.pop() if parts else ""
    events: list[str] = []
    for part in parts:
        data_lines: list[str] = []
        for line in part.split("\n"):
            s = line.strip()
            if not s or s.startswith(":") or s.startswith("event:"):
                continue
            if s.startswith("data:"):
                data_lines.append(s[5:].strip())
        if data_lines:
            events.append("\n".join(data_lines))
    return events, remainder


def parse_gemini_event(data: dict[str, Any]) -> list[dict[str, Any]]:
    """解析 Google 返回的单个 SSE 事件。"""
    out: list[dict[str, Any]] = []
    resp_obj = data.get("response") if isinstance(data.get("response"), dict) else data
    if not isinstance(resp_obj, dict):
        return out

    candidates = resp_obj.get("candidates") or []
    if candidates:
        cand = candidates[0]
        content = cand.get("content") or {}
        parts = content.get("parts") or []
        for p in parts:
            if not isinstance(p, dict):
                continue
            txt = p.get("text")
            thought_val = p.get("thought")
            thought_sig = p.get("thoughtSignature")

            if thought_val is True:
                if txt:
                    out.append({"kind": "thinking", "thinking": txt})
            elif isinstance(thought_val, str) and thought_val:
                out.append({"kind": "thinking", "thinking": thought_val})
            elif isinstance(thought_sig, str) and thought_sig:
                out.append({"kind": "thinking", "thinking": thought_sig})
            else:
                if txt:
                    out.append({"kind": "text", "text": txt})

            fc = p.get("functionCall")
            if isinstance(fc, dict):
                out.append({
                    "kind": "tool_call",
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "name": fc.get("name") or "",
                    "arguments": json.dumps(fc.get("args") or {}, ensure_ascii=False),
                })

        raw_finish = cand.get("finishReason")
        if raw_finish:
            finish_map = {
                "STOP": "stop",
                "MAX_TOKENS": "length",
                "SAFETY": "content_filter",
                "RECITATION": "content_filter",
            }
            out.append({"kind": "finish", "finish_reason": finish_map.get(raw_finish, "stop")})

    usage = resp_obj.get("usageMetadata")
    if isinstance(usage, dict):
        p_tokens = int(usage.get("promptTokenCount") or 0)
        c_tokens = int(usage.get("candidatesTokenCount") or 0)
        out.append({
            "kind": "usage",
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
            "total_tokens": int(usage.get("totalTokenCount") or (p_tokens + c_tokens)),
        })

    return out


async def convert_stream(
    raw_stream: AsyncIterator[bytes],
    inbound: str,
    model: str,
    resp_id: str | None = None,
    usage_sink: dict[str, int] | None = None,
) -> AsyncIterator[bytes]:
    """统一流式转换入口。"""
    resp_id = resp_id or f"chatcmpl-{uuid.uuid4().hex[:24]}"
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buf = ""

    if inbound == "chat":
        async for chunk in _stream_to_chat(raw_stream, decoder, buf, model, resp_id, usage_sink):
            yield chunk
    elif inbound == "messages":
        async for chunk in _stream_to_anthropic(raw_stream, decoder, buf, model, resp_id, usage_sink):
            yield chunk
    elif inbound == "responses":
        async for chunk in _stream_to_responses(raw_stream, decoder, buf, model, resp_id, usage_sink):
            yield chunk
    else:
        async for chunk in raw_stream:
            yield chunk


async def _stream_to_chat(
    raw_stream: AsyncIterator[bytes],
    decoder: Any,
    buf: str,
    model: str,
    resp_id: str,
    usage_sink: dict[str, int] | None = None,
) -> AsyncIterator[bytes]:
    created = int(time.time())
    yield sse_format({
        "id": resp_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    })

    finish_sent = False
    usage_info: dict[str, Any] | None = None

    async for raw in raw_stream:
        buf += decoder.decode(raw)
        events, buf = split_sse_buffer(buf)
        for ev_str in events:
            try:
                ev_data = json.loads(ev_str)
            except Exception:
                continue
            parsed = parse_gemini_event(ev_data)
            for item in parsed:
                k = item["kind"]
                if k == "text":
                    yield sse_format({
                        "id": resp_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": item["text"]}, "finish_reason": None}],
                    })
                elif k == "thinking":
                    yield sse_format({
                        "id": resp_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"reasoning_content": item["thinking"]}, "finish_reason": None}],
                    })
                elif k == "tool_call":
                    yield sse_format({
                        "id": resp_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "tool_calls": [{
                                    "index": 0,
                                    "id": item["id"],
                                    "type": "function",
                                    "function": {"name": item["name"], "arguments": item["arguments"]},
                                }]
                            },
                            "finish_reason": None,
                        }],
                    })
                elif k == "finish":
                    finish_sent = True
                    yield sse_format({
                        "id": resp_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": item["finish_reason"]}],
                    })
                elif k == "usage":
                    usage_info = {
                        "prompt_tokens": item["prompt_tokens"],
                        "completion_tokens": item["completion_tokens"],
                        "total_tokens": item["total_tokens"],
                    }
                    if usage_sink is not None:
                        usage_sink.update(usage_info)

    if not finish_sent:
        yield sse_format({
            "id": resp_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })

    if usage_info:
        yield sse_format({
            "id": resp_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": usage_info,
        })

    yield b"data: [DONE]\n\n"


async def _stream_to_anthropic(
    raw_stream: AsyncIterator[bytes],
    decoder: Any,
    buf: str,
    model: str,
    resp_id: str,
    usage_sink: dict[str, int] | None = None,
) -> AsyncIterator[bytes]:
    anthropic_id = resp_id.replace("chatcmpl-", "msg_")
    yield sse_format({
        "type": "message_start",
        "message": {
            "id": anthropic_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }, event="message_start")

    block_index = 0
    text_block_open = False
    thinking_block_open = False
    stop_reason = "end_turn"
    usage_info = {"input_tokens": 0, "output_tokens": 0}

    async for raw in raw_stream:
        buf += decoder.decode(raw)
        events, buf = split_sse_buffer(buf)
        for ev_str in events:
            try:
                ev_data = json.loads(ev_str)
            except Exception:
                continue
            parsed = parse_gemini_event(ev_data)
            for item in parsed:
                k = item["kind"]
                if k == "thinking":
                    if not thinking_block_open:
                        if text_block_open:
                            yield sse_format({"type": "content_block_stop", "index": block_index}, event="content_block_stop")
                            block_index += 1
                            text_block_open = False
                        yield sse_format({
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "thinking", "thinking": ""},
                        }, event="content_block_start")
                        thinking_block_open = True
                    yield sse_format({
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "thinking_delta", "thinking": item["thinking"]},
                    }, event="content_block_delta")

                elif k == "text":
                    if thinking_block_open:
                        yield sse_format({"type": "content_block_stop", "index": block_index}, event="content_block_stop")
                        block_index += 1
                        thinking_block_open = False
                    if not text_block_open:
                        yield sse_format({
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "text", "text": ""},
                        }, event="content_block_start")
                        text_block_open = True
                    yield sse_format({
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "text_delta", "text": item["text"]},
                    }, event="content_block_delta")

                elif k == "tool_call":
                    if text_block_open or thinking_block_open:
                        yield sse_format({"type": "content_block_stop", "index": block_index}, event="content_block_stop")
                        block_index += 1
                        text_block_open = False
                        thinking_block_open = False
                    yield sse_format({
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": item["id"],
                            "name": item["name"],
                            "input": {},
                        },
                    }, event="content_block_start")
                    yield sse_format({
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "input_json_delta", "partial_json": item["arguments"]},
                    }, event="content_block_delta")
                    yield sse_format({"type": "content_block_stop", "index": block_index}, event="content_block_stop")
                    block_index += 1

                elif k == "finish":
                    fr = item["finish_reason"]
                    if fr == "length":
                        stop_reason = "max_tokens"
                    elif fr == "tool_calls":
                        stop_reason = "tool_use"
                    else:
                        stop_reason = "end_turn"

                elif k == "usage":
                    usage_info["input_tokens"] = item["prompt_tokens"]
                    usage_info["output_tokens"] = item["completion_tokens"]
                    if usage_sink is not None:
                        usage_sink.update({
                            "prompt_tokens": item["prompt_tokens"],
                            "completion_tokens": item["completion_tokens"],
                            "total_tokens": item["total_tokens"],
                        })

    if text_block_open or thinking_block_open:
        yield sse_format({"type": "content_block_stop", "index": block_index}, event="content_block_stop")

    yield sse_format({
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": usage_info["output_tokens"]},
    }, event="message_delta")

    yield sse_format({"type": "message_stop"}, event="message_stop")


async def _stream_to_responses(
    raw_stream: AsyncIterator[bytes],
    decoder: Any,
    buf: str,
    model: str,
    resp_id: str,
    usage_sink: dict[str, int] | None = None,
) -> AsyncIterator[bytes]:
    created = int(time.time())
    resp_uuid = resp_id.replace("chatcmpl-", "resp_")
    item_id = f"msg_{uuid.uuid4().hex[:24]}"

    yield sse_format({
        "type": "response.created",
        "response": {"id": resp_uuid, "object": "response", "created_at": created, "status": "in_progress", "model": model},
    })
    yield sse_format({
        "type": "response.in_progress",
        "response": {"id": resp_uuid, "status": "in_progress"},
    })
    yield sse_format({
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []},
    })

    full_text = ""
    usage_info: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    async for raw in raw_stream:
        buf += decoder.decode(raw)
        events, buf = split_sse_buffer(buf)
        for ev_str in events:
            try:
                ev_data = json.loads(ev_str)
            except Exception:
                continue
            parsed = parse_gemini_event(ev_data)
            for item in parsed:
                k = item["kind"]
                if k == "text":
                    txt = item["text"]
                    full_text += txt
                    yield sse_format({
                        "type": "response.output_text.delta",
                        "item_id": item_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": txt,
                    })
                elif k == "usage":
                    usage_info["input_tokens"] = item["prompt_tokens"]
                    usage_info["output_tokens"] = item["completion_tokens"]
                    usage_info["total_tokens"] = item["total_tokens"]
                    if usage_sink is not None:
                        usage_sink.update({
                            "prompt_tokens": item["prompt_tokens"],
                            "completion_tokens": item["completion_tokens"],
                            "total_tokens": item["total_tokens"],
                        })

    yield sse_format({
        "type": "response.output_text.done",
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "text": full_text,
    })
    yield sse_format({
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            "id": item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_text}],
        },
    })
    yield sse_format({
        "type": "response.completed",
        "response": {
            "id": resp_uuid,
            "status": "completed",
            "model": model,
            "output": [{
                "id": item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": full_text}],
            }],
            "usage": usage_info,
        },
    })

    yield b"data: [DONE]\n\n"
