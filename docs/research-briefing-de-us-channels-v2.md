# Recherche-Briefing v2.2 — DE/US-Channel-Whitelist-Erweiterung
## Creative Radar Backlog-Sprint, Mai 2026

> **Adressat dieser Recherche:** Claude Research / ChatGPT Deep
> Research / Perplexity Pro. Ergebnis: priorisierte Liste neuer
> Social-Media-Channels für die Whitelist von creative-radar.de.

---

## Geschäftskontext (3-Satz-Erklärung für den Agent)

Creative Radar ist eine KI-gestützte Social-Media-Monitoring-
Plattform für Film/Serien-Marketing, deren Kern-Use-Case der
**DE+US-Marketing-Vergleich** ist: wie unterscheidet sich die
Vermarktung desselben Films im deutschen Markt vs US-/Global-
Markt? Die Plattform scrapt offizielle Studio-, Verleih- und
Streaming-Plattform-Accounts auf Instagram, TikTok und YouTube,
extrahiert per Vision-Pipeline strukturierte Erkenntnisse zu
Asset-Typen (Trailer, Poster, Tickets-CTA, Behind the Scenes
etc.), Kinetics, Title-Placement und Creative-Mechaniken.
**Ohne systematische DE+US-Channel-Pairs liefert die Plattform
keinen Vergleich — und genau das ist das Problem heute.**

---

## Aktueller Zustand (Reality-Check)

**Datenanalyse 2026-05-04:**
- 311 Assets in der DB
- Nur **27 Assets** (8.7%) haben einen Cross-Market-Match-Key
- Nur **2 Match-Keys** mit Assets aus ≥2 Märkten
- Davon nur **ein einziges echtes DE+US-Cross-Market-Pair**
  ("Der Teufel trägt Prada 2")
- Format-Inkonsistenz: heute via PR #67 behoben (alle 24 Werte
  in Slug-Form, Schreib-Pfade vereinheitlicht)

**Ursache:** Whitelist hat 55 IG-Channels, aber wenige
systematische DE+US-Studio-Paarungen. Ein DE-Channel ohne
US-Pendant kann nie Cross-Market-Pairing liefern.

---

## Phase 1 — Datenanalyse (Wolf-Aktion VOR der Recherche)

> **Wichtig: Phase 1 ist verpflichtender Vorab-Schritt.** Die
> Recherche darf NICHT generisch laufen, weil die Priorisierung
> sonst falsch wird. Ein produktiver kleiner Channel könnte
> übersehen, ein großer aber inaktiver zu hoch gewichtet
> werden. Reality-Daten schlagen Bauchgefühl.

### Schritt 1.1: Top-produktive Channels (letzte 30 Tage)

Container-Python via `railway ssh`:

```python
python3 << 'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("""
    SELECT
      c.handle,
      c.market,
      c.platform,
      c.channel_type,
      COUNT(DISTINCT a.id) AS total_assets,
      COUNT(DISTINCT a.id) FILTER (WHERE a.title_id IS NOT NULL) AS assets_with_title,
      COUNT(DISTINCT a.title_id) AS unique_titles,
      COUNT(DISTINCT a.de_us_match_key) FILTER (WHERE a.de_us_match_key IS NOT NULL) AS unique_match_keys
    FROM creative_radar.channel c
    LEFT JOIN creative_radar.post p ON p.channel_id = c.id
    LEFT JOIN creative_radar.asset a ON a.post_id = p.id
    WHERE p.detected_at > NOW() - INTERVAL '30 days'
       OR p.detected_at IS NULL
    GROUP BY c.handle, c.market, c.platform, c.channel_type
    ORDER BY total_assets DESC NULLS LAST
    LIMIT 30;
""")
print(f"{'Handle':<35s} {'Market':<8s} {'Platform':<10s} {'Type':<20s} "
      f"{'Assets':>7s} {'Titles':>7s} {'UniqueT':>8s} {'MatchK':>7s}")
print("-" * 130)
for row in cur.fetchall():
    handle, market, platform, ctype, total, with_title, unique_t, unique_m = row
    print(f"{handle or '-':<35s} {market or '-':<8s} {platform or '-':<10s} "
          f"{(ctype or '-')[:18]:<20s} {total or 0:>7d} {with_title or 0:>7d} "
          f"{unique_t or 0:>8d} {unique_m or 0:>7d}")

cur.close()
conn.close()
PYEOF
```

### Schritt 1.2: Channel-Coverage-Lücken pro Markt+Plattform

```python
python3 << 'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("""
    SELECT
      market,
      platform,
      COUNT(*) AS channel_count,
      COUNT(*) FILTER (WHERE channel_type LIKE '%Studio%' OR channel_type LIKE '%Verleih%') AS studio_count,
      COUNT(*) FILTER (WHERE channel_type LIKE '%Streaming%' OR channel_type LIKE '%Plattform%') AS streaming_count
    FROM creative_radar.channel
    GROUP BY market, platform
    ORDER BY market, platform;
""")
print(f"{'Market':<8s} {'Platform':<10s} {'Total':>6s} {'Studio':>7s} {'Streaming':>10s}")
print("-" * 50)
for row in cur.fetchall():
    print(f"{row[0] or '-':<8s} {row[1] or '-':<10s} {row[2]:>6d} {row[3]:>7d} {row[4]:>10d}")

cur.close()
conn.close()
PYEOF
```

### Schritt 1.3: Aktuelle Whitelist als Liste (alle 3 Plattformen)

```python
python3 << 'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

for plat in ['instagram', 'tiktok', 'youtube']:
    cur.execute("""
        SELECT handle, market, channel_type, channel_priority
        FROM creative_radar.channel
        WHERE platform = %s
        ORDER BY market, channel_type, handle;
    """, (plat,))
    rows = cur.fetchall()
    print(f"\n=== {plat.upper()} ({len(rows)} Channels) ===")
    print(f"{'Handle':<35s} {'Market':<8s} {'Type':<25s} {'Priority':<5s}")
    print("-" * 80)
    for row in rows:
        print(f"{row[0]:<35s} {row[1] or '-':<8s} {(row[2] or '-')[:23]:<25s} {row[3] or '-':<5s}")

cur.close()
conn.close()
PYEOF
```

### Phase-1-Output einfügen (HIER vor Versand an Agent)

> **Wolf-Aktion:** Output von 1.1, 1.2, 1.3 hier hineinkopieren,
> bevor das Briefing an den Recherche-Agent geht. Ohne diese
> Daten läuft die Recherche im Blindflug.

