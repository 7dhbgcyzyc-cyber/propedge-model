"""PropEdge NBA matchup data collector (experimental).

Uses completed ESPN NBA box scores. Metrics are observational, not adjusted
for opponent quality. Requires no API key or third-party Python packages.
"""
import json
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'nba_matchups.json'
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
HEADERS = {'User-Agent': 'Mozilla/5.0 (PropEdge research)', 'Accept': 'application/json'}
DAYS_BACK = 21


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def as_number(value):
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def stat_map(team_box):
    """ESPN boxscore teams[].statistics usually uses name/displayValue."""
    result = {}
    for item in team_box.get('statistics', []):
        if not isinstance(item, dict):
            continue
        key = str(item.get('name') or item.get('abbreviation') or '').lower()
        value = item.get('displayValue', item.get('value'))
        result[key] = value
    return result


def number_for(stats, *names):
    for name in names:
        value = stats.get(name.lower())
        number = as_number(value)
        if number is not None:
            return number
    return None


def pair_from(stats, *names):
    for name in names:
        raw = stats.get(name.lower())
        if isinstance(raw, str):
            parts = raw.replace('/', '-').split('-')
            if len(parts) == 2:
                a, b = as_number(parts[0]), as_number(parts[1])
                if a is not None and b is not None and 0 <= a <= b:
                    return a, b
    return None, None


def team_values(box):
    stats = stat_map(box)
    fgm = number_for(stats, 'fieldgoalsmade')
    fga = number_for(stats, 'fieldgoalsattempted')
    ftm = number_for(stats, 'freethrowsmade')
    fta = number_for(stats, 'freethrowsattempted')
    if fga is None:
        fgm, fga = pair_from(stats, 'fieldgoalsmade-fieldgoalsattempted', 'fieldgoals')
    if fta is None:
        ftm, fta = pair_from(stats, 'freethrowsmade-freethrowsattempted', 'freethrows')
    oreb = number_for(stats, 'offensiverebounds', 'offrebounds')
    dreb = number_for(stats, 'defensiverebounds', 'defrebounds')
    turnovers = number_for(stats, 'turnovers', 'totalturnovers')
    team = box.get('team') or {}
    return {
        'team': str(team.get('abbreviation') or '').upper(),
        'fgm': fgm, 'fga': fga, 'fta': fta, 'oreb': oreb,
        'dreb': dreb, 'turnovers': turnovers,
    }


def possessions(stats):
    required = ('fga', 'fta', 'oreb', 'turnovers')
    if any(stats.get(k) is None for k in required):
        return None
    value = stats['fga'] + 0.44 * stats['fta'] - stats['oreb'] + stats['turnovers']
    return value if value > 0 else None


def completed_game_ids():
    ids = set()
    today = datetime.now(timezone.utc).date()
    for offset in range(1, DAYS_BACK + 1):
        day = (today - timedelta(days=offset)).strftime('%Y%m%d')
        try:
            data = fetch(f'{BASE}/scoreboard?dates={day}&limit=100')
            for event in data.get('events', []):
                if event.get('status', {}).get('type', {}).get('completed') is True:
                    ids.add(str(event['id']))
        except Exception as exc:
            print(f'Scoreboard {day}: {exc}', flush=True)
        time.sleep(0.1)
    return sorted(ids)

def upcoming_matchups():
    """Find NBA matchups scheduled over the next 7 days."""
    schedule = {}
    today = datetime.now(timezone.utc).date()

    for offset in range(8):
        day = (today + timedelta(days=offset)).strftime("%Y%m%d")

        try:
            data = fetch(f"{BASE}/scoreboard?dates={day}&limit=100")

            for event in data.get("events", []):
                status = event.get("status", {}).get("type", {})

                if status.get("completed"):
                    continue

                for competition in event.get("competitions", []):
                    competitors = competition.get("competitors", [])

                    if len(competitors) != 2:
                        continue

                    for team in competitors:
                        opponent = next(
                            (x for x in competitors if x is not team),
                            None
                        )

                        if opponent is None:
                            continue

                        abbreviation = str(
                            team.get("team", {}).get("abbreviation", "")
                        ).upper()

                        opponent_abbreviation = str(
                            opponent.get("team", {}).get("abbreviation", "")
                        ).upper()

                        if not abbreviation or not opponent_abbreviation:
                            continue

                        game = {
                            "opponent": opponent_abbreviation,
                            "home_away": team.get("homeAway"),
                            "game_date": event.get("date"),
                            "event_id": str(event.get("id", ""))
                        }

                        if abbreviation not in schedule:
                            schedule[abbreviation] = game
                        elif (
                            game["game_date"]
                            and game["game_date"] <
                            str(schedule[abbreviation].get("game_date") or "")
                        ):
                            schedule[abbreviation] = game

        except Exception as exc:
            print(f"Upcoming schedule {day}: {exc}", flush=True)

        time.sleep(0.1)

    print(
        f"Upcoming matchups identified for {len(schedule)} teams",
        flush=True
    )

    return schedule
