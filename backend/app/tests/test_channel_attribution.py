"""Sprint channel-attribution-inputurl (2026-06-11) — Attribution-Tests.

Vorher: ``_match_channel`` ordnete per ownerUsername zu, mit
positionsbasiertem Index-Fallback bei Nicht-Match — Fremd-Owner-Items
(Instagram-Collabs, Einstreuung) landeten auf einem effektiv zufälligen
Channel (Müll-Sink-Bug, 551/2.414 IG-Posts falsch). Jetzt: inputUrl-
Identität (Echo der eigenen directUrls) ist die Wahrheit; Items ohne
inputUrl (TikTok) laufen über Owner-Identität; Unauflösbares wird
verworfen + geloggt, nie geraten.
"""
from app.api.monitor import (
    _group_items_by_channel,
    _match_channel_by_input_url,
    _match_channel_by_owner,
    _normalize_profile_url,
)
from app.models.entities import Channel
from app.services.apify_connector import normalize_public_item, normalize_tiktok_item


def _ig_channel(handle: str, url: str | None = None) -> Channel:
    return Channel(
        name=handle,
        platform="instagram",
        handle=handle,
        url=url or f"https://www.instagram.com/{handle}/",
    )


WARNER = _ig_channel("warnerbrosepics")
XYZ = _ig_channel("xyzfilms")
CHANNELS = [WARNER, XYZ]


def test_normalize_profile_url_variants():
    expected = "https://instagram.com/warnerbrosepics"
    assert _normalize_profile_url("https://www.instagram.com/warnerbrosepics/") == expected
    assert _normalize_profile_url("https://instagram.com/WarnerBrosEpics") == expected
    assert _normalize_profile_url("https://www.instagram.com/warnerbrosepics?hl=en") == expected
    assert _normalize_profile_url("https://www.instagram.com/warnerbrosepics/#top") == expected
    assert _normalize_profile_url(None) == ""


def test_input_url_match_hits_scraped_profile():
    found = _match_channel_by_input_url(CHANNELS, "https://instagram.com/xyzfilms?hl=de")
    assert found is XYZ


def test_input_url_match_unknown_profile_returns_none():
    assert _match_channel_by_input_url(CHANNELS, "https://www.instagram.com/bad_robot/") is None


def test_owner_match_without_fallback():
    assert _match_channel_by_owner(CHANNELS, "warnerbrosepics") is WARNER
    # Fremder Owner: None statt Index-Roulette.
    assert _match_channel_by_owner(CHANNELS, "disneydescendants") is None
    assert _match_channel_by_owner(CHANNELS, None) is None


def test_grouping_collab_post_lands_on_scraped_profile_not_owner():
    """Kernfall: Collab-Post — ownerUsername ist der Co-Autor (Fremd-
    Account), inputUrl ist das gescrapte Warner-Profil. Der Post gehört
    zu Warner, nicht zum Owner und nicht auf einen Index-Sink."""
    raw_items = [
        {
            "url": "https://www.instagram.com/p/COLLAB1/",
            "ownerUsername": "mortalkombatmovie",
            "inputUrl": "https://www.instagram.com/warnerbrosepics/",
            "likesCount": 10,
        },
        {
            "url": "https://www.instagram.com/p/OWN1/",
            "ownerUsername": "xyzfilms",
            "inputUrl": "https://www.instagram.com/xyzfilms/",
            "likesCount": 5,
        },
    ]
    grouped = dict(
        (channel.handle, [i["post_url"] for i in items])
        for channel, items in _group_items_by_channel(raw_items, CHANNELS, normalize_public_item)
    )
    assert grouped["warnerbrosepics"] == ["https://www.instagram.com/p/COLLAB1"]
    assert grouped["xyzfilms"] == ["https://www.instagram.com/p/OWN1"]


def test_grouping_unresolvable_input_url_is_discarded(caplog):
    """inputUrl zeigt auf ein Profil ohne Channel-Eintrag → verwerfen +
    Log, KEINE Zuordnung an irgendeinen Channel (Index-Fallback ist weg)."""
    raw_items = [
        {
            "url": "https://www.instagram.com/p/STRAY1/",
            "ownerUsername": "bad_robot",
            "inputUrl": "https://www.instagram.com/bad_robot/",
        },
    ]
    with caplog.at_level("WARNING"):
        grouped = _group_items_by_channel(raw_items, CHANNELS, normalize_public_item)
    assert grouped == []
    assert any("channel_attribution_unresolved" in r.message for r in caplog.records)


def test_grouping_no_input_url_falls_back_to_owner_identity():
    """TikTok-Pfad: kein inputUrl im Item → Owner-Identität greift."""
    tt_channel = Channel(
        name="netflixde", platform="tiktok", handle="netflixde",
        url="https://www.tiktok.com/@netflixde",
    )
    raw_items = [
        {
            "webVideoUrl": "https://www.tiktok.com/@netflixde/video/1",
            "authorMeta": {"name": "netflixde"},
            "diggCount": 3,
        },
        # Fremder Owner ohne inputUrl → verworfen, nicht geraten.
        {
            "webVideoUrl": "https://www.tiktok.com/@fremd/video/2",
            "authorMeta": {"name": "fremdaccount"},
        },
    ]
    grouped = _group_items_by_channel(raw_items, [tt_channel], normalize_tiktok_item)
    assert len(grouped) == 1
    channel, items = grouped[0]
    assert channel is tt_channel
    assert [i["post_url"] for i in items] == ["https://www.tiktok.com/@netflixde/video/1"]
