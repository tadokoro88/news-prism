"""News Prism Lambda 本番のバースト計測。

5-10 記事を連投し、cache hit / wall_time / token / 相対コスト削減率を集計する。

Usage:
    export INVOKE_URL=$(terraform -chdir=infra output -raw invoke_url)
    export API_KEY=$(terraform -chdir=infra output -raw api_key_value)
    python3 scripts/measure_burst.py > out.json

    # 任意の URL list を渡したい場合:
    python3 scripts/measure_burst.py --urls scripts/urls.txt > out.json

stdout: 結果の JSON (per-call + summary)
stderr: 進行ログ + 集計サマリ
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URLS = [
    "https://aws.amazon.com/jp/blogs/news/",
    "https://aws.amazon.com/blogs/security/",
    "https://aws.amazon.com/blogs/machine-learning/",
    "https://aws.amazon.com/blogs/devops/",
    "https://aws.amazon.com/blogs/aws/",
    "https://aws.amazon.com/blogs/compute/",
    "https://aws.amazon.com/blogs/database/",
    "https://aws.amazon.com/blogs/architecture/",
]

# Bedrock 料金単価 (Anthropic 公開値ベース、相対比)
COST_INPUT = 1.0
COST_CACHE_WRITE = 1.25
COST_CACHE_READ = 0.1
COST_OUTPUT = 5.0


def call_api(url: str, invoke_url: str, api_key: str) -> dict[str, Any]:
    req = urllib.request.Request(
        invoke_url,
        method="POST",
        data=json.dumps({"url": url}).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
        },
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return {"status": resp.status, "body": body, "client_ms": elapsed_ms}
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        return {"status": e.code, "error": err_body}
    except Exception as e:
        return {"status": -1, "error": str(e)}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in results if "wall_ms" in r]
    if not ok:
        return {"ok_count": 0, "fail_count": len(results)}

    n = len(ok)
    wall_times = [r["wall_ms"] for r in ok]
    client_times = [r["client_ms"] for r in ok]
    total_in = sum(r["input_tokens"] for r in ok)
    total_cr = sum(r["cache_read"] for r in ok)
    total_cw = sum(r["cache_write"] for r in ok)
    total_out = sum(r["output_tokens"] for r in ok)

    # 実コスト (cache 効果あり)
    cost_actual = (
        total_in * COST_INPUT
        + total_cr * COST_CACHE_READ
        + total_cw * COST_CACHE_WRITE
        + total_out * COST_OUTPUT
    )
    # 仮想: cache が全くない場合、cache 対象 token も全部 fresh input になる
    hypo_input = total_in + total_cr + total_cw
    cost_nocache = hypo_input * COST_INPUT + total_out * COST_OUTPUT

    input_actual = (
        total_in * COST_INPUT + total_cr * COST_CACHE_READ + total_cw * COST_CACHE_WRITE
    )
    input_nocache = hypo_input * COST_INPUT

    return {
        "ok_count": n,
        "fail_count": len(results) - n,
        "wall_ms_min": min(wall_times),
        "wall_ms_max": max(wall_times),
        "wall_ms_avg": round(sum(wall_times) / n, 1),
        "client_ms_min": min(client_times),
        "client_ms_max": max(client_times),
        "client_ms_avg": round(sum(client_times) / n, 1),
        "total_input_tokens": total_in,
        "total_cache_read": total_cr,
        "total_cache_write": total_cw,
        "total_output_tokens": total_out,
        "cost_relative_actual": round(cost_actual, 1),
        "cost_relative_no_cache": round(cost_nocache, 1),
        "total_savings_pct": round((1 - cost_actual / cost_nocache) * 100, 1),
        "input_only_actual": round(input_actual, 1),
        "input_only_no_cache": round(input_nocache, 1),
        "input_only_savings_pct": round((1 - input_actual / input_nocache) * 100, 1),
    }


def main() -> int:
    invoke_url = os.environ.get("INVOKE_URL")
    api_key = os.environ.get("API_KEY")
    if not invoke_url or not api_key:
        print(
            "[error] set INVOKE_URL and API_KEY env vars (terraform output -raw ...)",
            file=sys.stderr,
        )
        return 1

    urls = DEFAULT_URLS
    if "--urls" in sys.argv:
        idx = sys.argv.index("--urls")
        with open(sys.argv[idx + 1]) as f:
            urls = [
                line.strip() for line in f if line.strip() and not line.startswith("#")
            ]

    print(f"[burst] {len(urls)} requests → {invoke_url}", file=sys.stderr)
    print(
        f"{'#':>2} {'wall_ms':>8} {'client_ms':>10} {'cache_r':>8} {'cache_w':>8} "
        f"{'in':>5} {'out':>5} status url",
        file=sys.stderr,
    )

    results: list[dict[str, Any]] = []
    for i, u in enumerate(urls, 1):
        r = call_api(u, invoke_url, api_key)
        if "body" in r and r.get("status") == 200:
            body = r["body"]
            m = body["meta"]
            tu = m["total_usage"]
            row: dict[str, Any] = {
                "i": i,
                "url": u,
                "wall_ms": m["wall_time_ms"],
                "client_ms": r["client_ms"],
                "cache_read": tu["cache_read_input_tokens"],
                "cache_write": tu["cache_write_input_tokens"],
                "input_tokens": tu["input_tokens"],
                "output_tokens": tu["output_tokens"],
                "failed_personas": m.get("failed_personas", []),
                "analysis_id": body["result"]["analysis_id"],
                "status": 200,
            }
            results.append(row)
            print(
                f"{i:>2} {row['wall_ms']:>8} {row['client_ms']:>10} "
                f"{row['cache_read']:>8} {row['cache_write']:>8} "
                f"{row['input_tokens']:>5} {row['output_tokens']:>5} 200 {u}",
                file=sys.stderr,
            )
        else:
            err_row: dict[str, Any] = {
                "i": i,
                "url": u,
                "status": r.get("status"),
                "error": r.get("error", "")[:200],
            }
            results.append(err_row)
            print(f"{i:>2} ERROR status={r.get('status')} url={u}", file=sys.stderr)

    summary = summarize(results)
    print("", file=sys.stderr)
    print(f"[summary] {json.dumps(summary, ensure_ascii=False)}", file=sys.stderr)

    json.dump(
        {"summary": summary, "results": results},
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
