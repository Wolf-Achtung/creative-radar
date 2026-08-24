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
prueft nur, was der ueberspringt. Seit dem 24.08.2026 gehoeren dazu auch
Ein-Wort-Titel mit schwachem Treffer: der Autopilot verlangt fuer die
Confidence >= 0.95 und laesst den Rest liegen (Vorfall: 83 Assets an
"Driven", "Personality" & Co., weil das Wort in der Caption stand).
Ohne diese Ausnahme fielen sie zwischen Autopilot und Assist hindurch
und blieben komplett Handarbeit. Kosten: Haiku, ein Call je Kandidat
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
    is_anthropic_configured,
    messages_create_strict_json,
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

# Nach so vielen erfolglosen Auswertungen gibt der Assist auf und legt
# den Kandidaten in die Hand-Pruefung, statt jeden Klick zu blockieren.
MAX_FEHLVERSUCHE = 3

URTEIL_TOOL_NAME = "urteil_melden"

# Erzwungenes Schema statt Text-Parsen (24.08.2026). Der Assist lief ueber
# ``call_with_json_retry``: das Modell schrieb freien Text, der Code parste
# JSON heraus. Gemessen an Wolfs Lauf vom 24.08.: 113 API-Calls fuer 79
# gepruefte Kandidaten — rund 40 % Aufschlag durch ``parse-retry``, und ein
# Kandidat war ueberhaupt nicht auswertbar. Ueber Tool-Use validiert
# Anthropic die Antwort gegen dieses Schema, bevor sie zurueckkommt; ein
# Parse-Fehler ist damit strukturell ausgeschlossen.
URTEIL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "auswahl": {
            "type": ["integer", "null"],
            "description": (
                "Nummer aus der Kandidatenliste, oder null wenn keiner passt."
            ),
        },
        "sicher": {"type": "boolean"},
        "begruendung": {"type": "string", "maxLength": 300},
    },
    "required": ["auswahl", "sicher", "begruendung"],
}


def _tool_input(msg) -> dict | None:
    """Das ``input`` des erzwungenen Tool-Use-Blocks.

    ``tool_choice`` mit ``disable_parallel_tool_use`` garantiert genau
    einen solchen Block; der Fallback deckt API-Drift ab (siehe
    ``messages_create_strict_json``-Doc).
    """
    if msg is None:
        return None
    for block in getattr(msg, "content", None) or []:
        if getattr(block, "type", None) == "tool_use":
            eingabe = getattr(block, "input", None)
            if isinstance(eingabe, dict):
                return eingabe
    return None


SYSTEM_PROMPT = """Du ordnest Social-Media-Posts von Film- und Serien-Kanälen dem beworbenen Titel zu.

Du bekommst je Post: den Vorschlag des automatischen Matchers, die Caption, ggf. im Bild erkannten Text (OCR) — und eine nummerierte Kandidatenliste aus dem Titel-Katalog.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{"auswahl": <Nummer aus der Liste oder null>, "sicher": true|false, "begruendung": "1 Satz"}

Regeln:
1. auswahl nur, wenn der Post eindeutig GENAU diesen Titel bewirbt.
2. sicher=true, wenn Caption oder Bildtext den Titel klar benennen oder unverwechselbar auf ihn verweisen (Teil-Titel, Hashtag, Figuren-/Franchise-Name zählen). sicher=false nur bei echtem Zweifel zwischen mehreren Titeln oder ohne Titel-Bezug.
3. Ein Post über einen Schauspieler, ein Genre oder mehrere Titel gleichzeitig bekommt auswahl=null.
3a. Bewirbt der Post ein ANDERES Werk, dessen Name den Kandidaten nur enthält, ist das NICHT dieser Kandidat — auswahl=null. "Sam & Cat" ist nicht "CAT", "American Hostage" ist nicht "Hostage", ein Post über eine Serie ist kein Post über ihr angebliches Spin-off. Der Kandidatenname muss der Titel des beworbenen Werks sein, nicht ein Teil davon.
3b. Behaupte keinen Alternativtitel und keine Verwandtschaft zwischen Werken, die nicht in Caption oder Bildtext steht. Was du nicht im Text siehst, begründet keine Auswahl.
4. begruendung: 1 kurzer Satz auf Deutsch — er wird dem Prüfer als Hinweis angezeigt, auch wenn du unsicher bist (dann: warum unsicher)."""


