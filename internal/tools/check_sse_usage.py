#!/usr/bin/env python3
"""Inspect SSE chunk ordering and usage fields from an OpenAI-compatible API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="https://ee.niceaigc.cn/v1",
        help="API base URL (default: https://ee.niceaigc.cn/v1)",
    )
    parser.add_argument("--model", default="kimi-k3", help="Model name")
    parser.add_argument("--prompt", default="你好。", help="Short test prompt")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Send the same prompt this many times to test cache behavior (default: 1)",
    )
    parser.add_argument(
        "--api-key-env",
        default="NICEAIGC_API_KEY",
        help="Environment variable holding the API key",
    )
    args = parser.parse_args()

    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        print(f"Missing API key in environment variable {args.api_key_env}.", file=sys.stderr)
        return 2

    url = args.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 64,
    }
    request_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    totals = {"requests": 0, "usage_events": 0, "finish_reason_events": 0}
    try:
        for request_number in range(1, args.repeat + 1):
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=request_headers,
                method="POST",
            )
            started = time.monotonic()
            data_lines = 0
            events = 0
            usage_events = 0
            finish_events = 0
            print(f"request={request_number}/{args.repeat}")
            with urllib.request.urlopen(request, timeout=90) as response:
                new_api_request_id = response.headers.get("X-Oneapi-Request-Id")
                print(
                    f"HTTP {response.status}; content-type={response.headers.get('Content-Type', '')}; "
                    f"new_api_request_id={new_api_request_id or 'n/a'}"
                )
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data_lines += 1
                    data = line[5:].strip()
                    if data == "[DONE]":
                        print(f"frame={data_lines} event=DONE elapsed={time.monotonic() - started:.3f}s")
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        print(f"frame={data_lines} event=INVALID_JSON")
                        continue

                    events += 1
                    choices = chunk.get("choices") or []
                    usage = chunk.get("usage")
                    reasons = [choice.get("finish_reason") for choice in choices if choice.get("finish_reason")]
                    if usage is not None:
                        usage_events += 1
                    if reasons:
                        finish_events += 1
                    detail = {
                        "request": request_number,
                        "frame": data_lines,
                        "upstream_event_id": chunk.get("id"),
                        "model": chunk.get("model"),
                        "choices_count": len(choices),
                        "finish_reason": reasons,
                        "usage": usage,
                        "elapsed_s": round(time.monotonic() - started, 3),
                    }
                    print(json.dumps(detail, ensure_ascii=False))
            totals["requests"] += 1
            totals["usage_events"] += usage_events
            totals["finish_reason_events"] += finish_events
            print(json.dumps({"request_summary": {
                "request": request_number,
                "data_frames": data_lines,
                "json_events": events,
                "finish_reason_events": finish_events,
                "usage_events": usage_events,
            }}))
    except urllib.error.HTTPError as error:
        # Do not print headers or request data. API error bodies can contain sensitive details.
        print(f"HTTP error {error.code}; response body omitted.", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"Request failed: {error.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        print("Request timed out.", file=sys.stderr)
        return 1

    print(json.dumps({"summary": totals}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
