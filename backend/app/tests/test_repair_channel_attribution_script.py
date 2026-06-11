"""Sprint channel-attribution-inputurl — active-Filter der Ziel-Map.

Mini-Commit nach Wolf-Freigabe (11.06.): Deaktivierte Channels duerfen
in ``scripts/repair_channel_attribution.py`` weder als Umhaeng-Ziel
dienen noch die Eindeutigkeitspruefung ausloesen — "leeren Doppeleintrag
deaktivieren" (ParamountPlus-/starwars-Fall) ist damit ein gueltiger
Aufloesungsweg. Das Skript liegt im Repo-Root ``scripts/`` (nicht im
importierbaren ``backend/scripts``-Package), daher laufen die Tests als
Subprozess gegen eine tmp-SQLite-DB — das testet zugleich die echte CLI
(Dry-Run-Default, --apply).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Channel, Post

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "repair_channel_attribution.py"

STARWARS_URL = "https://www.instagram.com/starwars/"


def _run_script(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CR_DB_URL": f"sqlite:///{db_path}"}
    env.pop("DATABASE_URL", None)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


@pytest.fixture
def seeded_db(tmp_path: Path):
    """Aktiver starwars-Channel + deaktiviertes URL-Duplikat + xyz-Channel.

    Posts:
    - P1 sitzt auf xyz, wurde aber laut inputUrl von starwars gescraped
      → muss auf den AKTIVEN starwars-Zwilling geplant werden.
    - P2 sitzt auf dem DEAKTIVIERTEN Duplikat mit input_norm ==
      current_norm → bleibt liegen (starwars-Fall: 23 Posts auf dem
      deaktivierten INT-Channel bleiben unberuehrt).
    """
    db_path = tmp_path / "repair.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        active_twin = Channel(
            name="starwars", platform="instagram", handle="starwars",
            url=STARWARS_URL, active=True,
        )
        inactive_dup = Channel(
            name="starwars-int (alt)", platform="instagram", handle="starwars",
            url=STARWARS_URL, active=False,
        )
        xyz = Channel(
            name="xyz", platform="instagram", handle="xyzfilms",
            url="https://www.instagram.com/xyzfilms/", active=True,
        )
        s.add(active_twin); s.add(inactive_dup); s.add(xyz)
        s.commit()
        s.refresh(active_twin); s.refresh(inactive_dup); s.refresh(xyz)

        p1 = Post(
            channel_id=xyz.id, platform="instagram",
            post_url="https://x/p/misattributed",
            raw_payload={"inputUrl": STARWARS_URL, "ownerUsername": "hamillhimself"},
        )
        p2 = Post(
            channel_id=inactive_dup.id, platform="instagram",
            post_url="https://x/p/on-inactive",
            raw_payload={"inputUrl": STARWARS_URL, "ownerUsername": "starwars"},
        )
        s.add(p1); s.add(p2); s.commit()
        ids = {
            "active_twin": active_twin.id,
            "inactive_dup": inactive_dup.id,
        }
    return db_path, engine, ids


def test_inactive_duplicate_does_not_trigger_ambiguity_abort(seeded_db):
    """Fall 1 (Freigabe): gleiche normalisierte URL, ein Channel
    active=false → kein exit-3-Abbruch, Dry-Run plant das Ziel auf den
    aktiven Zwilling."""
    db_path, _engine, _ids = seeded_db
    result = _run_script(db_path)
    assert result.returncode == 0, result.stderr
    assert "Mehrdeutige Channel-URL" not in result.stderr
    assert "1 falsch zugeordnet" in result.stdout
    assert "@xyzfilms -> @starwars" in result.stdout
    assert "DRY-RUN — nichts geschrieben" in result.stdout


def test_apply_targets_active_twin_and_leaves_inactive_resident_post(seeded_db):
    """Fall 2 (Freigabe): --apply haengt den fehl-attribuierten Post auf
    den AKTIVEN Zwilling; der Post AUF dem deaktivierten Duplikat
    (input_norm == current_norm) bleibt liegen — kein current=None-Fehler,
    nicht als umhaengbar gewertet."""
    db_path, engine, ids = seeded_db
    result = _run_script(db_path, "--apply")
    assert result.returncode == 0, result.stderr
    assert "1 Posts umgehängt" in result.stdout
    assert "0 verbleibende" in result.stdout

    with Session(engine) as s:
        moved = s.exec(select(Post).where(Post.post_url == "https://x/p/misattributed")).one()
        resident = s.exec(select(Post).where(Post.post_url == "https://x/p/on-inactive")).one()
    assert moved.channel_id == ids["active_twin"]
    assert resident.channel_id == ids["inactive_dup"]
