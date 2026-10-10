"""Merge the per-country raw statistics into data/names.json (the app's seed).

Each country gives {name: births} per sex over its most recent ~3 years. Names
are merged across countries by an accent/case-insensitive key, then picked
round-robin by rank across countries until TARGET names per sex are reached.
A name is tagged with a country when it is in that country's top TAG_RANK, or
when that country is the one that contributed it.
"""
import collections
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

import openpyxl

RAW = Path(__file__).parent / "raw"
OUT = Path(__file__).parent / "names.json"
TARGET = 3000
TAG_RANK = 500   # a country tags a name when it is in that country's top TAG_RANK
MIN_COUNT = 5    # births in the window needed for a country to contribute a name

COUNTRIES = {
    "CH": "Switzerland", "IT": "Italy", "FR": "France", "DE": "Germany", "AT": "Austria",
    "UK": "United Kingdom", "ES": "Spain", "BE": "Belgium", "NO": "Norway",
}

VALID = re.compile(r"^[^\W\d_]+(?:[-'][^\W\d_]+)*$")


def counter():
    return {"m": collections.Counter(), "f": collections.Counter()}


def add(c, sex, name, n):
    name = (name or "").strip()
    if n and VALID.match(name) and len(name) >= 2:
        c[sex][name] += int(n)


