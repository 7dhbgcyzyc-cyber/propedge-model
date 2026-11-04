"""Experimental NBA PTS/REB/AST projections from independent NBA game logs.
Fetches 2026-27 and 2025-26 regular season logs using nba_api; failure never fabricates values.
"""
import csv, json, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent

def norm(s):
    s = unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]','',s)

def get_logs(season):
    from nba_api.stats.endpoints import leaguegamelog
    obj=leaguegamelog.LeagueGameLog(season=season,player_or_team_abbreviation='P',season_type_all_star='Regular Season',timeout=40)
    return obj.get_data_frames()[0].to_dict('records')

def main():
    data=[]; errors=[]
    for season in ('2026-27','2025-26'):
        try:
            entries=get_logs(season)
            for r in entries:
                r['_season']=season
            data.extend(entries)
            print(f'NBA logs {season}: {len(entries)}')
        except Exception as exc:
            errors.append(f'{season}: {type(exc).__name__}: {exc}')
    if not data:
        print('No NBA logs available; keeping existing NBA projections unchanged. '+str(errors))
        return
    players={}
    for r in data:
        name=norm(r.get('PLAYER_NAME',''))
        if not name: continue
        players.setdefault(name,[]).append(r)
    out=[]
    for name,games in players.items():
        games.sort(key=lambda r:str(r.get('GAME_DATE','')),reverse=True)
        current=[r for r in games if r['_season']=='2026-27' and float(r.get('MIN') or 0)>=8]
        previous=[r for r in games if r['_season']=='2025-26' and float(r.get('MIN') or 0)>=8]
        if len(current)<3 or len(current)+len(previous)<8: continue
        last5=current[:5]; last10=current[:10]; last20=current[:20]; season=current
        # Project playing time separately. A 3-game window is highly uncertain.
        min5=sum(float(x['MIN']) for x in last5)/len(last5)
        min10=sum(float(x['MIN']) for x in last10)/len(last10)
        expected_minutes=0.65*min5+0.35*min10
        for stat in ('PTS','REB','AST'):
            def rate(group):
                return sum(float(x.get(stat) or 0) for x in group)/max(1,sum(float(x['MIN']) for x in group))
            # Small current-season samples shrink toward previous season's per-minute production.
            cur_rate=.30*rate(last5)+.25*rate(last10)+.25*rate(last20)+.20*rate(season)
            prior_rate=rate(previous) if previous else cur_rate
            current_weight=min(.85,len(current)/(len(current)+12))
            predicted_rate=current_weight*cur_rate+(1-current_weight)*prior_rate
            prediction=round(max(0,expected_minutes*predicted_rate),2)
            label={'PTS':'Points','REB':'Rebounds','AST':'Assists'}[stat]
            out.append({'player':games[0]['PLAYER_NAME'],'league':'NBA','stat':label,'projection':prediction,'source':'PropEdge NBA v0.1 (NBA game logs)','games_current':len(current),'minutes_est':round(expected_minutes,1),'last5_avg':round(sum(float(x.get(stat) or 0) for x in last5)/len(last5),2),'last10_avg':round(sum(float(x.get(stat) or 0) for x in last10)/len(last10),2),'last20_avg':round(sum(float(x.get(stat) or 0) for x in last20)/len(last20),2),'last20_games_used':len(last20)})
    target=ROOT/'data';target.mkdir(exist_ok=True)
    (target/'nba_model.json').write_text(json.dumps({'updated_utc':datetime.now(timezone.utc).isoformat(),'projections':out,'errors':errors,'method':'experimental per-minute model; 5/10/20/season weighted and prior-season shrinkage; not calibrated'},indent=2))
    print(f'NBA model projections: {len(out)}; warnings: {errors}')

if __name__=='__main__': main()
