#!/usr/bin/env bash
# Downloads the public first-name statistics into data/raw/ (not deployed).
# Then run: .venv/bin/python data/build_names.py
set -e
cd "$(dirname "$0")" && mkdir -p raw && cd raw
UA="Mozilla/5.0 (Macintosh)"
get() { curl -sSL -m 180 -A "$UA" -o "$1" "$2"; echo "  $1"; }

echo "[fetch] France (INSEE)"
get fr.zip https://www.insee.fr/fr/statistiques/fichier/8595130/prenoms-2025-nat_csv.zip && unzip -o -q fr.zip

echo "[fetch] Switzerland (BFS, residents by year of birth)"
get ch_m.csv https://dam-api.bfs.admin.ch/hub/api/dam/assets/36752513/master
get ch_f.csv https://dam-api.bfs.admin.ch/hub/api/dam/assets/36752506/master

echo "[fetch] Italy (ISTAT contanomi web service)"
IT="https://www.istat.it/wp-content/themes/EGPbs5-child/contanomi/nati/index2022.php"
for y in 2022 2023 2024; do
  get it_m_$y.jsonp "$IT?type=list&limit=5000&year=$y"
  get it_f_$y.jsonp "$IT?type=list&limit=5000&year=$y&gender=f"
done

echo "[fetch] Austria (Statistik Austria OGD)"
get at_OGDEXT_VORNAMEN_1.csv https://data.statistik.gv.at/data/OGDEXT_VORNAMEN_1.csv

echo "[fetch] Germany (no national data: Bonn, Essen, Düsseldorf, Köln open data)"
get de_bonn_2024.csv "https://opendata.bonn.de/sites/default/files/Vornamenstatistik_2024-01-01_2024-12-31.csv"
get de_essen_2024.csv "https://opendata.essen.de/sites/default/files/Beliebteste%20Vornamen%202024.csv"
get de_dus_2024.csv "https://opendata.duesseldorf.de/sites/default/files/Vornamenstatistik_2024_insgesamt.csv"
get de_koeln_2019_2022.csv "https://www.offenedaten-koeln.de/sites/default/files/distribution/Gesamt_Vornamen_2019-2022_0.csv"

echo "[fetch] UK (ONS, England & Wales top 100)"
ONS="https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/birthsdeathsandmarriages/livebirths/datasets"
get uk_m.xlsx "$ONS/babynamesenglandandwalesbabynamesstatisticsboys/2025/2025boysbabynames.xlsx"
get uk_f.xlsx "$ONS/babynamesenglandandwalesbabynamesstatisticsgirls/2025/2025girlsbabynames.xlsx"

echo "[fetch] Spain (INE top 100)"
get es_24.xlsx https://www.ine.es/daco/daco42/nombyapel/nomnac24.xlsx

echo "[fetch] Belgium (Statbel, newborns 2012-2021)"
SB="https://statbel.fgov.be/sites/default/files/files/opendata/Voornamen%20pasgeborenen%20per%20gemeente"
get be_m.zip "$SB/TA_BIRTHS_MALE_2012_2021.zip" && unzip -o -q be_m.zip -d be_m
get be_f.zip "$SB/TA_BIRTHS_FEMALE_2012_2021.zip" && unzip -o -q be_f.zip -d be_f

echo "[fetch] Norway (SSB table 10467, births by first name)"
curl -sS -m 120 -A "$UA" -X POST https://data.ssb.no/api/v0/no/table/10467/ -H 'Content-Type: application/json' \
  -d '{"query":[{"code":"Fornavn","selection":{"filter":"all","values":["*"]}},{"code":"ContentsCode","selection":{"filter":"item","values":["Personer"]}},{"code":"Tid","selection":{"filter":"top","values":["3"]}}],"response":{"format":"json-stat2"}}' \
  -o no.json && echo "  no.json"
