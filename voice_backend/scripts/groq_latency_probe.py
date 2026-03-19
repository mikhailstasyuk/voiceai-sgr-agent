#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import statistics
import time

from openai import OpenAI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Groq chat completion latency with a fixed prompt.")
    parser.add_argument("--text", default="hello", help="User text to send (default: hello)")
    parser.add_argument("--runs", type=int, default=1, help="Number of requests to run (default: 1)")
    parser.add_argument("--model", default=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), help="Model name")
    parser.add_argument(
        "--base-url",
        default=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        help="Groq OpenAI-compatible base URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY is not set.")
        return 1
    if args.runs < 1:
        print("ERROR: --runs must be >= 1")
        return 1

    client = OpenAI(api_key=api_key, base_url=args.base_url)
    samples_ms: list[float] = []

    for i in range(1, args.runs + 1):
        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model=args.model,
            messages=[{"role": "user", "content": args.text}],
            stream=False,
            temperature=0,
            max_tokens=64,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        samples_ms.append(elapsed_ms)

        content = ""
        if response.choices and response.choices[0].message and response.choices[0].message.content:
            content = response.choices[0].message.content.strip().replace("\n", " ")
        print(f"run={i} latency_ms={elapsed_ms:.2f} response={content!r}")

    if len(samples_ms) == 1:
        print(f"summary runs=1 avg_ms={samples_ms[0]:.2f}")
    else:
        print(
            "summary "
            f"runs={len(samples_ms)} "
            f"avg_ms={statistics.mean(samples_ms):.2f} "
            f"min_ms={min(samples_ms):.2f} "
            f"max_ms={max(samples_ms):.2f} "
            f"p50_ms={statistics.median(samples_ms):.2f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
