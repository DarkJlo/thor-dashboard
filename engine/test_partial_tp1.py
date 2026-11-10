"""Offline tests for generic partial_tp1 + runner_max_hours (default off): fraction off at TP1 -> BE;
rest at TP2 or market after runner_max_hours. No network. Synthetic 1m candles. PAPER ONLY.
usage: python3 test_partial_tp1.py"""
import os,sys,json,shutil,tempfile,subprocess,numpy as np
HERE=os.path.dirname(os.path.abspath(__file__))
HARNESS=r'''
import sys,json,os,numpy as np; sys.argv=["x"]
import pandas as pd, tracker as T
ms=lambda s:int(pd.Timestamp(s,tz="UTC").timestamp()*1000)
T.hl_meta=lambda: json.load(open("hl_meta.json"))
T.btc_trend=lambda n=None: None
# Build a tiny candle cache for ETH/USD: fill at t0, TP1 at +2h, TP2 at +4h (or stop / timeout paths)
t0=ms("2026-10-01 00:00:00"); e=100.0; tp1=103.0; tp2=105.0; tp3=107.0; stop=e-(tp1-e)/1.5  # 98.0
os.makedirs("candle_cache",exist_ok=True)
def write_candles(rows):
    # rows: list of (ts,o,h,l,c) -> engine candle cols ts,o,h,l,c,iv,src
    a=np.array([[ts,o,h,l,c,60_000,0] for ts,o,h,l,c in rows], dtype=float)
    np.save("candle_cache/ETH_USD.npy", a)
# Path A: hits TP1 then TP2 (no stop). Expect blended exit = 0.5*103 + 0.5*105 = 104, reason tp2
rows=[]
for i in range(0, 6*60):  # 6 hours of 1m bars
    ts=t0+i*60_000
    if i < 120:
        o=h=l=cl=100.5  # oscillating above entry, below TP1
    elif i < 240:
        o=103.2; cl=103.2; h=103.6; l=101.5  # TP1 touched; low stays above BE=100
    else:
        o=105.2; cl=105.2; h=105.6; l=103.0  # TP2 touched
    rows.append((ts,o,h,l,cl))
write_candles(rows)
cfg=T.CFG["portfolios"]
assert "partial_tp1" not in cfg["MAIN"]
assert cfg["MAIN_HALFTP1"]["partial_tp1"]==0.5 and cfg["MAIN_HALFTP1"]["runner_max_hours"]==48
T.CFG["portfolios"]={k:cfg[k] for k in ("MAIN","MAIN_HALFTP1")}
c=T.db()
def put(k, exit_px=tp1, reason="tp1", status="closed", xt=t0+3*3600_000):
    row=dict(key=k,symbol="ETH/USD",timeframe="4h",direction="Long",entry_low=99.5,entry_high=100.0,tp1=tp1,tp2=tp2,tp3=tp3,
             invalid=110.0,dt="2026-10-01",raw_text="",source="ask_thor",format="new",start_ts=t0-3600_000,first_seen_ts=t0-3600_000,
             status=status,fill_ts=t0,entry=e,stop=stop,exit_ts=xt,exit_px=exit_px,reason=reason,last_ts=xt,last_px=exit_px,note=None,px_src="test")
    c.execute(f"insert into signals ({','.join(row)}) values ({','.join('?'*len(row))})",list(row.values()))
put("hit_tp2")
c.commit(); c.close()
c=T.db(); T.confluence(c); T.portfolios(c)
m=c.execute("select status,reason,round(exit_px,4),round(r,4) from trades where portfolio='MAIN' and key='hit_tp2'").fetchone()
h=c.execute("select status,reason,round(exit_px,4) from trades where portfolio='MAIN_HALFTP1' and key='hit_tp2'").fetchone()
assert m and m[0]=="closed" and m[1]=="tp1", m
assert h and h[0]=="closed" and h[1]=="tp2", h
assert abs(h[2]-104.0)<1e-6, h  # 0.5*103 + 0.5*105
# Path B: TP1 then 48h timeout before TP2 (runner_max_hours). Rebuild with TP2 never hit, hold past 48h.
rows=[]
for i in range(0, 50*60):  # 50 hours
    ts=t0+i*60_000
    if i < 120:
        o=h=l=cl=100.5
    else:
        o=103.0; cl=103.0; h=103.2; l=101.5  # TP1 done; grind sideways above BE, never TP2
    rows.append((ts,o,h,l,cl))
write_candles(rows)
# fresh db via deleting trades/equity by re-running portfolios on same signals after updating candles;
# simplest: new process state — wipe trades by re-calling portfolios after truncate
c.execute("delete from trades"); c.execute("delete from equity"); c.execute("delete from margin"); c.commit()
# also need signal still there; re-run
T.portfolios(c)
h=c.execute("select status,reason from trades where portfolio='MAIN_HALFTP1' and key='hit_tp2'").fetchone()
assert h and h[0]=="closed" and h[1]=="time_stop", h
# Path C: stop before TP1 -> full stop (MAIN and HALFTP1 both stop)
rows=[]
for i in range(0, 3*60):
    ts=t0+i*60_000
    if i < 30:
        o=h=l=cl=100.5
    else:
        o=98.0; cl=98.0; h=100.5; l=97.5  # hits stop 98.0; high stays below TP1
    rows.append((ts,o,h,l,cl))
write_candles(rows)
c.execute("delete from trades"); c.execute("delete from equity"); c.execute("delete from margin"); c.commit()
T.portfolios(c)
# MAIN keeps the seed signal exit (tp1) — it does not re-path. HALFTP1 re-simulates to stop.
m=c.execute("select status,reason from trades where portfolio='MAIN' and key='hit_tp2'").fetchone()
h=c.execute("select status,reason from trades where portfolio='MAIN_HALFTP1' and key='hit_tp2'").fetchone()
assert m and m[1]=="tp1", m
assert h and h[1]=="stop", h
print("OK")
'''
def main():
    d=tempfile.mkdtemp(prefix="thor_partial_tp1_test_")
    try:
        for f in os.listdir(HERE):
            if f.endswith((".py",".yaml",".json",".html",".txt")) and f not in ("tracker.db","heartbeat","ingested.txt","inbox_report.json"):
                shutil.copy(os.path.join(HERE,f),d)
        r=subprocess.run([sys.executable,"-c",HARNESS],cwd=d,capture_output=True,text=True,timeout=120,env={**os.environ,"TZ":"America/Phoenix"})
        if r.returncode or "OK" not in r.stdout:
            print(r.stdout[-3000:], r.stderr[-4000:]); print("FAIL test_partial_tp1"); sys.exit(1)
        print("test_partial_tp1: OK (TP2 blend 104; 48h time_stop after TP1; full stop before TP1)")
    finally:
        shutil.rmtree(d, ignore_errors=True)
if __name__=="__main__":
    main()
