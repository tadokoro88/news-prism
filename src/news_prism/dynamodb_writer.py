"""DynamoDB writer for analysis history.

Schema (DECISION-0015):
    Main table `news-prism-analyses`
        PK: analysis_id (S)  — ULID
    GSI1: `gsi1`
        PK: user_id (S)      — 現状 "taka" 固定
        SK: created_at (S)   — ISO 8601 UTC

Lambda は PutItem のみ、Streams は使わない (PLAN §4.1)。
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Crockford base32 (ULID spec)。0/I/L/O 等は除外
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_ulid() -> str:
    """ULID を生成する (26 文字、48 bit ms timestamp + 80 bit randomness)。

    spec: https://github.com/ulid/spec
    stdlib のみで実装、外部依存を増やさない。
    """
    ts_ms = int(time.time() * 1000)
    rand_bits = secrets.randbits(80)
    # 48-bit timestamp を 10 文字に encode
    ts_chars = []
    for _ in range(10):
        ts_chars.append(_CROCKFORD[ts_ms & 0x1F])
        ts_ms >>= 5
    ts_part = "".join(reversed(ts_chars))
    # 80-bit random を 16 文字に encode
    rand_chars = []
    for _ in range(16):
        rand_chars.append(_CROCKFORD[rand_bits & 0x1F])
        rand_bits >>= 5
    rand_part = "".join(reversed(rand_chars))
    return ts_part + rand_part


def _to_dynamodb_safe(value: Any) -> Any:
    """DynamoDB が受け付けない float を Decimal に変換 (再帰)。

    boto3 の resource API は float を拒否する。usage 由来の整数 (input_tokens 等)
    は int のまま、wall_time_ms も int だが、将来 ratio や fraction が混ざる可能性に備える。
    空文字列は許容される (boto3 v1.35+)。
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamodb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_safe(v) for v in value]
    return value


class DynamoDBWriteError(Exception):
    """DynamoDB PutItem 失敗時に投げる。"""


def put_analysis(item: dict[str, Any]) -> None:
    """analyses テーブルに 1 item を PutItem する。

    item には少なくとも analysis_id, user_id, created_at が必須。
    """
    table_name = os.environ.get("ANALYSES_TABLE_NAME", "news-prism-analyses")
    region = os.environ.get("AWS_REGION", "ap-northeast-1")

    for key in ("analysis_id", "user_id", "created_at"):
        if key not in item:
            raise DynamoDBWriteError(f"required key missing: {key}")

    safe_item = _to_dynamodb_safe(item)
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    try:
        table.put_item(Item=safe_item)
    except ClientError as e:
        raise DynamoDBWriteError(
            f"PutItem failed on {table_name}: {e.response.get('Error', {}).get('Code')}"
        ) from e
