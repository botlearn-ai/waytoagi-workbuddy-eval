"""judge 调用封装(OpenRouter)。返回文本与本次调用花费。"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_ATTEMPTS = 4
TIMEOUT = 120

# judge 取中立第三方(不是被测的字节/阿里/腾讯任何一家),带日期的快照钉死版本。
# provider 钉 DeepSeek 官方端点并禁回落:阿里也是 OpenRouter 上这个模型的托管方之一,
# 而阿里是被测厂商,不能让它给自己判分。
DEFAULT_MODEL = "deepseek/deepseek-v4-pro-0813"
PROVIDER = {"order": ["DeepSeek"], "allow_fallbacks": False}


class JudgeError(Exception):
    pass


@dataclass
class Reply:
    text: str
    cost: float
    provider: str = ""


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        path = os.path.expanduser("~/.config/openrouter.key")
        if os.path.exists(path):
            with open(path) as f:
                key = f.read().strip()
    if not key:
        raise JudgeError("缺 OPENROUTER_API_KEY")
    return key


def ask(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    key: str | None = None,
    system: str | None = None,
    client: httpx.Client | None = None,
) -> Reply:
    """问一次 judge。失败按指数退避重试,返回文本和花费。"""
    key = key or api_key()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "usage": {"include": True},
        "provider": PROVIDER,
    }
    headers = {"Authorization": f"Bearer {key}"}

    own = client is None
    client = client or httpx.Client()
    try:
        last = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                r = client.post(URL, json=body, headers=headers, timeout=TIMEOUT)
                if r.status_code == 200:
                    data = r.json()
                    text = (data["choices"][0]["message"]["content"] or "").strip()
                    cost = float(data.get("usage", {}).get("cost") or 0.0)
                    served = data.get("provider") or ""
                    if served and served not in PROVIDER["order"]:
                        raise JudgeError(
                            f"provider 漂移: 期望 {PROVIDER['order']},实际 {served!r}"
                        )
                    if text:
                        return Reply(text, cost, served)
                    last = "空响应"
                else:
                    last = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2**attempt)
        raise JudgeError(f"judge 调用失败({MAX_ATTEMPTS} 次): {last}")
    finally:
        if own:
            client.close()
