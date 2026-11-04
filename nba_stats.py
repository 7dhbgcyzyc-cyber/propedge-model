"""PropEdge NBA v0.5: experimental ESPN game-log projections.

Unofficial ESPN endpoints. Estimates are not backtested or calibrated.
"""
import json
import statistics
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
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; PropEdge/0.5; research dashboard)', 'Accept': 'application/json'}

# ESPN field -> output label. Exact labels may need alignment with PrizePicks feed.
STATS = {
    'PTS': 'Points', 'REB': 'Rebounds', 'AST': 'Assists',
    '3PM': '3-PT Made', '3PA': '3-PT Attempted',
    'FGM': 'FG Made', 'FGA': 'FG Attempted',
    'FTM': 'FT Made', 'FTA': 'FT Attempted',
    'STL': 'Steals', 'BLK': 'Blocked Shots', 'TO': 'Turnovers',
    'OREB': 'Offensive Rebounds', 'DREB': 'Defensive Rebounds',
}
COMBOS = {
    'Pts+Rebs+Asts': ('PTS', 'REB', 'AST'),
    'Pts+Rebs': ('PTS', 'REB'),
    'Pts+Asts': ('PTS', 'AST'),
    'Rebs+Asts': ('REB', 'AST'),
    'Steals+Blocks': ('STL', 'BLK'),
    'Asts+TOs': ('AST', 'TO'),
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
