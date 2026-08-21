"""Kandidaten-LLM-Assist — die Rest-Kandidaten des Autopiloten (21.08.2026).

Wolfs Befund: nach dem mechanischen Autopiloten bleiben Dutzende offene
Titel-Vorschlaege ("63 offene Tabs ganz schoen viel"), fast alle in der
Kategorie ``skipped_no_exact_match`` — der Matcher-Vorschlag steht NICHT
wortgleich in der Whitelist ("beware" statt "Beware Boiúna", Tippfehler,
Teil-Titel). Ein Mensch loest die per Blick auf die Caption in Sekunden;
genau diesen Blick uebernimmt hier Haiku.

Arbeitsteilung, dieselbe Linie wie ueberall im Projekt:

1. **Der Code waehlt die Kandidatenliste** (Shortlist): Token-Ueberlapp
   zwischen Katalog-Namen (title_original, title_local, Aliasse) und dem
   Post-Text (suggested_title + Caption + OCR). Das LLM sieht NUR diese
   Shortlist — es kann keinen Titel zuordnen, den der Code nicht
   vorgeschlagen hat.
2. **Haiku entscheidet**, welcher Shortlist-Eintrag beworben wird —
   oder keiner. Zuordnung passiert AUSSCHLIESSLICH bei ``sicher: true``
   und gueltiger Auswahl; alles andere bleibt in der manuellen Queue.
3. **Die Zuordnung selbst ist identisch zum Autopiloten** (und damit zum
   manuellen Bestaetigen-Klick): ``title_id`` + ``de_us_match_key``,
   offene Kandidaten des Assets resolved.

Exakt-Treffer bleiben Sache des (kostenlosen) Autopiloten — der Assist
prueft nur, was der ueberspringt. Kosten: Haiku, ein Call je Kandidat
(~1k Token rein, ~100 raus, deutlich unter 1 Cent); jeder Call landet
via ``record_anthropic_call`` im Costlog und damit im Monatsdeckel.

Batch je Aufruf (``max_candidates``, Default 12): der Admin-Button
wartet synchron auf die Antwort — 12 Calls sind ~30 s, ein voller
63er-Backlog waere wieder der "Failed to fetch" vom Titel-Sync.
Die Antwort sagt ehrlich, wie viele noch offen sind: nochmal klicken.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.config import settings
from app.models.entities import (
    Asset,
    CandidateStatus,
    Post,
    Title,
    TitleCandidate,
    utc_now,
)
from app.services.anthropic_client import (
    call_with_json_retry,
    is_anthropic_configured,
)
from app.services.candidate_autopilot import _build_exact_title_lookup, _normalize
from app.services.cost_log import record_anthropic_call
from app.services.match_key import slugify_match_key
from app.services.title_candidates import resolve_open_candidates_for_asset

logger = logging.getLogger(__name__)

# Batch-Groesse je Aufruf — siehe Modul-Docstring (synchroner Button).
DEFAULT_MAX_CANDIDATES_PER_RUN = 12

# Shortlist-Groesse: genug Auswahl fuer Mehrdeutigkeit, kurz genug, dass
# Haiku jede Zeile wirklich liest.
SHORTLIST_SIZE = 12

_TOKEN_RE = re.compile(r"[a-z0-9äöüß]+")

SYSTEM_PROMPT = """Du ordnest Social-Media-Posts von Film- und Serien-Kanälen dem beworbenen Titel zu.

Du bekommst je Post: den Vorschlag des automatischen Matchers, die Caption, ggf. im Bild erkannten Text (OCR) — und eine nummerierte Kandidatenliste aus dem Titel-Katalog.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{"auswahl": <Nummer aus der Liste oder null>, "sicher": true|false, "begruendung": "1 Satz"}

