"""PropEdge snapshot builder: PrizePicks feed + experimental matchup adjustments."""
import os
import json
import csv
import datetime
import urllib.request
import urllib.parse
import unicodedata
import re
import time
import math
from pathlib import Path
from collections import Counter
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data'
OUT.mkdir(exist_ok=True)
KEY = os.environ.get('PRIZEPICKS_API_KEY', '')
if not KEY:
    raise SystemExit('Missing PRIZEPICKS_API_KEY secret')

rows, errors, seen = [], [], set()

def normalized_name(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    return re.sub(r'[^a-z0-9]', '', s.encode('ascii', 'ignore').decode('ascii').lower())

def normalized_stat(value):
    s = str(value or '').lower().replace('&', '+')
    return re.sub(r'[^a-z0-9+]', '', s)

def projection_key(player, league, stat):
    return (normalized_name(player), str(league).upper(), normalized_stat(stat))

def get_text(value, field='name'):
    return str(value.get(field) or '') if isinstance(value, dict) else str(value or '')

def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None

def line_type(z):
    candidates = [z.get(k) for k in ('line_type', 'lineType', 'odds_type',
                  'oddsType', 'pick_type', 'pickType', 'promotion_type', 'tier', 'type')]
    selections = z.get('selections')
    if isinstance(selections, list):
        candidates.extend(s.get('tier') for s in selections if isinstance(s, dict) and s.get('available', True))
    for value in candidates:
        if not isinstance(value, (str, int, float)):
            continue
        t = str(value).lower()
        if 'goblin' in t:
            return 'goblin'
        if 'demon' in t:
            return 'demon'
        if t in ('standard', 'normal', 'regular'):
            return 'normal'
    return 'unknown'

def fetch_league(league):
    cursor, used_cursors, league_rows, completed = None, set(), [], False
    for page_number in range(1, 101):
        params = {'league': league, 'view': 'compact', 'limit': 1000}
        if cursor:
            params['cursor'] = cursor
        url = 'https://prizepicksapi.com/api/v1/projections?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + KEY,
            'X-User-Request': 'PropEdge player props dashboard',
            'Accept': 'application/json',
        })
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                payload = json.load(response)
        except Exception as exc:
            errors.append(f'{league} page {page_number}: {exc}')
            break
        if isinstance(payload, list):
            items, meta = payload, {}
        elif isinstance(payload, dict):
            items = next((payload[k] for k in ('data', 'projections', 'results', 'items')
                          if isinstance(payload.get(k), list)), [])
            meta = payload.get('meta') or {}
        else:
            errors.append(f'{league}: Unexpected API response')
            break
        for x in items:
            if not isinstance(x, dict):
                continue
            attrs = x.get('attributes') if isinstance(x.get('attributes'), dict) else {}
            z = {**attrs, **x}
            line = numeric(z.get('line', z.get('line_score')))
            if line is None:
                continue
            player = get_text(z.get('player') or z.get('player_name') or z.get('name'))
            player_obj = z.get('player') if isinstance(z.get('player'), dict) else {}
            market = z.get('market') if isinstance(z.get('market'), dict) else {}
            event = z.get('event') if isinstance(z.get('event'), dict) else {}
            stat = get_text(z.get('stat') or z.get('stat_type') or market.get('stat'))
            team = get_text(z.get('team') or player_obj.get('team'))
            opponent = get_text(z.get('opponent') or event.get('opponent'))
            period = get_text(z.get('period') or market.get('period'))
            item_id = str(z.get('id') or '')
            dedup_key = (league, item_id, player, stat, line, str(z.get('tier') or z.get('odds_type') or ''))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            league_rows.append({
                'id': item_id, 'league': league, 'player': player, 'team': team,
                'opponent': opponent, 'stat': stat, 'line': line,
                'tier': get_text(z.get('tier')), 'line_type': line_type(z), 'period': period,
            })
        next_cursor = meta.get('next_cursor') if isinstance(meta, dict) else None
        print(f'{league} page {page_number}: {len(items)} items, {len(league_rows)} unique lines', flush=True)
        if not next_cursor:
            completed = True
            break
        if next_cursor in used_cursors:
            errors.append(f'{league}: Repeated pagination cursor')
            break
        used_cursors.add(next_cursor)
        cursor = next_cursor
        time.sleep(1.2)
    if not completed:
        errors.append(f'{league}: Pagination incomplete')
        return []
    return league_rows

