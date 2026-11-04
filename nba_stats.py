"""PropEdge NBA v0.4: experimental ESPN game-log projections.

Unofficial ESPN endpoints. Estimates are not backtested or calibrated.
"""
import json
import random
import re
import time
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'nba_model.json'
CACHE = Path(tempfile.gettempdir()) / 'propedge_espn_cache'
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
GAMELOG = 'https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{}/gamelog?season={}'
SEASONS = (2027, 2026)
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; PropEdge/0.4; research dashboard)', 'Accept': 'application/json'}

# ESPN field -> output label. Exact labels may need alignment with PrizePicks feed.
STATS = {
    'PTS': 'Points', 'REB': 'Rebounds', 'AST': 'Assists',
    '3PM': '3-PT Made', '3PA': '3-PT Attempted',
    'FGM': 'FG Made', 'FGA': 'FG Attempted',
    'FTM': 'FT Made', 'FTA': 'FT Attempted',
    'STL': 'Steals', 'BLK': 'Blocked Shots', 'TO': 'Turnovers',
}
COMBOS = {
    'Pts+Rebs+Asts': ('PTS', 'REB', 'AST'),
    'Pts+Rebs': ('PTS', 'REB'),
    'Pts+Asts': ('PTS', 'AST'),
    'Rebs+Asts': ('REB', 'AST'),
    'Steals+Blocks': ('STL', 'BLK'),
}

def fetch(url, cache_path=None, max_age_hours=18):
    if cache_path and cache_path.exists():
        try:
            if time.time() - cache_path.stat().st_mtime < max_age_hours * 3600:
                return json.loads(cache_path.read_text())
        except (ValueError, OSError):
            pass
    error = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=18) as response:
                obj = json.load(response)
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(obj))
            time.sleep(0.16 + random.uniform(0, 0.14))
            return obj
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            error = exc
            if attempt < 2:
                time.sleep((attempt + 1) * 1.5)
    if cache_path and cache_path.exists():
        try:
            print(f'Using older cached data after request failure: {url}', flush=True)
            return json.loads(cache_path.read_text())
        except (ValueError, OSError):
            pass
    raise RuntimeError(f'{url}: {error}')

def roster_players():
    listing = fetch(BASE + '/teams?limit=100', CACHE / 'teams.json', 24)
    teams = listing.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', [])
    if len(teams) < 30:
        raise RuntimeError(f'Expected 30 NBA teams; found {len(teams)}')
    players, failures = {}, []
    for item in teams:
        team = item.get('team', item)
        tid = str(team.get('id', ''))
        if not tid:
            continue
        try:
            data = fetch(f'{BASE}/teams/{tid}/roster', CACHE / f'roster_{tid}.json', 24)
            for group in data.get('athletes', []):
                entries = group.get('items', []) if isinstance(group, dict) and 'items' in group else [group]
                for athlete in entries:
                    if isinstance(athlete, dict):
                        pid = str(athlete.get('id', ''))
                        name = athlete.get('fullName') or athlete.get('displayName')
                        if pid and name:
                            players[pid] = name
        except Exception as exc:
            failures.append(f'{team.get("abbreviation", tid)}: {exc}')
    print(f'Roster discovery: {len(teams)} teams; {len(players)} unique players; {len(failures)} team errors', flush=True)
    if len(players) < 150:
        raise RuntimeError(f'Incomplete roster: only {len(players)} players')
    return players, failures

def number(value):
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None

def minutes(value):
    if isinstance(value, str) and ':' in value:
        parts = value.split(':')
        try:
            return float(parts[0]) + float(parts[1]) / 60
        except (ValueError, IndexError):
            return None
    return number(value)

def made_attempted(value):
    # ESPN commonly represents made-attempted as "3-8"; also accept "3/8".
    if value is None:
        return None, None
    match = re.fullmatch(r'\s*(\d+)\s*[-/]\s*(\d+)\s*', str(value))
    if not match:
        return None, None
    made, attempted = map(float, match.groups())
    if made > attempted:
        return None, None
    return made, attempted

def parse_games(data, season):
    labels = [str(v).upper().strip() for v in data.get('labels', [])]
    lookup = {name: i for i, name in enumerate(labels)}
    for field in ('MIN', 'PTS', 'REB', 'AST'):
        if field not in lookup:
            raise ValueError(f'Missing ESPN column {field}; got {labels}')
    event_meta = data.get('events', {})
    if isinstance(event_meta, list):
        event_meta = {str(x.get('id', '')): x for x in event_meta if isinstance(x, dict)}
    found = {}
    for stype in data.get('seasonTypes', []):
        # Exclude preseason and postseason when the ESPN category supplies a type.
        kind = str(stype.get('type', stype.get('id', ''))).lower()
        if kind in ('1', '3', 'preseason', 'postseason', 'playoffs'):
            continue
        for category in stype.get('categories', []):
            for entry in category.get('events', []):
                if not isinstance(entry, dict):
                    continue
                eid = str(entry.get('eventId') or entry.get('id') or '')
                stats = entry.get('stats')
                if not eid or not isinstance(stats, list):
                    continue
                meta = event_meta.get(eid, {})
                date = meta.get('gameDate') or meta.get('date') or entry.get('gameDate') or entry.get('date')
                if not date:
                    continue
                def field(label):
                    i = lookup.get(label)
                    return stats[i] if i is not None and i < len(stats) else None
                mins = minutes(field('MIN'))
                if mins is None or mins < 8:
                    continue
                values = {'MIN': mins}
                for label in ('PTS', 'REB', 'AST', 'STL', 'BLK', 'TO'):
                    values[label] = number(field(label))
                for label, prefix in (('3PT', '3P'), ('FG', 'FG'), ('FT', 'FT')):
                    made, attempts = made_attempted(field(label))
                    values[prefix + 'M'] = made
                    values[prefix + 'A'] = attempts
                if any(values[k] is None for k in ('PTS', 'REB', 'AST')):
                    continue
                found[eid] = {'date': str(date), 'season': season, **values}
    return sorted(found.values(), key=lambda x: x['date'], reverse=True)

