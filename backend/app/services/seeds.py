from sqlmodel import Session, select
from app.models.entities import Channel, Market, Priority, Title, TitleKeyword

MVP_CHANNELS = [
    ("Constantin Film", "https://www.instagram.com/constantinfilm/", "constantinfilm", Market.DE, "Verleih", Priority.A),
    ("STUDIOCANAL Deutschland", "https://www.instagram.com/studiocanal.de/", "studiocanal.de", Market.DE, "Verleih", Priority.A),
    ("Warner Bros. DE", "https://www.instagram.com/warnerbrosde/", "warnerbrosde", Market.DE, "Studio", Priority.A),
    ("Universal Pictures DE", "https://www.instagram.com/universalpicturesde/", "universalpicturesde", Market.DE, "Studio", Priority.A),
    ("Sony Pictures DE", "https://www.instagram.com/sonypicturesde/", "sonypicturesde", Market.DE, "Studio", Priority.A),
    ("Paramount Pictures DE", "https://www.instagram.com/paramountpicturesde/", "paramountpicturesde", Market.DE, "Studio", Priority.A),
    ("Weltkino", "https://www.instagram.com/weltkino/", "weltkino", Market.DE, "Verleih", Priority.B),
    ("LEONINE Distribution", "https://www.instagram.com/leoninedistribution/", "leoninedistribution", Market.DE, "Verleih", Priority.B),
    ("Tobis Film", "https://www.instagram.com/tobisfilm/", "tobisfilm", Market.DE, "Verleih", Priority.B),
    ("Alamode Film", "https://www.instagram.com/alamodefilm/", "alamodefilm", Market.DE, "Verleih", Priority.B),
    ("A24", "https://www.instagram.com/a24/", "a24", Market.US, "Studio/Verleih", Priority.A),
    ("NEON", "https://www.instagram.com/neonrated/", "neonrated", Market.US, "Verleih", Priority.A),
    ("Warner Bros.", "https://www.instagram.com/warnerbros/", "warnerbros", Market.US, "Studio", Priority.A),
    ("Universal Pictures", "https://www.instagram.com/universalpictures/", "universalpictures", Market.US, "Studio", Priority.A),
    ("Paramount Pictures", "https://www.instagram.com/paramountpics/", "paramountpics", Market.US, "Studio", Priority.A),
    ("Sony Pictures", "https://www.instagram.com/sonypictures/", "sonypictures", Market.US, "Studio", Priority.A),
    ("20th Century Studios", "https://www.instagram.com/20thcenturystudios/", "20thcenturystudios", Market.US, "Studio", Priority.A),
    ("Amazon MGM Studios", "https://www.instagram.com/amazonmgmstudios/", "amazonmgmstudios", Market.US, "Studio/Streamer", Priority.A),
    ("Apple TV", "https://www.instagram.com/appletv/", "appletv", Market.INT, "Streamer", Priority.B),
    ("Netflix Film", "https://www.instagram.com/netflixfilm/", "netflixfilm", Market.INT, "Streamer", Priority.B),
]

MVP_TITLES = {
    "Mission: Impossible": ["Mission Impossible", "The Final Reckoning", "Tom Cruise", "Ethan Hunt", "MI8", "#MissionImpossible"],
    "Jurassic World": ["Jurassic", "Jurassic World", "#JurassicWorld"],
    "Superman": ["Superman", "Clark Kent", "#Superman"],
    "Fantastic Four": ["Fantastic Four", "Fantastic 4", "First Steps", "#FantasticFour"],
    "Avatar": ["Avatar", "Pandora", "#Avatar"],
    "Dune": ["Dune", "Arrakis", "Paul Atreides", "#Dune"],
    "John Wick": ["John Wick", "Ballerina", "Wick", "#JohnWick"],
    "The Last of Us": ["The Last of Us", "TLOU", "#TheLastOfUs"],
    "Stranger Things": ["Stranger Things", "Hawkins", "#StrangerThings"],
    "Grand Theft Auto": ["Grand Theft Auto", "GTA", "GTA 6", "GTA VI", "#GTAVI", "#GTA6"],
}


def seed_channels(session: Session) -> int:
    created = 0
    for name, url, handle, market, channel_type, priority in MVP_CHANNELS:
        exists = session.exec(select(Channel).where(Channel.handle == handle)).first()
        if exists:
            continue
        session.add(Channel(
            name=name,
            url=url,
            handle=handle,
            market=market,
            channel_type=channel_type,
            priority=priority,
            active=True,
            mvp=True,
        ))
        created += 1
    session.commit()
    return created


def seed_titles(session: Session) -> int:
    created = 0
    for title_name, keywords in MVP_TITLES.items():
        title = session.exec(select(Title).where(Title.title_original == title_name)).first()
        if not title:
            title = Title(
                title_original=title_name,
                franchise=title_name,
                content_type="Film/Serie/Game",
                market_relevance=Market.MIXED,
                priority=Priority.A,
                active=True,
            )
            session.add(title)
            session.commit()
            session.refresh(title)
            created += 1
        for keyword in keywords:
            exists = session.exec(
                select(TitleKeyword).where(
                    TitleKeyword.title_id == title.id,
                    TitleKeyword.keyword == keyword,
                )
            ).first()
            if not exists:
                session.add(TitleKeyword(title_id=title.id, keyword=keyword))
    session.commit()
    return created