```
=== PHASE-1-OUTPUT (erhoben 2026-05-05 via railway ssh) ===

--- 1.0 SMOKE-TEST MATCH-KEY-KONSISTENZ ---

Raw-Form:      0 (erwartet: 0)
Slug-Form:     27
Total mit Key: 27
Konsistenz:    OK

→ PR #67 (Match-Key-Slug-Migration) ist sauber durchgelaufen.

--- 1.1 TOP-30 PRODUKTIVSTE CHANNELS (letzte 30 Tage) ---

Handle                              Market   Platform   Type                  Assets  Titles  UniqueT  MatchK
----------------------------------------------------------------------------------------------------------------------------------
xyzfilms                            INT      instagram  Verleih/Produktion        40       1        1       0
wildbunchfilmlounge                 INT      instagram  Verleih/Produktion        13       0        0       0
20thcenturystudios                  US       instagram  Studio/Verleih            13       1        1       7
@netflix                            UNKNOWN  youtube    -                         10       0        0       0
warnerbros                          US       tiktok     Studio/Verleih             9       2        2       4
warnerbrosepics                     US       instagram  Studio/Verleih             9       1        1       1
20thcenturystudiosde                DE       instagram  Studio/Verleih             8       6        1       4
wowtv                               DE       instagram  Streamer                   8       0        0       0
sonypicturesaustria                 DE       instagram  Studio/Verleih             7       0        0       0
skydeutschland                      DE       instagram  Streamer                   7       0        0       0
disneyplusde                        DE       instagram  Streamer                   7       2        2       0
paramount_pictures_germany          DE       instagram  Studio/Verleih             7       0        0       1
primevideode                        DE       instagram  Streamer                   7       0        0       0
paramountplusde                     DE       instagram  -                          7       0        0       1
warnerbrosentertainment             US       instagram  Studio/Verleih             7       1        1       2
starwars                            INT      instagram  Verleih/Produktion         6       0        0       0
piffl_medien                        INT      instagram  Verleih/Produktion         6       1        1       0
tobisfilm                           DE       instagram  Verleih/Produktion         6       0        0       0
wowtvde                             DE       instagram  -                          6       1        1       0
universalpicturesde                 DE       instagram  Studio/Verleih             5       0        0       0
rialtofilm                          INT      instagram  Verleih/Produktion         5       0        0       0
plaionpictures                      INT      instagram  Game Publisher             5       0        0       0
plaion_official                     INT      instagram  Game Publisher             5       0        0       0
primevideo                          US       instagram  Streamer                   5       0        0       1
sonypictures.de                     DE       instagram  -                          5       0        0       0
weltkinofilmverleih                 DE       instagram  Verleih/Produktion         5       0        0       0
pantaleonfilms                      INT      instagram  Verleih/Produktion         5       0        0       0
warnerbrosde                        DE       instagram  Studio/Verleih             5       1        1       0
studiocanal                         INT      instagram  Studio/Verleih             5       0        0       0
squarepegfilms                      INT      instagram  Verleih/Produktion         5       0        0       0

→ Beobachtung Top-30: 22 von 30 Channels haben 0 Match-Keys.
   Match-Keys konzentrieren sich auf 8 Channels mit klarer
   DE+US-Pairing-Struktur (20thcenturystudios×de, warnerbros-Familie,
   paramount_pictures_germany, paramountplusde, primevideo).

--- 1.2 COVERAGE-VERTEILUNG (erweitert um Schema-Felder) ---

Market   Platform    Total   S/V  Strmr  Game  Unclass  Active  Monit   MVP
--------------------------------------------------------------------------------
DE       instagram      34    21      7     2        4      34     34    34
DE       youtube         5     0      0     0        5       5      5     5
US       instagram      25    16      8     0        1      25     25    25
US       tiktok          7     2      0     0        5       7      7     7
US       youtube        14     0      0     0       14      14     14    14
INT      instagram      46    43      1     2        0      46     46    46
UNKNOWN  youtube         1     0      0     0        1       1      1     1

GESAMT: 132 Channels — IG: 105 (80%) | YT: 20 (15%) | TT: 7 (5%)

Priority/Quality-Tier-Verteilung:
Priority        Quality-Tier     Count
----------------------------------------
A               P1                  43
B               P1                  43
C               P1                  46

→ Alle 132 Channels: quality_tier=P1, active=true, monitored=true,
   mvp=true. Tier-Differenzierung wird heute nicht zur Steuerung
   genutzt — Priority A/B/C ist gleichverteilt aber semantisch ungenutzt.

--- 1.3 AKTUELLE WHITELIST KOMPLETT ---

=== INSTAGRAM (105 Channels) ===
Handle                              Market   Type
----------------------------------------------------------------------
plaion_dach                         DE       Game Publisher
plaion_de                           DE       Game Publisher
discoveryplusde                     DE       Streamer
disneyplusde                        DE       Streamer
hbomaxde                            DE       Streamer
netflixde                           DE       Streamer
primevideode                        DE       Streamer
skydeutschland                      DE       Streamer
wowtv                               DE       Streamer
20thcenturystudiosde                DE       Studio/Verleih
disneydeutschland                   DE       Studio/Verleih
leoninestudios                      DE       Studio/Verleih
paramount_pictures_germany          DE       Studio/Verleih
sonypicturesaustria                 DE       Studio/Verleih
sonypicturesde                      DE       Studio/Verleih
studiocanalde                       DE       Studio/Verleih
universalpicturesau                 DE       Studio/Verleih
universalpicturesde                 DE       Studio/Verleih
warnerbrosde                        DE       Studio/Verleih
arsenal.filmverleih                 DE       Verleih/Produktion
constantinfilm                      DE       Verleih/Produktion
eksystent_filmverleih               DE       Verleih/Produktion
farbfilmverleih                     DE       Verleih/Produktion
grandfilm_verleih                   DE       Verleih/Produktion
natgeodeutschland                   DE       Verleih/Produktion
pandorafilmverleih                  DE       Verleih/Produktion
starwars_de                         DE       Verleih/Produktion
tobisfilm                           DE       Verleih/Produktion
vueltagermany                       DE       Verleih/Produktion
weltkinofilmverleih                 DE       Verleih/Produktion
paramountplusde                     DE       -
sonypictures.de                     DE       -
studiocanal.de                      DE       -
wowtvde                             DE       -
disneyplus                          US       Streamer
hbo                                 US       Streamer
hbomax                              US       Streamer
hulu                                US       Streamer
netflix                             US       Streamer
netflixfilm                         US       Streamer
paramountplus                       US       Streamer
primevideo                          US       Streamer
20thcenturystudios                  US       Studio/Verleih
amazonmgmstudios                    US       Studio/Verleih
disney                              US       Studio/Verleih
disneychannel                       US       Studio/Verleih
disneystudios                       US       Studio/Verleih
marvel                              US       Studio/Verleih
marvelstudios                       US       Studio/Verleih
paramountpics                       US       Studio/Verleih
pixar                               US       Studio/Verleih
sonypictures                        US       Studio/Verleih
universalhorror                     US       Studio/Verleih
universalpictures                   US       Studio/Verleih
warnerbros                          US       Studio/Verleih
warnerbrosentertainment             US       Studio/Verleih
warnerbrosepics                     US       Studio/Verleih
a24                                 US       Verleih/Produktion
streamonmax                         US       -
plaion_official                     INT      Game Publisher
plaionpictures                      INT      Game Publisher
appletv                             INT      Streamer
anapurna                            INT      Studio/Verleih
capelightpictures                   INT      Studio/Verleih
elarapictures                       INT      Studio/Verleih
elevation_pics                      INT      Studio/Verleih
magnoliapics                        INT      Studio/Verleih
orionpictures                       INT      Studio/Verleih
searchlightpics                     INT      Studio/Verleih
studiocanal                         INT      Studio/Verleih
24bilder                            INT      Verleih/Produktion
akkordfilm                          INT      Verleih/Produktion
alpenrepublik                       INT      Verleih/Produktion
bad_robot                           INT      Verleih/Produktion
blumhouse                           INT      Verleih/Produktion
dcmfilm                             INT      Verleih/Produktion
film4                               INT      Verleih/Produktion
flare.film.berlin                   INT      Verleih/Produktion
hanway_films                        INT      Verleih/Produktion
independentfilmco                   INT      Verleih/Produktion
legendary                           INT      Verleih/Produktion
lionsgate                           INT      Verleih/Produktion
lionsgatehorror                     INT      Verleih/Produktion
lionsgateuk                         INT      Verleih/Produktion
lucasfilm                           INT      Verleih/Produktion
madmanfilms                         INT      Verleih/Produktion
majestic.film                       INT      Verleih/Produktion
meteor_film                         INT      Verleih/Produktion
mfa_film                            INT      Verleih/Produktion
mgmplus                             INT      Verleih/Produktion
natgeo                              INT      Verleih/Produktion
neue_visionen                       INT      Verleih/Produktion
pantaleonfilms                      INT      Verleih/Produktion
piffl_medien                        INT      Verleih/Produktion
portauprincefilms                   INT      Verleih/Produktion
rialtofilm                          INT      Verleih/Produktion
riseandshinecinema                  INT      Verleih/Produktion
rusticfilms                         INT      Verleih/Produktion
sabanfilms                          INT      Verleih/Produktion
seruanimation                       INT      Verleih/Produktion
squarepegfilms                      INT      Verleih/Produktion
starwars                            INT      Verleih/Produktion
verticalentertainment               INT      Verleih/Produktion
wildbunchfilmlounge                 INT      Verleih/Produktion
xyzfilms                            INT      Verleih/Produktion

=== TIKTOK (7 Channels) ===
Handle                              Market   Type
----------------------------------------------------------------------
warnerbros                          US       Studio/Verleih
warnerbros                          US       Studio/Verleih   ← DUPLIKAT-EINTRAG
disney                              US       -
netflix                             US       -
paramountpics                       US       -
sonypictures                        US       -
universalpictures                   US       -

=== YOUTUBE (20 Channels) ===
Handle                              Market   Type
----------------------------------------------------------------------
ConstantinFilm                      DE       -
NetflixDE                           DE       -
SonyPicturesGermany                 DE       -
UniversalPicturesDE                 DE       -
WarnerBrosDE                        DE       -
20thCenturyStudios                  US       -
AppleTV                             US       -
DisneyPlus                          US       -
LionsgateMovies                     US       -
Max                                 US       -
Netflix                             US       -
ParamountPictures                   US       -
ParamountPlus                       US       -
PrimeVideo                          US       -
SonyPicturesEntertainment           US       -
STUDIOCANAL                         US       -
UniversalPictures                   US       -
WaltDisneyStudios                   US       -
WarnerBrosPictures                  US       -
@netflix                            UNKNOWN  -

--- DATEN-HYGIENE-FINDINGS (für Whitelist-Update-Sprint zu fixen) ---

H1: TT-Duplikat: warnerbros US zweimal in TikTok-Whitelist
H2: TT unklassifiziert: 5/7 Channels ohne channel_type
H3: YT unklassifiziert: 20/20 Channels ohne channel_type (strukturell)
H4: DE-IG unklassifiziert: paramountplusde, sonypictures.de,
    studiocanal.de, wowtvde
H5: streamonmax (US-IG) ohne channel_type
H6: @netflix UNKNOWN auf YouTube (sollte INT oder US sein)
H7: Handle-Inkonsistenz: YouTube CamelCase ("ConstantinFilm")
    vs Instagram lowercase ("constantinfilm") — könnte
    Cross-Platform-Matching erschweren falls handle-basiert
H8: quality_tier strukturell ungenutzt (alle P1)
H9: priority A/B/C gleichverteilt aber als Steuerungslogik
    de facto inaktiv

--- KONTEXT FÜR DEN RECHERCHE-AGENT ---

Diese 132 Channels sind die heutige Whitelist. Plattform-
Imbalance ist offensichtlich: Instagram dominiert (105),
TikTok ist strukturell unterversorgt (7, davon nur US),
YouTube ist breit aber unklassifiziert.

DE+US-Pairing-Potenzial: Heute bilden sich nur 27
Match-Keys, davon nur ein einziges echtes Cross-Market-Pair.
Der Recherche-Agent soll Lücken zur DE+US-Symmetrie schließen,
TikTok-DE-Whitelist neu aufbauen (heute 0 Channels) und
YouTube-Coverage erweitern (heute extrem dünn in DE).

=== ENDE PHASE-1-OUTPUT ===
```