# ESPN and feed can use different abbreviations. Do not guess unrecognized team names.
TEAM_ALIASES = {
    'ATL':'ATL','BOS':'BOS','BKN':'BKN','BRK':'BKN','CHA':'CHA','CHI':'CHI',
    'CLE':'CLE','DAL':'DAL','DEN':'DEN','DET':'DET','GS':'GS','GSW':'GS',
    'HOU':'HOU','IND':'IND','LAC':'LAC','LAL':'LAL','MEM':'MEM','MIA':'MIA',
    'MIL':'MIL','MIN':'MIN','NO':'NO','NOP':'NO','NY':'NY','NYK':'NY',
    'OKC':'OKC','ORL':'ORL','PHI':'PHI','PHX':'PHX','PHO':'PHX','POR':'POR',
    'SAC':'SAC','SA':'SA','SAS':'SA','TOR':'TOR','UTA':'UTA','UTAH':'UTA',
    'WAS':'WAS','WSH':'WAS',
}

def canonical_team(value):
    s = re.sub(r'[^A-Z]', '', str(value or '').upper())
    return TEAM_ALIASES.get(s)

def load_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        errors.append(f'{path.name} load: {exc}')
        return {}

# This file is produced by nba_matchups.py in the preceding workflow step.
matchups = load_json(OUT / 'nba_matchups.json')
team_metrics = matchups.get('teams') if isinstance(matchups.get('teams'), dict) else {}
schedule = matchups.get('upcoming_matchups') if isinstance(matchups.get('upcoming_matchups'), dict) else {}
metrics = {canonical_team(k): v for k, v in team_metrics.items() if canonical_team(k) and isinstance(v, dict)}
upcoming = {canonical_team(k): v for k, v in schedule.items() if canonical_team(k) and isinstance(v, dict)}

def league_mean(field):
    vals = [numeric(v.get(field)) for v in metrics.values() if numeric(v.get(field)) is not None and numeric(v.get('games')) is not None and numeric(v.get('games')) >= 3]
    return sum(vals) / len(vals) if len(vals) >= 20 else None

AVG_PACE = league_mean('pace_proxy')
AVG_DEF = league_mean('defensive_rating_proxy')
AVG_MISSES = league_mean('opponent_missed_fg_per_game')

# Conservative, uncalibrated caps: no adjustment unless opponent and sample validate.
def adjustment(base, stat, opponent, location, model_record):
    details = {'defense': 0.0, 'pace': 0.0, 'rebound_opportunities': 0.0, 'home_away': 0.0}
    info = metrics.get(opponent)
    if not info or numeric(info.get('games')) is None or numeric(info.get('games')) < 3:
        return base, details, False
    key = normalized_stat(stat)
    scoring = key in ('points', 'pts')
    rebounding = key in ('rebounds', 'rebs')
    # Combined props require component-level adjustments, not a full scoring multiplier.
    if not (scoring or rebounding):
        return base, details, False
    if scoring and AVG_DEF:
        defense = numeric(info.get('defensive_rating_proxy'))
        if defense is not None:
            details['defense'] = base * max(-0.04, min(0.04, (defense / AVG_DEF - 1) * 0.35))
    if AVG_PACE:
        pace = numeric(info.get('pace_proxy'))
        if pace is not None:
            details['pace'] = base * max(-0.035, min(0.035, (pace / AVG_PACE - 1) * 0.35))
    if rebounding and AVG_MISSES:
        misses = numeric(info.get('opponent_missed_fg_per_game'))
        # Opponent's missed FG measure is shots missed BY the opponent, not
        # necessarily shots missed AGAINST them. Do not misapply it to the player's rebounds.
        # The current dataset lacks a reliable opponent-allowed rebound-opportunity measure.
        _ = misses
    # v0.5 model home/away fields have not been schema-verified: retain as metadata only.
    _ = location, model_record
    total = sum(details.values())
    total = max(-0.06 * base, min(0.06 * base, total))
    adjusted = round(max(0, base + total), 2)
    return adjusted, details, adjusted != base

for league in ('NBA', 'NFL'):
    fetched = fetch_league(league)
    rows.extend(fetched)
    print(f'{league}: {len(fetched)} total lines', flush=True)

