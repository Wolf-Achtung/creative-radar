#!/usr/bin/env python3
"""Diagnose — Generic-Title-False-Positive-Audit (Präzisionsproblem nach #277).

NUR DIAGNOSE. Read-only. Schreibt nichts, fasst keine Engine an.

Kontext: Nach dem Company-Axis-Sync (313 → ~29k Titel) feuert der Matcher
``exact_local``/``exact_alias`` conf=1.00 safe=True auf generische Ein-Wort-Titel
(Driven, Beloved, Experience, London …), weil der Substring-Pfad
(``_contains_phrase``) einen Wort-im-Caption-Treffer eines ``local``/``alias``-
Kandidaten als ``exact_local``/``exact_alias`` labelt → Score 1.0 → safe
(whitelist_matcher.py:336-339 + :358). Der Substring-Magnet-Schutz greift nur bei
Compact-Länge ≤ 4 — gängige 5–10-Zeichen-Wörter passieren.

Dieses Script quantifiziert die Risiko-Menge am ECHTEN Katalog:
  - Index-Keys, die EIN Token sind und Compact-Länge > _MIN_SUBSTRING_CANDIDATE_LEN
    (=4) haben → matchen via _contains_phrase als safe, sobald das Wort in einer
    Caption vorkommt.
  - Aufschlüsselung nach Quelle (exact = title_original, local = title_local,
    alias) und der daraus folgenden Safe-Score-Abbildung.
  - Heuristik-Flag „likely_generic" gegen eine eingebaute Common-Word-Liste
    (EN+DE) — nur Triage-Hilfe, das Wolf-Auge entscheidet endgültig.

Beantwortet §(a): Wie viele 29k-Titel sind generische Ein-Wort-Titel, die als
safe matchen können, und kommen sie aus title_original vs. local/alias?

Aufruf:
    source ~/.creative-radar/db.env && \\
        DATABASE_URL="$CR_DB_URL" python -m scripts.diag_generic_title_audit
    DATABASE_URL="$CR_DB_URL" python -m scripts.diag_generic_title_audit --sample 80
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"diag_generic_title_audit: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _has_db_config() -> bool:
    if any(os.environ.get(v) for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")):
        return True
    return all(os.environ.get(v) for v in ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE"))


# Heuristik-Common-Words (EN+DE) — NUR Triage. Keine Match-Logik haengt daran.
# Wenn ein Ein-Token-Key hier drin steht, ist er sehr wahrscheinlich ein
# Alltagswort-Titel (False-Positive-Magnet). Die Liste deckt die von Wolf
# genannten Beispiele + haeufige Caption-Woerter ab; nicht erschoepfend.
_COMMON_WORDS = {
    # Wolf-Beispiele
    "driven", "beloved", "experience", "countdown", "london", "underworld",
    "home", "friends", "ocean", "opinions", "reaction",
    # haeufige EN-Caption-Woerter
    "love", "happy", "today", "moment", "moments", "story", "stories", "dream",
    "dreams", "magic", "family", "summer", "winter", "holiday", "holidays",
    "weekend", "tonight", "forever", "always", "together", "everything",
    "beautiful", "amazing", "perfect", "secret", "secrets", "legends", "legend",
    "heroes", "hero", "wonder", "wonders", "journey", "adventure", "adventures",
    "world", "people", "future", "history", "memories", "vibes", "energy",
    "release", "trailer", "premiere", "tickets", "cinema", "movie", "movies",
    "watch", "coming", "soon", "available", "streaming", "exclusive",
    # haeufige DE-Caption-Woerter
    "liebe", "leben", "zukunft", "freunde", "familie", "abenteuer", "geschichte",
    "geschichten", "kino", "jetzt", "bald", "heute", "morgen", "zusammen",
    "wunder", "traum", "traeume", "sommer", "winter", "welt", "zuhause",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="diag_generic_title_audit.py",
        description="Audit generischer Ein-Wort-Titel, die als safe matchen. Read-only.",
    )
    parser.add_argument("--sample", type=int, default=60,
                        help="Wie viele Risiko-Keys auflisten (Default: 60).")
    args = parser.parse_args()

    if not _has_db_config():
        _die("Keine DB-Config in der Umgebung. Setze DATABASE_URL.")

    from sqlmodel import Session

    from app.database import engine
    from app.services.whitelist_matcher import (
        _GENERIC_WORDS,
        _MIN_SUBSTRING_CANDIDATE_LEN,
        _normalize_text,
        load_title_bundle,
    )

    # Substring-Pfad-Quelle -> Safe-Score (whitelist_matcher.py:336-339 + :358).
    # exact  -> unique_text (0.97, safe)
    # local  -> exact_local (1.00, safe)
    # alias  -> exact_alias (1.00, safe)
    source_to_safe = {
        "exact": ("unique_text", 0.97),
        "local": ("exact_local", 1.00),
        "alias": ("exact_alias", 1.00),
    }

    with Session(engine) as session:
        bundle = load_title_bundle(session)
        if not bundle:
            print("# Keine aktiven Titel im Katalog.")
            return

        # Replikat von build_normalized_index, aber mit Herkunft + Title je Key.
        key_sources: dict[str, set[str]] = defaultdict(set)
        key_titles: dict[str, list[str]] = defaultdict(list)
        total_keys = 0
        for title, candidate_map in bundle:
            for source_key, values in candidate_map.items():
                if source_key == "weak":
                    continue
                for candidate in values:
                    norm = _normalize_text(candidate)
                    if not norm or len(norm) <= 2 or norm in _GENERIC_WORDS:
                        continue
                    if norm not in key_sources:
                        total_keys += 1
                    key_sources[norm].add(source_key)
                    if len(key_titles[norm]) < 3:
                        key_titles[norm].append(title.title_original)

        # Risiko-Keys: EIN Token, Compact-Laenge > Guard → _contains_phrase matcht
        # sie als Wort-im-Caption-Treffer (safe).
        single_token_risk: list[str] = [
            k for k in key_sources
            if " " not in k and len(k.replace(" ", "")) > _MIN_SUBSTRING_CANDIDATE_LEN
        ]
        # 2-Token-Phrasen als Sekundaer-Risiko (brauchen beide Woerter zusammen).
        two_token_risk = [k for k in key_sources if k.count(" ") == 1]

        # Quelle-Aufschluesselung der Single-Token-Risiken: welcher Safe-Score?
        by_source: Counter = Counter()
        local_or_alias_only = 0  # die mit Score 1.00 (schlimmer)
        for k in single_token_risk:
            srcs = key_sources[k]
            # "schlimmster" (hoechster Safe-Score) Pfad zaehlt
            if srcs & {"local", "alias"}:
                local_or_alias_only += 1
            for s in srcs:
                if s in source_to_safe:
                    by_source[s] += 1

        likely_generic = sorted(k for k in single_token_risk if k in _COMMON_WORDS)

        print(f"# Generic-Title-False-Positive-Audit")
        print(f"# active_titles={len(bundle)}  distinct_index_keys={total_keys}")
        print()
        print(f"## Risiko-Menge (matchen via _contains_phrase als SAFE)")
        print(f"  single_token_keys (>{_MIN_SUBSTRING_CANDIDATE_LEN} compact) : {len(single_token_risk)}")
        print(f"    davon mit local/alias-Quelle (Score 1.00, schlimmer)    : {local_or_alias_only}")
        print(f"    Quelle-Aufschluesselung (Token kann aus mehreren kommen):")
        for s in ("exact", "local", "alias"):
            mapped, score = source_to_safe[s]
            print(f"      {s:<6} → {mapped:<12} (score {score:.2f}, safe): {by_source.get(s, 0)}")
        print(f"  two_token_phrase_keys (Sekundaer-Risiko)                  : {len(two_token_risk)}")
        print()
        print(f"## Heuristik-Flag 'likely_generic' (Common-Word-Liste, Triage)")
        print(f"  count: {len(likely_generic)}")
        print(f"  {likely_generic}")
        print()
        print(f"## Stichprobe Single-Token-Risiko-Keys (max {args.sample}) — Key | Quellen | Beispiel-Titel")
        for k in sorted(single_token_risk)[: args.sample]:
            srcs = ",".join(sorted(key_sources[k]))
            titles = "; ".join(dict.fromkeys(key_titles[k]))
            flag = "  ⚠likely_generic" if k in _COMMON_WORDS else ""
            print(f"  {k!r:<24} [{srcs}] {titles}{flag}")
        print()
        print("# Lesehilfe:")
        print("#  - Jeder Single-Token-Key >4 matcht als SAFE, sobald das Wort in einer Caption steht")
        print("#    (whitelist_matcher.py:336-339 mappt local/alias-Substring auf exact_* → Score 1.0).")
        print("#  - local/alias-Quelle ist der gefaehrlichere Pfad (Score 1.00 vs 0.97 bei exact).")
        print("#  - 'likely_generic' ist Heuristik — die echte generisch/legitim-Trennung macht das Auge")
        print("#    (Moana/Up/Cars sind auch Single-Token, aber legitim).")


if __name__ == "__main__":
    main()
