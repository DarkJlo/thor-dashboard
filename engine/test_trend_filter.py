"""Offline test for the opt-in portfolio key trend_filter: donchian55_slope (TREND55). PAPER ONLY.
Longs only if the last completed UTC day's close > 55d Donchian midpoint AND the midpoint rose over 5 days; shorts mirrored;
unknown (<61 completed days, missing day, no data) -> skip. No network (daily candles are synthetic), never touches this directory's
tracker.db: the engine is copied to a temp dir. usage: python3 test_trend_filter.py"""
import os,sys,shutil,tempfile,subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
HARNESS=r'''
import sys,json; sys.argv=['x']
import numpy as np, pandas as pd, tracker as T
DAY=T.DAYMS; ms=lambda s:int(pd.Timestamp(s,tz='UTC').timestamp()*1000)
T.hl_meta=lambda: json.load(open('hl_meta.json')); T.btc_trend=lambda: None
DC0=T.daily_candles; T.requests=None   # any network use would crash the test
def series(n_days,last_day,f):   # {day: (h,l,c)} for days last_day-n_days+1..last_day, close = f(i)
    return {d:(f(i)*1.01,f(i)*0.99,f(i)) for i,d in enumerate(range(last_day-n_days+1,last_day+1))}
t=ms('2026-10-20 12:00:00'); k=t//DAY-1
UP=series(120,k,lambda i:100+i); DOWN=series(120,k,lambda i:300-i); FLAT=series(120,k,lambda i:100+(i%2))
n=0
# 1-4: long/short allowed/blocked
assert T.donchian_trend(UP,t)==1; n+=1                  # long allowed in a rising regime, short blocked
assert T.donchian_trend(DOWN,t)==-1; n+=1               # short allowed in a falling regime, long blocked
assert T.donchian_trend(FLAT,t)==0; n+=1                # flat midpoint: neither
U2=dict(UP); U2[k]=(UP[k][0],1.0,1.0)                   # close below mid while mid still rising -> neither (close fails)
assert T.donchian_trend(U2,t)==0; n+=1
# 5-7: history / data
assert T.donchian_trend(series(60,k,lambda i:100+i),t) is None; n+=1   # <61 completed days (55 window + 5 slope + day k): unknown
assert T.donchian_trend(series(61,k,lambda i:100+i),t)==1; n+=1        # exactly 61: known
G=dict(UP); del G[k-10]; assert T.donchian_trend(G,t) is None; n+=1    # a missing day inside the window: unknown (backtest: NaN)
assert T.donchian_trend(None,t) is None and T.donchian_trend({},t) is None; n+=1
# 9-10: no lookahead: the signal day itself and later days are ignored; a signal at 00:00:01 UTC uses the previous day
L=dict(UP); L.update({k+1:(1e9,1.0,1.0),k+2:(1e9,1.0,1.0)})
assert T.donchian_trend(L,t)==1 and T.donchian_trend(L,(k+1)*DAY+1000)==1; n+=1
assert T.donchian_trend(L,(k+2)*DAY+1000)==0; n+=1      # one day later, day k+1 (close 1 < mid) is the completed day
# 11: parity with the backtest's pandas rolling logic (scratch/bt_idea1_trend.py, variant b) on random data with gaps
rng=np.random.default_rng(7); days=list(range(20000,20300)); c=100*np.exp(np.cumsum(rng.normal(0,0.03,len(days))))
R={d:(x*(1+abs(rng.normal(0,.02))),x*(1-abs(rng.normal(0,.02))),x) for d,x in zip(days,c) if rng.random()>0.01}
g=pd.DataFrame.from_dict(R,orient='index',columns=['h','l','c']).sort_index(); g=g.reindex(range(g.index.min(),g.index.max()+1))
hh=g.h.shift(1).rolling(55,min_periods=55).max(); ll=g.l.shift(1).rolling(55,min_periods=55).min(); g['mid']=(hh+ll)/2; g['slope']=g.mid-g.mid.shift(5)
m=0
for d in range(20001,20300):
    tt=d*DAY+3600_000; kk=d-1; got=T.donchian_trend(R,tt)
    if kk not in g.index or pd.isna(g.at[kk,'mid']) or pd.isna(g.at[kk,'c']) or pd.isna(g.at[kk,'slope']): exp=None
    else:
        rg=1 if g.at[kk,'c']>g.at[kk,'mid'] else -1; sl=g.at[kk,'slope']; sl=1 if sl>0 else (-1 if sl<0 else 0)
        exp=rg if rg==sl else 0
    assert got==exp,(d,got,exp); m+=got is not None
assert m>100,m; n+=1
# 12: portfolio integration: only TREND55 is filtered; reasons skip_trend / skip_trend_nodata; ALL unchanged
cfg=T.CFG['portfolios']; assert cfg['TREND55']['trend_filter']=='donchian55_slope' and cfg['TREND55']['timeframes']==['1h','4h','1d']
T.CFG['portfolios']={k2:cfg[k2] for k2 in ('ALL','TREND55')}
D={'BTC/USD':UP,'ETH/USD':UP,'SOL/USD':DOWN,'DOGE/USD':DOWN,'AVAX/USD':series(30,k,lambda i:100+i),'LINK/USD':None,'XRP/USD':UP}
T.daily_candles=lambda s:D[s]
c=T.db()
for key_,sym,dr,tf in [('btcL','BTC/USD','Long','1h'),('ethS','ETH/USD','Short','4h'),('solS','SOL/USD','Short','1d'),('dogeL','DOGE/USD','Long','1h'),
                    ('avaxL','AVAX/USD','Long','4h'),('linkL','LINK/USD','Long','1h'),('xrp15','XRP/USD','Long','15m')]:
    sg=-1 if dr=='Short' else 1; e=100.0; tp1=e+sg*3; row=dict(key=key_,symbol=sym,timeframe=tf,direction=dr,entry_low=e-0.5*(sg>0),entry_high=e+0.5*(sg<0),tp1=tp1,tp2=tp1+sg,tp3=tp1+2*sg,
        invalid=tp1+5*sg,dt='',raw_text='',source='ask_thor',format='new',start_ts=t,first_seen_ts=t,status='closed',fill_ts=t+3600_000,entry=e,stop=e-(tp1-e)/1.5,
        exit_ts=t+7200_000,exit_px=tp1,reason='tp1',last_ts=t+7200_000,last_px=tp1,note=None,px_src='test')
    c.execute(f"insert into signals ({','.join(row)}) values ({','.join('?'*len(row))})",list(row.values()))
c.commit(); c.close(); c=T.db(); T.confluence(c); T.portfolios(c)
st=dict(c.execute("select key,status from trades where portfolio='TREND55'").fetchall())
assert st=={'btcL':'closed','ethS':'skip_trend','solS':'closed','dogeL':'skip_trend','avaxL':'skip_trend_nodata','linkL':'skip_trend_nodata'},st   # 15m never considered
al=dict(c.execute("select key,status from trades where portfolio='ALL'").fetchall()); assert all(v=='closed' for v in al.values()) and len(al)==7,al
n+=1
# 13: regression (closed_trades flap 815<816): a known decision is memoised; a later run whose daily fetch is empty/None keeps it
D['BTC/USD']=None; D['ETH/USD']={}; T.portfolios(c)
st2=dict(c.execute("select key,status from trades where portfolio='TREND55'").fetchall()); assert st2==st,st2
assert c.execute("select count(*) from trend_memo").fetchone()[0]==4   # unknown (avax, link) never memoised -> retried next run
n+=1
# 14: daily_candles retries an empty (non-error) snapshot once and logs it
class Rq:
    def __init__(s,seq): s.seq=list(seq); s.calls=0
    def post(s,*a,**k):
        s.calls+=1; v=s.seq.pop(0); return type('R',(),{'json':lambda self:v})()
now=T.time.time()*1000; today=int(now)//DAY; good=[dict(t=(today-i)*DAY,h='2',l='1',c='1.5') for i in range(1,80)]
T._pause=lambda:None; T.DAILY.clear(); T.requests=Rq([[],good]); dd=DC0('BTC/USD')
assert T.requests.calls==2 and today-1 in dd and len(dd)==79,(T.requests.calls,len(dd or {}))
T.DAILY.clear(); T.requests=Rq([[],[]]); assert DC0('BTC/USD')=={} and T.requests.calls==2
n+=1
print('OK',n)
'''
def main():
    d=tempfile.mkdtemp(prefix='thor_trend_filter_test_')
    try:
        for f in os.listdir(HERE):
            if f.endswith(('.py','.yaml','.json','.html','.txt')) and f not in ('tracker.db','heartbeat','ingested.txt','inbox_report.json'): shutil.copy(os.path.join(HERE,f),d)
        r=subprocess.run([sys.executable,'-c',HARNESS],cwd=d,capture_output=True,text=True,timeout=120,env={**os.environ,'TZ':'America/Phoenix'})
        if r.returncode or 'OK' not in r.stdout: print(r.stdout[-2000:],r.stderr[-3000:]); print('FAIL test_trend_filter'); sys.exit(1)
        print(f'test_trend_filter: {r.stdout.split()[-1]}/14 OK (long/short allowed+blocked, <61d/missing/no data -> skip, no lookahead, backtest parity, TREND55-only)')
    finally: shutil.rmtree(d,ignore_errors=True)
if __name__=='__main__': main()
