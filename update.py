import os,json,csv,datetime,urllib.request,urllib.parse
from pathlib import Path
ROOT=Path(__file__).parent
OUT=ROOT/'data'; OUT.mkdir(exist_ok=True)
key=os.environ.get('PRIZEPICKS_API_KEY','')
if not key: raise SystemExit('Missing PRIZEPICKS_API_KEY secret')
rows=[]; errors=[]
for league in ('NBA','NFL'):
    url='https://prizepicksapi.com/api/v1/projections?'+urllib.parse.urlencode({'league':league,'view':'compact','limit':500})
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+key,'X-User-Request':'Build a live player-props dashboard','Accept':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=30) as resp: payload=json.load(resp)
        if isinstance(payload,list): items=payload
        elif isinstance(payload,dict):
            items=next((payload[k] for k in ('data','projections','results','items') if isinstance(payload.get(k),list)),[])
        else: items=[]
        for x in items:
            if not isinstance(x,dict): continue
            a=x.get('attributes',{}) if isinstance(x.get('attributes'),dict) else {}
            z={**a,**x}
            line=z.get('line',z.get('line_score'))
            try: line=float(line)
            except (TypeError,ValueError): continue
            rows.append({'id':str(z.get('id','')),'league':league,'player':str(z.get('player',z.get('player_name',z.get('name','')))),'team':str(z.get('team','')),'opponent':str(z.get('opponent','')),'stat':str(z.get('stat',z.get('stat_type',''))),'line':line,'tier':str(z.get('tier','')),'period':str(z.get('period',''))})
    except Exception as e: errors.append(f'{league}: {type(e).__name__}: {e}')
# Optional independently researched projections file: player,league,stat,projection,source
projs={}
p=ROOT/'projections.csv'
if p.exists():
    with p.open(newline='',encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try: projs[(r['player'].strip().lower(),r['league'].strip().upper(),r['stat'].strip().lower())]=(float(r['projection']),r.get('source','Independent estimate'))
            except (KeyError,ValueError): pass
# Independently calculated NBA model, if the game-log fetch succeeded.
model_file=OUT/'nba_model.json'
if model_file.exists():
    try:
        model_data=json.loads(model_file.read_text(encoding='utf-8'))
        for r in model_data.get('projections',[]):
            k=(r['player'].strip().lower(),r['league'],r['stat'].strip().lower())
            # Keep manually supplied independent estimates as explicit overrides.
            projs.setdefault(k,(float(r['projection']),r.get('source','PropEdge NBA model')))
    except (OSError,ValueError,KeyError,TypeError) as e:
        errors.append('NBA model load: '+str(e))
for r in rows:
    k=(r['player'].strip().lower(),r['league'],r['stat'].strip().lower())
    v=projs.get(k)
    r['projection']=v[0] if v else None
    r['source']=v[1] if v else None
    r['difference']=round(v[0]-r['line'],2) if v else None
snapshot={'updated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'lines':rows,'errors':errors,'note':'Projection differences are research signals, not estimated win probabilities. No projection is inferred from the PrizePicks line.'}
(OUT/'latest.json').write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
print(f'Fetched {len(rows)} lines. Errors: {errors}')
if not rows: raise SystemExit('No usable lines returned; leaving previous published snapshot unchanged via failed workflow.')
