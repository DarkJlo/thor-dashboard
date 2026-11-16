"""Offline tests for generic portfolio filter asset_ema_gate (default off): Long only while the
asset's last closed daily close is above its daily EMA(n); Short only while below. Uncovered
(<n completed days) passes, matching scratch/bt_ema200_supertrend.py; no/failed data skips.
No network (daily candles stubbed / requests disabled). Never touches this directory's tracker.db. PAPER ONLY.
usage: python3 test_asset_ema_gate.py"""
import os,sys,json,shutil,tempfile,subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
HARNESS=r'''
import sys,json; sys.argv=["x"]
import numpy as np, pandas as pd, tracker as T
DAY=T.DAYMS; ms=lambda s:int(pd.Timestamp(s,tz="UTC").timestamp()*1000)
T.hl_meta=lambda: json.load(open("hl_meta.json")); T.btc_trend=lambda n=None: None
T.requests=None   # any network use would crash the test
def series(n_days,last_day,f):
    return {d:(f(i)*1.01,f(i)*0.99,f(i)) for i,d in enumerate(range(last_day-n_days+1,last_day+1))}
t=ms("2026-10-20 12:00:00"); k=t//DAY-1
n=0
# 1-4: rising close stays above EMA; falling stays below; constant equals EMA (neither)
UP=series(250,k,lambda i:100+i); DOWN=series(250,k,lambda i:300-i); FLAT=series(250,k,lambda i:100.0)
assert T.asset_ema_ok(UP,t,"Long",200) is True and T.asset_ema_ok(UP,t,"Short",200) is False; n+=1
assert T.asset_ema_ok(DOWN,t,"Short",200) is True and T.asset_ema_ok(DOWN,t,"Long",200) is False; n+=1
assert T.asset_ema_ok(FLAT,t,"Long",200) is False and T.asset_ema_ok(FLAT,t,"Short",200) is False; n+=1
assert T.asset_ema_ok(None,t,"Long",200) is None and T.asset_ema_ok({},t,"Long",200) is None; n+=1
# 5-6: warmup / uncovered (<n days) passes; exactly n days is known
assert T.asset_ema_ok(series(199,k,lambda i:100+i),t,"Long",200) is True; n+=1
assert T.asset_ema_ok(series(199,k,lambda i:300-i),t,"Long",200) is True; n+=1   # would be False if warmed
assert T.asset_ema_ok(series(200,k,lambda i:300-i),t,"Long",200) is False; n+=1
# 7: no lookahead — a later-day crash/spike is ignored; a fill at 00:00:01 UTC uses the previous day
L=dict(UP); L[k+1]=(1.0,0.1,0.1)
assert T.asset_ema_ok(L,t,"Long",200) is True; n+=1
assert T.asset_ema_ok(L,(k+1)*DAY+1000,"Long",200) is True; n+=1   # still day k
assert T.asset_ema_ok(L,(k+2)*DAY+1000,"Long",200) is False; n+=1  # day k+1 close 0.1 is now completed and below EMA
# 8: parity with the backtest (ewm span=n adjust=False; first n-1 NaN; last closed day at fill; uncovered -> True)
rng=np.random.default_rng(7); days=list(range(20000,20400)); c=100*np.exp(np.cumsum(rng.normal(0,0.03,len(days))))
R={d:(x*1.01,x*0.99,x) for d,x in zip(days,c) if rng.random()>0.02}
m=0
for d in range(min(R)+1, max(R)+2):
    tt=d*DAY+3600_000
    got=T.asset_ema_ok(R,tt,"Long",200)
    use=sorted(x for x in R if x<=d-1)
    if not use: exp=None
    else:
        s=pd.Series([R[x][2] for x in use]); e=s.ewm(span=200,adjust=False).mean(); e.iloc[:199]=np.nan
        ev=e.iloc[-1]; exp=True if pd.isna(ev) else bool(s.iloc[-1]>ev)
    assert got==exp,(d,got,exp); m+=got is not None
assert m>100,m; n+=1
# 9: portfolio integration — MAIN keeps every long; MAIN_EMA200 applies the gate; CONTROL copy tests shorts; ALL unchanged
cfg=T.CFG["portfolios"]
assert "asset_ema_gate" not in cfg["MAIN"] and "asset_ema_gate" not in cfg["ALL"]
assert cfg["MAIN_EMA200"]["asset_ema_gate"]==200
assert cfg["MAIN_EMA200"]["timeframes"]==["4h"] and cfg["MAIN_EMA200"]["directions"]==["Long"] and cfg["MAIN_EMA200"]["min_tp1_dist"]==0.02
ctl={**cfg["CONTROL"],"asset_ema_gate":200}
wide={**{"caps":{"15m":999,"1h":999,"4h":999,"1d":999},"no_caps":True}}
T.CFG["portfolios"]={"MAIN":{**cfg["MAIN"],**wide},"MAIN_EMA200":{**cfg["MAIN_EMA200"],**wide},"ALL":cfg["ALL"],"CTL_EMA":{**ctl,**wide}}
D={"BTC/USD":UP,"ETH/USD":DOWN,"SOL/USD":series(50,k,lambda i:100+i),"LINK/USD":None,"XRP/USD":UP}
T.daily_candles=lambda s:D[s]
c=T.db()
def sig(key,sym,d="Long",tf="4h"):
    sg=-1 if d=="Short" else 1; e=100.0; tp1=e+sg*3
    row=dict(key=key,symbol=sym,timeframe=tf,direction=d,entry_low=e-0.5*(sg>0),entry_high=e+0.5*(sg<0),tp1=tp1,tp2=tp1+sg,tp3=tp1+2*sg,
             invalid=tp1+5*sg,dt="",raw_text="",source="ask_thor",format="new",start_ts=t,first_seen_ts=t,status="closed",fill_ts=t+3600_000,entry=e,stop=e-(tp1-e)/1.5,
             exit_ts=t+7200_000,exit_px=tp1,reason="tp1",last_ts=t+7200_000,last_px=tp1,note=None,px_src="test")
    c.execute(f"insert into signals ({','.join(row)}) values ({','.join('?'*len(row))})",list(row.values()))
for key,sym,d in [("btcL","BTC/USD","Long"),("ethL","ETH/USD","Long"),("solL","SOL/USD","Long"),("linkL","LINK/USD","Long"),
                  ("btcS","BTC/USD","Short"),("ethS","ETH/USD","Short")]:
    sig(key,sym,d)
c.commit(); c.close(); c=T.db(); T.confluence(c); T.portfolios(c)
def st(p): return dict(c.execute("select key,status from trades where portfolio=?",(p,)).fetchall())
# MAIN is long-only, no gate: takes the four longs, never sees shorts
assert st("MAIN")=={"btcL":"closed","ethL":"closed","solL":"closed","linkL":"closed"},st("MAIN")
# MAIN_EMA200: BTC above -> take; ETH below -> skip; SOL uncovered -> pass; LINK no data -> nodata
assert st("MAIN_EMA200")=={"btcL":"closed","ethL":"skip_asset_ema","solL":"closed","linkL":"skip_asset_ema_nodata"},st("MAIN_EMA200")
# CTL_EMA (CONTROL + gate): shorts allowed only below EMA
assert st("CTL_EMA")["btcL"]=="closed" and st("CTL_EMA")["btcS"]=="skip_asset_ema"
assert st("CTL_EMA")["ethL"]=="skip_asset_ema" and st("CTL_EMA")["ethS"]=="closed"
# ALL (no gate) takes every signal including shorts
al=st("ALL"); assert all(v=="closed" for v in al.values()) and set(al)=={"btcL","ethL","solL","linkL","btcS","ethS"},al
n+=1
print("OK",n)
'''
def main():
    d=tempfile.mkdtemp(prefix="thor_asset_ema_gate_test_")
    try:
        for f in os.listdir(HERE):
            if f.endswith((".py",".yaml",".json",".html",".txt")) and f not in ("tracker.db","heartbeat","ingested.txt","inbox_report.json"):
                shutil.copy(os.path.join(HERE,f),d)
        r=subprocess.run([sys.executable,"-c",HARNESS],cwd=d,capture_output=True,text=True,timeout=120,env={**os.environ,"TZ":"America/Phoenix"})
        if r.returncode or "OK" not in r.stdout:
            print(r.stdout[-3000:], r.stderr[-4000:]); print("FAIL test_asset_ema_gate"); sys.exit(1)
        print("test_asset_ema_gate: OK (MAIN unchanged; MAIN_EMA200 skips long while asset daily close is below EMA200; uncovered passes)")
    finally:
        shutil.rmtree(d, ignore_errors=True)
if __name__=="__main__":
    main()
