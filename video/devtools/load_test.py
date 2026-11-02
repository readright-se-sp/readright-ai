"""Send concurrent jobs to a running video lane and report latency percentiles.

Usage: uv run python -m devtools.load_test <url> <webm_path_on_lane>... [-n 10] [--deadline 115]

Exit code 1 if any job failed (status 0 means a transport error) or none succeeded.
"""

import argparse
import asyncio
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np


@dataclass
class Report:
    ok_times: list[float] = field(default_factory=list)
    errors: dict[int, int] = field(default_factory=dict)
    stats: Any = None


def percentiles(times: list[float]) -> tuple[float, float, float]:
    """p50, p95 (linear interpolation) and max."""
    if not times:
        raise ValueError("no samples")
    return (
        float(np.percentile(times, 50)),
        float(np.percentile(times, 95)),
        float(max(times)),
    )


async def _one(
    client: httpx.AsyncClient, url: str, path: str, deadline_s: float
) -> tuple[int, float]:
    body = {
        "job_id": str(uuid.uuid4()),
        "deadline": int(time.time() * 1000 + deadline_s * 1000),
        "webm_path": path,
    }
    start = time.perf_counter()
    try:
        response = await client.post(f"{url}/v1/jobs", json=body, timeout=deadline_s + 10)
    except (httpx.HTTPError, httpx.InvalidURL):
        return 0, time.perf_counter() - start
    return response.status_code, time.perf_counter() - start


async def run_load(
    url: str,
    paths: list[str],
    n: int,
    deadline_s: float,
    client: httpx.AsyncClient | None = None,
) -> Report:
    owned = client is None
    if client is None:
        client = httpx.AsyncClient()
    try:
        results = await asyncio.gather(
            *(_one(client, url, paths[i % len(paths)], deadline_s) for i in range(n))
        )
        try:
            stats = (await client.get(f"{url}/stats")).json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            stats = None
    finally:
        if owned:
            await client.aclose()
    report = Report(stats=stats)
    errors: Counter[int] = Counter()
    for status, elapsed in results:
        if status == 200:
            report.ok_times.append(elapsed)
        else:
            errors[status] += 1
    report.errors = dict(errors)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("-n", type=int, default=10)
    parser.add_argument("--deadline", type=float, default=115)
    args = parser.parse_args(argv)

    report = asyncio.run(run_load(args.url.rstrip("/"), args.paths, args.n, args.deadline))
    if report.ok_times:
        p50, p95, longest = percentiles(report.ok_times)
        print(f"ok: {len(report.ok_times)}  p50 {p50:.2f} s  p95 {p95:.2f} s  max {longest:.2f} s")
    else:
        print("ok: 0")
    print(f"errors: {report.errors or 'none'}")
    print(f"stats: {report.stats}")
    return 1 if report.errors or not report.ok_times else 0


if __name__ == "__main__":
    raise SystemExit(main())
