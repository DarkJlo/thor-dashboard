"""Offline tests for the generic portfolio filter signal_utc_hours: [start, end) UTC hours on the signal's start_ts (Thor message /
ladder's own Dt). No network, never touches this directory's tracker.db: the engine is copied to a temp dir. PAPER ONLY.
usage: python3 test_utc_hours.py"""
import os,sys,json,shutil,tempfile,subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
HARNESS=r'''
import sys,json,time; sys.argv=['x']
import pandas as pd, tracker as T
ms=lambda s:int(pd.Timestamp(s,tz='UTC').timestamp()*1000)
H=T.in_utc_hours; W=[8,16]
# 1) boundaries: start included, end excluded; UTC regardless of the box/runner TZ; None/NaN never pass; wrap-around windows
assert H(ms('2026-10-21 08:00:00'),W) and H(ms('2026-10-21 15:59:59'),W)
assert not H(ms('2026-10-21 07:59:59'),W) and not H(ms('2026-10-21 16:00:00'),W)
assert H(ms('2026-10-21 01:30:00-07:00'),W)          # 01:30 Phoenix = 08:30 UTC -> in
assert not H(ms('2026-10-21 09:30:00-07:00'),W)      # 09:30 Phoenix = 16:30 UTC -> out
assert not H(None,W) and not H(float('nan'),W)
assert H(ms('2026-10-21 23:00'),[22,2]) and H(ms('2026-10-21 01:59'),[22,2]) and not H(ms('2026-10-21 02:00'),[22,2])
assert H(ms('2026-10-21 00:00'),[0,24]) and H(ms('2026-10-21 23:59'),[0,24])
# 2) portfolio replay: only FAST_DAY_BIG filters on hour; TP1 >= 2% also applies; FAST (no hour key) keeps every signal
T.hl_meta=lambda: json.load(open('hl_meta.json')); T.btc_trend=lambda: None
cfg=T.CFG['portfolios']; assert cfg['FAST_DAY_BIG']['signal_utc_hours']==[8,16] and cfg['FAST_DAY_BIG']['min_tp1_dist']==0.02
T.CFG['portfolios']={k:cfg[k] for k in ('FAST','FAST_DAY_BIG')}
c=T.db()
def sig(k,start,tf='1h',tp1=103.0,d='Long'):
    st=ms(start); e=100.0; stop=e-(tp1-e)/1.5 if d=='Long' else e+(e-tp1)/1.5
    row=dict(key=k,symbol='BTC/USD',timeframe=tf,direction=d,entry_low=99.5,entry_high=100.0,tp1=tp1,tp2=tp1+1 if d=='Long' else tp1-1,tp3=tp1+2 if d=='Long' else tp1-2,
             invalid=tp1+5 if d=='Long' else tp1-5,dt=start,raw_text='',source='ask_thor',format='new',start_ts=st,first_seen_ts=st,status='closed',fill_ts=st+3600_000,
             entry=e,stop=stop,exit_ts=st+7200_000,exit_px=tp1,reason='tp1',last_ts=st+7200_000,last_px=tp1,note=None,px_src='test')
    c.execute(f"insert into signals ({','.join(row)}) values ({','.join('?'*len(row))})",list(row.values()))
sig('in_0800','2026-10-01 08:00:00'); sig('in_1559','2026-10-02 15:59:00',tf='15m'); sig('out_0759','2026-10-03 07:59:00')
sig('out_1600','2026-10-04 16:00:00'); sig('in_small_tp1','2026-10-05 10:00:00',tp1=101.0); sig('in_short','2026-10-06 12:00:00',d='Short',tp1=97.0)
sig('4h_in','2026-10-07 12:00:00',tf='4h'); c.commit(); c.close()
c=T.db(); T.confluence(c); T.portfolios(c)
got={p:sorted(k for (k,) in c.execute("select key from trades where portfolio=? and status in ('open','closed')",(p,))) for p in ('FAST','FAST_DAY_BIG')}
assert got['FAST_DAY_BIG']==['in_0800','in_1559','in_short'],got
assert got['FAST']==['in_0800','in_1559','in_short','in_small_tp1','out_0759','out_1600'],got
eq=c.execute("select count(*) from equity where portfolio='FAST_DAY_BIG'").fetchone()[0]; assert eq>=2
print('OK')
'''
def main():
    d=tempfile.mkdtemp(prefix='thor_utc_hours_test_')
    try:
        for f in os.listdir(HERE):
            if f.endswith(('.py','.yaml','.json','.html','.txt')) and f not in ('tracker.db','heartbeat','ingested.txt','inbox_report.json'): shutil.copy(os.path.join(HERE,f),d)
        r=subprocess.run([sys.executable,'-c',HARNESS],cwd=d,capture_output=True,text=True,timeout=120,env={**os.environ,'TZ':'America/Phoenix'})
        if r.returncode or 'OK' not in r.stdout: print(r.stdout[-2000:],r.stderr[-3000:]); print('FAIL test_utc_hours'); sys.exit(1)
        print('test_utc_hours: 2/2 OK (hour boundaries incl. wrap/None; portfolio replay keeps only 08-16 UTC + TP1>=2% for FAST_DAY_BIG, FAST unchanged)')
    finally: shutil.rmtree(d,ignore_errors=True)
if __name__=='__main__': main()