Regeln:
1. auswahl nur, wenn der Post eindeutig GENAU diesen Titel bewirbt.
2. sicher=true nur, wenn kein anderer Titel (auch keiner außerhalb der Liste) plausibel ist. Im Zweifel: auswahl=null, sicher=false.
3. Ein Post über einen Schauspieler, ein Genre oder mehrere Titel gleichzeitig bekommt auswahl=null."""


@dataclass
class LlmAssistSummary:
    geprueft: int = 0
    zugeordnet: int = 0
    unsicher: int = 0
    keine_shortlist: int = 0
    fehler: int = 0
    offen_danach: int = 0
    kosten_calls: int = 0
    zugeordnete_titel: list[str] = field(default_factory=list)
    skipped: str | None = None

    def to_dict(self) -> dict:
        return {
            "geprueft": self.geprueft,
            "zugeordnet": self.zugeordnet,
            "unsicher": self.unsicher,
            "keine_shortlist": self.keine_shortlist,
            "fehler": self.fehler,
            "offen_danach": self.offen_danach,
            "kosten_calls": self.kosten_calls,
            "zugeordnete_titel": self.zugeordnete_titel[:20],
            "skipped": self.skipped,
        }


def _tokens(text: str | None) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _katalog_eintraege(session: Session) -> list[tuple[Title, set[str]]]:
    """Aktive Titel mit dem Token-Set aller ihrer Namen (Original,
    Lokal, Aliasse) — die Vergleichsbasis der Shortlist."""
    eintraege: list[tuple[Title, set[str]]] = []
    for title in session.exec(select(Title).where(Title.active == True)).all():  # noqa: E712
        namen_tokens: set[str] = set()
        for name in [title.title_original, title.title_local, *(title.aliases or [])]:
            namen_tokens |= _tokens(name)
        if namen_tokens:
            eintraege.append((title, namen_tokens))
    return eintraege


def _shortlist(
    katalog: list[tuple[Title, set[str]]], post_text_tokens: set[str]
) -> list[tuple[Title, float]]:
    """Top-Titel nach Token-Ueberlapp: Anteil der Namens-Tokens, die im
    Post-Text vorkommen. "beware" trifft "Beware Boiúna" mit 0.5 —
    genau die Teil-Treffer, an denen der Exakt-Autopilot scheitert."""
    bewertet: list[tuple[Title, float]] = []
    for title, namen_tokens in katalog:
        if not namen_tokens:
            continue
        score = len(namen_tokens & post_text_tokens) / len(namen_tokens)
        if score > 0:
            bewertet.append((title, score))
    bewertet.sort(key=lambda paar: (-paar[1], paar[0].title_original or ""))
    return bewertet[:SHORTLIST_SIZE]


def _user_prompt(
    candidate: TitleCandidate, post: Post | None, asset: Asset | None,
    shortlist: list[tuple[Title, float]],
) -> str:
    zeilen = [
        f"Matcher-Vorschlag: {candidate.suggested_title}",
        f"Caption: {(post.caption or '')[:400] if post else ''}",
        f"OCR-Text: {(asset.ocr_text or '')[:200] if asset else ''}",
        "",
        "Kandidaten aus dem Katalog:",
    ]
    for i, (title, _score) in enumerate(shortlist, start=1):
        jahr = ""
        for datum in (title.release_date_de, title.release_date_us):
            if datum:
                jahr = f", {datum.year}"
                break
        art = "Serie" if (title.content_type or "") == "Series" else "Film"
        zeilen.append(f"{i}. {title.title_original} ({art}{jahr})")
    return "\n".join(zeilen)


def run_candidate_llm_assist(
    session: Session,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES_PER_RUN,
) -> LlmAssistSummary:
    """Loest offene Kandidaten OHNE Exakt-Treffer per Haiku auf.

    Nur diese Kategorie: Exakt-Treffer gehoeren dem mechanischen
    Autopiloten (kostenlos, deterministisch), Mehrdeutigkeit und
    Niedrig-Confidence-Exakt-Treffer bleiben bewusst Menschensache.
    """
    summary = LlmAssistSummary()
    if not is_anthropic_configured():
        summary.skipped = "anthropic_not_configured"
        return summary

    lookup = _build_exact_title_lookup(session)
    katalog = _katalog_eintraege(session)
    modell = settings.anthropic_haiku_model

    offene = session.exec(
        select(TitleCandidate).where(TitleCandidate.status == CandidateStatus.OPEN)
    ).all()

    # Nur die Faelle, die der Autopilot als no_exact_match ueberspringt:
    # unzugeordnetes Asset, Vorschlag ohne (eindeutigen) Whitelist-Namen.
    arbeitsvorrat: list[tuple[TitleCandidate, Asset]] = []
    for candidate in offene:
        asset = session.get(Asset, candidate.asset_id)
        if asset is None or asset.title_id is not None:
            continue
        if _normalize(candidate.suggested_title) in lookup:
            continue  # Exakt- oder Mehrdeutig-Fall — nicht unser Revier
        arbeitsvorrat.append((candidate, asset))

    for candidate, asset in arbeitsvorrat[:max_candidates]:
        summary.geprueft += 1
        post = session.get(Post, asset.post_id)
        text_tokens = (
            _tokens(candidate.suggested_title)
            | _tokens(post.caption if post else None)
            | _tokens(asset.ocr_text)
        )
        shortlist = _shortlist(katalog, text_tokens)
        if not shortlist:
            summary.keine_shortlist += 1
            continue

        retry_result = call_with_json_retry(
            model=modell,
            system=SYSTEM_PROMPT,
            user_message=_user_prompt(candidate, post, asset, shortlist),
            max_tokens=300,
            log_prefix="candidate-llm-assist",
            log_extra={"candidate_id": str(candidate.id)},
        )
        for msg_attempt, _raw in retry_result.call_attempts:
            usage = getattr(msg_attempt, "usage", None)
            if usage is not None:
                record_anthropic_call(
                    usage, modell, "candidate_llm_assist",
                    meta={"candidate_id": str(candidate.id)},
                )
                summary.kosten_calls += 1

        parsed = retry_result.parsed
        if not isinstance(parsed, dict):
            summary.fehler += 1
            continue
        auswahl = parsed.get("auswahl")
        sicher = parsed.get("sicher") is True
        gueltig = isinstance(auswahl, int) and 1 <= auswahl <= len(shortlist)
        if not (sicher and gueltig):
            summary.unsicher += 1
            continue

        titel = shortlist[auswahl - 1][0]
        # Identische Zuordnung wie Autopilot / manueller Bestaetigen-Klick.
        asset.title_id = titel.id
        asset.de_us_match_key = slugify_match_key(
            titel.franchise or titel.title_original
        )
        asset.updated_at = utc_now()
        session.add(asset)
        resolve_open_candidates_for_asset(session, asset.id, commit=False)
        summary.zugeordnet += 1
        summary.zugeordnete_titel.append(titel.title_original)
        logger.info(
            "candidate-llm-assist assigned asset=%s title=%s begruendung=%s",
            asset.id, titel.title_original,
            str(parsed.get("begruendung", ""))[:200],
        )

    session.commit()
    summary.offen_danach = max(len(arbeitsvorrat) - summary.geprueft, 0) + (
        summary.unsicher + summary.keine_shortlist + summary.fehler
    )
    logger.info("candidate-llm-assist done %s", summary.to_dict())
    return summary
