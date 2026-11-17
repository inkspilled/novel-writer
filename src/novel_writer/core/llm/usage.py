"""usage 归一化：把各厂商的用量字段统一成一套键（缓存观测的地基）。

统一口径（见 docs/上下文缓存与1M预算.md §8.2）：
- prompt_tokens      总输入 token。OpenAI/DeepSeek 原样（官方语义已含命中部分）；
                     Anthropic = input_tokens + cache_read + cache_creation
- completion_tokens  输出 token
- cached_tokens      命中缓存的输入部分（按折扣价计费）
- cache_write_tokens 缓存写入部分（Anthropic 约 1.25× 计费；自动缓存厂商为 0）
- cache_meta         本次响应是否携带缓存字段（1/0）。聚合后即「返回缓存字段
                     的调用次数」——为 0 时命中率不可知，UI 显示「缓存 —」，
                     与「真没命中（0%）」区分开。

注意：cached_tokens / cache_write_tokens 也以 tokens 结尾，任何「把 *tokens
键求和当总量」的旧逻辑都会重复计数——总量一律用 prompt + completion 显式求和
（workflow_bar.set_usage 与 runner 的 LONGRUN 日志已同步改掉）。
"""
from __future__ import annotations

import json


def normalize_openai_usage(usage) -> dict:
    """OpenAI 兼容口径（DeepSeek / GLM / Kimi / 通义等）。"""
    if usage is None:
        return {}
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
    # DeepSeek 命名字段（部分网关/旧 SDK 才透传）
    deepseek_hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if cached == 0 and deepseek_hit is not None:
        cached = int(deepseek_hit or 0)
    meta = 1 if (details is not None or deepseek_hit is not None) else 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "cache_write_tokens": 0,
        "cache_meta": meta,
    }


def normalize_claude_usage(usage) -> dict:
    """Anthropic 口径：input_tokens 不含缓存部分，需并回总输入。"""
    if usage is None:
        return {}
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return {
        "prompt_tokens": input_tokens + cache_read + cache_write,
        "completion_tokens": completion,
        "cached_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cache_meta": 1,
    }


def parse_stream_usage(payload: str) -> dict | None:
    """解析流式末尾的 usage JSON；坏数据返回 None 而不是抛错。"""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
