import httpx

from app.config import settings


async def fetch_weekly_market_context(assets_count: int, titles: list[str]) -> str:
    if not settings.perplexity_api_key or assets_count == 0:
        return "Perplexity-Kontext nicht aktiv. Der Trend Snapshot basiert aktuell auf kuratierten Assets und OpenAI-Zusammenfassung."

    title_list = ", ".join(titles[:12]) if titles else "Film, Serie, Games"
    prompt = (
        "Recherchiere knapp den aktuellen öffentlichen Kontext für Film-, Serien- und Game-Marketing. "
        "Fokus: Social-Video, Trailer, Teaser, Poster, Kinetic Typography, Titelplatzierung, Release-CTA. "
        f"Relevante Titel/Franchises aus dem internen Report: {title_list}. "
        "Gib 3 bis 5 kurze Trendhinweise auf Deutsch. Keine Erfolgsbehauptungen und keine Klickzahlen."
    )

    payload = {
        "model": settings.perplexity_model,
        "messages": [
            {"role": "system", "content": "Du bist ein präziser Recherche-Assistent für Creative-Marketing-Trends."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.perplexity_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        return f"Perplexity-Kontext konnte nicht geladen werden: {exc}"
