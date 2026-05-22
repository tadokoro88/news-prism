"""API Gateway → Lambda handler。

POST /analyze
    body: {"url": "<記事URL>"} または {"url": "<URL>", "article_body": "<本文>"}
    後者は CLI の --paste mode 相当 (記事 fetch 失敗時の fallback)

Phase 2 (5/20-21): 既存 analyze() を wrap + DynamoDB PutItem。
Secrets Manager 化、API Gateway / IaC は別タスク。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any

from news_prism.article_fetch import ArticleFetchError, fetch_article
from news_prism.bedrock_client import analyze
from news_prism.context_fetch import ContextFetchError, fetch_context
from news_prism.dynamodb_writer import (
    DynamoDBWriteError,
    generate_ulid,
    put_analysis,
)

_DEFAULT_USER_ID = "taka"

# 入力サイズ上限 — Bedrock コスト abuse と Lambda memory 枯渇の両方を防ぐ
# URL: 通常 256 chars 程度、long share URL でも 1KB 内に収まる
# article_body: 100K chars ≒ 日本語で 25-50K tokens、Sonnet 1 req あたり数十円に収まる範囲
_MAX_URL_CHARS = 2048
_MAX_ARTICLE_BODY_CHARS = 100_000

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class _BadRequest(Exception):
    """4xx 相当の client error。message が response body に載る。"""


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    """API Gateway proxy event の body を JSON として decode する。

    isBase64Encoded には未対応 (binary body は受け付けない)。
    """
    body = event.get("body")
    if body is None:
        raise _BadRequest("missing request body")
    if event.get("isBase64Encoded"):
        raise _BadRequest("base64 body not supported")
    if isinstance(body, dict):
        return body
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise _BadRequest(f"invalid JSON body: {e}") from e


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _failed_personas(per_call: dict[str, dict[str, Any]]) -> list[str]:
    return [p for p, info in per_call.items() if "error" in info]


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway proxy integration の入り口。"""
    try:
        payload = _parse_body(event)
    except _BadRequest as e:
        logger.warning("bad request: %s", e)
        return _response(400, {"error": str(e)})

    url = payload.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return _response(400, {"error": "field 'url' must be an http(s) URL"})
    if len(url) > _MAX_URL_CHARS:
        return _response(
            400, {"error": f"field 'url' too long (>{_MAX_URL_CHARS} chars)"}
        )

    # 1. 記事本文 — body 内の article_body 優先 (paste mode 相当)
    article_body = payload.get("article_body")
    title: str | None = None
    if isinstance(article_body, str) and article_body.strip():
        if len(article_body) > _MAX_ARTICLE_BODY_CHARS:
            return _response(
                400,
                {
                    "error": (
                        f"field 'article_body' too long "
                        f"(>{_MAX_ARTICLE_BODY_CHARS} chars)"
                    )
                },
            )
        logger.info("using article_body from request (%d chars)", len(article_body))
    else:
        try:
            article_body, title = fetch_article(url)
        except ArticleFetchError as e:
            logger.warning("article fetch failed: %s", e)
            # 内部の例外詳細 (refused IP, redirect 履歴等) は client に出さない
            return _response(
                422,
                {
                    "error": "article fetch failed",
                    "hint": "POST { url, article_body } で本文を直接送る fallback が使えます",
                },
            )
        if len(article_body) > _MAX_ARTICLE_BODY_CHARS:
            logger.warning(
                "fetched article_body exceeds limit: %d chars", len(article_body)
            )
            return _response(422, {"error": "fetched article body too large"})

    # 2. my-goals context.md
    try:
        context_md = fetch_context()
        logger.info("context fetched (%d chars)", len(context_md))
    except ContextFetchError as e:
        logger.warning("context fetch failed: %s — fallback to empty context", e)
        context_md = ""

    # 3. Bedrock 4 並列呼び出し
    try:
        result = analyze(context_md, article_body)
    except RuntimeError:
        logger.exception("bedrock orchestration failed")
        # 上流の詳細 (model id, ARN, region 等) は client に出さない
        return _response(502, {"error": "upstream orchestration error"})

    # 4. DynamoDB 保存 (DECISION-0015 schema)
    analysis_id = generate_ulid()
    created_at = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
    failed_personas = _failed_personas(result["per_call"])
    item: dict[str, Any] = {
        "analysis_id": analysis_id,
        "user_id": _DEFAULT_USER_ID,
        "created_at": created_at,
        "url": url,
        "title": title,
        "summary": result["output"].get("summary", ""),
        "perspectives": result["output"].get("perspectives", {}),
        "relevant_okr_refs": result["output"].get("relevant_okr_refs", []),
        "action_items": result["output"].get("action_items", []),
        "meta": {
            "wall_time_ms": result["wall_time_ms"],
            "total_usage": result["total_usage"],
            "failed_personas": failed_personas,
        },
    }
    try:
        put_analysis(item)
        logger.info("dynamodb put_item ok (analysis_id=%s)", analysis_id)
    except DynamoDBWriteError as e:
        # 保存失敗でも解析結果は返す (UX 優先、保存は副作用扱い)
        logger.exception("dynamodb put_item failed: %s", e)

    # 5. PLAN §5 schema を返却
    final = {
        "analysis_id": analysis_id,
        "url": url,
        "title": title,
        "fetched_at": created_at,
        **result["output"],
    }

    # CloudWatch には usage を 1 行 JSON で残す (SPEC §2.3 と同形式)
    logger.info(
        json.dumps(
            {
                "type": "usage",
                "analysis_id": analysis_id,
                "url": url,
                "wall_time_ms": result["wall_time_ms"],
                "total_usage": result["total_usage"],
                "per_call": result["per_call"],
                "failed_personas": failed_personas,
            },
            ensure_ascii=False,
        )
    )

    return _response(
        200,
        {
            "result": final,
            "meta": {
                "wall_time_ms": result["wall_time_ms"],
                "total_usage": result["total_usage"],
                "failed_personas": failed_personas,
            },
        },
    )