---

## Phase 2 — Markt-Priorisierung (Tier-System)

> **Achtung: Drei Tiers verschiedener Wichtigkeit. Nicht
> gleichgewichten.**

### Markt-Tier-A — Kern-Use-Case (höchste Priorität)

**DE + US.** Das ist der wirtschaftliche Kern von Creative
Radar. Hier mindestens 70% der Recherche-Zeit investieren.

Konkrete Zielzahl: **15-25 neue Channels** in DE+US-Pairings.

### Markt-Tier-B — Sekundär (mittlere Priorität)

**UK + andere englischsprachige Märkte mit eigener Marketing-
Identität.**

Begründung: UK-Marketing ist oft eigenständig vom US-Marketing
und parallel zum DE-Markt (gleiche Release-Termine). Beispiele:
- `marvelukandireland`
- `universalpicturesuk`
- `disneystudiosuk`
- Kanadische Studios (selten relevant — meistens identisch mit US)
- Australische Studios (selten relevant)

Konkrete Zielzahl: **5-10 neue Channels** als optionale
Cross-Market-Quellen.

### Markt-Tier-C — Bedingt (niedrige Priorität)

**Global-Hubs nur wenn sie messbar anderes Material posten
als US-Pendant.**

Beispiele die zu prüfen sind:
- `marvelentertainment` vs `marvelstudios` — postet
  Marvel-Entertainment substantiell andere Inhalte? Falls ja:
  aufnehmen. Falls fast identisch zum US-Pendant: skip.
- `disney` vs `disneyplus` — Branding vs. Streaming-Releases.
  Klare Differenzierung? Falls ja: beide. Falls Doublette:
  nur eines.

Konkrete Zielzahl: **0-5 Channels** nur bei klar
unterschiedlichem Content.

---

## Phase 3 — Plattform-Gewichtung

> **Wichtig: Plattformen NICHT gleichgewichten. Verschiedene
> Coverage-Lücken-Größen, verschiedene Erweiterungs-Bedarfe.**

### Plattform-Tier-Instagram (tiefe Recherche, viele Lücken)

- **Heutige Coverage:** 55 Channels, produktive Plattform
- **Problem:** unsystematische DE+US-Pairings
- **Recherche-Fokus:** Komplettierung von Pairs
  (wenn DE-Account drin, US-Account suchen — und umgekehrt)
- **Konkrete Zielzahl: 15-25 neue Channels**

### Plattform-Tier-TikTok (mittel-tiefe Recherche, große Lücken)

- **Heutige Coverage:** 3 Channels, lächerlich klein
- **Problem:** Whitelist deckt TikTok-Marketing nicht ab,
  obwohl Plattform für Film-Promotion (besonders Gen-Z-Filme)
  zunehmend wichtig wird
- **Recherche-Fokus:** Substanzielle Erweiterung; pro
  empfohlenem IG-Studio prüfen ob es einen aktiven TikTok-
  Account gibt
- **Konkrete Zielzahl: 10-15 neue Channels**

### Plattform-Tier-YouTube (mittlere Recherche, mittlere Lücken)

- **Heutige Coverage:** unklar (in Cron-Daten nicht aktiv
  sichtbar)
- **Problem:** Vision-Pipeline für YT fehlt sowieso (Backlog-
  Item), heute weniger akut
- **Recherche-Fokus:** Trailer-spezifische YT-Channels von
  Studios mit regelmäßigen Uploads
- **Konkrete Zielzahl: 5-10 neue Channels**

**Gesamt-Zielzahl: 30-50 neue Channels über alle Plattformen.**

---

## Phase 4 — Recherche-Aufgaben

Mit Phase-1-Daten + Tier-System hat der Agent klare
Priorisierung. Jetzt die eigentliche Recherche.

### Aufgabe 4.1 — Direct-Pair-Matching (Markt-Tier-A)

Für jeden der Top-produktiven Channels aus Phase 1 (insbesondere
`channel_priority='A'` und Studio/Verleih/Streaming):

**Wenn DE-Channel:** existiert ein US/Global-Pendant mit
analoger Account-Struktur?

Beispiel-Suchen:
- `disneyplusde` → existiert `disneyplus` auf IG? Aktivitätslevel?
- `netflixde` → `netflix` ist trivial, aber: wie aktiv ist
  `netflix` mit Film/Serien-Posts vs allgemeine Brand-Posts?
- `wowtv` → existiert ein US-Pendant von Sky/Showtime mit
  vergleichbarem Posting-Stil?
- `tobisfilm` → DE-Verleih für US-Filme; gibt's dort
  systematische US-Quelle (oft kein direktes Pendant, weil
  Tobis-Verleih Mischportfolio hat)?

**Wenn US/Global-Channel:** existiert ein DE-Pendant?

Beispiel-Suchen:
- `warnerbros` → `wbpicturesgermany`? `warnerbrosde`?
- `20thcenturystudios` → `20thcenturystudiosde` ist da, aber
  gibt es weitere DE-Sub-Accounts (z.B. franchisespezifisch)?

**Output-Format pro Pair:**

```
Pair: <de-handle> ↔ <us-handle>
- DE-Account: <handle>, <follower-count>, <posting-frequency>,
  <last-active-date>
- US-Account: <handle>, <follower-count>, <posting-frequency>,
  <last-active-date>
- Asset-Overlap-Wahrscheinlichkeit: <hoch/mittel/niedrig>
  (basierend auf: posten beide aktuelle Filme? Selbe Franchises?
  Selbe Release-Kalender?)
- Aufnahme-Empfehlung: <ja/nein/conditional>
- Risiko: <z.B. "US-Account zu generisch, postet wenig
  film-spezifisch", "DE-Account inaktiv seit 6 Monaten">
```

### Aufgabe 4.2 — Franchise-Pair-Matching (Markt-Tier-A)

Studios mit unterschiedlichen Account-Strukturen, aber gleichen
Film-Inhalten:

Beispiele:
- A24: hat einen US-Account, aber DE-Verleih ist meist
  Capelight Pictures oder Plaion Pictures — gibt's deren
  Social-Accounts mit aktiven A24-Film-Posts?
- Neon (US-Indie-Studio): wer macht den DE-Vertrieb? Existiert
  dort ein aktiver Channel?
- Lionsgate: in DE oft Universum Film oder Constantin Film —
  welche Channels?

**Output-Format pro Franchise-Pair:**

```
Franchise: <Beispiel-Filmtitel>
- US-Studio: <name>, <handle>
- DE-Verleih: <name>, <handle> (oder "kein aktiver Channel
  identifiziert")
- Match-Mechanismus: <z.B. "title_slug-basiert">
- Aufnahme-Empfehlung: <ja/nein>
```

### Aufgabe 4.3 — UK + andere Tier-B-Märkte

Pro recherchiertem US-Channel auch prüfen:

- Existiert ein UK-Sub-Account? (`marvelukandireland`,
  `universalpicturesuk`, `paramountukandireland`)
- Aktivitätslevel UK vs US? Eigenständiges Marketing?
- Welche UK-Pendants haben DE-Pairing-Potential (gleicher
  Release-Kalender)?

### Aufgabe 4.4 — Streaming-Plattform-Coverage (Markt-Tier-A)

Streaming-Plattformen sind oft Cross-Market wertvoll, weil
gleiche Inhalte (z.B. Stranger Things) parallel beworben werden:

Recherche pro Plattform:
- **Netflix:** US-Hauptaccount, DE-Account, ggf. Genre-Sub-
  Accounts wie `netflixfilm`, `netflixqueue`, `strongblacklead`
- **Disney+:** Hauptaccount + Film-spezifische Sub-Accounts
  wie `marvelstudios`, `starwars`
- **Amazon Prime Video:** US: `primevideo`, DE: `primevideode`
  ist drin — gibt's `amazonmgmstudios` oder ähnliches?
- **Apple TV+:** `appletvplus` global, gibt's DE-Variante?
- **HBO/Max:** US: `streamonmax`, früher `hbo` — aktuelle
  Struktur?
- **Paramount+:** US und DE-Variante?

### Aufgabe 4.5 — TikTok-Coverage (Plattform-Tier-TikTok)

TikTok hat heute nur 3 Channels in der Whitelist. Recherche:
- Welche der DE+US-Studios aus 4.1-4.4 haben aktive TikTok-
  Pendants?
