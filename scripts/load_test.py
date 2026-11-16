#!/usr/bin/env python3
"""Нагрузочный прогон входа SentinelAI через HAProxy."""

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


def one(url: str) -> tuple[float, int, str, str]:
    started = time.perf_counter()
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=5) as response:
        raw = response.read()
        backend = response.headers.get("X-Sentinel-Backend", "")
        service = ""
        try:
            service = str(json.loads(raw).get("service", ""))
        except json.JSONDecodeError:
            service = ""
        return time.perf_counter() - started, response.status, backend, service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=15)
    args = parser.parse_args()

    started = time.perf_counter()
    latencies: list[float] = []
    statuses: Counter[int] = Counter()
    backends: Counter[str] = Counter()
    services: Counter[str] = Counter()
    errors = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(one, args.url) for _ in range(args.requests)]
        for future in futures:
            try:
                elapsed, status, backend, service = future.result()
            except (urllib.error.URLError, TimeoutError) as exc:
                errors += 1
                statuses[0] += 1
                backends[type(exc).__name__] += 1
                continue
            latencies.append(elapsed)
            statuses[status] += 1
            backends[backend or "—"] += 1
            services[service or "—"] += 1

    elapsed = time.perf_counter() - started
    latencies.sort()
    print(f"url {args.url}")
    print(f"requests {args.requests} concurrency {args.concurrency} errors {errors}")
    print(f"elapsed_s {elapsed:.3f} rps {args.requests / elapsed:.1f}")
    if latencies:
        avg = sum(latencies) / len(latencies)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(
            "latency_ms "
            f"min {latencies[0] * 1000:.1f} "
            f"avg {avg * 1000:.1f} "
            f"p95 {p95 * 1000:.1f} "
            f"max {latencies[-1] * 1000:.1f}"
        )
    print("status " + " ".join(f"{code}={count}" for code, count in sorted(statuses.items())))
    print("backend " + " ".join(f"{name}={count}" for name, count in sorted(backends.items())))
    print("service " + " ".join(f"{name}={count}" for name, count in sorted(services.items())))


if __name__ == "__main__":
    main()
