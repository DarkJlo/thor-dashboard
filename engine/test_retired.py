"""Offline test for the opt-in portfolio key retired_after ("YYYY-MM-DD HH:MM", Phoenix time like start_after; TOP_ASSETS):
signals filled at/after it are skipped (skip_retired); earlier trades, incl. ones still open, replay and close normally. PAPER ONLY.
No network, never touches this directory's tracker.db: the engine is copied to a temp dir. usage: python3 test_retired.py"""
import os,sys,shutil,tempfile,subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
HARNESS=r'''
import sys,json; sys.argv=['x']
import pandas as pd, tracker as T
ms=lambda s:int(pd.Timestamp(s).tz_localize('-07:00').timestamp()*1000)
T.hl_meta=lambda: json.load(open('hl_meta.json')); T.btc_trend=lambda: None
cfg=T.CFG['portfolios']; assert cfg['TOP_ASSETS'].get('retired_after'),cfg['TOP_ASSETS']
base=dict(timeframes=['4h'],directions=['Long'],min_tp1_dist=0.0,caps={'15m':99,'1h':99,'4h':99,'1d':99})
T.CFG['portfolios']={'LIVE':base,'RET':dict(base,retired_after='2026-10-20 12:00')}
c=T.db()
def sig(k,fill,closed=True):
    f=ms(fill); e=100.0; tp1=103.0
    row=dict(key=k,symbol='BTC/USD',timeframe='4h',direction='Long',entry_low=99.5,entry_high=100.0,tp1=tp1,tp2=104,tp3=105,invalid=108,dt='',raw_text='',source='ask_thor',format='new',
             start_ts=f-3600_000,first_seen_ts=f-3600_000,status='closed' if closed else 'filled',fill_ts=f,entry=e,stop=e-2,exit_ts=f+86400_000 if closed else None,
             exit_px=tp1 if closed else None,reason='tp1' if closed else None,last_ts=f,last_px=101.0,note=None,px_src='test')
    c.execute(f"insert into signals ({','.join(row)}) values ({','.join('?'*len(row))})",list(row.values()))
sig('old_closed','2026-10-10 10:00'); sig('old_open','2026-10-20 11:59',closed=False); sig('closes_after','2026-10-19 10:00')
sig('new_at','2026-10-20 12:00'); sig('new_later','2026-10-25 10:00',closed=False)
c.commit(); c.close(); c=T.db(); T.confluence(c); T.portfolios(c)
st=lambda p:dict(c.execute("select key,status from trades where portfolio=?",(p,)).fetchall())
assert st('RET')=={'old_closed':'closed','old_open':'open','closes_after':'closed','new_at':'skip_retired','new_later':'skip_retired'},st('RET')
assert st('LIVE')=={'old_closed':'closed','old_open':'open','closes_after':'closed','new_at':'closed','new_later':'open'},st('LIVE')
pl=lambda p,k:c.execute("select pnl from trades where portfolio=? and key=?",(p,k)).fetchone()[0]
assert all(abs(pl('RET',k)-pl('LIVE',k))<1e-9 for k in ('old_closed','closes_after')); print('OK')
'''
def main():
    d=tempfile.mkdtemp(prefix='thor_retired_test_')
    try:
        for f in os.listdir(HERE):
            if f.endswith(('.py','.yaml','.json','.html','.txt')) and f not in ('tracker.db','heartbeat','ingested.txt','inbox_report.json'): shutil.copy(os.path.join(HERE,f),d)
        r=subprocess.run([sys.executable,'-c',HARNESS],cwd=d,capture_output=True,text=True,timeout=120,env={**os.environ,'TZ':'America/Phoenix'})
        if r.returncode or 'OK' not in r.stdout: print(r.stdout[-2000:],r.stderr[-3000:]); print('FAIL test_retired'); sys.exit(1)
        print('test_retired: 1/1 OK (retired_after: fills at/after cutoff -> skip_retired; earlier trades incl. open ones unchanged)')
    finally: shutil.rmtree(d,ignore_errors=True)
if __name__=='__main__': main()
