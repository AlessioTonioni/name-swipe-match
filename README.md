# Name Match

Swipe-to-match for baby names. Log in with a username (no password, private network), swipe
right/left on names, get "It's a match!" when your partner liked the same name, and run
1 vs 1 duels between your liked names to build an Elo ranking (yours, and a joint one
averaged over the names you both like).


## Layout

- `app.py`: FastAPI + SQLite (`name_match.db`, override with `NAME_MATCH_DB`). Seeds the
  `names` table from `data/names.json` on startup (upsert, so swipes survive a rebuild).
- `static/`: vanilla JS frontend (drag or ←/→ to swipe, Backspace to undo).
- `data/fetch_raw.sh` + `data/build_names.py`: rebuild `data/names.json` from public stats.
- `deploy.sh`, `setup.sh`, `name-match.service`, `backup-db.sh`: deploy to a home server.

## Name data

3,000 boys' and 3,000 girls' names. They are picked round-robin by rank across countries
and merged across accents/case (Léo = Leo). A name is tagged with a country when it is in
that country's top 500, or when that country contributed it.

| Country | Source | Years |
|---|---|---|
| Switzerland | BFS, residents by first name and year of birth | born 2022–2024 |
| Italy | ISTAT "Contanomi" web service, top 5,000 per sex | 2022–2024 |
| France | INSEE Fichier des prénoms | 2023–2025 |
| Austria | Statistik Austria OGD Vornamen | 2023–2025 |
| Germany | No national statistic: Bonn, Essen, Düsseldorf (2024) and Köln (2021–22) open data, first given name only | 2021–2024 |
| United Kingdom | ONS, England & Wales top 100 | 2025 |
| Spain | INE, top 100 | 2024 |
| Belgium | Statbel, newborns by municipality | 2012–2021 |
| Norway | SSB table 10467 | 2023–2025 |

The Netherlands is not included: there is no downloadable newborn-names file.

Rebuild: `./data/fetch_raw.sh && .venv/bin/python data/build_names.py`, then `./deploy.sh`.

## Deploy

```
./deploy.sh --dry-run   # preview
./deploy.sh --setup     # first time
./deploy.sh             # afterwards
```
