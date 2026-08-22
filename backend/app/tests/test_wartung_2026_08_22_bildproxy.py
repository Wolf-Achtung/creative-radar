"""Wartung 22.08.2026 — Bild-Proxy-Vorfall aus Wolfs Railway-Log.

Symptom: alle YouTube-Karten der Vorschlags-Queue zeigten „Bild lädt
nicht". Zwei Ursachen, beide hier festgenagelt:

1. Das Frontend routet ``i.ytimg.com``-URLs seit jeher durch
   ``/api/img`` (``PROXY_HOST_SUFFIXES`` in imageUrl.js), aber die
   server-seitige Allowlist (``image_proxy_allowed_hosts``) kannte
   ytimg/ggpht nicht — der Proxy antwortete 403 „host not allowed",
   ohne je zu fetchen. Der Paritäts-Kommentar in imageUrl.js („The
   proxy's host-allowlist must mirror PROXY_HOST_SUFFIXES below") war
   eine unbewachte Behauptung. Jetzt liest der Test beide Listen.

2. ``thumbnail_url`` zeigt bei YouTube oft auf ``maxresdefault.jpg``,
   das bei vielen Videos nicht existiert (404) — die Qualitäts-Leiter
   dagegen steht in test_api_thumbnails.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.proxy import _host_is_allowed
from app.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _backend_default_suffixes() -> set[str]:
    """Der eingecheckte Default aus ``config.py`` — bewusst NICHT die
    laufende ``settings``-Instanz, damit eine lokale ENV-Belegung den
    Wächter nicht still bestehen (oder fallen) lässt."""
    default = Settings.model_fields["image_proxy_allowed_hosts"].default
    return {item.strip().lower().lstrip(".") for item in default.split(",") if item.strip()}


def _frontend_suffixes() -> set[str]:
    quelle = (_REPO_ROOT / "frontend" / "src" / "api" / "imageUrl.js").read_text(
        encoding="utf-8"
    )
    block = re.search(
        r"export const PROXY_HOST_SUFFIXES = \[(.*?)\];", quelle, re.DOTALL
    )
    assert block, (
        "PROXY_HOST_SUFFIXES nicht in frontend/src/api/imageUrl.js gefunden — "
        "wurde die Liste umbenannt/verschoben, muss dieser Wächter mitziehen."
    )
    suffixes = set(re.findall(r"'([^']+)'", block.group(1)))
    assert suffixes, "PROXY_HOST_SUFFIXES ist leer — das kann nicht stimmen."
    return suffixes


@pytest.mark.vertrag
def test_server_allowlist_deckt_die_frontend_proxy_liste():
    """Jeder Host, den das Frontend durch /api/img schickt, muss der
    Server auch annehmen — sonst produziert das Frontend selbst die
    403-Antworten, die der Nutzer als kaputte Bilder sieht (YouTube-
    Vorfall 22.08.2026: ytimg.com/ggpht.com standen nur im Frontend)."""
    frontend = _frontend_suffixes()
    backend = _backend_default_suffixes()
    fehlend = frontend - backend
    assert not fehlend, (
        f"Server-Allowlist (config.py image_proxy_allowed_hosts) fehlt: "
        f"{sorted(fehlend)}. Das Frontend routet diese Hosts durch /api/img, "
        f"der Server lehnt sie mit 403 ab, BEVOR er fetcht."
    )


def test_ytimg_und_ggpht_passieren_den_host_check():
    """Das konkrete Log-Symptom, end-to-end am echten Check: die URLs
    aus Wolfs Railway-Log dürfen nicht mehr am Allowlist-403 scheitern.
    Läuft gegen die laufende ``settings``-Instanz — in CI ist keine
    IMAGE_PROXY_ALLOWED_HOSTS-ENV gesetzt, es gilt der Code-Default."""
    assert _host_is_allowed("i.ytimg.com")
    assert _host_is_allowed("yt3.ggpht.com")
    # Und die Grenze bleibt eine Grenze: fremde Hosts weiter verboten.
    assert not _host_is_allowed("i.ytimg.com.evil.example")
    assert not _host_is_allowed("example.com")
