"""Sprint negative-likes-sentinel (2026-06-11) — Ingest-Guard-Tests.

Apify-Instagram liefert ``likesCount: -1`` als Sentinel fuer "Likes
verborgen" (Hide-like-counts). Recon-Befund: 39 Bestands-Rows, alle
Instagram, alle -1; der Wert lief ungefiltert bis in
``compute_activation_rate`` (-1/189 = -0,5%) und die ER-Summen von
``market_timeline``. Fix-Entscheidung Wolf: negativ → ``None`` (nicht 0),
analog zur Foto-Post-View-Behandlung — ``None`` = "unbekannt", 0 waere
ein behaupteter Messwert.
"""
from app.services.apify_connector import (
    _count_or_none,
    normalize_public_item,
    normalize_tiktok_item,
)
from app.services.youtube_connector import _int_or_none


def test_count_or_none_negative_becomes_none():
    assert _count_or_none(-1) is None
    assert _count_or_none(-500) is None


def test_count_or_none_passthrough_and_edge_cases():
    assert _count_or_none(0) == 0
    assert _count_or_none(189) == 189
    assert _count_or_none("42") == 42
    assert _count_or_none(None) is None
    assert _count_or_none("n/a") is None


def test_instagram_hidden_likes_sentinel_becomes_none():
    item = {
        "url": "https://www.instagram.com/p/ABC123/",
        "likesCount": -1,
        "commentsCount": 7,
        "videoViewCount": 189,
    }
    normalized = normalize_public_item(item)
    assert normalized["visible_likes"] is None
    # Nachbarn unveraendert: comments/views laufen wie bisher durch.
    assert normalized["visible_comments"] == 7
    assert normalized["visible_views"] == 189


def test_instagram_positive_likes_unchanged():
    normalized = normalize_public_item({"url": "https://x/p/1/", "likesCount": 1234})
    assert normalized["visible_likes"] == 1234


def test_tiktok_negative_likes_sentinel_becomes_none():
    item = {"webVideoUrl": "https://www.tiktok.com/@x/video/1", "diggCount": -1}
    assert normalize_tiktok_item(item)["visible_likes"] is None


def test_youtube_int_or_none_negative_becomes_none():
    assert _int_or_none("-1") is None
    assert _int_or_none(-1) is None
    assert _int_or_none("98765") == 98765
    assert _int_or_none(None) is None
