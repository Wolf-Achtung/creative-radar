"""Sprint 5.3.5 — Channel-Auswahl-Logik für Cron-Auto-Sync.

A-Class: hohe Aktivität (assets_30d >= threshold) — wird jeden Cron-Lauf gesynct,
gedeckelt durch ``a_class_max`` (Cost-Cap).
B-Class: alle übrigen mvp+active Channels der Plattform — werden jeden Cron-Lauf
vollständig gesynct.

Historie: Bis 2026-06 wurde die B-Class über drei Cron-Läufe gedrittelt
(``run_index``-Rotation). Das war ein Frühphasen-Konstrukt für eine 3-tägige
Cadence und wurde beim Umstieg auf den wöchentlichen Schedule
(``cron-sync.yml`` ``'0 6 * * 1'``) nie nachkalibriert: ``compute_run_index``
rechnete weiter 3-tägig, sodass ``run_index`` faktisch nicht sauber rotierte
und ~2/3 der B-Class über Wochen nie selektiert wurden. Die Rotation ist
deshalb entfernt — die volle B-Class läuft jetzt jeden Lauf.

Threshold-Default ist 1, weil die Datenbasis 2026-05-04 nur ~31 Posts gesamt
enthält; ENV ``CRON_A_CLASS_THRESHOLD`` macht das konfigurierbar, sobald das
Volumen wächst.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Post


def _a_class_threshold_default() -> int:
    raw = os.environ.get("CRON_A_CLASS_THRESHOLD", "1")
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def _a_class_max_default() -> int:
    raw = os.environ.get("CRON_A_CLASS_MAX", "30")
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


def select_channels_for_cron(
    session: Session,
    platform: str,
    run_index: int,
    *,
    a_class_threshold: int | None = None,
    a_class_max: int | None = None,
    reference_date: datetime | None = None,
) -> list[Channel]:
    """Pick the channels to sync for one cron run on the given platform.

    Returns the union of A-class (always, capped by ``a_class_max``) and the
    full B-class, deterministically ordered by handle.

    ``run_index`` is vestigial: it no longer affects selection since the
    B-class third-rotation was removed (see module docstring). It is retained
    in the signature so the cron call sites (``api/cron.py``) and the
    ``CronRun.run_index`` audit column keep working unchanged.
    """
    threshold = a_class_threshold if a_class_threshold is not None else _a_class_threshold_default()
    max_a = a_class_max if a_class_max is not None else _a_class_max_default()
    cutoff = (reference_date or datetime.now(timezone.utc)) - timedelta(days=30)

    candidates = list(
        session.exec(
            select(Channel)
            .where(
                Channel.mvp == True,  # noqa: E712
                Channel.active == True,  # noqa: E712
                Channel.platform == platform,
            )
            .order_by(Channel.handle)
        ).all()
    )

    counts: dict[UUID, int] = {}
    if candidates:
        ids = [c.id for c in candidates]
        rows = session.exec(
            select(Post.channel_id, func.count(Asset.id))
            .join(Asset, Asset.post_id == Post.id)
            .where(Post.channel_id.in_(ids), Asset.created_at > cutoff)
            .group_by(Post.channel_id)
        ).all()
        for channel_id, count in rows:
            counts[channel_id] = count

    a_class = sorted(
        (c for c in candidates if counts.get(c.id, 0) >= threshold),
        key=lambda c: (-counts.get(c.id, 0), c.handle or ""),
    )[:max_a]
    a_ids = {c.id for c in a_class}

    b_class = [c for c in candidates if c.id not in a_ids]

    # No rotation: the full B-class is synced every run (the third-rotation was
    # removed because run_index never cycled cleanly under the weekly schedule,
    # see module docstring). The F0.6 Apify monthly hard-cap pre-flight in
    # ``api/cron.py`` is the structural guard against the higher per-run volume.
    return a_class + b_class


def compute_run_index(reference_date: datetime | None = None) -> int:
    """Deterministic run_index ∈ {0, 1, 2} from the calendar day.

    Cron is configured by Wolf to fire every ``CRON_SYNC_INTERVAL_DAYS`` days
    (default 3). Each fire bumps run_index by 1 modulo 3, so each B-class
    third is touched once per ``3 * interval`` days.
    """
    interval = max(1, int(os.environ.get("CRON_SYNC_INTERVAL_DAYS", "3")))
    ref = reference_date or datetime.now(timezone.utc)
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    days_since_epoch = (ref - epoch).days
    return (days_since_epoch // interval) % 3
