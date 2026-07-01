import pytest

from app.services import rate_limit


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """Rate-limit counters (app/services/rate_limit.py) live in a
    module-level dict, keyed by (bucket, client-ip). TestClient always
    reports the same client-ip ("testclient"), so without this reset,
    unrelated tests hitting the same route in sequence would accumulate
    hits across the whole test session and start tripping 429s that have
    nothing to do with the test actually running."""
    rate_limit.reset()
    yield
    rate_limit.reset()
