
import os
import json
import csv
import datetime
import urllib.request
import urllib.parse
import urllib.error
import unicodedata
import re
import time
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parent
OUT = ROOT / "data"
OUT.mkdir(exist_ok=True)

key = os.environ.get("PRIZEPICKS_API_KEY", "")
if not key:
    raise SystemExit("Missing PRIZEPICKS_API_KEY secret")

rows = []
errors = []
seen = set()

def normalized_name(value):
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]", "", s)

def normalized_stat(value):
    s = str(value or "").lower().replace("&", "+")
    return re.sub(r"[^a-z0-9+]", "", s)

def projection_key(player, league, stat):
    return (
        normalized_name(player),
        str(league).upper(),
        normalized_stat(stat)
    )

def get_text(value, field="name"):
    if isinstance(value, dict):
        return str(value.get(field) or "")
    return str(value or "")

def line_type(z):
    candidates = [
        z.get("line_type"),
        z.get("lineType"),
        z.get("odds_type"),
        z.get("oddsType"),
        z.get("pick_type"),
        z.get("pickType"),
        z.get("promotion_type"),
        z.get("tier"),
        z.get("type")
    ]
    selections = z.get("selections")
    if isinstance(selections, list):
        candidates.extend(
            s.get("tier")
            for s in selections
            if isinstance(s, dict) and s.get("available", True)
        )
    for value in candidates:
        if not isinstance(value, (str, int, float)):
            continue
        t = str(value).lower()
        if "goblin" in t:
            return "goblin"
        if "demon" in t:
            return "demon"
        if t in ("standard", "normal", "regular"):
            return "normal"
    return "unknown"

def fetch_league(league):
    cursor = None
    used_cursors = set()
    league_rows = []
    completed = False

    for page_number in range(1, 101):
        params = {
            "league": league,
            "view": "compact",
            "limit": 1000
        }
        if cursor:
            params["cursor"] = cursor

        url = (
            "https://prizepicksapi.com/api/v1/projections?"
            + urllib.parse.urlencode(params)
        )
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + key,
            "X-User-Request": "PropEdge player props dashboard",
            "Accept": "application/json"
        })

        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                payload = json.load(resp)
        except Exception as e:
            errors.append(f"{league} page {page_number}: {e}")
            break

        if isinstance(payload, list):
            items = payload
            meta = {}
        elif isinstance(payload, dict):
            items = next(
                (
                    payload[k]
                    for k in ("data", "projections", "results", "items")
                    if isinstance(payload.get(k), list)
                ),
                []
            )
            meta = payload.get("meta") or {}
        else:
            errors.append(f"{league}: Unexpected API response")
            break

        for x in items:
            if not isinstance(x, dict):
                continue

            attrs = x.get("attributes")
            if not isinstance(attrs, dict):
                attrs = {}
            z = {**attrs, **x}

            try:
                line = float(z.get("line", z.get("line_score")))
            except (TypeError, ValueError):
                continue

            player = get_text(
                z.get("player") or z.get("player_name") or z.get("name")
            )
            player_obj = z.get("player")
            market = z.get("market")
            event = z.get("event")

            if not isinstance(player_obj, dict):
                player_obj = {}
            if not isinstance(market, dict):
                market = {}
            if not isinstance(event, dict):
                event = {}

            stat = get_text(
                z.get("stat") or z.get("stat_type") or market.get("stat")
            )
            team = get_text(z.get("team") or player_obj.get("team"))
            opponent = get_text(
                z.get("opponent") or event.get("opponent")
            )
            period = get_text(
                z.get("period") or market.get("period")
            )

            item_id = str(z.get("id") or "")
            dedup_key = (
                league, item_id, player, stat, line,
                str(z.get("tier") or z.get("odds_type") or "")
            )
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            league_rows.append({
                "id": item_id,
                "league": league,
                "player": player,
                "team": team,
                "opponent": opponent,
                "stat": stat,
                "line": line,
                "tier": get_text(z.get("tier")),
                "line_type": line_type(z),
                "period": period
            })

        next_cursor = (
            meta.get("next_cursor")
            if isinstance(meta, dict) else None
        )
        print(
            f"{league} page {page_number}: "
            f"{len(items)} items, "
            f"{len(league_rows)} unique lines",
            flush=True
        )

        if not next_cursor:
            completed = True
            break

        if next_cursor in used_cursors:
            errors.append(f"{league}: Repeated pagination cursor")
            break

        used_cursors.add(next_cursor)
        cursor = next_cursor
        time.sleep(1.2)

    if not completed:
        errors.append(f"{league}: Pagination incomplete")
        return []

    return league_rows

for league in ("NBA", "NFL"):
    league_rows = fetch_league(league)
    rows.extend(league_rows)
    print(f"{league}: {len(league_rows)} total lines", flush=True)

projs = {}

p = ROOT / "projections.csv"
if p.exists():
    with p.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                k = projection_key(
                    r["player"], r["league"], r["stat"]
                )
                projs[k] = (
                    float(r["projection"]),
                    r.get("source", "Independent estimate")
                )
            except (KeyError, ValueError, TypeError):
                pass

model_file = OUT / "nba_model.json"
if model_file.exists():
    try:
        model_data = json.loads(
            model_file.read_text(encoding="utf-8")
        )
        for r in model_data.get("projections", []):
            k = projection_key(
                r["player"], r["league"], r["stat"]
            )
            projs.setdefault(k, (
                float(r["projection"]),
                r.get("source", "PropEdge NBA model")
            ))
    except (OSError, ValueError, KeyError, TypeError) as e:
        errors.append("NBA model load: " + str(e))

for r in rows:
    v = projs.get(
        projection_key(r["player"], r["league"], r["stat"])
    )
    r["projection"] = v[0] if v else None
    r["source"] = v[1] if v else None
    r["difference"] = (
        round(v[0] - r["line"], 2) if v else None
    )

snapshot = {
    "updated_utc": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "lines": rows,
    "errors": errors,
    "note": (
        "Projection differences are research signals, "
        "not estimated win probabilities. "
        "No projection is inferred from the PrizePicks line."
    )
}

if not rows or any("Pagination incomplete" in e for e in errors):
    print(f"API errors: {errors}", flush=True)
    print(
        "Keeping previous PrizePicks snapshot "
        "rather than publishing incomplete data.",
        flush=True
    )
else:
    (OUT / "latest.json").write_text(
        json.dumps(snapshot, indent=2),
        encoding="utf-8"
    )
    print(
        f"Fetched {len(rows)} lines. "
        f"Matched {sum(r['projection'] is not None for r in rows)}. "
        f"Line types: {dict(Counter(r['line_type'] for r in rows))}. "
        f"Errors: {errors}",
        flush=True
    )
