"""Offline tests for the pair-view ladder age filter (ingest) and migration v4 (suspect -> stale_history). No network, never touches
this directory's tracker.db: the engine is copied to a temp dir. PAPER ONLY.
usage: python3 test_stale_ladders.py                 # self-contained tests (real T:UNIUSDT view of 2026-10-20 12:20 Phoenix)
       python3 test_stale_ladders.py --check FILE    # ingest one check_*.json into an empty temp DB, print JSON {signals, report}"""
import os,sys,json,shutil,tempfile,subprocess,sqlite3
HERE=os.path.dirname(os.path.abspath(__file__))
UNI=("T:UNIUSDT 15m | Price: 7.339\nHitrate: 76.4%\n\n🟢 Long 6.9918 - 6.931\nTPs: 7.1726, 7.2284, 7.2842\nInvalid: 7.465\nDt: 2026-10-20 18:33:11\n\n"
     "T:UNIUSDT 1h | Price: 7.339\nHitrate: 67.9%\n\n🟢 Long 6.8047 - 6.706\nTPs: 7.097, 7.1873, 7.2777\nInvalid: 7.57\nDt: 2026-10-20 15:41:09\n\n"
     "🔴 Short 8.2914 - 8.2121\nTPs: 7.667, 7.4986, 7.3302\nInvalid: 6.785\nDt: 2026-10-15 03:41:09\n\n"
     "T:UNIUSDT 4h | Price: 7.339\nHitrate: 52.1%\n\n🟢 Long 7.1412 - 7.0066\nTPs: 8.3074, 8.6676, 9.0278\nInvalid: 10.194\nDt: 2026-09-27 08:31:06\n")
HARNESS=r'''
import sys,json; sys.argv=['x']
import tracker as T
T.marks=lambda: None   # no network: no HL mids -> price check skipped
c=T.db(); n=T.ingest(c)
rows=[dict(zip([d[0] for d in cur.description],r)) for cur in [c.execute("select symbol,timeframe,direction,entry_low,entry_high,dt,start_ts,status,source from signals order by start_ts")] for r in cur.fetchall()]
print(json.dumps(dict(new=n,signals=rows,report=T.INBOX_REPORT)))
'''
def engine_copy():
    d=tempfile.mkdtemp(prefix='thor_engine_test_')
    for f in os.listdir(HERE):
        if f.endswith(('.py','.yaml','.json','.html','.txt')) and f not in ('tracker.db','heartbeat'): shutil.copy(os.path.join(HERE,f),d)
    for f in ('tracker.db','ingested.txt','inbox_report.json'):
        if os.path.exists(os.path.join(d,f)): os.remove(os.path.join(d,f))
    os.makedirs(os.path.join(d,'inbox','processed'))
    return d
def ingest(check):
    d=engine_copy()
    try:
        shutil.copy(check,os.path.join(d,'inbox',os.path.basename(check)))
        r=subprocess.run([sys.executable,'-c',HARNESS],cwd=d,capture_output=True,text=True,timeout=120)
        if r.returncode: raise RuntimeError(r.stderr[-2000:])
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally: shutil.rmtree(d,ignore_errors=True)
def ms(s):
    import pandas as pd; return int(pd.Timestamp(s,tz='UTC').timestamp()*1000)
