"""Offline test for the generic portfolio filter exclude_assets (list of base assets to drop, e.g. SKIP_BIGCAPS).
No network, never touches this directory's tracker.db: the engine is copied to a temp dir. PAPER ONLY.
usage: python3 test_exclude_assets.py"""
import os,sys,json,shutil,tempfile,subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
HARNESS=r'''
import sys,json; sys.argv=['x']
import pandas as pd, tracker as T
ms=lambda s:int(pd.Timestamp(s,tz='UTC').timestamp()*1000)
T.hl_meta=lambda: json.load(open('hl_meta.json')); T.btc_trend=lambda: None
cfg=T.CFG['portfolios']; assert cfg['SKIP_BIGCAPS']['exclude_assets']==['ETH','SOL','BNB','HYPE']
T.CFG['portfolios']={k:cfg[k] for k in ('ALL','SKIP_BIGCAPS')}
c=T.db()
def sig(k,sym,start,tf='1h'):
    st=ms(start); e=100.0; tp1=103.0; stop=e-(tp1-e)/1.5
    row=dict(key=k,symbol=sym,timeframe=tf,direction='Long',entry_low=99.5,entry_high=100.0,tp1=tp1,tp2=tp1+1,tp3=tp1+2,
             invalid=tp1+5,dt=start,raw_text='',source='ask_thor',format='new',start_ts=st,first_seen_ts=st,status='closed',fill_ts=st+3600_000,
             entry=e,stop=stop,exit_ts=st+7200_000,exit_px=tp1,reason='tp1',last_ts=st+7200_000,last_px=tp1,note=None,px_src='test')
    c.execute(f"insert into signals ({','.join(row)}) values ({','.join('?'*len(row))})",list(row.values()))
for i,(s,tf) in enumerate([('BTC/USD','1h'),('ETH/USD','1h'),('SOL/USD','4h'),('BNB/USD','15m'),('HYPE/USD','1d'),('DOGE/USD','4h')]):
    sig(s.split('/')[0].lower(),s,f'2026-10-0{i+1} 10:00:00',tf)
c.commit(); c.close()
c=T.db(); T.confluence(c); T.portfolios(c)
got={p:sorted(k for (k,) in c.execute("select key from trades where portfolio=? and status in ('open','closed')",(p,))) for p in ('ALL','SKIP_BIGCAPS')}
assert got['SKIP_BIGCAPS']==['btc','doge'],got
assert set(got['ALL'])>={'btc','doge','eth'},got
print('OK')
'''
def main():
    d=tempfile.mkdtemp(prefix='thor_exclude_assets_test_')
    try:
        for f in os.listdir(HERE):
            if f.endswith(('.py','.yaml','.json','.html','.txt')) and f not in ('tracker.db','heartbeat','ingested.txt','inbox_report.json'): shutil.copy(os.path.join(HERE,f),d)
        r=subprocess.run([sys.executable,'-c',HARNESS],cwd=d,capture_output=True,text=True,timeout=120,env={**os.environ,'TZ':'America/Phoenix'})
        if r.returncode or 'OK' not in r.stdout: print(r.stdout[-2000:],r.stderr[-3000:]); print('FAIL test_exclude_assets'); sys.exit(1)
        print('test_exclude_assets: 1/1 OK (SKIP_BIGCAPS drops ETH/SOL/BNB/HYPE on every timeframe, ALL unchanged)')
    finally: shutil.rmtree(d,ignore_errors=True)
if __name__=='__main__': main()