- Gibt es TikTok-only-Accounts die im Film-Marketing-Kontext
  relevant sind (z.B. `screenrant` für Trailer-Reactions,
  Studio-eigene TikToks)?
- Welche Major-Studios haben **keine** TikTok-Präsenz und
  sollten daher übersprungen werden?

**Output:** Top-15-TikTok-Channels mit Empfehlung.

### Aufgabe 4.6 — YouTube-Coverage (Plattform-Tier-YouTube)

YouTube ist heute primär für Trailer relevant. Recherche:
- Welche Studios haben aktive YouTube-Channels mit regelmäßigen
  Trailer-/Featurette-Uploads in DE+US?
- Aufnahme-Empfehlung für Top-10.

### Aufgabe 4.7 — Discovery-Channels (Markt-Tier-übergreifend, optional)

Channels die nicht direkt einem Studio gehören, aber hohe
Trend-Sichtbarkeit für DE/US-Vergleich liefern:

- `moviepilot.de` — DE-Marktstandard-Tracker
- `thefilmstage`, `indiewire` — US-Trade
- TMDB-eigene Accounts?
- Letterboxd-eigene Accounts?

**Vorsicht:**
- Keine Personal-Influencer-Accounts (zu unstrukturiert)
- Keine Fan-Accounts (rechtliche Grauzone)
- Keine Aggregator-Apps wie Letterboxd/IMDB (bereits via
  TMDB-API abgedeckt)

**Output:** 3-7 Discovery-Channels mit klarer Markierung
"Discovery-Mode" und Empfehlung welche `is_discovery=True`
geflaggt werden sollen.

---

## Phase 5 — Strukturiertes Output-Format

Der Recherche-Agent soll das Endergebnis in genau diesem
Format liefern:

```markdown
# DE/US-Channel-Recherche — Ergebnis

## Executive Summary
- Recherchierte Channels gesamt: <N>
- Empfohlen für Aufnahme: <N>
  - Markt-Tier-A (DE+US Direct/Franchise Pairs): <N>
  - Markt-Tier-B (UK + Tier-B): <N>
  - Markt-Tier-C (Global-Hubs bedingt): <N>
- Empfehlungen pro Plattform:
  - Instagram: <N>
  - TikTok: <N>
  - YouTube: <N>
- Erwarteter Gewinn an DE+US-Match-Pairs: <Schätzung>

## Empfehlungs-Tabelle

| Handle              | Platform | Market | Type   | Markt-Tier | Plattform-Tier | Pair mit       | Priorität | Aktivität | Notiz |
|---------------------|----------|--------|--------|------------|----------------|----------------|-----------|-----------|-------|
| disneyplus          | IG       | US     | Stream | A          | IG             | disneyplusde   | A         | aktiv     | ...   |
| ...                 | ...      | ...    | ...    | ...        | ...            | ...            | ...       | ...       | ...   |

## Detailbefunde pro Channel
[Pro empfohlenen Channel: ein Absatz mit Begründung, Risiko,
Posting-Frequenz, Asset-Erwartung]

## Nicht-Empfehlungen
[Channels die geprüft, aber nicht empfohlen wurden — mit
Begründung, damit nicht später nochmal recherchiert wird]

## Quellen
[Liste der Quellen/URLs, an denen Recherche stattgefunden hat]
```

---

## Strategische Hinweise für den Recherche-Agent

**Worauf besonders achten:**

1. **Aktivität ist Pflicht.** Ein DE-Account der seit 6 Monaten
   nichts gepostet hat ist wertlos. Cron-System holt Posts der
   letzten 7-14 Tage. Mindestens 2-3 Posts pro Woche sind
   wünschenswert.

2. **Film-Marketing-Fokus.** Generische Brand-Accounts
   (z.B. `disney` als allgemeines Branding) sind weniger
   wertvoll als film-spezifische Sub-Accounts (`disneyplus`
   für aktuelle Releases).

3. **Account-Konsistenz.** Manche Studios wechseln
   Account-Strategien — z.B. `hbomax` wurde zu `streamonmax`.
   Aktuelle Handles verwenden, alte als deprecated markieren.

4. **Sprache verifizieren.** Manche DE-Accounts posten primär
   auf Englisch (Marketing-Strategie). Das ist OK, solange
   `channel_market='DE'` korrekt ist (Markt-Zuordnung, nicht
   Sprach-Zuordnung).

5. **Pairing-Wahrscheinlichkeit ist das Hauptkriterium.** Ein
   eigenständiger US-Account ohne DE-Pendant bringt für DE+US-
   Cross-Market-Vergleich wenig Wert. Lieber einen weniger
   großen Account aufnehmen, der ein klares Pairing ergibt,
   als einen riesigen Solitär.

6. **Nicht-empfohlene Channels begründen.** Wenn ein Account
   geprüft und nicht empfohlen wird, kurzer Grund — verhindert
   dass später jemand denselben Account nochmal vorschlägt.

**Was NICHT recherchiert werden soll:**

- Personal-Influencer-Accounts (zu unstrukturiert)
- Fan-Accounts (rechtliche Grauzone)
- Nicht-Film/Serien-Inhalte
- Aggregator-Apps wie Letterboxd/IMDB (bereits via TMDB-API
  abgedeckt)
- Nationale Märkte außerhalb Tier-A/B (Asien, LATAM) —
  separater Recherche-Sprint falls je gewollt

---

## Tool-Empfehlungen

**Empfohlener Workflow:**

1. **Claude Research** für die Hauptrecherche (Aufgaben 4.1-4.7)
   - Stärke: strukturierter Markdown-Output, gute Quellen-Zitate
   - Anweisung: dieses Briefing in voller Länge inkl. Phase-1-
     Output als Eingabe geben

2. **Perplexity Pro** für Live-Verifikation
   - Pro empfohlenem Channel: ist der Account aktuell aktiv?
   - Letzte 5 Posts der letzten 30 Tage?
   - Schneller als manuelle Channel-Besuche

3. **ChatGPT Deep Research** als Alternative oder Ergänzung
   - Falls Claude Research zu wenig Tiefe liefert
   - Stärke: sehr lange detaillierte Berichte

4. **Manuelle IG-/TikTok-/YT-Verifikation**
   - Stichprobe der Top-10-Empfehlungen direkt im Browser
   - 5-10 Min, prüft ob die Recherche-Ergebnisse stimmen

---

## Erwarteter Output-Umfang

- 60-100 recherchierte Channels (mehr als v1, weil 3 Plattformen)
- 30-50 empfohlene Channels (verteilt über die drei
  Plattform-Tiers)
- 5-8 Seiten strukturierter Markdown-Output
- Aufwand für Recherche-Agent: 45-75 Min Echtzeit
- Wolf-Reading-Time: 20-30 Min

---

## Nach erfolgreicher Recherche — Folge-Sprints

### Folge-Sprint 1: WHITELIST-UPDATE (~30-45 Min Code+SQL)

```sql
-- Beispiel-Insert pro neuem Channel
INSERT INTO creative_radar.channel
  (handle, market, platform, channel_type, channel_priority, ...)
VALUES
  ('disneyplus', 'US', 'instagram', 'Streaming/Plattform', 'A', ...),
  ('netflix', 'US', 'instagram', 'Streaming/Plattform', 'A', ...),
  ...;
```

Plus: nächsten Cron-Trigger manuell starten, neue Channels
werden gescraped, neue Assets fließen rein.

### Folge-Sprint 2: REALITY-CHECK 14 Tage später

```sql
SELECT COUNT(DISTINCT de_us_match_key)
FROM creative_radar.asset a
LEFT JOIN creative_radar.post p ON p.id = a.post_id
LEFT JOIN creative_radar.channel c ON c.id = p.channel_id
WHERE a.de_us_match_key IS NOT NULL
GROUP BY a.de_us_match_key
HAVING COUNT(DISTINCT c.market) >= 2;
```

Wenn das Ergebnis ≥ 10 ist → Sprint 5.3.7 (Frontend-Pairing-
View) lohnt sich endlich. Vorher nicht.

---

## Versions-Historie dieses Briefings

- **v1**: Initial-Briefing, IG-fokussiert, kein Markt-Tier
- **v2**: Markt-Tier-System (DE+US/UK+/Global), Plattform-
  Gewichtung mit konkreten Zielzahlen, Phase 1 als zwingend
  markiert, Geschäftskontext-Block hinzugefügt
