"""PropEdge ESPN NBA game-log model. Unofficial ESPN endpoints; best effort.

Discovers all 30 NBA rosters, retrieves current and prior season game logs,
and writes data/nba_model.json. No invented statistics. Failed players are reported.
"""
import json
import os
import random
import re
import time
import tempfile
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'nba_model.json'
CACHE = Path(tempfile.gettempdir()) / 'propedge_espn_cache'
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
GAMELOG = 'https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{}/gamelog?season={}'
SEASONS = (2027, 2026)  # ESPN season end years: 2026-27 and 2025-26
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; PropEdge/0.2; research dashboard)', 'Accept': 'application/json'}


def norm(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower())


def fetch(url, cache_path=None, max_age_hours=18):
    if cache_path and cache_path.exists():
        if time.time() - cache_path.stat().st_mtime < max_age_hours * 3600:
            return json.loads(cache_path.read_text())
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
        print(f'Using cached data after request failure: {url}: {error}')
        return json.loads(cache_path.read_text())
    raise RuntimeError(f'{url}: {error}')


def roster_players():
    listing = fetch(BASE + '/teams?limit=100', CACHE / 'teams.json', 24)
    teams = listing.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', [])
    if len(teams) < 30:
        raise RuntimeError(f'Expected 30 NBA teams; found {len(teams)}. ESPN roster format may have changed.')
    players = {}
    failures = []
    for item in teams:
        team = item.get('team', item)
        tid = str(team.get('id', ''))
        if not tid:
            continue
        try:
            data = fetch(f'{BASE}/teams/{tid}/roster', CACHE / f'roster_{tid}.json', 24)
            athletes = data.get('athletes', [])
            # ESPN sometimes groups athletes by position rather than returning a flat array.
            for group in athletes:
                entries = group.get('items', []) if isinstance(group, dict) and 'items' in group else [group]
                for athlete in entries:
                    if not isinstance(athlete, dict):
                        continue
                    pid = str(athlete.get('id', ''))
                    name = athlete.get('fullName') or athlete.get('displayName')
                    if pid and name:
                        players[pid] = name
        except Exception as exc:
            failures.append(f'{team.get("abbreviation", tid)}: {exc}')
    print(f'Roster discovery: {len(teams)} teams; {len(players)} unique players; {len(failures)} team errors', flush=True)
    if len(players) < 150:
        raise RuntimeError(f'Only {len(players)} players discovered. Refusing incomplete roster run. Examples: {failures[:3]}')
    return players, failures


def numeric(value):
    try:
        return float(str(value).replace(',', '').split(':')[0])
    except (TypeError, ValueError):
        return None


def parse_games(data, season):
    """Read ESPN gamelog event references and per-event stat arrays.

    Expected shape: labels [MIN,...,PTS], events {id: {date,...}},
    seasonTypes[].categories[].events [{eventId, stats: [...]}].
    """
    labels = [str(v).upper() for v in data.get('labels', [])]
    lookup = {name: i for i, name in enumerate(labels)}
    required = ('MIN', 'PTS', 'REB', 'AST')
    if not all(x in lookup for x in required):
        raise ValueError(f'Missing ESPN stat columns: {required}; got {labels}')
    event_meta = data.get('events', {})
    if isinstance(event_meta, list):
        event_meta = {str(x.get('id', '')): x for x in event_meta if isinstance(x, dict)}
    found = {}
    for stype in data.get('seasonTypes', []):
        for category in stype.get('categories', []):
            for entry in category.get('events', []):
                if not isinstance(entry, dict):
                    continue
                event_id = str(entry.get('eventId') or entry.get('id') or '')
                stats = entry.get('stats')
                if not event_id or not isinstance(stats, list):
                    continue
                meta = event_meta.get(event_id, {})
                date = meta.get('gameDate') or meta.get('date') or entry.get('gameDate') or entry.get('date')
                if not date:
                    continue
                values = {k: numeric(stats[lookup[k]]) if len(stats) > lookup[k] else None for k in required}
                if any(values[k] is None for k in required):
                    continue
                if values['MIN'] < 8:
                    continue
                found[event_id] = {'date': str(date), 'season': season, **values}
    return sorted(found.values(), key=lambda x: x['date'], reverse=True)


def rate(games, stat):
    mins = sum(x['MIN'] for x in games)
    return sum(x[stat] for x in games) / mins if mins else 0


def average(games, stat):
    return round(sum(x[stat] for x in games) / len(games), 2) if games else None