def main():
    ok=0
    # 1) ingest: only ladders young enough for their tf (and not before the previous read) enter; each starts at its own Dt
    d=tempfile.mkdtemp(); f=os.path.join(d,'check_2026-10-20T1220.json')
    json.dump(dict(checked_at='2026-10-20 12:20:45',active=[],pair_views={'T:UNIUSDT (1)':dict(text=UNI,captured_at='2026-10-20 12:20:53')}),open(f,'w'))
    R=ingest(f); S=R['signals']
    assert [(s['timeframe'],s['dt']) for s in S]==[('1h','2026-10-20 15:41:09'),('15m','2026-10-20 18:33:11')],S
    assert all(s['start_ts']==ms(s['dt']) for s in S),S
    st=R['report'][os.path.basename(f)]['stale_ladders']; assert len(st)==2 and all('old at view time' in x for x in st),st; ok+=1
    # 2) prev_captured_at: a ladder dated before the previous read of that pair (beyond the 60 min grace) is skipped
    json.dump(dict(checked_at='2026-10-20 12:20:45',active=[],pair_views={'T:UNIUSDT (1)':dict(text=UNI,captured_at='2026-10-20 12:20:53',prev_captured_at='2026-10-20 10:00:00')}),open(f,'w'))
    S=ingest(f)['signals']; assert [(s['timeframe'],s['dt']) for s in S]==[('15m','2026-10-20 18:33:11')],S; ok+=1
    # 3) All active items are never dropped by age (authoritative: entered now)
    json.dump(dict(checked_at='2026-10-20 12:20:45',pair_views={},active=[dict(symbol='UNI/USD',timeframe='4h',direction='Long',source='all_active',status_text='All active',
        entry_low=7.0066,entry_high=7.1412,tp1=8.3074,tp2=8.6676,tp3=9.0278,invalid=10.194,dt='2026-09-27 08:31:06')]),open(f,'w'))
    S=ingest(f)['signals']; assert len(S)==1 and S[0]['source']=='all_active',S; ok+=1
    shutil.rmtree(d,ignore_errors=True)
    # 4) migration v4: old suspects -> stale_history (reason kept in note); fresh suspects stay suspect; user_version 4
    e=engine_copy()
    try:
        r=subprocess.run([sys.executable,'-c','import sys;sys.argv=["x"];import tracker as T;T.db().close()'],cwd=e,capture_output=True,text=True,timeout=60)   # real schema
        assert r.returncode==0,r.stderr[-1500:]
        c=sqlite3.connect(os.path.join(e,'tracker.db'))
        cols='key,symbol,timeframe,direction,entry_low,entry_high,tp1,tp2,tp3,invalid,dt,source,start_ts,first_seen_ts,status,reason'
        fs=ms('2026-10-20 19:30:00')
        c.execute(f'insert into signals ({cols}) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('a','T:UNIUSDT','4h','Long',5.08,5.36,7.2,7.8,8.3,10.2,'2026-09-27 08:31:06','ask_thor',fs,fs,'suspect','suspect: entry mid 5.2198 is -27.5% from HL mid 7.1977'))
        c.execute(f'insert into signals ({cols}) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('b','SUI/USD','4h','Short',1.194,1.202,1.102,1.074,1.045,0.953,'2026-10-20 12:00:00','ask_thor',fs,fs,'suspect','suspect: entry mid 1.198 is +22.1% from HL mid 0.98'))
        c.execute('pragma user_version=3'); c.commit(); c.close()
        r=subprocess.run([sys.executable,'-c','import sys;sys.argv=["x"];import tracker as T;c=T.db();import json;print(json.dumps([list(x) for x in c.execute("select key,status,reason,note from signals order by key")]+[c.execute("pragma user_version").fetchone()[0]]))'],cwd=e,capture_output=True,text=True,timeout=60)
        assert r.returncode==0,r.stderr[-1500:]
        out=json.loads(r.stdout.strip().splitlines()[-1]); a,b,uv=out
        assert a[1]=='stale_history' and a[2].startswith('stale_history: Dt 2026-09-27 08:31:06 UTC') and 'when first seen' in a[2] and a[3].startswith('was suspect before v4 (suspect: entry mid'),a
        assert b[1]=='suspect' and uv==4,(b,uv); ok+=1
    finally: shutil.rmtree(e,ignore_errors=True)
    print(f'{ok}/4 OK')
if __name__=='__main__':
    if len(sys.argv)>2 and sys.argv[1]=='--check': print(json.dumps(ingest(sys.argv[2]),default=str))
    else: main()