- **v2.1**: Format-Bug in Query 1.1 gefixt (`unique_titles` und
  `unique_match_keys` waren in `print()` verloren); Match-Key-
  Konsistenz-Folge-Sprint entfernt (durch PR #67 erledigt);
  Reality-Check-Block aktualisiert (Format-Inkonsistenz beseitigt)
- **v2.2 (aktuell)**: Phase-1-Output eingefügt (erhoben
  2026-05-05 via railway ssh). 132 Channels heute (105 IG /
  20 YT / 7 TT). 27 Match-Keys total. 9 Daten-Hygiene-Findings
  dokumentiert (TT-Duplikat, YT komplett unklassifiziert,
  DE-IG-Klassifikations-Lücken, Handle-Case-Inkonsistenzen,
  quality_tier strukturell ungenutzt). Briefing ist
  recherchefertig.
# Recherche-Briefing v2.2 — DE/US-Channel-Whitelist-Erweiterung
## Creative Radar Backlog-Sprint, Mai 2026

> **Adressat dieser Recherche:** Claude Research / ChatGPT Deep
> Research / Perplexity Pro. Ergebnis: priorisierte Liste neuer
> Social-Media-Channels für die Whitelist von creative-radar.de.

---

## Geschäftskontext (3-Satz-Erklärung für den Agent)

Creative Radar ist eine KI-gestützte Social-Media-Monitoring-
Plattform für Film/Serien-Marketing, deren Kern-Use-Case der
**DE+US-Marketing-Vergleich** ist: wie unterscheidet sich die
Vermarktung desselben Films im deutschen Markt vs US-/Global-
Markt? Die Plattform scrapt offizielle Studio-, Verleih- und
Streaming-Plattform-Accounts auf Instagram, TikTok und YouTube,
extrahiert per Vision-Pipeline strukturierte Erkenntnisse zu
Asset-Typen (Trailer, Poster, Tickets-CTA, Behind the Scenes
etc.), Kinetics, Title-Placement und Creative-Mechaniken.
**Ohne systematische DE+US-Channel-Pairs liefert die Plattform
keinen Vergleich — und genau das ist das Problem heute.**

---

## Aktueller Zustand (Reality-Check)

**Datenanalyse 2026-05-04:**
- 311 Assets in der DB
- Nur **27 Assets** (8.7%) haben einen Cross-Market-Match-Key
- Nur **2 Match-Keys** mit Assets aus ≥2 Märkten
- Davon nur **ein einziges echtes DE+US-Cross-Market-Pair**
  ("Der Teufel trägt Prada 2")
- Format-Inkonsistenz: heute via PR #67 behoben (alle 24 Werte
  in Slug-Form, Schreib-Pfade vereinheitlicht)

**Ursache:** Whitelist hat 55 IG-Channels, aber wenige
systematische DE+US-Studio-Paarungen. Ein DE-Channel ohne
US-Pendant kann nie Cross-Market-Pairing liefern.

---

## Phase 1 — Datenanalyse (Wolf-Aktion VOR der Recherche)

> **Wichtig: Phase 1 ist verpflichtender Vorab-Schritt.** Die
> Recherche darf NICHT generisch laufen, weil die Priorisierung
> sonst falsch wird. Ein produktiver kleiner Channel könnte
> übersehen, ein großer aber inaktiver zu hoch gewichtet
> werden. Reality-Daten schlagen Bauchgefühl.

### Schritt 1.1: Top-produktive Channels (letzte 30 Tage)

Container-Python via `railway ssh`:

```python
python3 << 'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("""
    SELECT
      c.handle,
      c.market,
      c.platform,
      c.channel_type,
      COUNT(DISTINCT a.id) AS total_assets,
      COUNT(DISTINCT a.id) FILTER (WHERE a.title_id IS NOT NULL) AS assets_with_title,
      COUNT(DISTINCT a.title_id) AS unique_titles,
      COUNT(DISTINCT a.de_us_match_key) FILTER (WHERE a.de_us_match_key IS NOT NULL) AS unique_match_keys
    FROM creative_radar.channel c
    LEFT JOIN creative_radar.post p ON p.channel_id = c.id
    LEFT JOIN creative_radar.asset a ON a.post_id = p.id
    WHERE p.detected_at > NOW() - INTERVAL '30 days'
       OR p.detected_at IS NULL
    GROUP BY c.handle, c.market, c.platform, c.channel_type
    ORDER BY total_assets DESC NULLS LAST
    LIMIT 30;
""")
print(f"{'Handle':<35s} {'Market':<8s} {'Platform':<10s} {'Type':<20s} "
      f"{'Assets':>7s} {'Titles':>7s} {'UniqueT':>8s} {'MatchK':>7s}")
print("-" * 130)
for row in cur.fetchall():
    handle, market, platform, ctype, total, with_title, unique_t, unique_m = row
    print(f"{handle or '-':<35s} {market or '-':<8s} {platform or '-':<10s} "
          f"{(ctype or '-')[:18]:<20s} {total or 0:>7d} {with_title or 0:>7d} "
          f"{unique_t or 0:>8d} {unique_m or 0:>7d}")

cur.close()
conn.close()
PYEOF
```

### Schritt 1.2: Channel-Coverage-Lücken pro Markt+Plattform

```python
python3 << 'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("""
    SELECT
      market,
      platform,
      COUNT(*) AS channel_count,
      COUNT(*) FILTER (WHERE channel_type LIKE '%Studio%' OR channel_type LIKE '%Verleih%') AS studio_count,
      COUNT(*) FILTER (WHERE channel_type LIKE '%Streaming%' OR channel_type LIKE '%Plattform%') AS streaming_count
    FROM creative_radar.channel
    GROUP BY market, platform
    ORDER BY market, platform;
""")
print(f"{'Market':<8s} {'Platform':<10s} {'Total':>6s} {'Studio':>7s} {'Streaming':>10s}")
print("-" * 50)
for row in cur.fetchall():
    print(f"{row[0] or '-':<8s} {row[1] or '-':<10s} {row[2]:>6d} {row[3]:>7d} {row[4]:>10d}")

cur.close()
conn.close()
PYEOF
```

### Schritt 1.3: Aktuelle Whitelist als Liste (alle 3 Plattformen)

```python
python3 << 'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

for plat in ['instagram', 'tiktok', 'youtube']:
    cur.execute("""
        SELECT handle, market, channel_type, channel_priority
        FROM creative_radar.channel
        WHERE platform = %s
        ORDER BY market, channel_type, handle;
    """, (plat,))
    rows = cur.fetchall()
    print(f"\n=== {plat.upper()} ({len(rows)} Channels) ===")
    print(f"{'Handle':<35s} {'Market':<8s} {'Type':<25s} {'Priority':<5s}")
    print("-" * 80)
    for row in rows:
        print(f"{row[0]:<35s} {row[1] or '-':<8s} {(row[2] or '-')[:23]:<25s} {row[3] or '-':<5s}")

cur.close()
conn.close()
PYEOF
```

### Phase-1-Output einfügen (HIER vor Versand an Agent)

> **Wolf-Aktion:** Output von 1.1, 1.2, 1.3 hier hineinkopieren,
> bevor das Briefing an den Recherche-Agent geht. Ohne diese
> Daten läuft die Recherche im Blindflug.