def project(name, current, previous):
    current = sorted(current, key=lambda x: x['date'], reverse=True)
    previous = sorted(previous, key=lambda x: x['date'], reverse=True)

    total_games = len(current) + len(previous)

    # Allow established players with prior-season history.
    # New players need at least 5 usable games.
    if total_games < 5:
        print(
            f'SKIPPED {name}: only {total_games} usable games',
            flush=True
        )
        return []

    history = current + previous
    last5 = history[:5]
    last10 = history[:10]
    last20 = history[:20]

    # Recent minutes, with a fallback to historical minutes.
    expected_minutes = (
        0.65 * average(last5, 'MIN')
        + 0.35 * average(last10, 'MIN')
    )

    if expected_minutes <= 0:
        print(f'SKIPPED {name}: invalid minutes', flush=True)
        return []

    # Distinguish historical-only estimates from current-season ones.
    if len(current) == 0:
        confidence = 'Historical only'
    elif len(current) < 3:
        confidence = 'Limited current-season data'
    elif total_games < 10:
        confidence = 'Limited sample'
    else:
        confidence = 'Standard sample'

    result = []

    for stat, label in (
        ('PTS', 'Points'),
        ('REB', 'Rebounds'),
        ('AST', 'Assists')
    ):
        weighted_rate = (
            0.30 * rate(last5, stat)
            + 0.25 * rate(last10, stat)
            + 0.25 * rate(last20, stat)
            + 0.20 * rate(current or history, stat)
        )

        prior_rate = (
            rate(previous, stat)
            if previous else weighted_rate
        )

        if not current:
            predicted_rate = prior_rate
        elif not previous:
            predicted_rate = weighted_rate
        else:
            current_weight = min(
                0.85,
                len(current) / (len(current) + 12)
            )
            predicted_rate = (
                current_weight * weighted_rate
                + (1 - current_weight) * prior_rate
            )

        projection = round(
            max(0, expected_minutes * predicted_rate), 2
        )

        result.append({
            'player': name,
            'league': 'NBA',
            'stat': label,
            'projection': projection,
            'source': 'PropEdge NBA v0.3 (ESPN game logs)',
            'games_current': len(current),
            'games_previous': len(previous),
            'sample_status': confidence,
            'minutes_est': round(expected_minutes, 1),
            'last5_avg': average(last5, stat),
            'last10_avg': average(last10, stat),
            'last20_avg': average(last20, stat),
            'last20_games_used': len(last20)
        })

    # Add combined-stat projections
    individual = {
        r['stat']: r['projection']
        for r in result
    }

    combinations = {
        'Pts+Rebs+Asts': ['Points', 'Rebounds', 'Assists'],
        'Pts+Rebs': ['Points', 'Rebounds'],
        'Pts+Asts': ['Points', 'Assists'],
        'Rebs+Asts': ['Rebounds', 'Assists']
    }

    for label, stats in combinations.items():
        result.append({
            'player': name,
            'league': 'NBA',
            'stat': label,
            'projection': round(
                sum(individual[s] for s in stats), 2
            ),
            'source': 'PropEdge NBA v0.3 (ESPN game logs)',
            'games_current': len(current),
            'games_previous': len(previous),
            'sample_status': confidence,
            'minutes_est': round(expected_minutes, 1)
        })

    return result


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    players, roster_errors = roster_players()
    errors = list(roster_errors)
    projections = []
    with_logs = 0
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
        projections.extend(project(name, logs[SEASONS[0]], logs[SEASONS[1]]))
        if index % 25 == 0 or index == len(players):
            print(f'Processed {index}/{len(players)} players; projections={len(projections)}; errors={len(errors)}', flush=True)
    if not projections:
        print('No projections produced; leaving existing model file unchanged. Errors:', errors[:10])
        raise RuntimeError('ESPN player logs did not produce any projections')
    OUT.parent.mkdir(exist_ok=True)
    payload = {
        'updated_utc': datetime.now(timezone.utc).isoformat(),
        'projections': projections,
        'errors': errors[:150],
        'summary': {'roster_players': len(players), 'players_with_logs': with_logs, 'projected_players': len(projections) // 3, 'error_count': len(errors)},
        'method': 'Experimental ESPN per-minute model; weighted last 5/10/20/current season; prior season shrinkage. Not backtested or calibrated.'
    }
    temp = OUT.with_suffix('.json.tmp')
    temp.write_text(json.dumps(payload, indent=2))
    temp.replace(OUT)
    print('Saved', OUT, 'summary:', payload['summary'], flush=True)


if __name__ == '__main__':
    main()
