"""Misst die echte Token-Zahl der System-Prompts gegen das Cache-Minimum.

OPTIONALES DIAGNOSE-WERKZEUG — laeuft in keiner Pipeline und ist keine
Merge-Voraussetzung. Es liegt hier, weil die Frage "ist dieser Prompt
ueberhaupt cachebar?" wiederkehrt, sobald ein Prompt gekuerzt oder ein
Modell gewechselt wird.

Hintergrund: Anthropic cacht einen Prefix erst ab einem modellabhaengigen
Minimum — 1024 Token fuer Opus 4.8, 4096 fuer Haiku 4.5. Liegt der Prompt
darunter, wird der Request ohne Caching verarbeitet: kein Fehler, keine
Zusatzkosten, ``cache_creation_input_tokens`` bleibt schlicht 0.

Konkret offen sind Cutter-Weekly (~2.919 Zeichen) und Designer-Weekly
(~3.081 Zeichen): die zeichenbasierte Schaetzung liegt bei 834-1.027 Token
und damit exakt auf der 1024er-Kante. Bewusst nicht vorab gemessen — beide
zusammen sind 6 Aufrufe und 0,29 USD in 30 Tagen (0,7 % der Ausgaben), und
die ``[CACHE-USAGE]``-Logs beantworten die Frage nach dem ersten Cron-Lauf
von selbst.

``tiktoken`` ist KEIN Ersatz: das ist OpenAIs Tokenizer und zaehlt Claude-
Token systematisch falsch.

Aufruf (braucht ANTHROPIC_API_KEY):

    cd backend
    ANTHROPIC_API_KEY=sk-ant-... DATABASE_URL=sqlite:///./x.db \\
      python3 scripts/measure_system_prompt_tokens.py

Kostet nichts — ``count_tokens`` wird nicht abgerechnet.
"""
from __future__ import annotations

import os
import sys

# Repo-Root in den Pfad, damit ``app.*`` importierbar ist.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY ist nicht gesetzt — Abbruch.", file=sys.stderr)
        return 2

    import anthropic

    from app.config import settings
    from app.services.cutter_weekly import CUTTER_WEEKLY_SYSTEM_PROMPT
    from app.services.designer_weekly import DESIGNER_WEEKLY_SYSTEM_PROMPT
    from app.services.insight_engine import SYSTEM_PROMPT
    from app.services.segment_roundup import ROUNDUP_SYSTEM_PROMPT
    from app.services.title_brief import TITLE_SYSTEM_PROMPT

    # Minimum cachebarer Prefix je Modell (Anthropic-Doku). Nicht monoton
    # ueber die Generationen — deshalb hier explizit statt abgeleitet.
    minimums = {
        "claude-opus-4-8": 1024,
        "claude-opus-5": 512,
        "claude-sonnet-5": 1024,
        "claude-haiku-4-5-20251001": 4096,
    }

    model = settings.anthropic_opus_model
    minimum = minimums.get(model)
    client = anthropic.Anthropic()

    targets = [
        ("F  insight_engine  SYSTEM_PROMPT", SYSTEM_PROMPT),
        ("G  segment_roundup ROUNDUP_SYSTEM_PROMPT", ROUNDUP_SYSTEM_PROMPT),
        ("H  title_brief     TITLE_SYSTEM_PROMPT", TITLE_SYSTEM_PROMPT),
        ("I  cutter_weekly   CUTTER_WEEKLY_SYSTEM_PROMPT", CUTTER_WEEKLY_SYSTEM_PROMPT),
        ("J  designer_weekly DESIGNER_WEEKLY_SYSTEM_PROMPT", DESIGNER_WEEKLY_SYSTEM_PROMPT),
    ]

    print(f"Modell: {model}   Cache-Minimum: {minimum if minimum else 'unbekannt'} Token\n")
    print(f"{'Call-Site':46s} {'Zeichen':>8s} {'Token':>7s}  {'cachebar?':>10s}")
    for label, prompt in targets:
        resp = client.messages.count_tokens(
            model=model,
            system=prompt,
            messages=[{"role": "user", "content": "x"}],
        )
        # Das "x" der Pflicht-User-Nachricht mitzaehlen zu lassen ist
        # unschaerfer als noetig — der Breakpoint sitzt am Ende von system,
        # der gecachte Prefix ist also system allein. Die Differenz von
        # wenigen Token ist an der 1024er-Kante relevant, deshalb wird sie
        # hier ausgewiesen statt versteckt.
        tokens = resp.input_tokens
        verdict = "?" if minimum is None else ("JA" if tokens >= minimum else "NEIN")
        print(f"{label:46s} {len(prompt):8d} {tokens:7d}  {verdict:>10s}")

    print(
        "\nHinweis: gezaehlt ist system + eine 1-Zeichen-User-Nachricht "
        "(die API verlangt mindestens eine). Der real gecachte Prefix ist "
        "system allein, liegt also wenige Token niedriger."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
