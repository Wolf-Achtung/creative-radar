#!/usr/bin/env python3
"""Diagnose §3 — TMDb-Company-ID-Resolution & Slate-Count (Studio-Sync-Umbau).

NUR DIAGNOSE / Resolve. Read-only gegen die TMDb-API (kein DB-Zugriff, kein
Write). Beantwortet §3 des Sprint-Briefings, OHNE IDs zu raten:

  1. ``--mode search``  : /search/company je Suchbegriff → Kandidaten-IDs
                          (id, name, origin_country) zum Kuratieren.
  2. ``--mode verify``  : prueft, ob ein bekannter Titel die Company-ID
                          wirklich traegt — sucht den Titel (/search/movie,
                          /search/tv), holt /movie|/tv/{id} und prueft, ob
                          ``production_companies`` (movie) bzw.
                          ``production_companies``/``networks`` (tv) die ID
                          enthaelt. So ist die Achse pro Titel belegt.
  3. ``--mode count``   : /discover/movie + /discover/tv mit ``with_companies``
                          (Pipe-OR-Set) → ``total_results`` je Medientyp =
                          Slate-Count des Sets.
  4. ``--mode plan``    : Batch — fuer alle 6 Produktionsstudios die
                          ZU-RESOLVENDEN Sub-Label-Suchbegriffe (mit Verifik.-
                          Titel) durchsuchen + die BEWIESENEN Sets zaehlen.
                          Ein Lauf liefert alles, was zum Kuratieren noetig ist.

Nur TMDb-Token noetig (KEIN DATABASE_URL):
    export TMDB_READ_ACCESS_TOKEN=...   # oder TMDB_API_KEY=...
    python -m scripts.diag_resolve_company_ids --mode plan
    python -m scripts.diag_resolve_company_ids --mode search "Columbia Pictures" "Illumination"
    python -m scripts.diag_resolve_company_ids --mode verify --company 6125 --title "Encanto"
    python -m scripts.diag_resolve_company_ids --mode count --companies "2|3|420|1|127928|3475"

Die Token-Quelle ist identisch zu app.config.Settings (TMDB_READ_ACCESS_TOKEN /
TMDB_API_KEY), d.h. ``source ~/.creative-radar/db.env`` reicht, sofern die
TMDb-Variablen dort liegen.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# --- §3-Resolutionsplan (kuratierbar; KEINE geratenen Sub-Label-IDs) ---------
# Pro Studio:
#   known  : BEWIESENE Company-IDs (US-kanonisch) -> Slate-Count-Basis.
#   resolve: (Suchbegriff, Verifikations-Titel) -> via /search/company finden,
#            gegen den Titel verifizieren. Erst nach Wolf-Ping in die Config.
RESOLVE_PLAN: dict[str, dict] = {
    "disney": {
        "known": {"2": "Walt Disney Pictures", "3": "Pixar", "420": "Marvel Studios",
                  "1": "Lucasfilm", "127928": "20th Century Studios", "3475": "Disney TV Animation"},
        "resolve": [("Walt Disney Animation Studios", "Encanto"),
                    ("Walt Disney Animation Studios", "Zootopia")],
    },
    "sonypictures": {
        "known": {"34": "Sony Pictures"},
        "resolve": [("Columbia Pictures", "Spider-Man"),
                    ("TriStar Pictures", "Terminator 2"),
                    ("Screen Gems", "Resident Evil")],
    },
    "warnerbros": {
        "known": {"174": "Warner Bros. Pictures"},
        "resolve": [("New Line Cinema", "It"),
                    ("Warner Bros. Pictures Animation", "The Lego Movie")],
    },
    "universalpictures": {
        "known": {"33": "Universal Pictures"},
        "resolve": [("Illumination", "Minions"),
                    ("DreamWorks Animation", "Kung Fu Panda"),
                    ("Focus Features", "Oppenheimer")],
    },
    "paramountpictures": {
        "known": {"4": "Paramount Pictures"},
        "resolve": [],  # bewiesen, keine offene Resolution
    },
    "lionsgate": {
        "known": {"1632": "Lionsgate"},
        "resolve": [],  # bewiesen
    },
}

# Movie-discover-Filter wie im Live-Sync (with_release_type), damit der
# Slate-Count vergleichbar zum kuenftigen Company-Pfad ist. NICHT mit
# date-window — auf der Company-Achse faellt das Fenster weg.
_DISCOVER_MOVIE_PARAMS = {
    "sort_by": "popularity.desc",
    "include_adult": "false",
    "include_video": "false",
}
_DISCOVER_TV_PARAMS = {
    "sort_by": "popularity.desc",
    "include_adult": "false",
}


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"diag_resolve_company_ids: {msg}", file=sys.stderr)
    raise SystemExit(code)


async def _search_company(client, term: str) -> list[dict]:
    data = await client._get("/search/company", {"query": term})
    return data.get("results") or []


async def _slate_count(client, companies: str) -> tuple[int, int]:
    """total_results fuer movie + tv bei ``with_companies=<set>``."""
    movie = await client._get("/discover/movie", {**_DISCOVER_MOVIE_PARAMS, "with_companies": companies})
    tv = await client._get("/discover/tv", {**_DISCOVER_TV_PARAMS, "with_companies": companies})
    return int(movie.get("total_results") or 0), int(tv.get("total_results") or 0)


async def _verify_company_on_title(client, company_id: str, title: str) -> dict:
    """Sucht ``title`` (movie, dann tv) und prueft, ob ``company_id`` in den
    production_companies (movie) bzw. production_companies/networks (tv) steht."""
    out: dict = {"title": title, "company_id": str(company_id), "movie": None, "tv": None}
    # Movie
    msr = await client._get("/search/movie", {"query": title})
    mres = msr.get("results") or []
    if mres:
        mid = mres[0].get("id")
        detail = await client._get(f"/movie/{mid}", None)
        comps = {str(c.get("id")) for c in (detail.get("production_companies") or [])}
        out["movie"] = {
            "tmdb_id": mid,
            "matched_title": detail.get("title"),
            "company_ids": sorted(comps),
            "hit": str(company_id) in comps,
        }
    # TV
    tsr = await client._get("/search/tv", {"query": title})
    tres = tsr.get("results") or []
    if tres:
        tid = tres[0].get("id")
        detail = await client._get(f"/tv/{tid}", None)
        comps = {str(c.get("id")) for c in (detail.get("production_companies") or [])}
        nets = {str(n.get("id")) for n in (detail.get("networks") or [])}
        out["tv"] = {
            "tmdb_id": tid,
            "matched_title": detail.get("name"),
            "company_ids": sorted(comps),
            "network_ids": sorted(nets),
            "hit": str(company_id) in comps or str(company_id) in nets,
        }
    return out


def _print_company_results(term: str, results: list[dict]) -> None:
    print(f"  search {term!r}: {len(results)} Treffer")
    for r in results[:8]:
        print(f"    id={r.get('id'):<8} origin={str(r.get('origin_country') or '--'):<3} "
              f"name={r.get('name')!r}")


async def run_search(client, terms: list[str]) -> None:
    print("## /search/company")
    for term in terms:
        _print_company_results(term, await _search_company(client, term))
    print()


async def run_verify(client, company_id: str, title: str) -> None:
    print(f"## verify company={company_id} gegen Titel {title!r}")
    res = await _verify_company_on_title(client, company_id, title)
    for medium in ("movie", "tv"):
        block = res.get(medium)
        if block is None:
            print(f"  {medium}: kein Such-Treffer fuer {title!r}")
            continue
        print(f"  {medium}: matched={block.get('matched_title')!r} "
              f"hit={block['hit']}  companies={block.get('company_ids')}"
              + (f" networks={block.get('network_ids')}" if medium == "tv" else ""))
    print()


async def run_count(client, companies: str) -> None:
    m, t = await _slate_count(client, companies)
    print(f"## count with_companies={companies!r}")
    print(f"  movies total_results = {m}")
    print(f"  tv     total_results = {t}")
    print(f"  slate (movie+tv)     = {m + t}")
    print()


async def run_plan(client) -> None:
    print("## §3 PLAN — Resolve (Sub-Labels) + Count (bewiesene Sets)")
    print()
    for pair, spec in RESOLVE_PLAN.items():
        known = spec["known"]
        known_set = "|".join(known.keys())
        print(f"### {pair}")
        print(f"  known set: {known}")
        try:
            m, t = await _slate_count(client, known_set)
            print(f"  slate(known) with_companies={known_set!r}: movie={m} tv={t} sum={m + t}")
        except Exception as exc:  # noqa: BLE001 — Diagnose, pro Studio isolieren
            print(f"  slate(known): FEHLER {type(exc).__name__}: {exc}")
        for term, verify_title in spec["resolve"]:
            try:
                results = await _search_company(client, term)
                _print_company_results(term, results)
                # Bequemlichkeit: den Top-Kandidaten direkt gegen den Titel verifizieren.
                if results:
                    top_id = results[0].get("id")
                    v = await _verify_company_on_title(client, str(top_id), verify_title)
                    mv = v.get("movie") or {}
                    print(f"    ↳ verify top id={top_id} gegen {verify_title!r}: "
                          f"movie_hit={mv.get('hit')} (movie companies={mv.get('company_ids')})")
            except Exception as exc:  # noqa: BLE001
                print(f"    FEHLER bei {term!r}: {type(exc).__name__}: {exc}")
        print()
    print("# Hinweis: 'verify top' prueft NUR den ersten Suchtreffer automatisch. Bei mehreren")
    print("# Kandidaten (z.B. AU/FR-Huellen) die korrekte ID manuell mit --mode verify gegen den")
    print("# Titel bestaetigen, bevor sie ins finale Set geht. KEINE ID ungeprueft uebernehmen.")


async def amain() -> None:
    parser = argparse.ArgumentParser(
        prog="diag_resolve_company_ids.py",
        description="TMDb-Company-ID-Resolution & Slate-Count. Read-only gegen TMDb.",
    )
    parser.add_argument("--mode", choices=["search", "verify", "count", "plan"], default="plan")
    parser.add_argument("terms", nargs="*", help="Suchbegriffe fuer --mode search.")
    parser.add_argument("--company", help="Company-ID fuer --mode verify.")
    parser.add_argument("--title", help="Verifikations-Titel fuer --mode verify.")
    parser.add_argument("--companies", help="Pipe-OR-Set fuer --mode count, z.B. '2|3|420'.")
    args = parser.parse_args()

    from app.services.tmdb_client import TMDbClient, TMDbAuthError
    client = TMDbClient()
    try:
        client._ensure_auth()
    except TMDbAuthError as exc:
        _die(f"{exc} (setze TMDB_READ_ACCESS_TOKEN oder TMDB_API_KEY).")

    try:
        if args.mode == "search":
            if not args.terms:
                _die("--mode search braucht mindestens einen Suchbegriff.")
            await run_search(client, args.terms)
        elif args.mode == "verify":
            if not (args.company and args.title):
                _die("--mode verify braucht --company und --title.")
            await run_verify(client, args.company, args.title)
        elif args.mode == "count":
            if not args.companies:
                _die("--mode count braucht --companies 'a|b|c'.")
            await run_count(client, args.companies)
        else:
            await run_plan(client)
    except TMDbAuthError as exc:
        _die(str(exc))


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