@dataclass
class LlmAssistSummary:
    geprueft: int = 0
    zugeordnet: int = 0
    unsicher: int = 0
    keine_shortlist: int = 0
    fehler: int = 0
    bereits_geprueft: int = 0
    ein_wort_zweifel: int = 0
    aufgegeben: int = 0
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
            "bereits_geprueft": self.bereits_geprueft,
            "ein_wort_zweifel": self.ein_wort_zweifel,
            "aufgegeben": self.aufgegeben,
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


def _ist_ein_wort_zweifelsfall(candidate: TitleCandidate) -> bool:
    """Ein-Wort-Titel, den der Autopilot seit dem 24.08.2026 ablehnt.

    Vorfall: 83 Assets wurden Titeln wie "Driven" oder "Personality"
    zugeordnet, weil das Wort irgendwo in der Caption stand. Der
    Autopilot verlangt fuer Ein-Wort-Titel jetzt Confidence >= 0.95 und
    laesst schwache Treffer liegen.

    Damit fielen sie zwischen zwei Stuehle: der Autopilot lehnt ab, und
    dieser Assist uebersprang sie als "Exakt-Fall — nicht unser Revier".
    Genau diese Annahme — Katalog-Treffer heisst sicher — hat der Vorfall
    widerlegt. Ein einzelnes Alltagswort im Katalog ist ein Zweifelsfall,
    und Zweifelsfaelle sind das Revier dieses Assists.
    """
    name = _normalize(candidate.suggested_title)
    if not name or " " in name:
        return False
    return candidate.confidence < settings.candidate_autopilot_min_confidence_single_word