```
=== PHASE-1-OUTPUT (erhoben 2026-05-05 via railway ssh) ===

--- 1.0 SMOKE-TEST MATCH-KEY-KONSISTENZ ---

Raw-Form:      0 (erwartet: 0)
Slug-Form:     27
Total mit Key: 27
Konsistenz:    OK

→ PR #67 (Match-Key-Slug-Migration) ist sauber durchgelaufen.

--- 1.1 TOP-30 PRODUKTIVSTE CHANNELS (letzte 30 Tage) ---

Handle                              Market   Platform   Type                  Assets  Titles  UniqueT  MatchK
----------------------------------------------------------------------------------------------------------------------------------
xyzfilms                            INT      instagram  Verleih/Produktion        40       1        1       0
wildbunchfilmlounge                 INT      instagram  Verleih/Produktion        13       0        0       0
20thcenturystudios                  US       instagram  Studio/Verleih            13       1        1       7
@netflix                            UNKNOWN  youtube    -                         10       0        0       0
warnerbros                          US       tiktok     Studio/Verleih             9       2        2       4
warnerbrosepics                     US       instagram  Studio/Verleih             9       1        1       1
20thcenturystudiosde                DE       instagram  Studio/Verleih             8       6        1       4
wowtv                               DE       instagram  Streamer                   8       0        0       0
sonypicturesaustria                 DE       instagram  Studio/Verleih             7       0        0       0
skydeutschland                      DE       instagram  Streamer                   7       0        0       0
disneyplusde                        DE       instagram  Streamer                   7       2        2       0
paramount_pictures_germany          DE       instagram  Studio/Verleih             7       0        0       1
primevideode                        DE       instagram  Streamer                   7       0        0       0
paramountplusde                     DE       instagram  -                          7       0        0       1
warnerbrosentertainment             US       instagram  Studio/Verleih             7       1        1       2
starwars                            INT      instagram  Verleih/Produktion         6       0        0       0
piffl_medien                        INT      instagram  Verleih/Produktion         6       1        1       0
tobisfilm                           DE       instagram  Verleih/Produktion         6       0        0       0
wowtvde                             DE       instagram  -                          6       1        1       0
universalpicturesde                 DE       instagram  Studio/Verleih             5       0        0       0
rialtofilm                          INT      instagram  Verleih/Produktion         5       0        0       0
plaionpictures                      INT      instagram  Game Publisher             5       0        0       0
plaion_official                     INT      instagram  Game Publisher             5       0        0       0
primevideo                          US       instagram  Streamer                   5       0        0       1
sonypictures.de                     DE       instagram  -                          5       0        0       0
weltkinofilmverleih                 DE       instagram  Verleih/Produktion         5       0        0       0
pantaleonfilms                      INT      instagram  Verleih/Produktion         5       0        0       0
warnerbrosde                        DE       instagram  Studio/Verleih             5       1        1       0
studiocanal                         INT      instagram  Studio/Verleih             5       0        0       0
squarepegfilms                      INT      instagram  Verleih/Produktion         5       0        0       0

→ Beobachtung Top-30: 22 von 30 Channels haben 0 Match-Keys.
   Match-Keys konzentrieren sich auf 8 Channels mit klarer
   DE+US-Pairing-Struktur (20thcenturystudios×de, warnerbros-Familie,
   paramount_pictures_germany, paramountplusde, primevideo).

--- 1.2 COVERAGE-VERTEILUNG (erweitert um Schema-Felder) ---

Market   Platform    Total   S/V  Strmr  Game  Unclass  Active  Monit   MVP
--------------------------------------------------------------------------------
DE       instagram      34    21      7     2        4      34     34    34
DE       youtube         5     0      0     0        5       5      5     5
US       instagram      25    16      8     0        1      25     25    25
US       tiktok          7     2      0     0        5       7      7     7
US       youtube        14     0      0     0       14      14     14    14
INT      instagram      46    43      1     2        0      46     46    46
UNKNOWN  youtube         1     0      0     0        1       1      1     1

GESAMT: 132 Channels — IG: 105 (80%) | YT: 20 (15%) | TT: 7 (5%)

Priority/Quality-Tier-Verteilung:
Priority        Quality-Tier     Count
----------------------------------------
A               P1                  43
B               P1                  43
C               P1                  46

→ Alle 132 Channels: quality_tier=P1, active=true, monitored=true,
   mvp=true. Tier-Differenzierung wird heute nicht zur Steuerung
   genutzt — Priority A/B/C ist gleichverteilt aber semantisch ungenutzt.

--- 1.3 AKTUELLE WHITELIST KOMPLETT ---

=== INSTAGRAM (105 Channels) ===
Handle                              Market   Type
----------------------------------------------------------------------
plaion_dach                         DE       Game Publisher
plaion_de                           DE       Game Publisher
discoveryplusde                     DE       Streamer
disneyplusde                        DE       Streamer
hbomaxde                            DE       Streamer
netflixde                           DE       Streamer
primevideode                        DE       Streamer
skydeutschland                      DE       Streamer
wowtv                               DE       Streamer
20thcenturystudiosde                DE       Studio/Verleih
disneydeutschland                   DE       Studio/Verleih
leoninestudios                      DE       Studio/Verleih
paramount_pictures_germany          DE       Studio/Verleih
sonypicturesaustria                 DE       Studio/Verleih
sonypicturesde                      DE       Studio/Verleih
studiocanalde                       DE       Studio/Verleih
universalpicturesau                 DE       Studio/Verleih
universalpicturesde                 DE       Studio/Verleih
warnerbrosde                        DE       Studio/Verleih
arsenal.filmverleih                 DE       Verleih/Produktion
constantinfilm                      DE       Verleih/Produktion
eksystent_filmverleih               DE       Verleih/Produktion
farbfilmverleih                     DE       Verleih/Produktion
grandfilm_verleih                   DE       Verleih/Produktion
natgeodeutschland                   DE       Verleih/Produktion
pandorafilmverleih                  DE       Verleih/Produktion
starwars_de                         DE       Verleih/Produktion
tobisfilm                           DE       Verleih/Produktion
vueltagermany                       DE       Verleih/Produktion
weltkinofilmverleih                 DE       Verleih/Produktion
paramountplusde                     DE       -
sonypictures.de                     DE       -
studiocanal.de                      DE       -
wowtvde                             DE       -
disneyplus                          US       Streamer
hbo                                 US       Streamer
hbomax                              US       Streamer
hulu                                US       Streamer
netflix                             US       Streamer
netflixfilm                         US       Streamer
paramountplus                       US       Streamer
primevideo                          US       Streamer
20thcenturystudios                  US       Studio/Verleih
amazonmgmstudios                    US       Studio/Verleih
disney                              US       Studio/Verleih
disneychannel                       US       Studio/Verleih
disneystudios                       US       Studio/Verleih
marvel                              US       Studio/Verleih
marvelstudios                       US       Studio/Verleih
paramountpics                       US       Studio/Verleih
pixar                               US       Studio/Verleih
sonypictures                        US       Studio/Verleih
universalhorror                     US       Studio/Verleih
universalpictures                   US       Studio/Verleih
warnerbros                          US       Studio/Verleih
warnerbrosentertainment             US       Studio/Verleih
warnerbrosepics                     US       Studio/Verleih
a24                                 US       Verleih/Produktion
streamonmax                         US       -
plaion_official                     INT      Game Publisher
plaionpictures                      INT      Game Publisher
appletv                             INT      Streamer
anapurna                            INT      Studio/Verleih
capelightpictures                   INT      Studio/Verleih
elarapictures                       INT      Studio/Verleih
elevation_pics                      INT      Studio/Verleih
magnoliapics                        INT      Studio/Verleih
orionpictures                       INT      Studio/Verleih
searchlightpics                     INT      Studio/Verleih
studiocanal                         INT      Studio/Verleih
24bilder                            INT      Verleih/Produktion
akkordfilm                          INT      Verleih/Produktion
alpenrepublik                       INT      Verleih/Produktion
bad_robot                           INT      Verleih/Produktion
blumhouse                           INT      Verleih/Produktion
dcmfilm                             INT      Verleih/Produktion
film4                               INT      Verleih/Produktion
flare.film.berlin                   INT      Verleih/Produktion
hanway_films                        INT      Verleih/Produktion
independentfilmco                   INT      Verleih/Produktion
legendary                           INT      Verleih/Produktion
lionsgate                           INT      Verleih/Produktion
lionsgatehorror                     INT      Verleih/Produktion
lionsgateuk                         INT      Verleih/Produktion
lucasfilm                           INT      Verleih/Produktion
madmanfilms                         INT      Verleih/Produktion
majestic.film                       INT      Verleih/Produktion
meteor_film                         INT      Verleih/Produktion
mfa_film                            INT      Verleih/Produktion
mgmplus                             INT      Verleih/Produktion
natgeo                              INT      Verleih/Produktion
neue_visionen                       INT      Verleih/Produktion
pantaleonfilms                      INT      Verleih/Produktion
piffl_medien                        INT      Verleih/Produktion
portauprincefilms                   INT      Verleih/Produktion
rialtofilm                          INT      Verleih/Produktion
riseandshinecinema                  INT      Verleih/Produktion
rusticfilms                         INT      Verleih/Produktion
sabanfilms                          INT      Verleih/Produktion
seruanimation                       INT      Verleih/Produktion
squarepegfilms                      INT      Verleih/Produktion
starwars                            INT      Verleih/Produktion
verticalentertainment               INT      Verleih/Produktion
wildbunchfilmlounge                 INT      Verleih/Produktion
xyzfilms                            INT      Verleih/Produktion

=== TIKTOK (7 Channels) ===
Handle                              Market   Type
----------------------------------------------------------------------
warnerbros                          US       Studio/Verleih
warnerbros                          US       Studio/Verleih   ← DUPLIKAT-EINTRAG
disney                              US       -
netflix                             US       -
paramountpics                       US       -
sonypictures                        US       -
universalpictures                   US       -

=== YOUTUBE (20 Channels) ===
Handle                              Market   Type
----------------------------------------------------------------------
ConstantinFilm                      DE       -
NetflixDE                           DE       -
SonyPicturesGermany                 DE       -
UniversalPicturesDE                 DE       -
WarnerBrosDE                        DE       -
20thCenturyStudios                  US       -
AppleTV                             US       -
DisneyPlus                          US       -
LionsgateMovies                     US       -
Max                                 US       -
Netflix                             US       -
ParamountPictures                   US       -
ParamountPlus                       US       -
PrimeVideo                          US       -
SonyPicturesEntertainment           US       -
STUDIOCANAL                         US       -
UniversalPictures                   US       -
WaltDisneyStudios                   US       -
WarnerBrosPictures                  US       -
@netflix                            UNKNOWN  -

--- DATEN-HYGIENE-FINDINGS (für Whitelist-Update-Sprint zu fixen) ---

H1: TT-Duplikat: warnerbros US zweimal in TikTok-Whitelist
H2: TT unklassifiziert: 5/7 Channels ohne channel_type
H3: YT unklassifiziert: 20/20 Channels ohne channel_type (strukturell)
H4: DE-IG unklassifiziert: paramountplusde, sonypictures.de,
    studiocanal.de, wowtvde
H5: streamonmax (US-IG) ohne channel_type
H6: @netflix UNKNOWN auf YouTube (sollte INT oder US sein)
H7: Handle-Inkonsistenz: YouTube CamelCase ("ConstantinFilm")
    vs Instagram lowercase ("constantinfilm") — könnte
    Cross-Platform-Matching erschweren falls handle-basiert
H8: quality_tier strukturell ungenutzt (alle P1)
H9: priority A/B/C gleichverteilt aber als Steuerungslogik
    de facto inaktiv

--- KONTEXT FÜR DEN RECHERCHE-AGENT ---

Diese 132 Channels sind die heutige Whitelist. Plattform-
Imbalance ist offensichtlich: Instagram dominiert (105),
TikTok ist strukturell unterversorgt (7, davon nur US),
YouTube ist breit aber unklassifiziert.

DE+US-Pairing-Potenzial: Heute bilden sich nur 27
Match-Keys, davon nur ein einziges echtes Cross-Market-Pair.
Der Recherche-Agent soll Lücken zur DE+US-Symmetrie schließen,
TikTok-DE-Whitelist neu aufbauen (heute 0 Channels) und
YouTube-Coverage erweitern (heute extrem dünn in DE).

=== ENDE PHASE-1-OUTPUT ===
```

