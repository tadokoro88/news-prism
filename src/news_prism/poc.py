"""News Prism PoC CLI entry point.

usage:
    python -m news_prism.poc <article_url>
    pbpaste | python -m news_prism.poc --paste <article_url>   # 本文を stdin から
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from dotenv import load_dotenv

from news_prism.article_fetch import ArticleFetchError, fetch_article
from news_prism.bedrock_client import analyze
from news_prism.context_fetch import ContextFetchError, fetch_context


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="news-prism",
        description="multi-perspective news analyzer (Phase 1 PoC, 4 parallel personas)",
    )
    parser.add_argument("url", help="記事の URL")
    parser.add_argument(
        "--paste",
        action="store_true",
        help="本文を stdin から読み込み (記事 fetch をスキップ)",
    )
    args = parser.parse_args()

    # 1. 記事本文を取得
    title: str | None = None
    if args.paste:
        if sys.stdin.isatty():
            print(
                "[paste] stdin が端末です。本文を貼り付けて Ctrl-D で終端、"
                "もしくは `pbpaste | python -m news_prism.poc --paste <url>` などで pipe してください。",
                file=sys.stderr,
            )
        article_body = sys.stdin.read()
        print(f"[paste] {len(article_body)} chars read from stdin", file=sys.stderr)
    else:
        try:
            article_body, title = fetch_article(args.url)
        except ArticleFetchError as e:
            print(f"[error] article fetch failed: {e}", file=sys.stderr)
            print(
                f"[hint] re-run with: pbpaste | python -m news_prism.poc --paste {args.url}",
                file=sys.stderr,
            )
            return 1

    # 2. my-goals context.md を取得
    print("[info] fetching my-goals context.md...", file=sys.stderr)
    try:
        context_md = fetch_context()
        print(f"[info] context fetched: {len(context_md)} chars", file=sys.stderr)
    except ContextFetchError as e:
        print(f"[warn] context fetch failed: {e}, using empty context", file=sys.stderr)
        context_md = ""

    # 3. Bedrock 4 並列呼び出し
    print(
        f"[info] calling Bedrock (article={len(article_body)} chars, context={len(context_md)} chars)",
        file=sys.stderr,
    )
    try:
        result = analyze(context_md, article_body)
    except RuntimeError as e:
        print(f"[error] bedrock orchestration failed: {e}", file=sys.stderr)
        return 2

    wall_time_ms = result["wall_time_ms"]
    total_usage = result["total_usage"]
    per_call = result["per_call"]
    print(
        f"[info] all 4 personas completed in {wall_time_ms}ms "
        f"(total_in={total_usage['input_tokens']}, "
        f"cache_r={total_usage['cache_read_input_tokens']}, "
        f"cache_w={total_usage['cache_write_input_tokens']}, "
        f"out={total_usage['output_tokens']})",
        file=sys.stderr,
    )

    # 4. usage ログ (stderr に 1 行 JSON) — SPEC §2.3 形式
    log = {
        "ts": dt.datetime.now(dt.UTC).isoformat(),
        "url": args.url,
        "personas_completed": sum(1 for v in per_call.values() if "error" not in v),
        "personas_failed": sum(1 for v in per_call.values() if "error" in v),
        "total_input_tokens": total_usage["input_tokens"],
        "total_cache_read_input_tokens": total_usage["cache_read_input_tokens"],
        "total_cache_write_input_tokens": total_usage["cache_write_input_tokens"],
        "total_output_tokens": total_usage["output_tokens"],
        "wall_time_ms": wall_time_ms,
        "per_call": per_call,
    }
    print(json.dumps(log, ensure_ascii=False), file=sys.stderr)

    # 5. 最終 JSON (stdout) — PLAN §5 schema
    final = {
        "url": args.url,
        "title": title,
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
        **result["output"],
    }
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
