"""Offline tests for generic portfolio filter btc_ema_gate (default off): Long only while BTC 4h close > EMA(n),
Short only while below. No network (btc_trend stubbed). Never touches this directory's tracker.db. PAPER ONLY.
usage: python3 test_btc_ema_gate.py"""
import os,sys,json,shutil,tempfile,subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
HARNESS=r'''
import sys,json; sys.argv=["x"]
import pandas as pd, numpy as np, tracker as T
ms=lambda s:int(pd.Timestamp(s,tz="UTC").timestamp()*1000)
# synthetic BTC trend: after T_UP, above=True; before, above=False
T_UP=ms("2026-10-10 00:00:00")
times=np.array([T_UP-14_400_000, T_UP, T_UP+14_400_000], dtype="int64")
flags=np.array([False, True, True])
T.btc_trend=lambda n=None: (times, flags)
T.hl_meta=lambda: json.load(open("hl_meta.json"))
cfg=T.CFG["portfolios"]
assert "btc_ema_gate" not in cfg["MAIN"]
assert cfg["MAIN_BTC50"]["btc_ema_gate"]==50
# only MAIN + MAIN_BTC50 for a tight replay
T.CFG["portfolios"]={k:cfg[k] for k in ("MAIN","MAIN_BTC50")}
c=T.db()
def sig(k,fill,d="Long",tp1=103.0):
    st=ms(fill); e=100.0; stop=e-(tp1-e)/1.5 if d=="Long" else e+(e-tp1)/1.5
    row=dict(key=k,symbol="ETH/USD",timeframe="4h",direction=d,entry_low=99.5,entry_high=100.0,tp1=tp1,tp2=tp1+1 if d=="Long" else tp1-1,
             tp3=tp1+2 if d=="Long" else tp1-2,invalid=tp1+5 if d=="Long" else tp1-5,dt=fill,raw_text="",source="ask_thor",format="new",
             start_ts=st,first_seen_ts=st,status="closed",fill_ts=st+3600_000,entry=e,stop=stop,exit_ts=st+7200_000,exit_px=tp1,reason="tp1",
             last_ts=st+7200_000,last_px=tp1,note=None,px_src="test")
    c.execute(f"insert into signals ({','.join(row)}) values ({','.join('?'*len(row))})",list(row.values()))
# fill after T_UP -> above; fill before -> below
sig("long_up","2026-10-10 12:00:00")          # fill ~13:00, above
sig("long_down","2026-10-09 12:00:00")        # fill before T_UP, below -> BTC50 skips
sig("short_up","2026-10-10 12:00:00",d="Short",tp1=97.0)  # MAIN is Long-only so neither takes Short; still useful if we had Shorts
c.commit(); c.close()
c=T.db(); T.confluence(c); T.portfolios(c)
def keys(p):
    return sorted(k for (k,) in c.execute("select key from trades where portfolio=? and status in ('open','closed')",(p,)))
assert keys("MAIN")==["long_down","long_up"], keys("MAIN")
assert keys("MAIN_BTC50")==["long_up"], keys("MAIN_BTC50")
# skip reason recorded
sk=c.execute("select status from trades where portfolio='MAIN_BTC50' and key='long_down'").fetchone()
assert sk and sk[0]=="skip_btc_trend", sk
print("OK")
'''
def main():
    d=tempfile.mkdtemp(prefix="thor_btc_ema_gate_test_")
    try:
        for f in os.listdir(HERE):
            if f.endswith((".py",".yaml",".json",".html",".txt")) and f not in ("tracker.db","heartbeat","ingested.txt","inbox_report.json"):
                shutil.copy(os.path.join(HERE,f),d)
        r=subprocess.run([sys.executable,"-c",HARNESS],cwd=d,capture_output=True,text=True,timeout=120,env={**os.environ,"TZ":"America/Phoenix"})
        if r.returncode or "OK" not in r.stdout:
            print(r.stdout[-2000:], r.stderr[-3000:]); print("FAIL test_btc_ema_gate"); sys.exit(1)
        print("test_btc_ema_gate: OK (MAIN keeps all longs; MAIN_BTC50 skips long while BTC 4h below EMA)")
    finally:
        shutil.rmtree(d, ignore_errors=True)
if __name__=="__main__":
    main()
