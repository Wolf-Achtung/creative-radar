"""Manual smoke test against Cloudflare R2 / any S3-compatible backend.

Not run by pytest — requires real credentials. Execute once after R2 setup
to confirm reachability before W2 wires the adapter into screenshot_capture.

Usage:
    cd backend && STORAGE_BACKEND=s3 python -m scripts.smoke_test_r2

Required env (already set in Railway via the R2-Setup Mini-Run):
    STORAGE_BACKEND=s3
    S3_BUCKET=creative-radar-assets
    S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
    S3_ACCESS_KEY_ID=...
    S3_SECRET_ACCESS_KEY=...
    S3_REGION=auto  (default; safe to leave unset)

Exit code: 0 on full round-trip success, 1 on any failure.
"""

from __future__ import annotations

import sys
import time

from app.services.storage import get_storage


def main() -> int:
    storage = get_storage()
    backend_name = type(storage).__name__
    key = f"smoke-test/hello-{int(time.time())}.txt"
    payload = b"hello r2 from creative-radar smoke test\n"

    print(f"Backend in use: {backend_name}")
    print(f"Test key:       {key}")

    try:
        url = storage.put(key, payload, "text/plain")
        print(f"PUT OK         -> {url}")

        if not storage.exists(key):
            print("EXISTS check failed: object reported missing right after PUT")
            return 1
        print("EXISTS OK")

        signed = storage.get_url(key)
        print(f"SIGNED URL OK  -> {signed[:120]}{'...' if len(signed) > 120 else ''}")

        storage.delete(key)
        print("DELETE OK")

        if storage.exists(key):
            print("EXISTS after DELETE failed: object still present")
            return 1
        print("Post-delete EXISTS=False OK")

        print("\nSmoke test PASSED.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\nSmoke test FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