def main():
    games = completed_game_ids()
    observations = defaultdict(list)
    failures = []
    for index, game_id in enumerate(games, 1):
        try:
            data = fetch(f'{BASE}/summary?event={game_id}')
            teams = data.get('boxscore', {}).get('teams', [])
            if len(teams) != 2:
                continue
            a, b = (team_values(x) for x in teams)
            if not a['team'] or not b['team']:
                continue
            pa, pb = possessions(a), possessions(b)
            if pa is None or pb is None:
                continue
            # Two-team average mitigates minor scorekeeper possession differences.
            pace = (pa + pb) / 2
            competitors = data.get('header', {}).get('competitions', [{}])[0].get('competitors', [])
            points = {}
            for competitor in competitors:
                abbrev = str((competitor.get('team') or {}).get('abbreviation') or '').upper()
                score = as_number(competitor.get('score'))
                if abbrev and score is not None:
                    points[abbrev] = score
            if a['team'] not in points or b['team'] not in points:
                continue
            for team, opponent in ((a, b), (b, a)):
                missed_fg = None
                if opponent['fga'] is not None and opponent['fgm'] is not None:
                    missed_fg = opponent['fga'] - opponent['fgm']
                observations[team['team']].append({
                    'opponent': opponent['team'],
                    'pace_proxy': pace,
                    'defensive_rating_proxy': 100 * points[opponent['team']] / pace,
                    'opponent_missed_fg': missed_fg,
                    'opponent_offensive_rebounds': opponent['oreb'],
                    'team_defensive_rebounds': team['dreb'],
                })
        except Exception as exc:
            failures.append(f'{game_id}: {str(exc)[:120]}')
        if index % 20 == 0:
            print(f'Matchup boxscores: {index}/{len(games)}', flush=True)
        time.sleep(0.12)

    def avg(records, key):
        vals = [r[key] for r in records if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    team_data = {}
    for team, records in sorted(observations.items()):
        team_data[team] = {
            'games': len(records),
            'pace_proxy': avg(records, 'pace_proxy'),
            'defensive_rating_proxy': avg(records, 'defensive_rating_proxy'),
            'opponent_missed_fg_per_game': avg(records, 'opponent_missed_fg'),
            'opponent_offensive_rebounds_per_game': avg(records, 'opponent_offensive_rebounds'),
            'team_defensive_rebounds_per_game': avg(records, 'team_defensive_rebounds'),
        }

    payload = {
        'updated_utc': datetime.now(timezone.utc).isoformat(),
        'window_days': DAYS_BACK,
        'games_found': len(games),
        'teams': team_data,
        'errors': failures[:50],
        'method': 'Completed ESPN box scores; estimated possessions = FGA + 0.44*FTA - OREB + TO. Pace and defensive rating are proxies, not official pace-adjusted NBA metrics. Opponent missed field goals are an opportunity indicator, not a rebound projection.',
    }
    if len(team_data) < 20:
        raise RuntimeError(f'Only {len(team_data)} teams had usable box scores; not publishing incomplete matchup data. Errors: {failures[:3]}')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix('.json.tmp')
    temp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    temp.replace(OUT)
    print(f'Saved {OUT}: {len(team_data)} teams, {len(games)} games found, {len(failures)} failures', flush=True)


if __name__ == '__main__':
    main()