---

## Phase 2 — Markt-Priorisierung (Tier-System)

> **Achtung: Drei Tiers verschiedener Wichtigkeit. Nicht
> gleichgewichten.**

### Markt-Tier-A — Kern-Use-Case (höchste Priorität)

**DE + US.** Das ist der wirtschaftliche Kern von Creative
Radar. Hier mindestens 70% der Recherche-Zeit investieren.

Konkrete Zielzahl: **15-25 neue Channels** in DE+US-Pairings.

### Markt-Tier-B — Sekundär (mittlere Priorität)

**UK + andere englischsprachige Märkte mit eigener Marketing-
Identität.**

Begründung: UK-Marketing ist oft eigenständig vom US-Marketing
und parallel zum DE-Markt (gleiche Release-Termine). Beispiele:
- `marvelukandireland`
- `universalpicturesuk`
- `disneystudiosuk`
- Kanadische Studios (selten relevant — meistens identisch mit US)
- Australische Studios (selten relevant)

Konkrete Zielzahl: **5-10 neue Channels** als optionale
Cross-Market-Quellen.

### Markt-Tier-C — Bedingt (niedrige Priorität)

**Global-Hubs nur wenn sie messbar anderes Material posten
als US-Pendant.**

Beispiele die zu prüfen sind:
- `marvelentertainment` vs `marvelstudios` — postet
  Marvel-Entertainment substantiell andere Inhalte? Falls ja:
  aufnehmen. Falls fast identisch zum US-Pendant: skip.
- `disney` vs `disneyplus` — Branding vs. Streaming-Releases.
  Klare Differenzierung? Falls ja: beide. Falls Doublette:
  nur eines.

Konkrete Zielzahl: **0-5 Channels** nur bei klar
unterschiedlichem Content.

---

## Phase 3 — Plattform-Gewichtung

> **Wichtig: Plattformen NICHT gleichgewichten. Verschiedene
> Coverage-Lücken-Größen, verschiedene Erweiterungs-Bedarfe.**

### Plattform-Tier-Instagram (tiefe Recherche, viele Lücken)

- **Heutige Coverage:** 55 Channels, produktive Plattform
- **Problem:** unsystematische DE+US-Pairings
- **Recherche-Fokus:** Komplettierung von Pairs
  (wenn DE-Account drin, US-Account suchen — und umgekehrt)
- **Konkrete Zielzahl: 15-25 neue Channels**

### Plattform-Tier-TikTok (mittel-tiefe Recherche, große Lücken)

- **Heutige Coverage:** 3 Channels, lächerlich klein
- **Problem:** Whitelist deckt TikTok-Marketing nicht ab,
  obwohl Plattform für Film-Promotion (besonders Gen-Z-Filme)
  zunehmend wichtig wird
- **Recherche-Fokus:** Substanzielle Erweiterung; pro
  empfohlenem IG-Studio prüfen ob es einen aktiven TikTok-
  Account gibt
- **Konkrete Zielzahl: 10-15 neue Channels**

### Plattform-Tier-YouTube (mittlere Recherche, mittlere Lücken)

- **Heutige Coverage:** unklar (in Cron-Daten nicht aktiv
  sichtbar)
- **Problem:** Vision-Pipeline für YT fehlt sowieso (Backlog-
  Item), heute weniger akut
- **Recherche-Fokus:** Trailer-spezifische YT-Channels von
  Studios mit regelmäßigen Uploads
- **Konkrete Zielzahl: 5-10 neue Channels**

**Gesamt-Zielzahl: 30-50 neue Channels über alle Plattformen.**

---

## Phase 4 — Recherche-Aufgaben

Mit Phase-1-Daten + Tier-System hat der Agent klare
Priorisierung. Jetzt die eigentliche Recherche.

### Aufgabe 4.1 — Direct-Pair-Matching (Markt-Tier-A)

Für jeden der Top-produktiven Channels aus Phase 1 (insbesondere
`channel_priority='A'` und Studio/Verleih/Streaming):

**Wenn DE-Channel:** existiert ein US/Global-Pendant mit
analoger Account-Struktur?

Beispiel-Suchen:
- `disneyplusde` → existiert `disneyplus` auf IG? Aktivitätslevel?
- `netflixde` → `netflix` ist trivial, aber: wie aktiv ist
  `netflix` mit Film/Serien-Posts vs allgemeine Brand-Posts?
- `wowtv` → existiert ein US-Pendant von Sky/Showtime mit
  vergleichbarem Posting-Stil?
- `tobisfilm` → DE-Verleih für US-Filme; gibt's dort
  systematische US-Quelle (oft kein direktes Pendant, weil
  Tobis-Verleih Mischportfolio hat)?

**Wenn US/Global-Channel:** existiert ein DE-Pendant?

Beispiel-Suchen:
- `warnerbros` → `wbpicturesgermany`? `warnerbrosde`?
- `20thcenturystudios` → `20thcenturystudiosde` ist da, aber
  gibt es weitere DE-Sub-Accounts (z.B. franchisespezifisch)?

**Output-Format pro Pair:**

```
Pair: <de-handle> ↔ <us-handle>
- DE-Account: <handle>, <follower-count>, <posting-frequency>,
  <last-active-date>
- US-Account: <handle>, <follower-count>, <posting-frequency>,
  <last-active-date>
- Asset-Overlap-Wahrscheinlichkeit: <hoch/mittel/niedrig>
  (basierend auf: posten beide aktuelle Filme? Selbe Franchises?
  Selbe Release-Kalender?)
- Aufnahme-Empfehlung: <ja/nein/conditional>
- Risiko: <z.B. "US-Account zu generisch, postet wenig
  film-spezifisch", "DE-Account inaktiv seit 6 Monaten">
```

### Aufgabe 4.2 — Franchise-Pair-Matching (Markt-Tier-A)

Studios mit unterschiedlichen Account-Strukturen, aber gleichen
Film-Inhalten:

Beispiele:
- A24: hat einen US-Account, aber DE-Verleih ist meist
  Capelight Pictures oder Plaion Pictures — gibt's deren
  Social-Accounts mit aktiven A24-Film-Posts?
- Neon (US-Indie-Studio): wer macht den DE-Vertrieb? Existiert
  dort ein aktiver Channel?
- Lionsgate: in DE oft Universum Film oder Constantin Film —
  welche Channels?

**Output-Format pro Franchise-Pair:**

```
Franchise: <Beispiel-Filmtitel>
- US-Studio: <name>, <handle>
- DE-Verleih: <name>, <handle> (oder "kein aktiver Channel
  identifiziert")
- Match-Mechanismus: <z.B. "title_slug-basiert">
- Aufnahme-Empfehlung: <ja/nein>
```

### Aufgabe 4.3 — UK + andere Tier-B-Märkte

Pro recherchiertem US-Channel auch prüfen:

- Existiert ein UK-Sub-Account? (`marvelukandireland`,
  `universalpicturesuk`, `paramountukandireland`)
- Aktivitätslevel UK vs US? Eigenständiges Marketing?
- Welche UK-Pendants haben DE-Pairing-Potential (gleicher
  Release-Kalender)?

### Aufgabe 4.4 — Streaming-Plattform-Coverage (Markt-Tier-A)

Streaming-Plattformen sind oft Cross-Market wertvoll, weil
gleiche Inhalte (z.B. Stranger Things) parallel beworben werden:

Recherche pro Plattform:
- **Netflix:** US-Hauptaccount, DE-Account, ggf. Genre-Sub-
  Accounts wie `netflixfilm`, `netflixqueue`, `strongblacklead`
- **Disney+:** Hauptaccount + Film-spezifische Sub-Accounts
  wie `marvelstudios`, `starwars`
- **Amazon Prime Video:** US: `primevideo`, DE: `primevideode`
  ist drin — gibt's `amazonmgmstudios` oder ähnliches?
- **Apple TV+:** `appletvplus` global, gibt's DE-Variante?
- **HBO/Max:** US: `streamonmax`, früher `hbo` — aktuelle
  Struktur?
- **Paramount+:** US und DE-Variante?

### Aufgabe 4.5 — TikTok-Coverage (Plattform-Tier-TikTok)

TikTok hat heute nur 3 Channels in der Whitelist. Recherche:
- Welche der DE+US-Studios aus 4.1-4.4 haben aktive TikTok-
  Pendants?
