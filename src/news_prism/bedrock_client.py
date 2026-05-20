"""Bedrock Converse API ラッパ。4 並列 persona call + streaming + cache 配置。

DECISION-0010 (streaming) / DECISION-0011 (Option α + 4 並列) を実装。
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from news_prism.prompts import PERSONA_LABELS, PERSONAS, get_persona_system
from news_prism.tool_schema import get_persona_tool, get_persona_tool_name

# Sonnet 4.6 + APAC CRIS + 8192 token 出力で 90-150 秒。Read timeout を 180 秒に
_BEDROCK_CONFIG = Config(
    read_timeout=180,
    connect_timeout=10,
    retries={"max_attempts": 2, "mode": "standard"},
)

# persona ごとの推論パラメータ。
# blogger は記事ごとに違うネタを発散させたいため temperature 高め
_PERSONA_TEMPERATURE = {
    "summary": 0.3,  # 中立的要約、安定優先
    "scoe": 0.6,
    "supply_chain_security": 0.6,
    "blogger": 0.9,  # ネタの発散重視
}


def _build_call_kwargs(
    persona: str, context_md: str, article_body: str
) -> dict[str, Any]:
    """指定 persona 用の converse_stream パラメータを組み立てる。"""
    tool_spec = get_persona_tool(persona)
    tool_name = get_persona_tool_name(persona)
    persona_label = PERSONA_LABELS[persona]

    return {
        "system": [{"text": get_persona_system(persona)}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            f"次に与える記事を {persona_label} として分析し、"
                            f"{tool_name} ツールで結果を返してください。"
                        )
                    },
                    {"text": f"<context>\n{context_md}\n</context>"},
                    {"cachePoint": {"type": "default"}},
                    {"text": f"<article>\n{article_body}\n</article>"},
                ],
            }
        ],
        "toolConfig": {
            "tools": [tool_spec],
            "toolChoice": {"tool": {"name": tool_name}},
        },
        "inferenceConfig": {
            "maxTokens": 4096,
            "temperature": _PERSONA_TEMPERATURE.get(persona, 0.6),
        },
    }


def _analyze_persona(
    client: Any, persona: str, context_md: str, article_body: str, model_id: str
) -> dict[str, Any]:
    """単一 persona の Bedrock streaming 呼び出し。

    Returns:
        {"output": <tool input dict>, "usage": {...}, "latency_ms": int}
    """
    kwargs = _build_call_kwargs(persona, context_md, article_body)
    start = time.monotonic()

    try:
        response = client.converse_stream(modelId=model_id, **kwargs)
    except ClientError as e:
        raise RuntimeError(f"Bedrock converse_stream error ({persona}): {e}") from e

    chunks: list[str] = []
    usage: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    stop_reason: str | None = None

    try:
        for event in response["stream"]:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"]["delta"]
                if "toolUse" in delta:
                    chunk = delta["toolUse"].get("input", "")
                    if chunk:
                        chunks.append(chunk)
            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason")
            elif "metadata" in event:
                usage = event["metadata"].get("usage", {})
                metrics = event["metadata"].get("metrics", {})
    except ClientError as e:
        raise RuntimeError(f"Bedrock stream error ({persona}): {e}") from e

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if stop_reason and stop_reason != "tool_use":
        print(
            f"[warn] {persona}: unexpected stop_reason: {stop_reason!r} (期待: 'tool_use')",
            file=sys.stderr,
        )

    raw = "".join(chunks)
    if not raw:
        raise RuntimeError(f"{persona}: stream produced no tool_use input")

    try:
        output: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"{persona}: failed to parse tool_use JSON: {e}\nRaw (first 500): {raw[:500]}"
        ) from e

    return {
        "output": output,
        "usage": usage,
        "latency_ms": metrics.get("latencyMs", elapsed_ms),
    }


def _empty_perspective(reason: str) -> str:
    return f"特になし: {reason}"


def _aggregate(results: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """4 persona の結果を PLAN §5 schema の最終 JSON 形に集約する。

    失敗した persona の値はプレースホルダー文字列を入れる (graceful degradation)。
    """
    summary_result = results.get("summary")
    if summary_result is not None:
        summary_text = summary_result["output"].get("summary", "")
    else:
        summary_text = _empty_perspective("Summary 呼び出し失敗")

    perspectives: dict[str, str] = {}
    okr_refs: set[str] = set()
    action_items: list[dict[str, Any]] = []

    for persona in ("scoe", "supply_chain_security", "blogger"):
        r = results.get(persona)
        if r is None:
            perspectives[persona] = _empty_perspective(f"{persona} 呼び出し失敗")
            continue
        out = r["output"]
        perspectives[persona] = out.get(
            "perspective", _empty_perspective("perspective なし")
        )
        okr_refs.update(out.get("relevant_okr_refs", []) or [])
        action = out.get("action_item")
        if action:
            action_items.append(
                {
                    "persona": persona,
                    "action": action.get("action", ""),
                    "linked_okr_refs": action.get("linked_okr_refs", []) or [],
                }
            )

    return {
        "summary": summary_text,
        "perspectives": perspectives,
        "relevant_okr_refs": sorted(okr_refs),
        "action_items": action_items,
    }


def analyze(context_md: str, article_body: str) -> dict[str, Any]:
    """4 並列で Summary + 3 視点 を Bedrock に投げて集約する。

    Returns:
        {
            "output": <PLAN §5 schema の最終 JSON>,
            "per_call": { persona: {"usage": ..., "latency_ms": ..., "error": ... } },
            "total_usage": { "input_tokens": ..., "cache_read_input_tokens": ..., ... },
            "wall_time_ms": int,
        }
    """
    model_id = os.environ.get("BEDROCK_MODEL_ID")
    if not model_id:
        raise RuntimeError("BEDROCK_MODEL_ID not set")

    client = boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "ap-northeast-1"),
        config=_BEDROCK_CONFIG,
    )

    print(
        f"[info] dispatching 4 parallel persona calls: {', '.join(PERSONAS)}",
        file=sys.stderr,
    )
    wall_start = time.monotonic()

    results: dict[str, dict[str, Any] | None] = dict.fromkeys(PERSONAS)
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=len(PERSONAS)) as executor:
        future_to_persona = {
            executor.submit(
                _analyze_persona, client, p, context_md, article_body, model_id
            ): p
            for p in PERSONAS
        }
        for future in as_completed(future_to_persona):
            persona = future_to_persona[future]
            try:
                result = future.result()
                results[persona] = result
                u = result["usage"]
                print(
                    f"[info] {persona} done in {result['latency_ms']}ms "
                    f"(in={u.get('inputTokens', 0)}, "
                    f"cache_r={u.get('cacheReadInputTokens', 0)}, "
                    f"cache_w={u.get('cacheWriteInputTokens', 0)}, "
                    f"out={u.get('outputTokens', 0)})",
                    file=sys.stderr,
                )
            except Exception as e:
                errors[persona] = str(e)
                results[persona] = None
                print(f"[error] {persona} failed: {e}", file=sys.stderr)

    wall_time_ms = int((time.monotonic() - wall_start) * 1000)
    failed = sum(1 for r in results.values() if r is None)
    if failed >= 3:
        raise RuntimeError(f"too many persona failures ({failed}/4): {errors}")

    aggregated_output = _aggregate(results)

    total_usage: dict[str, int] = {
        "input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
    }
    per_call: dict[str, dict[str, Any]] = {}
    for persona, r in results.items():
        if r is None:
            per_call[persona] = {"error": errors.get(persona, "unknown")}
            continue
        u = r["usage"]
        total_usage["input_tokens"] += u.get("inputTokens", 0)
        total_usage["cache_read_input_tokens"] += u.get("cacheReadInputTokens", 0)
        total_usage["cache_write_input_tokens"] += u.get("cacheWriteInputTokens", 0)
        total_usage["output_tokens"] += u.get("outputTokens", 0)
        per_call[persona] = {
            "input_tokens": u.get("inputTokens", 0),
            "cache_read_input_tokens": u.get("cacheReadInputTokens", 0),
            "cache_write_input_tokens": u.get("cacheWriteInputTokens", 0),
            "output_tokens": u.get("outputTokens", 0),
            "latency_ms": r["latency_ms"],
        }

    return {
        "output": aggregated_output,
        "per_call": per_call,
        "total_usage": total_usage,
        "wall_time_ms": wall_time_ms,
    }
