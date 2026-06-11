#!/usr/bin/env python3
"""Bestands-Reparatur: falsch zugeordnete Posts per inputUrl umhängen.

Sprint channel-attribution-inputurl (2026-06-11), Phase 2. Der alte
ownerUsername-Index-Fallback in ``_match_channel`` hat ~551 von 2.414
Instagram-Posts dem falschen Channel zugeordnet (Müll-Sink-Bug). Das
Payload-Feld ``inputUrl`` (Echo der eigenen ``directUrls`` =
``channel.url``) trägt das tatsächlich gescrapte Profil — dieses Skript
hängt jeden Post, dessen normalisierte ``inputUrl`` NICHT zur
``channel.url`` seines aktuellen Channels passt, auf den Channel mit der
passenden URL um (``UPDATE post SET channel_id = <ziel>``).

Sicherheits-Eigenschaften:
- DRY-RUN IST DEFAULT: ohne ``--apply`` wird nichts geschrieben — das
  Skript listet die geplanten Änderungen (post_id, @alt → @neu, post_url)
  plus Summary und erzwingt auf Postgres eine Read-only-Transaktion.
- Defensiver Abbruch: findet sich für einen falsch zugeordneten Post KEIN
  Ziel-Channel (Fall (b), laut Phase-0-Messung 0 Fälle), bricht das
  Skript VOR jeder Änderung ab und listet die unauflösbaren Posts.
  Ebenso bei mehrdeutigen Channel-URLs (zwei Channels, gleiche
  normalisierte URL).
- Idempotent: nach ``--apply`` findet ein zweiter Lauf 0 Fehlzuordnungen.
- ``raw_payload`` bleibt unangetastet (Audit-Trail: ownerUsername +
  inputUrl bleiben nachvollziehbar). ``asset`` folgt automatisch über
  den FK ``asset.post_id`` — Assets haben keine eigene channel_id,
  Title-Matches sind content-basiert (Phase-0-P0-3 bestätigt).

Normalisierung: exakt ``_normalize_profile_url`` aus ``app.api.monitor``
— dieselbe Funktion, die der Ingest-Fix (Commit A) benutzt. Eine
Quelle, kein Drift zwischen Ingest und Reparatur.

Aufruf:
    source ~/.creative-radar/db.env     # liefert CR_DB_URL
    # 1. Dry-Run (Default, schreibt nichts):
    python scripts/repair_channel_attribution.py
    # 2. Nach Wolf-OK scharf:
    python scripts/repair_channel_attribution.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"repair_channel_attribution: {msg}", file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="repair_channel_attribution.py",
        description="Falsch zugeordnete Posts per inputUrl-Identität umhängen (Dry-Run ist Default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Änderungen wirklich schreiben. Ohne dieses Flag: Dry-Run, nichts wird geschrieben.",
    )
    args = parser.parse_args()

    db_url = (os.environ.get("CR_DB_URL") or "").strip()
    if not db_url:
        _die("CR_DB_URL ist nicht gesetzt — bitte die DB-Verbindung in CR_DB_URL exportieren.")
    os.environ["DATABASE_URL"] = db_url

    from sqlmodel import Session, select  # noqa: E402 — nach sys.path / DATABASE_URL

    from app.api.monitor import _normalize_profile_url  # noqa: E402 — geteilte Quelle mit Ingest
    from app.database import engine  # noqa: E402
    from app.models.entities import Channel, Post  # noqa: E402

    session = Session(engine)
    if not args.apply:
        # Dry-Run: Read-only-Transaktion erzwingen (Postgres), damit ein
        # versehentlicher Write hart fehlschlägt statt still durchzugehen.
        try:
            if engine.url.get_backend_name().startswith("postgres"):
                session.connection(
                    execution_options={"postgresql_readonly": True, "postgresql_deferrable": True}
                )
        except Exception:  # noqa: BLE001 — best effort, SQLite/andere Treiber
            pass

    try:
        channels = list(session.exec(select(Channel)).all())
        by_norm_url: dict[str, Channel] = {}
        for channel in channels:
            if not channel.active:
                # Deaktivierte Channels sind keine Umhäng-Ziele und lösen
                # keine Mehrdeutigkeit aus — "Doppeleintrag deaktivieren"
                # ist damit ein gültiger Auflösungsweg (Wolf-Freigabe
                # 11.06.). channel_by_id unten kennt sie weiterhin, damit
                # Posts AUF deaktivierten Channels ihren current-Vergleich
                # behalten (input_norm == current_norm → bleibt liegen).
                continue
            norm = _normalize_profile_url(channel.url)
            if not norm:
                continue
            if norm in by_norm_url:
                _die(
                    "Mehrdeutige Channel-URL nach Normalisierung: "
                    f"{norm!r} gehört zu @{by_norm_url[norm].handle} UND "
                    f"@{channel.handle} — Ziel nicht eindeutig, Abbruch ohne Änderung.",
                    code=3,
                )
            by_norm_url[norm] = channel
        channel_by_id = {c.id: c for c in channels}

        posts = list(session.exec(select(Post)).all())
        planned: list[tuple[Post, Channel, Channel]] = []  # (post, alt, neu)
        unresolved: list[tuple[Post, str]] = []
        with_input_url = 0
        for post in posts:
            raw = post.raw_payload or {}
            input_norm = _normalize_profile_url(raw.get("inputUrl"))
            if not input_norm:
                continue  # kein inputUrl (z.B. TikTok/YouTube) — nicht Gegenstand
            with_input_url += 1
            current = channel_by_id.get(post.channel_id)
            current_norm = _normalize_profile_url(current.url) if current else ""
            if input_norm == current_norm:
                continue  # korrekt zugeordnet
            target = by_norm_url.get(input_norm)
            if target is None:
                unresolved.append((post, input_norm))
            else:
                planned.append((post, current, target))

        print(
            f"Bestand: {len(posts)} Posts gesamt, {with_input_url} mit inputUrl, "
            f"{len(planned)} falsch zugeordnet (auflösbar), {len(unresolved)} unauflösbar."
        )

        if unresolved:
            for post, input_norm in unresolved:
                print(
                    f"  UNAUFLÖSBAR: post={post.id} inputUrl={input_norm} "
                    f"post_url={post.post_url}",
                    file=sys.stderr,
                )
            _die(
                f"{len(unresolved)} Post(s) ohne auflösbares Ziel (Fall (b), erwartet 0) — "
                "Abbruch OHNE Änderung. Bitte an Wolf melden.",
                code=4,
            )

        if not planned:
            print("Nichts zu tun — 0 auflösbare Fehlzuordnungen (idempotenter Zustand).")
            return

        pair_counter: Counter = Counter()
        for post, old, new in planned:
            old_h = old.handle if old else "?"
            print(f"  {post.id}  @{old_h} -> @{new.handle}  {post.post_url}")
            pair_counter[(old_h, new.handle)] += 1

        print("\nSummary (alt -> neu: Anzahl):")
        for (old_h, new_h), n in pair_counter.most_common():
            print(f"  @{old_h} -> @{new_h}: {n}")
        print(f"Gesamt umzuhängen: {len(planned)}")

        if not args.apply:
            print("\nDRY-RUN — nichts geschrieben. Scharf: --apply (erst nach Wolf-OK).")
            return

        for post, _old, new in planned:
            post.channel_id = new.id
            session.add(post)
        session.commit()

        # Verifikation in derselben Sitzung: erneuter Scan muss 0 ergeben.
        remaining = 0
        for post in session.exec(select(Post)).all():
            raw = post.raw_payload or {}
            input_norm = _normalize_profile_url(raw.get("inputUrl"))
            if not input_norm:
                continue
            current = channel_by_id.get(post.channel_id)
            current_norm = _normalize_profile_url(current.url) if current else ""
            if input_norm != current_norm and input_norm in by_norm_url:
                remaining += 1
        print(
            f"\nAPPLY abgeschlossen: {len(planned)} Posts umgehängt. "
            f"Verifikation: {remaining} verbleibende auflösbare Fehlzuordnungen (erwartet 0)."
        )
        if remaining:
            _die("Verifikation fehlgeschlagen — bitte an Wolf melden.", code=5)
    finally:
        session.close()


if __name__ == "__main__":
    main()
