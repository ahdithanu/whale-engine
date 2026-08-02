"""Idempotent, cached download of raw source files into /data/raw.

Shared by every ingest source (EPA, OSHA, DoD) so re-runs never re-pull a
file that's already on disk and unchanged upstream.
"""

from pathlib import Path

import httpx


def download_cached(url: str, dest: Path, *, force: bool = False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not force and dest.exists():
        try:
            head = httpx.head(url, follow_redirects=True, timeout=30.0)
            head.raise_for_status()
            remote_size = int(head.headers.get("content-length", -1))
        except httpx.HTTPError as exc:
            print(f"[download] HEAD check failed for {url} ({exc}); re-downloading to be safe")
            remote_size = -1

        local_size = dest.stat().st_size
        if remote_size == local_size:
            print(f"[download] cached, skipping: {dest.name} ({local_size:,} bytes)")
            return dest
        print(
            f"[download] cache stale for {dest.name} "
            f"(local {local_size:,} bytes vs remote {remote_size:,} bytes); re-downloading"
        )

    print(f"[download] fetching {url} -> {dest}")
    with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)

    print(f"[download] done: {dest.name} ({dest.stat().st_size:,} bytes)")
    return dest
