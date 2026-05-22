"""my-goals private repo の context.md を GitHub raw URL で fetch する。

PAT 取得経路 (DECISION-0017):
  1. NEWS_PRISM_GH_PAT_SECRET_ARN が設定されていれば Secrets Manager から
     GetSecretValue (Lambda 本番経路)。同一 Lambda コンテナ内で module-level
     キャッシュし、warm 起動では API call なし
  2. それ以外は NEWS_PRISM_GH_PAT env var から直接読む (local CLI / 開発用)
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import boto3
from botocore.exceptions import ClientError

from news_prism.http_safety import (
    UnsafeURLError,
    make_ssl_context,
    safe_urlopen_bytes,
)

_SSL_CONTEXT = make_ssl_context()

# context.md は OKR markdown 想定。512KB を超えるなら prompt token も嵩むので reject
_MAX_RESPONSE_BYTES = 512 * 1024

# NEWS_PRISM_CONTEXT_URL を env で渡す。未設定なら fetch をスキップ (= 個人化なし、3 視点解析として動く)
# 例: my-goals 等の private repo の raw URL
#     "https://raw.githubusercontent.com/<account>/<repo>/main/context.md"
CONTEXT_URL_ENV = "NEWS_PRISM_CONTEXT_URL"

# Lambda コンテナ単位で PAT をキャッシュ。warm 起動では Secrets Manager API を呼ばない
_pat_cache: str | None = None


class ContextFetchError(Exception):
    """context.md の fetch 失敗時に投げる。caller 側で空 context にフォールバック想定。"""


def _resolve_pat() -> str | None:
    """PAT を解決して返す。Secrets Manager 優先、env var fallback、キャッシュあり。

    どちらも未設定なら None を返す (CONTEXT_URL が public で PAT 不要なケースに対応)。
    """
    global _pat_cache
    if _pat_cache:
        return _pat_cache

    secret_arn = os.environ.get("NEWS_PRISM_GH_PAT_SECRET_ARN")
    if secret_arn:
        region = os.environ.get("AWS_REGION", "ap-northeast-1")
        client = boto3.client("secretsmanager", region_name=region)
        try:
            resp = client.get_secret_value(SecretId=secret_arn)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            raise ContextFetchError(
                f"Secrets Manager GetSecretValue failed: {code}"
            ) from e
        secret_str = resp.get("SecretString")
        if not isinstance(secret_str, str) or not secret_str:
            raise ContextFetchError("secret has no SecretString (value not uploaded?)")
        pat = secret_str.strip()
        _pat_cache = pat
        return pat

    env_pat = os.environ.get("NEWS_PRISM_GH_PAT")
    if env_pat:
        _pat_cache = env_pat
        return env_pat

    return None


def fetch_context(timeout: float = 5.0) -> str:
    """NEWS_PRISM_CONTEXT_URL の指す URL から context.md を fetch する。

    URL が未設定なら ContextFetchError を投げる (caller 側で空 context フォールバック)。
    GitHub raw URL を想定するが、Authorization header を受ける任意のエンドポイントで動く。
    """
    context_url = os.environ.get(CONTEXT_URL_ENV)
    if not context_url:
        raise ContextFetchError(f"{CONTEXT_URL_ENV} not set")

    pat = _resolve_pat()
    headers = {
        "Accept": "application/vnd.github.raw",
        "User-Agent": "news-prism/0.1",
    }
    if pat:
        headers["Authorization"] = f"Bearer {pat}"

    req = urllib.request.Request(context_url, headers=headers)
    try:
        raw, _ = safe_urlopen_bytes(
            req,
            timeout=timeout,
            ssl_context=_SSL_CONTEXT,
            max_bytes=_MAX_RESPONSE_BYTES,
        )
    except UnsafeURLError as e:
        raise ContextFetchError(f"refused unsafe context URL: {e}") from e
    except urllib.error.HTTPError as e:
        raise ContextFetchError(f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise ContextFetchError(f"URL error: {e.reason}") from e
    return raw.decode("utf-8")
