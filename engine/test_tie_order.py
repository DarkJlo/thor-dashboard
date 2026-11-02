"""Offline test: portfolio replay must not depend on row order or on the runner CPU when several signals share one fill_ts.
Before 2026-11-02 ties were ordered by numpy's default (unstable, SIMD-dependent) sort, so caps/pauses picked different trades on
AVX-512 vs AVX2 GitHub runners and closed_trades flapped (815/816). Runs the replay under 3 numpy CPU-feature levels x 2 insertion
orders and asserts identical results. No network, never touches this directory's tracker.db. PAPER ONLY. usage: python3 test_tie_order.py"""
import os,sys,json,shutil,tempfile,subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
HARNESS=r'''
import sys,json,random; sys.argv=['x']
import pandas as pd, tracker as T
T.hl_meta=lambda: json.load(open('hl_meta.json')); T.btc_trend=lambda: None
base=dict(directions=['Long','Short'],min_tp1_dist=0.0,caps={'15m':2,'1h':2,'4h':2,'1d':2})
T.CFG['portfolios']={'PLAIN':dict(base,timeframes=['15m','1h','4h']),
                     'CONF':dict(base,timeframes=['15m','1h'],prioritize_confluence=True,caps_confluent={'15m':3,'1h':3})}
c=T.db(); t0=int(pd.Timestamp('2026-10-20 10:00',tz='UTC').timestamp()*1000)
syms=['BTC','ETH','SOL','HYPE','FART','SAND','ARB','SUI','W','HBAR','DOGE','LINK']
rows=[]
for i in range(240):
    tf=['15m','1h','4h'][i%3]; d=['Long','Short'][(i//3)%2]; s=syms[i%len(syms)]+'/USD'
    f=t0+(i//24)*3600_000            # 24 signals share each fill_ts -> heavy ties vs cap 2
    e=100.0+i%7; sg=1 if d=='Long' else -1; win=(i*7)%3!=0
    rows.append(dict(key=f'{s}|{tf}|{d}|{i}',symbol=s,timeframe=tf,direction=d,entry_low=e-0.5,entry_high=e,tp1=e+3*sg,tp2=e+4*sg,tp3=e+5*sg,invalid=e-8*sg,
        dt='',raw_text='',source='ask_thor',format='new',start_ts=f-1800_000,first_seen_ts=f-1800_000,status='closed',fill_ts=f,entry=e,stop=e-2*sg,
        exit_ts=f+1800_000+(i%5)*600_000,exit_px=e+(3 if win else -2)*sg,reason='tp1' if win else 'sl',last_ts=f,last_px=e,note=None,px_src='test'))
import os; random.Random(int(os.environ['TIE_SEED'])).shuffle(rows)
for r in rows: c.execute(f"insert into signals ({','.join(r)}) values ({','.join('?'*len(r))})",list(r.values()))
c.commit(); c.close(); c=T.db(); T.confluence(c); T.portfolios(c)
print('RESULT '+json.dumps(sorted(c.execute("select portfolio,key,status,round(coalesce(pnl,0),9) from trades").fetchall())))
'''
def main():
    d=tempfile.mkdtemp(prefix='thor_tie_test_')
    try:
        for f in os.listdir(HERE):
            if f.endswith(('.py','.yaml','.json','.html','.txt')) and f not in ('tracker.db','heartbeat','ingested.txt','inbox_report.json'): shutil.copy(os.path.join(HERE,f),d)
        out={}
        for feat in ('','X86_V4 AVX512_ICL AVX512_SPR','X86_V3 X86_V4 AVX512_ICL AVX512_SPR'):
            for seed in ('1','2'):
                if os.path.exists(os.path.join(d,'tracker.db')): os.remove(os.path.join(d,'tracker.db'))
                r=subprocess.run([sys.executable,'-c',HARNESS],cwd=d,capture_output=True,text=True,timeout=180,
                                 env={**os.environ,'TZ':'America/Phoenix','NPY_DISABLE_CPU_FEATURES':feat,'TIE_SEED':seed})
                line=[l for l in r.stdout.splitlines() if l.startswith('RESULT ')]
                if r.returncode or not line: print(r.stdout[-1500:],r.stderr[-3000:]); print('FAIL test_tie_order (run error)'); sys.exit(1)
                out[(feat or 'native',seed)]=line[-1]
        if len(set(out.values()))!=1:
            print('FAIL test_tie_order: replay differs by CPU features / row order:',{k:hash(v) for k,v in out.items()}); sys.exit(1)
        res=json.loads(next(iter(out.values()))[7:]); assert any(s.startswith('skip_cap') for _,_,s,_ in res),'test data must hit caps'
        print(f'test_tie_order: 1/1 OK ({len(out)} runs identical: 3 numpy CPU-feature levels x 2 row orders, {len(res)} trades)')
    finally: shutil.rmtree(d,ignore_errors=True)
if __name__=='__main__': main()
