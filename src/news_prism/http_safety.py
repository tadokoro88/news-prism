"""URL fetch の defensive helper — SSRF / 応答サイズ / redirect を制御する。

article_fetch.py と context_fetch.py から共通利用する。Lambda は VPC 外で動くので
プライベートレンジへの到達可能性は限定的だが、defense-in-depth として host 解決時に
private/loopback/link-local IP を弾き、巨大応答や redirect ループも遮断する。
"""

from __future__ import annotations

import ipaddress
import os
import socket
import ssl
import urllib.request
from http.client import HTTPMessage
from typing import IO
from urllib.parse import urlparse

DEFAULT_MAX_REDIRECTS = 5
_ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeURLError(Exception):
    """SSRF / 応答サイズ超過 / 不正リダイレクト等の defensive 拒否。"""


def make_ssl_context() -> ssl.SSLContext | None:
    """SSL_CERT_FILE があれば優先、なければ certifi、それもなければ None (デフォルト trust)。

    企業 MITM プロキシ環境では SSL_CERT_FILE に社内 CA を含むバンドルを指す。
    """
    ssl_cert_file = os.environ.get("SSL_CERT_FILE")
    if ssl_cert_file:
        return ssl.create_default_context(cafile=ssl_cert_file)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


def is_ip_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """global routable な public IP のみ True。

    private / loopback / link-local / multicast / reserved / unspecified を reject。
    """
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_host_safe(host: str) -> None:
    """host が public IP のみに解決されることを確認する。

    IP リテラル直書きも reject。DNS rebinding の完全防御 (connect 時 IP 固定) は
    行わないが、resolve 時点での reject で IMDS / 内部 LB 等への到達は塞げる。
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not is_ip_safe(ip):
            raise UnsafeURLError(f"refused private/reserved IP literal {host}")
        return

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"DNS resolution failed for {host}: {e}") from e

    for info in infos:
        ip_str = info[4][0]
        try:
            resolved = ipaddress.ip_address(ip_str)
        except ValueError:
            raise UnsafeURLError(f"could not parse resolved IP {ip_str}") from None
        if not is_ip_safe(resolved):
            raise UnsafeURLError(f"{host} resolves to private/reserved IP {ip_str}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """各 redirect hop でスキーム + host を再検査し、上限を超えたら reject。"""

    def __init__(self, max_redirects: int) -> None:
        super().__init__()
        self.max_redirects = max_redirects
        self._count = 0

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if self._count >= self.max_redirects:
            raise UnsafeURLError(f"redirect limit exceeded (>{self.max_redirects})")
        self._count += 1
        parsed = urlparse(newurl)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise UnsafeURLError(f"redirect to refused scheme {parsed.scheme!r}")
        if not parsed.hostname:
            raise UnsafeURLError(f"redirect URL has no host: {newurl}")
        check_host_safe(parsed.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen_bytes(
    req: urllib.request.Request,
    *,
    timeout: float,
    ssl_context: ssl.SSLContext | None,
    max_bytes: int,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> tuple[bytes, str | None]:
    """SSRF / 応答サイズ / redirect を制御して fetch し、(本体 bytes, HTTP charset) を返す。

    - 初回 URL のスキーム/host を検査
    - 全 redirect hop でも host を再検査
    - Content-Length が宣言されていれば事前に超過判定
    - 応答は max_bytes + 1 まで読み、超過は UnsafeURLError
    """
    parsed = urlparse(req.full_url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"refused scheme {parsed.scheme!r}")
    if not parsed.hostname:
        raise UnsafeURLError(f"URL has no host: {req.full_url}")
    check_host_safe(parsed.hostname)

    handlers: list[urllib.request.BaseHandler] = []
    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
    handlers.append(_SafeRedirectHandler(max_redirects))
    opener = urllib.request.build_opener(*handlers)

    with opener.open(req, timeout=timeout) as resp:
        declared = resp.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    raise UnsafeURLError(
                        f"declared Content-Length {declared} exceeds {max_bytes}"
                    )
            except ValueError:
                pass
        http_charset = resp.headers.get_content_charset()
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnsafeURLError(f"response exceeded {max_bytes} bytes")
    return data, http_charset