projs = {}
p = ROOT / 'projections.csv'
if p.exists():
    with p.open(newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                value = numeric(r['projection'])
                if value is not None:
                    projs[projection_key(r['player'], r['league'], r['stat'])] = (value, r.get('source', 'Independent estimate'), {})
            except (KeyError, ValueError, TypeError):
                pass

model_data = load_json(OUT / 'nba_model.json')
for r in model_data.get('projections', []):
    if not isinstance(r, dict):
        continue
    try:
        value = numeric(r.get('projection'))
        if value is None:
            continue
        k = projection_key(r['player'], r['league'], r['stat'])
        projs.setdefault(k, (value, r.get('source', 'PropEdge NBA model'), r))
    except (KeyError, ValueError, TypeError):
        continue

# Optional manually transcribed PropSims projections. These NEVER replace PropEdge.
# The dataset must be for the current Central Time calendar date; stale snapshots
# are ignored, so the daily workflow can continue safely on later days.
def load_propsims():
    path = OUT / 'propsims.json'
    if not path.exists():
        return {}, {'status': 'missing', 'matched': 0}
    payload = load_json(path)
    if not isinstance(payload, dict):
        return {}, {'status': 'invalid', 'matched': 0}
    if payload.get('source') != 'PropSims' or payload.get('points_definition') != 'PTS IF PLAY':
        return {}, {'status': 'wrong_schema_or_points_definition', 'matched': 0}
    run_text = str(payload.get('model_run') or '')
    match = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', run_text)
    today_ct = datetime.datetime.now(ZoneInfo('America/Chicago')).date().isoformat()
    if not match or match.group(1) != today_ct:
        return {}, {'status': 'stale', 'model_run': run_text, 'expected_date': today_ct, 'matched': 0}
    columns = payload.get('columns')
    required = ('player', 'points', 'rebounds', 'assists', 'threes')
    if not isinstance(columns, list) or any(c not in columns for c in required):
        return {}, {'status': 'missing_columns', 'matched': 0}
    indices = {k: columns.index(k) for k in required}
    players = payload.get('players')
    if not isinstance(players, list) or len(players) < 10:
        return {}, {'status': 'too_few_players', 'matched': 0}
    lookup = {}
    for item in players:
        if not isinstance(item, list) or len(item) < len(columns):
            continue
        name = normalized_name(item[indices['player']])
        if not name or name in lookup:
            continue
        stats = {key: numeric(item[indices[key]]) for key in required[1:]}
        if any(value is None or value < 0 for value in stats.values()):
            continue
        lookup[name] = stats
    if len(lookup) < 10:
        return {}, {'status': 'insufficient_valid_players', 'matched': 0}
    return lookup, {'status': 'loaded', 'model_run': run_text,
                    'players': len(lookup), 'matched': 0}


def propsims_stat_projection(stats, stat):
    # Only explicit, whole-game statistical markets are supported.
    # Unknown formats return None rather than guessing.
    normalized = normalized_stat(stat)
    aliases = {
        'points': 'points', 'pts': 'points',
        'rebounds': 'rebounds', 'rebs': 'rebounds', 'reb': 'rebounds',
        'assists': 'assists', 'asts': 'assists', 'ast': 'assists',
        '3ptmade': 'threes', '3ptsmade': 'threes', '3pm': 'threes',
        '3pointersmade': 'threes', '3pointsmade': 'threes',
        'threepointersmade': 'threes', 'threes': 'threes',
        'pra': ('points', 'rebounds', 'assists'),
        'pr': ('points', 'rebounds'),
        'pa': ('points', 'assists'),
        'ra': ('rebounds', 'assists'),
    }
    if normalized in ('pts+reb+ast', 'pts+rebs+asts', 'points+rebounds+assists',
                      'pointsreboundsassists', 'ptsrebsasts', 'pts+rebs+ast'):
        keys = ('points', 'rebounds', 'assists')
    elif normalized in ('points+rebounds', 'pts+rebs', 'pts+reb', 'pointsrebounds'):
        keys = ('points', 'rebounds')
    elif normalized in ('points+assists', 'pts+asts', 'pts+ast', 'pointsassists'):
        keys = ('points', 'assists')
    elif normalized in ('rebounds+assists', 'rebs+asts', 'reb+ast', 'reboundsassists'):
        keys = ('rebounds', 'assists')
    else:
        keys = aliases.get(normalized)
    if not keys:
        return None
    if isinstance(keys, str):
        keys = (keys,)
    if any(stats.get(k) is None for k in keys):
        return None
    return round(sum(stats[k] for k in keys), 2)

propsims_lookup, propsims_meta = load_propsims()

for r in rows:
    v = projs.get(projection_key(r['player'], r['league'], r['stat']))
    base = v[0] if v else None
    r['base_projection'] = base
    r['projection'] = base
    r['adjusted_projection'] = base
    r['source'] = v[1] if v else None
    r['matchup_adjustments'] = None
    r['matchup_applied'] = False
    r['matchup_opponent'] = None
    r['matchup_home_away'] = None
    r['matchup_game_date'] = None
    if base is not None and r['league'] == 'NBA':
        team = canonical_team(r.get('team'))
        game = upcoming.get(team) if team else None
        if game:
            opponent = canonical_team(game.get('opponent'))
            feed_opponent = canonical_team(r.get('opponent'))
            # A feed-provided opponent mismatch means the line may refer to a later game.
            if opponent and (not feed_opponent or opponent == feed_opponent):
                r['matchup_opponent'] = opponent
                r['matchup_home_away'] = game.get('home_away')
                r['matchup_game_date'] = game.get('game_date')
                adjusted, details, applied = adjustment(base, r['stat'], opponent, game.get('home_away'), v[2])
                r['adjusted_projection'] = adjusted
                r['projection'] = adjusted
                r['matchup_adjustments'] = {k: round(x, 3) for k, x in details.items()}
                r['matchup_applied'] = applied
    r['difference'] = round(r['projection'] - r['line'], 2) if r['projection'] is not None else None
    # Comparisons do not change original projection/difference or matchup logic.
    external = (propsims_lookup.get(normalized_name(r['player']))
                if r['league'] == 'NBA' else None)
    propsims_value = propsims_stat_projection(external, r['stat']) if external else None
    r['propsims_projection'] = propsims_value
    r['propsims_difference'] = (round(propsims_value - r['line'], 2)
                                if propsims_value is not None else None)
    original_diff = r['difference']
    external_diff = r['propsims_difference']
    r['model_agreement'] = None
    if original_diff is not None and external_diff is not None:
        if original_diff > 0 and external_diff > 0:
            r['model_agreement'] = 'Both Over'
        elif original_diff < 0 and external_diff < 0:
            r['model_agreement'] = 'Both Under'
        elif original_diff == 0 or external_diff == 0:
            r['model_agreement'] = 'At Line'
        else:
            r['model_agreement'] = 'Models Disagree'
        # Lower of the two absolute differences when both agree.
        r['agreement_edge'] = (round(min(abs(original_diff), abs(external_diff)), 2)
                               if r['model_agreement'] in ('Both Over', 'Both Under')
                               else None)
    else:
        r['agreement_edge'] = None
    if propsims_value is not None:
        propsims_meta['matched'] += 1


snapshot = {
    'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'lines': rows, 'errors': errors,
    'propsims': propsims_meta,
    'matchup_data_updated_utc': matchups.get('updated_utc'),
    'note': ('Experimental, unbacktested projections. Matchup adjustments apply only to supported '
             'NBA points and rebounds props when team and opponent data match. Rebound opportunity '
             'and home/away adjustments remain zero pending reliable schema validation. '
             'PropSims manual PTS IF PLAY projections are shown separately when current; '
             'combined markets sum components and are not separately modeled. '
             'Projection differences are research signals, not estimated win probabilities.'),
}
if not rows or any('Pagination incomplete' in e for e in errors):
    print(f'API errors: {errors}', flush=True)
    print('Keeping previous PrizePicks snapshot rather than publishing incomplete data.', flush=True)
else:
    (OUT / 'latest.json').write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
    print(f"Fetched {len(rows)} lines. Matched {sum(r['projection'] is not None for r in rows)}. "
          f"Matchup-adjusted {sum(r['matchup_applied'] for r in rows)}. "
          f"Line types: {dict(Counter(r['line_type'] for r in rows))}. Errors: {errors}", flush=True)
