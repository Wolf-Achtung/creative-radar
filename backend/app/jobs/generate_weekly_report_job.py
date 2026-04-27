"""v1 placeholder for Railway Cron.

Call POST /api/reports/generate-weekly from a scheduled job once review workflow is in use.
"""


def run() -> dict:
    return {"status": "manual", "endpoint": "/api/reports/generate-weekly"}
