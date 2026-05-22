"""http_safety の SSRF ガードと redirect/size 制御の単体テスト。"""

from __future__ import annotations

import ipaddress

import pytest

from news_prism.http_safety import (
    UnsafeURLError,
    check_host_safe,
    is_ip_safe,
)


class TestIsIpSafe:
    @pytest.mark.parametrize(
        "ip_str",
        [
            "127.0.0.1",  # loopback
            "10.0.0.1",  # private 10/8
            "172.16.0.1",  # private 172.16/12
            "192.168.1.1",  # private 192.168/16
            "169.254.169.254",  # link-local (EC2/Lambda IMDS)
            "0.0.0.0",  # unspecified
            "224.0.0.1",  # multicast
            "::1",  # IPv6 loopback
            "fe80::1",  # IPv6 link-local
            "fc00::1",  # IPv6 ULA (private)
        ],
    )
    def test_unsafe_ips_rejected(self, ip_str: str) -> None:
        assert not is_ip_safe(ipaddress.ip_address(ip_str))

    @pytest.mark.parametrize(
        "ip_str",
        [
            "8.8.8.8",  # Google DNS
            "1.1.1.1",  # Cloudflare DNS
            "140.82.114.4",  # GitHub
            "2606:4700:4700::1111",  # Cloudflare IPv6
        ],
    )
    def test_safe_ips_accepted(self, ip_str: str) -> None:
        assert is_ip_safe(ipaddress.ip_address(ip_str))


class TestCheckHostSafeIpLiterals:
    """IP リテラル直書きケース — DNS 不要なので CI で flaky にならない。"""

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",  # IMDS — 最重要
            "192.168.1.1",
            "::1",
        ],
    )
    def test_unsafe_ip_literal_rejected(self, host: str) -> None:
        with pytest.raises(UnsafeURLError, match="private/reserved"):
            check_host_safe(host)

    def test_safe_ip_literal_accepted(self) -> None:
        # 8.8.8.8 は public、DNS lookup 不要
        check_host_safe("8.8.8.8")  # no exception