- Gibt es TikTok-only-Accounts die im Film-Marketing-Kontext
  relevant sind (z.B. `screenrant` für Trailer-Reactions,
  Studio-eigene TikToks)?
- Welche Major-Studios haben **keine** TikTok-Präsenz und
  sollten daher übersprungen werden?

**Output:** Top-15-TikTok-Channels mit Empfehlung.

### Aufgabe 4.6 — YouTube-Coverage (Plattform-Tier-YouTube)

YouTube ist heute primär für Trailer relevant. Recherche:
- Welche Studios haben aktive YouTube-Channels mit regelmäßigen
  Trailer-/Featurette-Uploads in DE+US?
- Aufnahme-Empfehlung für Top-10.

### Aufgabe 4.7 — Discovery-Channels (Markt-Tier-übergreifend, optional)

Channels die nicht direkt einem Studio gehören, aber hohe
Trend-Sichtbarkeit für DE/US-Vergleich liefern:

- `moviepilot.de` — DE-Marktstandard-Tracker
- `thefilmstage`, `indiewire` — US-Trade
- TMDB-eigene Accounts?
- Letterboxd-eigene Accounts?

**Vorsicht:**
- Keine Personal-Influencer-Accounts (zu unstrukturiert)
- Keine Fan-Accounts (rechtliche Grauzone)
- Keine Aggregator-Apps wie Letterboxd/IMDB (bereits via
  TMDB-API abgedeckt)

**Output:** 3-7 Discovery-Channels mit klarer Markierung
"Discovery-Mode" und Empfehlung welche `is_discovery=True`
geflaggt werden sollen.

---

## Phase 5 — Strukturiertes Output-Format

Der Recherche-Agent soll das Endergebnis in genau diesem
Format liefern:

```markdown
# DE/US-Channel-Recherche — Ergebnis

## Executive Summary
- Recherchierte Channels gesamt: <N>
- Empfohlen für Aufnahme: <N>
  - Markt-Tier-A (DE+US Direct/Franchise Pairs): <N>
  - Markt-Tier-B (UK + Tier-B): <N>
  - Markt-Tier-C (Global-Hubs bedingt): <N>
- Empfehlungen pro Plattform:
  - Instagram: <N>
  - TikTok: <N>
  - YouTube: <N>
- Erwarteter Gewinn an DE+US-Match-Pairs: <Schätzung>

## Empfehlungs-Tabelle

| Handle              | Platform | Market | Type   | Markt-Tier | Plattform-Tier | Pair mit       | Priorität | Aktivität | Notiz |
|---------------------|----------|--------|--------|------------|----------------|----------------|-----------|-----------|-------|
| disneyplus          | IG       | US     | Stream | A          | IG             | disneyplusde   | A         | aktiv     | ...   |
| ...                 | ...      | ...    | ...    | ...        | ...            | ...            | ...       | ...       | ...   |

## Detailbefunde pro Channel
[Pro empfohlenen Channel: ein Absatz mit Begründung, Risiko,
Posting-Frequenz, Asset-Erwartung]

## Nicht-Empfehlungen
[Channels die geprüft, aber nicht empfohlen wurden — mit
Begründung, damit nicht später nochmal recherchiert wird]

## Quellen
[Liste der Quellen/URLs, an denen Recherche stattgefunden hat]
```

---

## Strategische Hinweise für den Recherche-Agent

**Worauf besonders achten:**

1. **Aktivität ist Pflicht.** Ein DE-Account der seit 6 Monaten
   nichts gepostet hat ist wertlos. Cron-System holt Posts der
   letzten 7-14 Tage. Mindestens 2-3 Posts pro Woche sind
   wünschenswert.

2. **Film-Marketing-Fokus.** Generische Brand-Accounts
   (z.B. `disney` als allgemeines Branding) sind weniger
   wertvoll als film-spezifische Sub-Accounts (`disneyplus`
   für aktuelle Releases).

3. **Account-Konsistenz.** Manche Studios wechseln
   Account-Strategien — z.B. `hbomax` wurde zu `streamonmax`.
   Aktuelle Handles verwenden, alte als deprecated markieren.

4. **Sprache verifizieren.** Manche DE-Accounts posten primär
   auf Englisch (Marketing-Strategie). Das ist OK, solange
   `channel_market='DE'` korrekt ist (Markt-Zuordnung, nicht
   Sprach-Zuordnung).

5. **Pairing-Wahrscheinlichkeit ist das Hauptkriterium.** Ein
   eigenständiger US-Account ohne DE-Pendant bringt für DE+US-
   Cross-Market-Vergleich wenig Wert. Lieber einen weniger
   großen Account aufnehmen, der ein klares Pairing ergibt,
   als einen riesigen Solitär.

6. **Nicht-empfohlene Channels begründen.** Wenn ein Account
   geprüft und nicht empfohlen wird, kurzer Grund — verhindert
   dass später jemand denselben Account nochmal vorschlägt.

**Was NICHT recherchiert werden soll:**

- Personal-Influencer-Accounts (zu unstrukturiert)
- Fan-Accounts (rechtliche Grauzone)
- Nicht-Film/Serien-Inhalte
- Aggregator-Apps wie Letterboxd/IMDB (bereits via TMDB-API
  abgedeckt)
- Nationale Märkte außerhalb Tier-A/B (Asien, LATAM) —
  separater Recherche-Sprint falls je gewollt

---

## Tool-Empfehlungen

**Empfohlener Workflow:**

1. **Claude Research** für die Hauptrecherche (Aufgaben 4.1-4.7)
   - Stärke: strukturierter Markdown-Output, gute Quellen-Zitate
   - Anweisung: dieses Briefing in voller Länge inkl. Phase-1-
     Output als Eingabe geben

2. **Perplexity Pro** für Live-Verifikation
   - Pro empfohlenem Channel: ist der Account aktuell aktiv?
   - Letzte 5 Posts der letzten 30 Tage?
   - Schneller als manuelle Channel-Besuche

3. **ChatGPT Deep Research** als Alternative oder Ergänzung
   - Falls Claude Research zu wenig Tiefe liefert
   - Stärke: sehr lange detaillierte Berichte

4. **Manuelle IG-/TikTok-/YT-Verifikation**
   - Stichprobe der Top-10-Empfehlungen direkt im Browser
   - 5-10 Min, prüft ob die Recherche-Ergebnisse stimmen

---

## Erwarteter Output-Umfang

- 60-100 recherchierte Channels (mehr als v1, weil 3 Plattformen)
- 30-50 empfohlene Channels (verteilt über die drei
  Plattform-Tiers)
- 5-8 Seiten strukturierter Markdown-Output
- Aufwand für Recherche-Agent: 45-75 Min Echtzeit
- Wolf-Reading-Time: 20-30 Min

---

## Nach erfolgreicher Recherche — Folge-Sprints

### Folge-Sprint 1: WHITELIST-UPDATE (~30-45 Min Code+SQL)

```sql
-- Beispiel-Insert pro neuem Channel
INSERT INTO creative_radar.channel
  (handle, market, platform, channel_type, channel_priority, ...)
VALUES
  ('disneyplus', 'US', 'instagram', 'Streaming/Plattform', 'A', ...),
  ('netflix', 'US', 'instagram', 'Streaming/Plattform', 'A', ...),
  ...;
```

Plus: nächsten Cron-Trigger manuell starten, neue Channels
werden gescraped, neue Assets fließen rein.

### Folge-Sprint 2: REALITY-CHECK 14 Tage später

```sql
SELECT COUNT(DISTINCT de_us_match_key)
FROM creative_radar.asset a
LEFT JOIN creative_radar.post p ON p.id = a.post_id
LEFT JOIN creative_radar.channel c ON c.id = p.channel_id
WHERE a.de_us_match_key IS NOT NULL
GROUP BY a.de_us_match_key
HAVING COUNT(DISTINCT c.market) >= 2;
```

Wenn das Ergebnis ≥ 10 ist → Sprint 5.3.7 (Frontend-Pairing-
View) lohnt sich endlich. Vorher nicht.

---

## Versions-Historie dieses Briefings

- **v1**: Initial-Briefing, IG-fokussiert, kein Markt-Tier
- **v2**: Markt-Tier-System (DE+US/UK+/Global), Plattform-
  Gewichtung mit konkreten Zielzahlen, Phase 1 als zwingend
  markiert, Geschäftskontext-Block hinzugefügt
- **v2.1**: Format-Bug in Query 1.1 gefixt (`unique_titles` und
  `unique_match_keys` waren in `print()` verloren); Match-Key-
  Konsistenz-Folge-Sprint entfernt (durch PR #67 erledigt);
  Reality-Check-Block aktualisiert (Format-Inkonsistenz beseitigt)
- **v2.2 (aktuell)**: Phase-1-Output eingefügt (erhoben
  2026-05-05 via railway ssh). 132 Channels heute (105 IG /
  20 YT / 7 TT). 27 Match-Keys total. 9 Daten-Hygiene-Findings
  dokumentiert (TT-Duplikat, YT komplett unklassifiziert,
  DE-IG-Klassifikations-Lücken, Handle-Case-Inkonsistenzen,
  quality_tier strukturell ungenutzt). Briefing ist
  recherchefertig.
