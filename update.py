import os, json, csv, datetime, urllib.request, urllib.parse, unicodedata, re
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / 'data'
OUT.mkdir(exist_ok=True)
key = os.environ.get('PRIZEPICKS_API_KEY', '')
if not key:
    raise SystemExit('Missing PRIZEPICKS_API_KEY secret')

rows, errors = [], []

def normalized_name(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = s.encode('ascii', 'ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]', '', s)

def normalized_stat(value):
    s = str(value or '').lower().replace('&', '+')
    return re.sub(r'[^a-z0-9+]', '', s)

def line_type(z):
    # Do not infer the type from the line's numerical value.
    candidates = [z.get(k) for k in (
        'line_type', 'lineType', 'odds_type', 'oddsType',
        'pick_type', 'pickType', 'promotion_type', 'tier', 'type'
    )]
    for value in candidates:
        if not isinstance(value, (str, int, float)):
            continue
        text = str(value).strip().lower()
        if 'goblin' in text: return 'goblin'
        if 'demon' in text: return 'demon'
        if text in ('normal', 'standard', 'regular'): return 'normal'
    return 'unknown'

for league in ('NBA', 'NFL'):
    url = 'https://prizepicksapi.com/api/v1/projections?' + urllib.parse.urlencode({
        'league': league, 'view': 'compact', 'limit': 500
    })
    req = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + key,
        'X-User-Request': 'Build a live player-props dashboard',
        'Accept': 'application/json'
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = next((payload[k] for k in ('data', 'projections', 'results', 'items')
                          if isinstance(payload.get(k), list)), [])
        else:
            items = []
        for x in items:
            if not isinstance(x, dict): continue
            a = x.get('attributes', {}) if isinstance(x.get('attributes'), dict) else {}
            z = {**a, **x}
            line = z.get('line', z.get('line_score'))
            try: line = float(line)
            except (TypeError, ValueError): continue
            rows.append({
                'id': str(z.get('id', '')),
                'league': league,
                'player': str(z.get('player', z.get('player_name', z.get('name', '')))),
                'team': str(z.get('team', '')),
                'opponent': str(z.get('opponent', '')),
                'stat': str(z.get('stat', z.get('stat_type', ''))),
                'line': line,
                'tier': str(z.get('tier', '')),
                'line_type': line_type(z),
                'period': str(z.get('period', ''))
            })
        print(f'{league}: received {len(items)} items; parsed {sum(r["league"] == league for r in rows)} lines')
    except Exception as e:
        errors.append(f'{league}: {type(e).__name__}: {e}')

projs = {}
def projection_key(player, league, stat):
    return (normalized_name(player), str(league).upper(), normalized_stat(stat))

p = ROOT / 'projections.csv'
if p.exists():
    with p.open(newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                projs[projection_key(r['player'], r['league'], r['stat'])] = (
                    float(r['projection']), r.get('source', 'Independent estimate')
                )
            except (KeyError, ValueError, TypeError):
                pass

model_file = OUT / 'nba_model.json'
if model_file.exists():
    try:
        model_data = json.loads(model_file.read_text(encoding='utf-8'))
        for r in model_data.get('projections', []):
            k = projection_key(r['player'], r['league'], r['stat'])
            projs.setdefault(k, (float(r['projection']), r.get('source', 'PropEdge NBA model')))
    except (OSError, ValueError, KeyError, TypeError) as e:
        errors.append('NBA model load: ' + str(e))

for r in rows:
    v = projs.get(projection_key(r['player'], r['league'], r['stat']))
    r['projection'] = v[0] if v else None
    r['source'] = v[1] if v else None
    r['difference'] = round(v[0] - r['line'], 2) if v else None

snapshot = {
    'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'lines': rows,
    'errors': errors,
    'note': 'Projection differences are research signals, not estimated win probabilities. No projection is inferred from the PrizePicks line.'
}
if not rows:
    raise SystemExit('No usable lines returned; leaving previous published snapshot unchanged.')
(OUT / 'latest.json').write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
from collections import Counter
print(f'Fetched {len(rows)} lines. Matched {sum(r["projection"] is not None for r in rows)}. Line types: {dict(Counter(r["line_type"] for r in rows))}. Errors: {errors}')
