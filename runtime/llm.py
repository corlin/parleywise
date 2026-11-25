"""LiteLLM 网关客户端与结构化输出解析。"""
import http.client
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from threading import Lock


def parse_findings(content):
    """抽取 JSON findings；网关未启用 json_object 时容忍代码块或前后说明文字。"""
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None
    fs = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(fs, list):
        return None
    return [f for f in fs if isinstance(f, dict) and isinstance(f.get("quote"), str)]


class LLM:
    def __init__(self, observer=None):
        self.base = os.environ.get("LITELLM_BASE_URL", "https://llm.corlin.cn").rstrip("/")
        self.key = os.environ.get("LITELLM_API_KEY", "")
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
                      "calls_without_cost": 0}
        self._usage_lock = Lock()
        self.observer = observer

    def _observe(self, event):
        if self.observer:
            try:
                self.observer(event)
            except Exception:
                print('无法保存模型调用记录', file=sys.stderr)

    def chat(self, model, system, user):
        """返回 findings 列表。回复不是合法 JSON 时重发一次，仍失败就报错，避免把坏回复当成“没问题”。"""
        for _ in range(2):
            findings = parse_findings(self._call(model, system, user))
            if findings is not None:
                return findings
        raise RuntimeError(f"模型 {model} 两次返回都无法解析为 JSON")

    def chat_messages(self, model, messages, *, tools=None, response_format=None):
        """通用多轮对话调用，支持可选 tools 与 response_format，返回完整 assistant message。"""
        req_body = {"model": model, "temperature": 0, "messages": messages}
        if tools:
            req_body["tools"] = tools
        if response_format:
            req_body["response_format"] = response_format
        body = json.dumps(req_body, ensure_ascii=False).encode()
        return self._send_request(model, body)

    def stream_messages(self, model, messages, *, tools=None):
        """流式对话调用：逐块产出 ('delta', str)，最后产出 ('message', dict) 并记录用量。"""
        req_body = {
            "model": model,
            "temperature": 0,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            req_body["tools"] = tools
        body = json.dumps(req_body, ensure_ascii=False).encode()
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        started_at, t0 = time.time(), time.monotonic()
        event = {"requested_model": model, "attempt": 1, "started_at": started_at}
        content_parts = []
        tc_acc = {}
        usage = {}
        served_model = None
        cost_value = None
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                cost_value = self._parse_cost(resp.headers.get("x-litellm-response-cost"))
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    chunk = json.loads(data_str)
                    if isinstance(chunk.get("model"), str):
                        served_model = chunk["model"]
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        content_parts.append(piece)
                        yield ("delta", piece)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tc_acc.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError, ValueError) as e:
            event.update(status="failed", duration_ms=round((time.monotonic() - t0) * 1000))
            self._observe(event)
            raise RuntimeError(f"网关流式调用失败：{e}") from e

        pt = usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int and usage.get("prompt_tokens") >= 0 else None
        ct = usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int and usage.get("completion_tokens") >= 0 else None
        event.update(
            status="responded",
            served_model=served_model[:200] if isinstance(served_model, str) else None,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=cost_value,
            duration_ms=round((time.monotonic() - t0) * 1000),
        )
        self._observe(event)
        with self._usage_lock:
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += pt or 0
            self.usage["completion_tokens"] += ct or 0
            if cost_value is None:
                self.usage["calls_without_cost"] += 1
            else:
                self.usage["cost_usd"] += cost_value
        tool_calls = [tc_acc[k] for k in sorted(tc_acc)] if tc_acc else []
        yield ("message", {"content": "".join(content_parts), "tool_calls": tool_calls})

    @staticmethod
    def _parse_cost(raw):
        try:
            v = float(raw)
            return v if math.isfinite(v) and v >= 0 else None
        except (TypeError, ValueError):
            return None

    def _call(self, model, system, user):
        body = json.dumps({
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode()
        return self._send_request(model, body).get("content") or ""

    def _send_request(self, model, body):
        req = urllib.request.Request(self.base + "/v1/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        for attempt in range(3):
            started_at, t0 = time.time(), time.monotonic()
            event = {'requested_model': model, 'attempt': attempt + 1, 'started_at': started_at}
            try:
                with urllib.request.urlopen(req, timeout=240) as resp:
                    payload = json.loads(resp.read())
                    cost = resp.headers.get("x-litellm-response-cost")
                u = payload.get('usage') or {}
                message = payload['choices'][0]['message']
                try:
                    cost_value = float(cost)
                    if not math.isfinite(cost_value) or cost_value < 0:
                        cost_value = None
                except (TypeError, ValueError):
                    cost_value = None

                def tokens(key):
                    value = u.get(key)
                    return value if type(value) is int and value >= 0 else None

                served_model = payload.get('model')
                event.update(status='responded', served_model=served_model[:200] if isinstance(served_model, str) else None,
                             prompt_tokens=tokens('prompt_tokens'), completion_tokens=tokens('completion_tokens'),
                             cost_usd=cost_value, duration_ms=round((time.monotonic() - t0) * 1000))
                self._observe(event)
                break
            except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as e:
                event.update(status='failed', duration_ms=round((time.monotonic() - t0) * 1000))
                self._observe(event)
                if attempt == 2:
                    raise RuntimeError(f"网关调用失败：{e}") from e
                time.sleep(2 * (attempt + 1))
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
                event.update(status='failed', duration_ms=round((time.monotonic() - t0) * 1000))
                self._observe(event)
                raise RuntimeError('网关响应格式不正确') from e
        with self._usage_lock:
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += event['prompt_tokens'] or 0
            self.usage["completion_tokens"] += event['completion_tokens'] or 0
            try:
                if cost_value is None:
                    raise ValueError("missing cost")
                self.usage["cost_usd"] += cost_value
            except (ValueError, TypeError):
                self.usage["calls_without_cost"] += 1
        return message