def load_fr():
    c = counter()
    with open(RAW / "prenoms-2025-nat.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["periode"] in ("2023", "2024", "2025") and not r["prenom"].startswith("_"):
                add(c, "m" if r["sexe"] == "1" else "f", r["prenom"], r["valeur"])
    return c, None


def load_ch():
    c = counter()
    for sex in "mf":
        with open(RAW / f"ch_{sex}.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["YEAROFBIRTH"] in ("2022", "2023", "2024"):
                    add(c, sex, r["firstname"], r["VALUE"])
    return c, None


def load_it():
    c = counter()
    seen = set()
    for path in sorted(RAW.glob("it_*_*.jsonp")):
        t = path.read_text()
        d = json.loads(t[t.index("(") + 1 : t.rindex(")")])
        for k, rows in d.items():
            if k == "years":
                continue
            for r in rows:
                key = (r["year"], r["gender"], r["name"])
                if key not in seen:
                    seen.add(key)
                    add(c, r["gender"], r["name"], r["count"])
    return c, None


def load_at():
    c = counter()
    with open(RAW / "at_OGDEXT_VORNAMEN_1.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["C-JAHR-0"] in ("2023", "2024", "2025"):
                add(c, "m" if r["C-GESCHLECHT-0"] == "1" else "f", r["F-VORNAME_NORMALISIERT"], r["F-ANZAHL_LGEB"])
    return c, None


def load_de():
    # Cities only (no national statistic exists). "position" = 1st, 2nd... given name.
    c = counter()
    for fn in ("de_bonn_2024.csv", "de_essen_2024.csv", "de_dus_2024.csv", "de_koeln_2019_2022.csv"):
        with open(RAW / fn, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r.get("jahr") and r["jahr"] not in ("2021", "2022"):
                    continue
                if r["position"].strip() != "1" or r["geschlecht"] not in ("m", "w"):
                    continue
                add(c, "m" if r["geschlecht"] == "m" else "f", r["vorname"], r["anzahl"])
    return c, None


def load_uk():
    c, totals = counter(), {}
    for sex in "mf":
        wb = openpyxl.load_workbook(RAW / f"uk_{sex}.xlsx", read_only=True)
        for row in wb["Table_1"].iter_rows(values_only=True):
            if isinstance(row[0], int):
                add(c, sex, row[1], row[2])
        # Table_5: per-month rows; "All Names" holds that month's total births
        totals[sex] = sum(r[3] for r in wb["Table_5"].iter_rows(values_only=True) if r[2] == "All Names")
    return c, totals


def load_es():
    c, totals = counter(), {}
    ws = openpyxl.load_workbook(RAW / "es_24.xlsx", read_only=True)["TOTAL"]
    for row in ws.iter_rows(values_only=True):
        for sex, (name, n) in (("m", row[0:2]), ("f", row[3:5])):
            if name == "TOTAL":
                totals[sex] = n
            elif isinstance(n, (int, float)):
                add(c, sex, name, n)
    return c, totals


def load_be():
    c = counter()
    for sex, fn in (("m", "be_m/TA_BIRTHS_MALE_2012_2021.txt"), ("f", "be_f/TA_BIRTHS_FEMALE_2012_2021.txt")):
        with open(RAW / fn, encoding="latin-1", newline="") as f:
            for r in csv.DictReader(f, delimiter="|"):
                add(c, sex, r["TX_FST_NAME"], r["MS_FREQUENCY"])
    return c, None


def load_no():
    c = counter()
    d = json.load(open(RAW / "no.json"))
    names = d["dimension"]["Fornavn"]["category"]
    years = len(d["dimension"]["Tid"]["category"]["index"])
    for code, i in names["index"].items():
        sex = "f" if code.startswith("1") else "m"
        n = sum(v or 0 for v in d["value"][i * years : (i + 1) * years])
        add(c, sex, names["label"][code], n)
    return c, None


LOADERS = {"CH": load_ch, "IT": load_it, "FR": load_fr, "DE": load_de, "AT": load_at,
           "UK": load_uk, "ES": load_es, "BE": load_be, "NO": load_no}


def key(name):
    s = unicodedata.normalize("NFKD", name.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def pretty(name):
    return "-".join(p[:1].upper() + p[1:].lower() for p in name.split("-"))


def main():
    # per country, per sex: ranked list of (key, count, share, variants)
    data = {}
    for cc, load in LOADERS.items():
        c, totals = load()
        data[cc] = {}
        for sex in "mf":
            total = (totals or {}).get(sex) or sum(c[sex].values())
            merged = collections.defaultdict(lambda: [0, collections.Counter()])
            for name, n in c[sex].items():
                m = merged[key(name)]
                m[0] += n
                m[1][name] += n
            ranked = sorted(merged.items(), key=lambda kv: -kv[1][0])
            data[cc][sex] = {k: {"rank": i + 1, "count": v[0], "share": v[0] / total, "variants": v[1]}
                             for i, (k, v) in enumerate(ranked)}
            print(f"{cc} {sex}: {len(ranked):6d} names, {total:8d} births, top: "
                  f"{', '.join(k for k, _ in ranked[:5])}", file=sys.stderr)

    out = []
    for sex in "mf":
        lists = {cc: [k for k, s in sorted(data[cc][sex].items(), key=lambda kv: kv[1]["rank"])
                      if s["count"] >= MIN_COUNT] for cc in data}
        chosen, depth = {}, 0
        while len(chosen) < TARGET and depth < max(map(len, lists.values())):
            depth += 1
            for cc, lst in lists.items():
                if depth <= len(lst) and lst[depth - 1] not in chosen and len(chosen) < TARGET:
                    chosen[lst[depth - 1]] = cc
        for k, picked_by in chosen.items():
            stats = {cc: data[cc][sex][k] for cc in data if k in data[cc][sex]}
            # Display: most-used variant from a source that keeps case, else title-case
            variants = collections.Counter()
            for s in stats.values():
                variants.update(s["variants"])
            cased = [v for v, _ in variants.most_common() if not v.isupper()]
            display = cased[0] if cased else pretty(variants.most_common(1)[0][0])
            out.append({
                "name": display,
                "sex": sex,
                "countries": {cc: {"rank": s["rank"], "per10k": round(s["share"] * 1e4, 2)}
                              for cc, s in stats.items() if s["rank"] <= TAG_RANK or cc == picked_by},
            })
        print(f"sex {sex}: {len(chosen)} names, depth {depth}", file=sys.stderr)

    out.sort(key=lambda n: (n["sex"], min(c["rank"] for c in n["countries"].values())))
    OUT.write_text(json.dumps({"countries": COUNTRIES, "names": out}, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT} ({len(out)} names, {OUT.stat().st_size // 1024} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
