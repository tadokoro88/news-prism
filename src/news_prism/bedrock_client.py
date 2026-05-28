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

# Phase 8.1: Guardrails (SPEC §15、DECISION-0024)。
# DRAFT を当面ハードコード、published version に切るときに env 経由へ寄せる。
_GUARDRAIL_VERSION = "DRAFT"


def _guardrail_config_for(persona: str) -> dict[str, Any] | None:
    """env から persona の guardrailConfig を組み立てる。

    env 未設定 (= CLI / unit test) では None を返し、Guardrail off で動作させる。
    Summary のみ news-prism-grounding profile、それ以外は news-prism-default。
    """
    default_id = os.environ.get("NEWS_PRISM_GUARDRAIL_DEFAULT_ID")
    grounding_id = os.environ.get("NEWS_PRISM_GUARDRAIL_GROUNDING_ID")
    if not default_id:
        return None
    gid = grounding_id if persona == "summary" and grounding_id else default_id
    return {
        "guardrailIdentifier": gid,
        "guardrailVersion": _GUARDRAIL_VERSION,
        "trace": "enabled",
    }


def _build_call_kwargs(
    persona: str, context_md: str, article_body: str
) -> dict[str, Any]:
    """指定 persona 用の converse_stream パラメータを組み立てる。

    Guardrail が有効な時:
    - 指示文を `query` qualifier ブロックでラップ (Contextual Grounding 用)
    - `<article>` を `guard_content` qualifier でラップ (Prompt Attack scope)
    - Summary persona だけ追加で `grounding_source` qualifier を combine
      (1 ブロックに複数 qualifier 指定可、Phase 8.2 検証で確認、SPEC §15.3 / §15.12)

    cachePoint は `<article>` の直前を維持。指示文と <context> が cache 対象。
    """
    tool_spec = get_persona_tool(persona)
    tool_name = get_persona_tool_name(persona)
    persona_label = PERSONA_LABELS[persona]
    instruction_text = (
        f"次に与える記事を {persona_label} として分析し、"
        f"{tool_name} ツールで結果を返してください。"
    )

    guardrail_config = _guardrail_config_for(persona)
    if guardrail_config is not None:
        # Summary だけ `grounding_source` を combine、それ以外は `guard_content` のみ。
        # 他 persona の guardrail (news-prism-default) には Grounding policy が
        # 入っていないので `query` qualifier は無視される (副作用なし、コード分岐削減)。
        article_qualifiers = ["guard_content"]
        if persona == "summary":
            article_qualifiers = ["guard_content", "grounding_source"]
        instruction_block: dict[str, Any] = {
            "guardContent": {
                "text": {"text": instruction_text, "qualifiers": ["query"]}
            }
        }
        article_block: dict[str, Any] = {
            "guardContent": {
                "text": {
                    "text": f"<article>\n{article_body}\n</article>",
                    "qualifiers": article_qualifiers,
                }
            }
        }
    else:
        instruction_block = {"text": instruction_text}
        article_block = {"text": f"<article>\n{article_body}\n</article>"}

    kwargs: dict[str, Any] = {
        "system": [{"text": get_persona_system(persona)}],
        "messages": [
            {
                "role": "user",
                "content": [
                    instruction_block,
                    {"text": f"<context>\n{context_md}\n</context>"},
                    {"cachePoint": {"type": "default"}},
                    article_block,
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
    if guardrail_config is not None:
        kwargs["guardrailConfig"] = guardrail_config
    return kwargs


def _classify_guardrail_action(trace: dict[str, Any]) -> str:
    """metadata.trace.guardrail から SPEC §15.5 のアクション文字列を返す。

    優先順: INPUT_BLOCKED > OUTPUT_BLOCKED > GROUNDING_FAILED > ANONYMIZED > NONE。
    Bedrock の trace schema は将来変わりうるので、想定外の構造は NONE にフォールバックする。
    """
    if not isinstance(trace, dict):
        return "NONE"

    input_assess = trace.get("inputAssessment", {}) or {}
    output_assess = trace.get("outputAssessments", {}) or {}

    def _any_blocked(assess: dict[str, Any]) -> bool:
        for assessment in _iter_assessments(assess):
            if _policy_has_action(assessment, "BLOCKED"):
                return True
        return False

    def _any_grounding_failed(assess: dict[str, Any]) -> bool:
        for assessment in _iter_assessments(assess):
            cg = assessment.get("contextualGroundingPolicy") or {}
            for f in cg.get("filters", []) or []:
                if f.get("action") == "BLOCKED":
                    return True
        return False

    def _any_anonymized(assess: dict[str, Any]) -> bool:
        for assessment in _iter_assessments(assess):
            sip = assessment.get("sensitiveInformationPolicy") or {}
            for entity in sip.get("piiEntities", []) or []:
                if entity.get("action") == "ANONYMIZED":
                    return True
        return False

    if _any_blocked(input_assess):
        return "INPUT_BLOCKED"
    if _any_blocked(output_assess):
        return "OUTPUT_BLOCKED"
    if _any_grounding_failed(output_assess):
        return "GROUNDING_FAILED"
    if _any_anonymized(input_assess) or _any_anonymized(output_assess):
        return "ANONYMIZED"
    return "NONE"


def _iter_assessments(assess: dict[str, Any]) -> list[dict[str, Any]]:
    """input/output どちらでも assessments の list を取り出せるよう正規化する。

    inputAssessment は guardrailId をキーに持つ dict、outputAssessments は同じく
    guardrailId → list[assessment] の dict、という非対称な形になっている。
    """
    if not isinstance(assess, dict):
        return []
    out: list[dict[str, Any]] = []
    for v in assess.values():
        if isinstance(v, list):
            out.extend(a for a in v if isinstance(a, dict))
        elif isinstance(v, dict):
            out.append(v)
    return out


def _policy_has_action(assessment: dict[str, Any], action: str) -> bool:
    for key, items_key in (
        ("topicPolicy", "topics"),
        ("contentPolicy", "filters"),
        ("wordPolicy", "customWords"),
        ("wordPolicy", "managedWordLists"),
        ("sensitiveInformationPolicy", "piiEntities"),
    ):
        policy = assessment.get(key) or {}
        for entry in policy.get(items_key, []) or []:
            if entry.get("action") == action:
                return True
    return False


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
    guardrail_action = "NONE"

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
                metadata = event["metadata"]
                usage = metadata.get("usage", {})
                metrics = metadata.get("metrics", {})
                trace = metadata.get("trace", {}) or {}
                guardrail_trace = trace.get("guardrail", {}) or {}
                # Phase 8.2 検証用 raw dump (DECISION-0025 #3、DECISION-0026)。
                # NEWS_PRISM_GUARDRAIL_TRACE_DEBUG=1 でのみ吐く。本番ログ汚染防止。
                if os.environ.get("NEWS_PRISM_GUARDRAIL_TRACE_DEBUG"):
                    print(
                        f"[trace-meta-keys] {persona}: "
                        f"metadata={list(metadata.keys())} trace={list(trace.keys())}",
                        file=sys.stderr,
                    )
                    print(
                        f"[trace] {persona}: "
                        f"{json.dumps(guardrail_trace, ensure_ascii=False)}",
                        file=sys.stderr,
                    )
                action = _classify_guardrail_action(guardrail_trace)
                # 最も重い action を保持 (NONE は上書きしない)
                if action != "NONE":
                    guardrail_action = action
    except ClientError as e:
        # SPEC §15.5: Guardrail API 自体のエラーも fail-open でログのみ
        raise RuntimeError(f"Bedrock stream error ({persona}): {e}") from e

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # guardrail intervened は SPEC §15.5 の stop reason として正常系扱い
    if stop_reason == "guardrail_intervened":
        # metadata.trace を読めずに到達した場合の fallback
        if guardrail_action == "NONE":
            guardrail_action = "INPUT_BLOCKED"
        print(
            f"[info] {persona}: guardrail intervened (action={guardrail_action})",
            file=sys.stderr,
        )
    elif stop_reason and stop_reason != "tool_use":
        print(
            f"[warn] {persona}: unexpected stop_reason: {stop_reason!r} (期待: 'tool_use')",
            file=sys.stderr,
        )

    raw = "".join(chunks)
    if not raw:
        # Guardrail BLOCKED で tool_use が出ない場合、空 output で fail-open (SPEC §15.5)
        if guardrail_action in ("INPUT_BLOCKED", "OUTPUT_BLOCKED"):
            return {
                "output": {},
                "usage": usage,
                "latency_ms": metrics.get("latencyMs", elapsed_ms),
                "guardrail_action": guardrail_action,
            }
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
        "guardrail_action": guardrail_action,
    }


def _empty_perspective(reason: str) -> str:
    return f"(特になし) {reason}"


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
    guardrail_actions: dict[str, str] = {}
    for persona, r in results.items():
        if r is None:
            per_call[persona] = {"error": errors.get(persona, "unknown")}
            continue
        u = r["usage"]
        total_usage["input_tokens"] += u.get("inputTokens", 0)
        total_usage["cache_read_input_tokens"] += u.get("cacheReadInputTokens", 0)
        total_usage["cache_write_input_tokens"] += u.get("cacheWriteInputTokens", 0)
        total_usage["output_tokens"] += u.get("outputTokens", 0)
        action = r.get("guardrail_action", "NONE")
        guardrail_actions[persona] = action
        per_call[persona] = {
            "input_tokens": u.get("inputTokens", 0),
            "cache_read_input_tokens": u.get("cacheReadInputTokens", 0),
            "cache_write_input_tokens": u.get("cacheWriteInputTokens", 0),
            "output_tokens": u.get("outputTokens", 0),
            "latency_ms": r["latency_ms"],
            "guardrail_action": action,
        }

    return {
        "output": aggregated_output,
        "per_call": per_call,
        "total_usage": total_usage,
        "wall_time_ms": wall_time_ms,
        "guardrail_actions": guardrail_actions,
    }