def rate(games, stat):
    usable = [g for g in games if g.get(stat) is not None]
    mins = sum(g['MIN'] for g in usable)
    return sum(g[stat] for g in usable) / mins if mins else None

def average(games, stat):
    values = [g[stat] for g in games if g.get(stat) is not None]
    return round(sum(values) / len(values), 2) if values else None

def project(name, current, previous):
    current = sorted(current, key=lambda x: x['date'], reverse=True)
    previous = sorted(previous, key=lambda x: x['date'], reverse=True)
    history = current + previous
    if len(history) < 5:
        return []
    last5, last10, last20 = history[:5], history[:10], history[:20]
    expected_minutes = 0.65 * average(last5, 'MIN') + 0.35 * average(last10, 'MIN')
    if expected_minutes <= 0:
        return []
    status = ('Historical only' if not current else
              'Limited current-season data' if len(current) < 3 else
              'Limited sample' if len(history) < 10 else 'Standard sample')
    predictions, output = {}, []
    for stat, label in STATS.items():
        # Require at least 5 real observations of this specific statistic.
        if sum(g.get(stat) is not None for g in history) < 5:
            continue
        rates = [rate(window, stat) for window in (last5, last10, last20, current or history)]
        if any(v is None for v in rates):
            continue
        weighted = sum(w * v for w, v in zip((.30, .25, .25, .20), rates))
        prior = rate(previous, stat)
        if not current:
            predicted_rate = prior
        elif prior is None:
            predicted_rate = weighted
        else:
            weight = min(.85, len(current) / (len(current) + 12))
            predicted_rate = weight * weighted + (1 - weight) * prior
        if predicted_rate is None:
            continue
        estimate = round(max(0, expected_minutes * predicted_rate), 2)
        predictions[stat] = estimate
        output.append({
            'player': name, 'league': 'NBA', 'stat': label,
            'projection': estimate, 'source': 'PropEdge NBA v0.4 (ESPN game logs)',
            'games_current': len(current), 'games_previous': len(previous),
            'sample_status': status, 'minutes_est': round(expected_minutes, 1),
            'last5_avg': average(last5, stat), 'last10_avg': average(last10, stat),
            'last20_avg': average(last20, stat),
        })
    for label, components in COMBOS.items():
        if not all(s in predictions for s in components):
            continue
        output.append({
            'player': name, 'league': 'NBA', 'stat': label,
            'projection': round(sum(predictions[s] for s in components), 2),
            'source': 'PropEdge NBA v0.4 (ESPN game logs)',
            'games_current': len(current), 'games_previous': len(previous),
            'sample_status': status, 'minutes_est': round(expected_minutes, 1),
        })
    return output

def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    players, roster_errors = roster_players()
    errors = list(roster_errors)
    projections = []
    with_logs, projected_players = 0, 0
    for index, (pid, name) in enumerate(players.items(), 1):
        logs = {}
        for season in SEASONS:
            try:
                obj = fetch(GAMELOG.format(pid, season), CACHE / f'gamelog_{pid}_{season}.json', 18 if season == SEASONS[0] else 24 * 14)
                logs[season] = parse_games(obj, season)
            except Exception as exc:
                errors.append(f'{name} ({season}): {str(exc)[:160]}')
                logs[season] = []
        if logs[SEASONS[0]] or logs[SEASONS[1]]:
            with_logs += 1
        player_projections = project(name, logs[SEASONS[0]], logs[SEASONS[1]])
        if player_projections:
            projected_players += 1
            projections.extend(player_projections)
        if index % 25 == 0 or index == len(players):
            print(f'Processed {index}/{len(players)} players; projections={len(projections)}; errors={len(errors)}', flush=True)
    if not projections:
        raise RuntimeError('No ESPN projections produced; keeping previous model file')
    payload = {
        'updated_utc': datetime.now(timezone.utc).isoformat(),
        'projections': projections,
        'errors': errors[:150],
        'summary': {
            'roster_players': len(players), 'players_with_logs': with_logs,
            'projected_players': projected_players, 'projection_entries': len(projections),
            'error_count': len(errors),
        },
        'method': 'Experimental ESPN per-minute estimates, recent 5/10/20 and prior-season shrinkage; not backtested or calibrated.'
    }
    OUT.parent.mkdir(exist_ok=True)
    temp = OUT.with_suffix('.json.tmp')
    temp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    temp.replace(OUT)
    print('Saved', OUT, 'summary:', payload['summary'], flush=True)

if __name__ == '__main__':
    main()
