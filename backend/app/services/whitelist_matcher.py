from sqlmodel import Session, select
from app.models.entities import Title, TitleKeyword


def find_title_matches(session: Session, text: str | None) -> list[Title]:
    if not text:
        return []
    haystack = text.lower()
    matches: list[Title] = []
    keywords = session.exec(select(TitleKeyword).where(TitleKeyword.active == True)).all()  # noqa: E712
    for item in keywords:
        if item.keyword.lower() in haystack:
            title = session.get(Title, item.title_id)
            if title and title.active and title not in matches:
                matches.append(title)
    return matches