def _user_prompt(
    candidate: TitleCandidate, post: Post | None, asset: Asset | None,
    shortlist: list[tuple[Title, float]],
) -> str:
    zeilen = [
        f"Matcher-Vorschlag: {candidate.suggested_title}",
        f"Caption: {(post.caption or '')[:400] if post else ''}",
        f"OCR-Text: {(asset.ocr_text or '')[:200] if asset else ''}",
        "",
    ]
    if _ist_ein_wort_zweifelsfall(candidate):
        # Ohne diese Zeile liest das Modell Regel 2 ("Caption benennt den
        # Titel") auf ein zufaelliges Wortvorkommen an — genau der Fehler,
        # den der mechanische Matcher gemacht hat.
        zeilen.append(
            "ACHTUNG: Der Vorschlag ist ein einzelnes, alltaegliches Wort. "
            "Dass es in der Caption vorkommt, heisst NICHT, dass der Post "
            "diesen Titel bewirbt — pruefe, ob der Post wirklich von diesem "
            "Werk handelt. Das gilt auch, wenn der Post ein anderes Werk "
            "bewirbt, dessen Name das Wort enthaelt: dann ist es NICHT "
            "dieser Kandidat. Im Zweifel auswahl=null."
        )
        zeilen.append("")
    zeilen.append("Kandidaten aus dem Katalog:")
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

    Ausnahme seit dem 24.08.2026: Ein-Wort-Titel mit schwachem Treffer
    (s. ``_ist_ein_wort_zweifelsfall``). Die lehnt der Autopilot ab,
    also braucht sie jemand — sonst bleiben sie komplett liegen.
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
    # Bereits KI-gepruefte (``llm_checked_at``) werden uebersprungen —
    # Wolfs Befund vom 21.08.: ohne den Marker prueften alle Klicks
    # dieselben ersten 12 und kamen nie bei den uebrigen an.
    arbeitsvorrat: list[tuple[TitleCandidate, Asset]] = []
    for candidate in offene:
        asset = session.get(Asset, candidate.asset_id)
        if asset is None or asset.title_id is not None:
            continue
        if (
            _normalize(candidate.suggested_title) in lookup
            and not _ist_ein_wort_zweifelsfall(candidate)
        ):
            continue  # Exakt- oder Mehrdeutig-Fall — nicht unser Revier
        if candidate.llm_checked_at is not None:
            summary.bereits_geprueft += 1
            continue
        if _ist_ein_wort_zweifelsfall(candidate):
            summary.ein_wort_zweifel += 1
        arbeitsvorrat.append((candidate, asset))

    def _fehlversuch_vermerken(
        candidate: TitleCandidate, summary: "LlmAssistSummary"
    ) -> None:
        """Zaehlt Fehlversuche und gibt nach ``MAX_FEHLVERSUCHE`` auf.

        Ohne Marker versucht jeder Klick denselben Kandidaten erneut —
        richtig fuer einen einmaligen API-Aussetzer, fatal fuer einen
        Kandidaten, der systematisch scheitert: Wolfs Lauf vom 24.08.
        endete mit ``offen_danach: 1``, das sich durch keinen weiteren
        Klick mehr aufloeste. Der Zaehler steht in der Notiz, damit kein
        Datenbank-Feld dafuer noetig ist; nach dem letzten Versuch wird
        der Kandidat markiert und landet mit Hinweis in der Hand-Pruefung.
        """
        bisher = 0
        if candidate.llm_note:
            treffer = re.search(r"Versuch (\d+)/", candidate.llm_note)
            if treffer:
                bisher = int(treffer.group(1))
        naechster = bisher + 1
        if naechster >= MAX_FEHLVERSUCHE:
            summary.aufgegeben += 1
            _markieren(
                candidate,
                f"KI-Auswertung nach {naechster} Versuchen fehlgeschlagen — "
                "bitte von Hand entscheiden.",
            )
            return
        candidate.llm_note = (
            f"KI-Auswertung fehlgeschlagen (Versuch {naechster}/"
            f"{MAX_FEHLVERSUCHE}) — naechster Lauf versucht es erneut."
        )[:300]
        candidate.updated_at = utc_now()
        session.add(candidate)

    def _markieren(candidate: TitleCandidate, note: str) -> None:
        candidate.llm_checked_at = utc_now()
        candidate.llm_note = note[:300]
        candidate.updated_at = utc_now()
        session.add(candidate)

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
            _markieren(candidate, "KI: kein passender Katalog-Titel gefunden.")
            continue

        try:
            msg = messages_create_strict_json(
                model=modell,
                system=SYSTEM_PROMPT,
                user_message=_user_prompt(candidate, post, asset, shortlist),
                tool_name=URTEIL_TOOL_NAME,
                tool_description=(
                    "Meldet, welcher Kandidat aus der Liste beworben wird."
                ),
                input_schema=URTEIL_SCHEMA,
                max_tokens=400,
            )
        except Exception:
            logger.exception(
                "candidate-llm-assist-call-failed",
                extra={"candidate_id": str(candidate.id)},
            )
            msg = None

        if msg is not None:
            usage = getattr(msg, "usage", None)
            if usage is not None:
                record_anthropic_call(
                    usage, modell, "candidate_llm_assist",
                    meta={"candidate_id": str(candidate.id)},
                )
                summary.kosten_calls += 1

        parsed = _tool_input(msg)
        if not isinstance(parsed, dict):
            summary.fehler += 1
            _fehlversuch_vermerken(candidate, summary)
            continue
        auswahl = _als_zahl(parsed.get("auswahl"))
        sicher = _als_wahr(parsed.get("sicher"))
        begruendung = str(parsed.get("begruendung", "")).strip()
        gueltig = auswahl is not None and 1 <= auswahl <= len(shortlist)
        if not (sicher and gueltig):
            summary.unsicher += 1
            _markieren(
                candidate,
                f"KI unsicher: {begruendung}" if begruendung
                else "KI unsicher, ohne Begruendung.",
            )
            continue

        titel = shortlist[auswahl - 1][0]
        _markieren(candidate, f"KI zugeordnet: {begruendung}"[:300])
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
    # Noch UNGEPRUEFT: was diese Runde nicht erreicht hat, plus die
    # Fehler, die wiederkommen. Aufgegebene Fehler zaehlen NICHT mit —
    # sie tragen jetzt einen Marker und kehren nie zurueck; sie hier
    # mitzuzaehlen hiesse, dem Nutzer eine Runde zu versprechen, die
    # nichts mehr findet (genau das Missverstaendnis vom 24.08.).
    # Unsichere und Shortlist-lose sind ebenso ERLEDIGT: Marker + Notiz,
    # ab jetzt Sache der Hand-Pruefung.
    summary.offen_danach = (
        max(len(arbeitsvorrat) - summary.geprueft, 0)
        + max(summary.fehler - summary.aufgegeben, 0)
    )
    logger.info("candidate-llm-assist done %s", summary.to_dict())
    return summary


def _als_zahl(wert) -> int | None:
    """Haiku antwortet die Auswahl gelegentlich als String ("1") —
    strikte int-Pruefung liess am 21.08. JEDEN Treffer als unsicher
    durchfallen. Booleans sind keine Auswahl (True waere sonst 1)."""
    if isinstance(wert, bool):
        return None
    if isinstance(wert, int):
        return wert
    if isinstance(wert, str) and wert.strip().isdigit():
        return int(wert.strip())
    return None


def _als_wahr(wert) -> bool:
    return wert is True or (isinstance(wert, str) and wert.strip().lower() == "true")
