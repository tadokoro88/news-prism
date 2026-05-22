"""記事 URL から本文を抽出する。"""

from __future__ import annotations

import re
import urllib.error
import urllib.request

import trafilatura

from news_prism.http_safety import (
    UnsafeURLError,
    make_ssl_context,
    safe_urlopen_bytes,
)

_SSL_CONTEXT = make_ssl_context()

# 5 MB 上限。Web 記事の生 HTML は通常 1 MB 未満で収まる
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024

# stdlib urllib で fetch する理由 (trafilatura.fetch_url を使わない理由):
# - trafilatura は内部で urllib3 + 自前 certifi バンドル固定で SSL_CERT_FILE env を respect しない
#   → 企業 MITM プロキシで再署名された cert を検証できず None を返す
# - stdlib urllib.request はデフォルト SSL context が SSL_CERT_FILE を respect する
# - エラーが None でなく具体的な例外 (HTTPError / URLError / TimeoutError) で出る点も診断しやすい
# ブラウザ風 UA は副次的な anti-bot 対策 (本件の原因ではなかったが、defense-in-depth として残置)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# HTML 内 <meta charset> 検出用。先頭 ~2KB を ASCII fallback で読んで正規表現で抜く
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?([\w\-]+)""", re.IGNORECASE
)


class ArticleFetchError(Exception):
    """記事 fetch / 本文抽出失敗時に投げる。"""


def _detect_charset(raw: bytes, http_charset: str | None) -> str:
    """3 段階で charset 検出: HTTP header → <meta charset> → charset-normalizer。

    ITmedia など日本語サイトの一部は HTTP header に charset を含めず HTML meta に書く。
    """
    if http_charset:
        return http_charset

    # 先頭 2KB から <meta charset="..."> を抜く (タグは HTML head 早期に出る前提)
    m = _META_CHARSET_RE.search(raw[:2048])
    if m:
        try:
            detected = m.group(1).decode("ascii")
            # "shift_jis", "utf-8", "euc-jp" などが入る想定
            return detected
        except UnicodeDecodeError:
            pass

    # 最終手段: charset-normalizer (trafilatura が既に依存に持っている)
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best and best.encoding:
            return best.encoding
    except ImportError:
        pass

    return "utf-8"


def fetch_article(url: str, timeout: float = 15.0) -> tuple[str, str | None]:
    """URL から記事本文と (取れれば) タイトルを返す。

    Returns:
        (body, title): body は抽出本文、title は metadata から取れなければ None
    """
    # 一般的な Chrome の Request Headers を真似る (low-stakes な anti-bot 対策)
    # Cloudflare / Akamai の本気の bot 防御は IP reputation + TLS 指紋まで見るので
    # これだけでは越えられない (paste fallback が現実解)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",  # gzip 受け取らない (decode 不要に)
            "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    try:
        raw, http_charset = safe_urlopen_bytes(
            req,
            timeout=timeout,
            ssl_context=_SSL_CONTEXT,
            max_bytes=_MAX_RESPONSE_BYTES,
        )
    except UnsafeURLError as e:
        raise ArticleFetchError(f"refused unsafe URL: {e}") from e
    except urllib.error.HTTPError as e:
        raise ArticleFetchError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise ArticleFetchError(f"URL error for {url}: {e.reason}") from e
    except TimeoutError as e:
        raise ArticleFetchError(f"timeout ({timeout}s) fetching {url}") from e

    charset = _detect_charset(raw, http_charset)
    html = raw.decode(charset, errors="replace")

    body = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not body:
        raise ArticleFetchError(f"trafilatura could not extract body from {url}")

    metadata = trafilatura.extract_metadata(html)
    title = metadata.title if metadata else None
    return body, title
