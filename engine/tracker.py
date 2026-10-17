#!/usr/bin/env python3
"""THOR PAPER tracker. Paper only: reads public candles, never places orders, needs no keys.
Signal life cycle (same rules as backtest/engine.py):
  pending -> filled (a closed candle trades into [entry_low, entry_high]; fill = candle open clamped into the range)
          -> cancelled_invalid (Invalid touched before the fill; this wins if both happen in one candle)
          -> cancelled_timeout (no fill within timeouts_days[tf] of start)
  filled  -> tp1 | stop (stop = entry -/+ TP1 dist/stop_rr) | timeout (exit at close)
  Conservative: on the fill candle only the stop is checked; if stop and TP1 are both touched in one candle, the stop wins.
Portfolios are recomputed from all signals each cycle (deterministic): caps, 1% risk sizing, 24h pause.
"""
import os,sys,json,time,glob,shutil,sqlite3,traceback,math,re
import requests,yaml,pandas as pd,numpy as np
HERE=os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
CFG=yaml.safe_load(open('config.yaml')); DB='tracker.db'; IV=300_000
TZ='-07:00'
def log(*a): print(time.strftime('%F %T'),*a,flush=True)
def num(x):
    if x is None or (isinstance(x,float) and math.isnan(x)): return None
    m=re.search(r'[\d.]+',str(x).replace(',','')); return float(m.group()) if m else None
def base(s): return (s[2:].replace('USDT','') if s.startswith('T:') else s.replace('/USD','')).replace('/','')
def key(r): return '|'.join([r['symbol'],r['timeframe'],r['direction']]+[f"{float(r[k]):.10g}" for k in['entry_low','entry_high','tp1','tp2','tp3']])
COLS=['key','symbol','timeframe','direction','entry_low','entry_high','tp1','tp2','tp3','invalid','dt','raw_text','source','format','start_ts','first_seen_ts','status','fill_ts','entry','stop','exit_ts','exit_px','reason','last_ts','last_px','note','px_src']
def db():
    c=sqlite3.connect(DB); c.execute('drop table if exists trades'); c.execute(f"create table if not exists signals ({','.join(k+(' primary key' if k=='key' else '') for k in COLS)})")
    c.execute("create table if not exists trades (px_src,leverage,margin,liq_px,max_lev,lev_src,portfolio,confluence,htf_confluent,key,symbol,timeframe,direction,fill_ts,entry,stop,exit_ts,exit_px,reason,status,notional,ret,pnl,r)")
    c.execute("create table if not exists equity (portfolio,ts,equity)")
    have=[x[1] for x in c.execute('pragma table_info(signals)')]
    for col in['confluence','live_state','px_src']:
        if col not in have: c.execute(f'alter table signals add column {col}')
    if c.execute('pragma user_version').fetchone()[0]<1:   # one-time: re-check every All active catch with the late-catch rule
        c.execute("update signals set status='filled',last_ts=fill_ts,exit_ts=null,exit_px=null,reason=null where source like '%all_active%' and fill_ts is not null")
        c.execute('pragma user_version=1'); c.commit()
    if c.execute('pragma user_version').fetchone()[0]<2:   # one-time: pre-fix (adc4872) Dt stamps were parsed as local time -> recompute start as UTC and replay
        FIX=1791947359000   # 2026-10-13 20:09:19 -07:00, commit adc4872
        for k,dt,fs in c.execute("select key,dt,first_seen_ts from signals where source in ('ask_thor','alert') and dt is not null and first_seen_ts<? and status!='malformed'",(FIX,)).fetchall():
            try: _t=pd.Timestamp(str(dt)); dts=ms(_t if _t.tzinfo else _t.tz_localize('UTC'))
            except Exception: continue
            st=min(max(dts,fs-CFG['max_backfill_hours']*3600_000),fs)   # first_seen_ts ~ check time (same cap as ingest)
            c.execute("update signals set start_ts=?,last_ts=?,status='pending',fill_ts=null,entry=null,stop=null,exit_ts=null,exit_px=null,reason=null,last_px=null,px_src=null where key=?",(st,st,k))
        c.execute('pragma user_version=2'); c.commit()
    return c
# ---------- ingest ----------
def add_signal(c,r,source,start_ts,fmt='new'):
    r=dict(r)
    for k in['entry_low','entry_high','tp1','tp2','tp3','invalid']: r[k]=num(r.get(k))
    if None in [r[k] for k in['entry_low','entry_high','tp1','tp2','tp3','invalid']] or r.get('timeframe') not in CFG['timeouts_days']: return False
    r['entry_low'],r['entry_high']=sorted([r['entry_low'],r['entry_high']]); r['direction']=r['direction'].capitalize()
    k=key(r); ex=c.execute('select status from signals where key=?',(k,)).fetchone()
    entered=source=='all_active'   # authoritative: All active screen = already hit entry
    sh=r['direction']=='Short'
    if ex:
        if entered and ex[0]=='pending':   # seen pending on a pair view, now on All active -> mark entered
            e=r['entry_low'] if sh else r['entry_high']; d1=abs(r['tp1']-e); sg=-1 if sh else 1
            c.execute("update signals set status='filled',fill_ts=?,entry=?,stop=?,last_ts=?,source=source||'+all_active',note='entered per All active' where key=?",(int(start_ts),e,e-sg*d1/CFG['stop_rr'],int(start_ts),k))
        return False
    sh=r['direction']=='Short'; lo,hi=r['entry_low'],r['entry_high']
    ok=(r['tp1']<lo and r['invalid']<r['tp1']) if sh else (r['tp1']>hi and r['invalid']>r['tp1'])
    row=dict(key=k,symbol=r['symbol'],timeframe=r['timeframe'],direction=r['direction'],entry_low=lo,entry_high=hi,tp1=r['tp1'],tp2=r['tp2'],tp3=r['tp3'],invalid=r['invalid'],dt=str(r.get('dt','')),raw_text=str(r.get('raw_text',''))[:500],
             source=source,format=fmt,start_ts=int(start_ts),first_seen_ts=int(time.time()*1000),status='pending' if ok else 'malformed',last_ts=int(start_ts))
    if entered and ok:
        e=hi if not sh else lo; d1=abs(r['tp1']-e); sg=-1 if sh else 1   # entry assumed at the edge price reaches first
        row.update(status='filled',fill_ts=int(start_ts),entry=e,stop=e-sg*d1/CFG['stop_rr'],note='entered per All active')
    c.execute(f"insert into signals ({','.join(row)}) values ({','.join('?'*len(row))})",list(row.values())); return True
def ms(t): return int(pd.Timestamp(t).tz_localize(TZ).timestamp()*1000) if pd.Timestamp(t).tzinfo is None else int(pd.Timestamp(t).timestamp()*1000)
def ingest(c):
    n=0
    for f in sorted(glob.glob('inbox/*.json')):
        try:
            d=json.load(open(f))
            if isinstance(d,dict) and 'messages' in d:
                sys.path.insert(0,HERE); from tgparse import parse
                for _,r in parse(f).iterrows(): n+=add_signal(c,r,'telegram_export',ms(r.msg_time),r.format)
            else:
                ca=ms(d['checked_at'])
                items=[dict(r,source=r.get('source') if r.get('source') in ('all_active','ask_thor','alert') else 'all_active') for r in d.get('active',[])]
                pv=d.get('pair_views') or {}
                if pv:
                    sys.path.insert(0,HERE); from tgparse import parse_texts
                    for _,r in parse_texts(list(pv.values()) if isinstance(pv,dict) else pv,ca/1000).iterrows():
                        items.append(dict(r.drop(['msg_time','format','msg_id']),source='ask_thor'))
                items.sort(key=lambda r:r.get('source')!='all_active')
                for r in items:
                    if r.get('msg_time'): st=ms(r['msg_time'])
                    else:
                        try: _t=pd.Timestamp(str(r.get('dt'))); dts=ms(_t if _t.tzinfo else _t.tz_localize('UTC'))   # Thor Dt stamps are UTC
                        except Exception: dts=ca
                        st=ca if r.get('source')=='all_active' else min(max(dts,ca-CFG['max_backfill_hours']*3600_000),ca)
                    n+=add_signal(c,r,r.get('source','all_active'),st)
            shutil.move(f,'inbox/processed/'+os.path.basename(f))
        except Exception as e:
            log('inbox error',f,e); shutil.move(f,'inbox/processed/BAD_'+os.path.basename(f))
    c.commit(); return n
# ---------- prices ----------
# Candles: Hyperliquid candleSnapshot (configurable interval, default 1m) for the window HL still serves
# (~5000 candles back); older parts and HL gaps fall back to Binance then OKX (1m, then 5m). Cached per symbol on disk.
# Array columns: ts, o, h, l, c, interval_ms, source_id
SRC={}; CC=CFG.get('candles',{}); SRCN=['hyperliquid','binance','okx','okxswap']
IVMS={'1m':60_000,'5m':300_000,'15m':900_000}
CDIR=os.path.join(HERE,CC.get('cache_dir','candle_cache')); os.makedirs(CDIR,exist_ok=True)
def _pause(): time.sleep(CC.get('request_pause',0.25))
def _hl(coin,t0,t1,iv):
    out=[]; step=5000*IVMS[iv]; t=t0
    while t<t1:
        r=requests.post('https://api.hyperliquid.xyz/info',json={'type':'candleSnapshot','req':{'coin':coin,'interval':iv,'startTime':int(t),'endTime':int(min(t+step,t1))}},timeout=20).json(); _pause()
        if not isinstance(r,list): raise RuntimeError(str(r)[:100])
        out+=[[x['t'],float(x['o']),float(x['h']),float(x['l']),float(x['c'])] for x in r]; t+=step
    return out
def _binance(sym,t0,t1,iv):
    rows=[];t=t0;b=base(sym)
    while t<t1:
        r=requests.get('https://data-api.binance.vision/api/v3/klines',params=dict(symbol=('SOLBTC' if sym=='SOL/BTC' else b+'USDT'),interval=iv,startTime=int(t),endTime=int(t1),limit=1000),timeout=20).json(); _pause()
        if not isinstance(r,list) or not r: break
        rows+=[[x[0],*map(float,x[1:5])] for x in r]; t=r[-1][0]+IVMS[iv]
    return rows
def _okx(sym,t0,t1,iv,swap):
    b=base(sym); insts=[f'{b}-USDT-SWAP',f'{b}COIN-USDT-SWAP'] if swap else [f'{b}-USDT']
    for inst in insts:
        rows=[];after=int(t1)
        while True:
            r=requests.get('https://www.okx.com/api/v5/market/history-candles',params=dict(instId=inst,bar=iv,after=after,limit=100),timeout=20).json(); _pause()
            if r.get('code')!='0' or not r['data']: break
            rows+=[[int(x[0]),*map(float,x[1:5])] for x in r['data']]; after=int(r['data'][-1][0])
            if after<t0: break
        if rows: return [x for x in rows if x[0]>=t0]
    return []
def _fallback(sym,t0,t1):
    for name in CC.get('fallback',['binance','okx','okxswap']):
        for iv in CC.get('fallback_intervals',['1m','5m']):
            try: rows=_binance(sym,t0,t1,iv) if name=='binance' else _okx(sym,t0,t1,iv,name=='okxswap')
            except Exception: rows=[]
            if rows: return [r+[IVMS[iv],SRCN.index(name)] for r in rows]
    return []
def candles(sym,start):
    now=int(time.time()*1000); f=os.path.join(CDIR,sym.replace('/','_')+'.npy')
    cache=np.load(f) if os.path.exists(f) else np.zeros((0,7))
    if len(cache) and cache[0,0]>start: cache=np.zeros((0,7))      # need older data than cached: rebuild
    have_to=int(cache[-1,0]+cache[-1,5]) if len(cache) else int(start)
    new=[]; iv=CC.get('interval','1m'); M=hl_meta(); a=hl_asset(sym,M)
    if CC.get('source','hyperliquid')=='hyperliquid' and a is not None:
        coin=next(k for k in (base(sym),'k'+base(sym),base(sym)+'COIN') if k in M['assets']); sc=1e-3 if coin.startswith('k') and coin!=base(sym) else 1
        try: h=[[x[0],x[1]*sc,x[2]*sc,x[3]*sc,x[4]*sc,IVMS[iv],0] for x in _hl(coin,have_to,now,iv)]
        except Exception as e: log('hl candles failed',sym,e); h=[]
        first=h[0][0] if h else now
        if first-have_to>IVMS[iv]: new+=_fallback(sym,have_to,first)   # HL history doesn't reach back far enough (or HL failed)
        new+=h
    else: new+=_fallback(sym,have_to,now)
    if new:
        n=np.array(new,dtype=float); n=n[n[:,0]+n[:,5]<=now]   # closed candles only
        cache=np.concatenate([cache[cache[:,0]<n[0,0]] if len(n) else cache,n]) if len(n) else cache
        np.save(f,cache)
    if not len(cache): return None
    SRC[sym]='+'.join(sorted({SRCN[int(x)] for x in np.unique(cache[cache[:,0]>=start][:,6])}))
    return cache[cache[:,0]>=start]
# ---------- signal state machine ----------
def step(s,a):
    sh=s['direction']=='Short'; sg=-1 if sh else 1; TO=CFG['timeouts_days'][s['timeframe']]*86400e3
    if a.shape[1]==5: a=np.column_stack([a,np.full(len(a),300_000.),np.full(len(a),1.)])   # legacy 5m arrays (parity test)
    a=a[a[:,0]>=s['last_ts']]
    for ts,o,h,l,c,iv,si in a:
        sn=SRCN[int(si)]
        if s['status']=='pending':
            if ts>s['start_ts']+TO: s.update(status='cancelled',reason='timeout_nofill',exit_ts=int(ts),px_src=sn);break
            if (l<=s['invalid']) if sh else (h>=s['invalid']): s.update(status='cancelled',reason='invalid_before_fill',exit_ts=int(ts),px_src=sn);break
            if l<=s['entry_high'] and h>=s['entry_low']:
                e=min(max(o,s['entry_low']),s['entry_high']); d1=abs(s['tp1']-e)
                s.update(status='filled',fill_ts=int(ts),entry=e,stop=e-sg*d1/CFG['stop_rr'],px_src=sn)
                if (h>=s['stop']) if sh else (l<=s['stop']): s.update(status='closed',exit_ts=int(ts),exit_px=s['stop'],reason='stop',px_src=sn);break
        elif s['status']=='filled':
            if s['last_ts']==s['fill_ts'] and 'all_active' in str(s.get('source') or ''):
                # caught late on All active: if price at catch is already past our stop or TP1, no real entry was possible
                if ((o>=s['stop']) if sh else (o<=s['stop'])) or ((o<=s['tp1']) if sh else (o>=s['tp1'])):
                    s.update(status='cancelled',reason='missed_late',exit_ts=int(ts),exit_px=None,entry=None,fill_ts=None,last_px=o,px_src=sn);break
            if ts>s['fill_ts']+TO: s.update(status='closed',exit_ts=int(ts),exit_px=s['last_px'],reason='timeout',px_src=sn);break
            if (h>=s['stop']) if sh else (l<=s['stop']): s.update(status='closed',exit_ts=int(ts),exit_px=s['stop'],reason='stop',px_src=sn);break
            if (l<=s['tp1']) if sh else (h>=s['tp1']): s.update(status='closed',exit_ts=int(ts),exit_px=s['tp1'],reason='tp1',px_src=sn);break
        s['last_px']=c; s['last_ts']=int(ts+iv)
    if len(a): s['last_px']=s['last_px'] if s['status']=='closed' else a[-1][4]
    return s
MARKS={}
def marks():
    """live mid prices: Hyperliquid allMids (perp marks), Binance ticker fallback"""
    MARKS.clear()
    try: MARKS.update({k:float(v) for k,v in requests.post('https://api.hyperliquid.xyz/info',json={'type':'allMids'},timeout=15).json().items() if not k.startswith('@') and not k.startswith('#')})
    except Exception as e: log('allMids failed',e)
def mark(sym):
    b=base(sym)
    for k,f in [(b,1),('k'+b,1e-3)]:
        if k in MARKS: return MARKS[k]*f
    try: return float(requests.get('https://data-api.binance.vision/api/v3/ticker/price',params=dict(symbol=b+'USDT'),timeout=10).json()['price'])
    except Exception: return None
def rescale(c,r,px):
    """THOR quotes some tiny coins scaled (e.g. SHIB 5.07 = 5.07e-6). Rescale pending signals by a power of 10 if levels are >100x off price."""
    mid=(r.entry_low+r.entry_high)/2; ratio=mid/px
    if 0.01<ratio<100: return False
    f=10**round(math.log10(ratio))
    c.execute("update signals set entry_low=entry_low/?,entry_high=entry_high/?,tp1=tp1/?,tp2=tp2/?,tp3=tp3/?,invalid=invalid/?,note=? where key=?",(f,)*6+(f'rescaled /{f:g}',r.key)); return True
def update_signals(c):
    marks()
    S=pd.read_sql("select * from signals where status in ('pending','filled')",c)
    for _,r in S[S.status=='pending'].iterrows():
        px=mark(r.symbol)
        if px: rescale(c,r,px)
    S=pd.read_sql("select * from signals where status in ('pending','filled')",c)
    for sym,g in S.groupby('symbol'):
        a=candles(sym,int(g.last_ts.min())); mk=mark(sym)
        if a is None:
            c.execute("update signals set note='no price data' where symbol=? and status in ('pending','filled')",(sym,)); continue
        for _,r in g.iterrows():
            s=step(r.to_dict(),a); s['note']=(s.get('note') if isinstance(s.get('note'),str) else '').split(' | ')[0]+' | px:'+str(SRC.get(sym))
            if s['status'] in('pending','filled') and mk: s['last_px']=mk   # live mark for still_enterable / open PnL
            c.execute(f"update signals set {','.join(k+'=?' for k in COLS if k!='key')} where key=?",[s[k] if not (isinstance(s[k],float) and math.isnan(s[k])) else None for k in COLS if k!='key']+[s['key']])
    c.commit()
# ---------- portfolios ----------
def cost(): return 2*(CFG['costs']['taker_fee']+CFG['costs']['slippage'])
def assets(f):
    if not f or not os.path.exists(f): return None
    return {l.strip().replace('/USD','') for l in open(f) if l.strip() and not l.startswith('#')}
def confluence(c):
    """score at time t = sum of TF weights of live same-asset same-direction signals (incl. itself). Live = start_ts <= t < (exit_ts or now)."""
    A=pd.read_sql("select key,symbol,direction,timeframe,start_ts,exit_ts,fill_ts,status,last_px,entry_low,entry_high from signals where status!='malformed'",c)
    W=CFG['confluence_weights']; now=int(time.time()*1000); A['end']=A.exit_ts.fillna(now+1)
    out={}
    for _,r in A.iterrows():
        t=r.fill_ts if r.fill_ts==r.fill_ts and r.fill_ts is not None else (r.exit_ts if r.status=='cancelled' else now)
        g=A[(A.symbol==r.symbol)&(A.direction==r.direction)&(A.start_ts<=t)&(A.end>t)|(A.key==r.key)]
        out[r.key]=int(g.timeframe.map(W).sum())
    for k,v in out.items(): c.execute('update signals set confluence=? where key=?',(v,k))
    # still_enterable: live signal whose last price is inside the entry range
    c.execute("update signals set live_state=case when status in ('pending','filled') and last_px between entry_low and entry_high then 'still_enterable' else status end")
    c.commit()
# ---------- leverage / margin (paper model) ----------
LEVF='hl_meta.json'
def hl_meta():
    """public Hyperliquid meta (POST /info {"type":"meta"}), cached to hl_meta.json, refreshed daily"""
    try:
        d=json.load(open(LEVF))
        if time.time()-d['fetched']<86400: return d
    except Exception: d=None
    try:
        m=requests.post('https://api.hyperliquid.xyz/info',json={'type':'meta'},timeout=15).json(); MT=dict(m.get('marginTables') or [])
        assets={}
        for u in m['universe']:
            t=MT.get(u.get('marginTableId'),{}).get('marginTiers') or []
            assets[u['name']]=dict(max_lev=u['maxLeverage'],delisted=bool(u.get('isDelisted')),tiers=t,mmr=1/(2*(t[0]['maxLeverage'] if t else u['maxLeverage'])))
        d=dict(fetched=time.time(),fetched_at=time.strftime('%F %T'),source='POST https://api.hyperliquid.xyz/info {"type":"meta"}',assets=assets)
        json.dump(d,open(LEVF+'.tmp','w'),indent=1); os.replace(LEVF+'.tmp',LEVF)
    except Exception as e: log('hl meta fetch failed',e)
    return d or dict(assets={})
def hl_asset(sym,M):
    if '/' in sym and not sym.endswith('/USD'): return None   # crosses like SOL/BTC are not HL perps
    b=base(sym); A=M['assets']
    for k in (b,'k'+b,b+'COIN'):   # FART -> FARTCOIN on Hyperliquid
        if k in A and not A[k]['delisted']: return A[k]
    return None
def leverage(sym,direction,entry,stop,M):
    """L = min(asset max, largest integer L whose liq price is >= buffer x stop distance away).
    HL isolated liq: liq = P - side*(P/L - P*mmr)/(1 - side*mmr), mmr=1/(2*maxLev of tier) (approximation: first tier; paper sizes are far below tier bounds)."""
    LC=CFG.get('leverage',{}); a=hl_asset(sym,M); sd=abs(entry-stop)/entry; sg=-1 if direction=='Short' else 1
    if not a: return None,None,None,'not on Hyperliquid (excluded)'
    mx=a['max_lev']; mmr=1/(2*mx)
    lv=max(1,min(mx,int(1/(mmr+LC.get('liq_buffer',1.5)*sd*(1-sg*mmr)))))
    liq=entry-sg*(entry/lv-entry*mmr)/(1-sg*mmr)
    return lv,liq,mx,'hyperliquid'
# ---------- portfolio variants (paper) ----------
def cached(sym):
    f=os.path.join(CDIR,sym.replace('/','_')+'.npy'); return np.load(f) if os.path.exists(f) else None
BTCT={}
def btc_trend():
    """BTC 4h close vs EMA(n) from Hyperliquid 4h candles (1 request/cycle). Returns (close_times, above_flags)."""
    if BTCT.get('t',0)>time.time()-120: return BTCT['v']
    n=CFG.get('variants',{}).get('btc_ema',50); now=int(time.time()*1000)
    try:
        r=requests.post('https://api.hyperliquid.xyz/info',json={'type':'candleSnapshot','req':{'coin':'BTC','interval':'4h','startTime':now-400*14400_000,'endTime':now}},timeout=20).json()
        a=np.array([[x['t']+14400_000,float(x['c'])] for x in r if x['t']+14400_000<=now])
        ema=pd.Series(a[:,1]).ewm(span=n,adjust=False).mean().values
        v=(a[n:,0],a[n:,1]>ema[n:]); BTCT.update(t=time.time(),v=v)
    except Exception as e: log('btc trend fetch failed',e); v=BTCT.get('v')
    return v
def path(s,mode):
    """Re-simulate one signal for an execution variant on cached candles (same conservative rules as step()).
    better_entry: limit at range midpoint (or better if a candle opens beyond it); skip_no_fill if the original signal ends/invalidates/times out first.
    runners: same fill as the signal; 50% off at TP1, stop -> entry; at TP2 stop -> TP1; rest out at TP3 (no TP2/TP3: trail by original stop distance).
    nostop: same fill as the signal; no stop loss at all: 100% out at TP1, else at market (last close) at the normal timeout."""
    a=cached(s['symbol']); sh=s['direction']=='Short'; sg=-1 if sh else 1; TO=CFG['timeouts_days'][s['timeframe']]*86400e3
    r=dict(fill_ts=s['fill_ts'],entry=s['entry'],stop=s['stop'],status=s['status'],exit_ts=s['exit_ts'],exit_px=s['exit_px'],reason=s['reason'],last_px=s['last_px'],why=None)
    if a is None: return r
    if mode=='better_entry':
        mid=(s['entry_low']+s['entry_high'])/2; end=s['exit_ts'] if s['status']=='closed' and s['exit_ts']==s['exit_ts'] and s['exit_ts'] is not None else s['start_ts']+TO
        b=a[(a[:,0]>=s['start_ts'])&(a[:,0]<=end)]; hit=np.where((b[:,2]>=mid) if sh else (b[:,3]<=mid))[0]
        if not len(hit): r['why']='skip_no_fill'; return r
        i=hit[0]; o=b[i,1]; e=max(o,mid) if sh else min(o,mid); d1=abs(s['tp1']-e)
        r.update(fill_ts=int(b[i,0]),entry=e,stop=e-sg*d1/CFG['stop_rr'],status='filled',exit_ts=None,exit_px=None,reason=None)
        tgt=[s['tp1']]; part=[1.0]
    else:
        if s['fill_ts'] is None: return r
        tgt=[s['tp2']] if mode=='tp2' else ([s['tp1']] if mode=='nostop' else ([s['entry']+(s['tp1']-s['entry'])*CFG.get('variants',{}).get('half_frac',0.5)] if mode=='half_tp1' else [s['tp1'],s['tp2'],s['tp3']])); part=[0.5,0.5]   # tp2: 100% out at TP2, stop unchanged
    f=r['fill_ts']; e=r['entry']; stop=r['stop']; d0=abs(e-stop); b=a[a[:,0]>=f]; rem=1.0; got=0.0; stage=0; best=None; last=None
    beyond=lambda x,lv:(x<=lv) if sh else (x>=lv)
    for k,(ts,o,h,l,c,iv,si) in enumerate(b):
        if ts>f+TO: r.update(status='closed',exit_ts=int(ts),reason='timeout'); got+=rem*(last if last is not None else o); rem=0; break
        if mode!='nostop' and ((h>=stop) if sh else (l<=stop)): r.update(status='closed',exit_ts=int(ts),reason='stop' if stage==0 else ('breakeven' if stage==1 else 'trail_stop')); got+=rem*stop; rem=0; break
        if k>0:
            x=l if sh else h
            if mode in('better_entry','tp2','half_tp1','nostop'):
                if beyond(x,tgt[0]): r.update(status='closed',exit_ts=int(ts),reason={'better_entry':'tp1','tp2':'tp2','half_tp1':'half_tp1','nostop':'tp1'}[mode]); got+=tgt[0]; rem=0; break
            else:
                if stage==0 and beyond(x,tgt[0]): got+=0.5*tgt[0]; rem=0.5; stage=1; stop=e
                if stage>=1:
                    if tgt[1]==tgt[1] and tgt[2]==tgt[2] and tgt[1] is not None:
                        if stage==1 and beyond(x,tgt[1]): stage=2; stop=tgt[0]
                        if stage==2 and beyond(x,tgt[2]): r.update(status='closed',exit_ts=int(ts),reason='tp3'); got+=rem*tgt[2]; rem=0; break
                    else:
                        best=x if best is None or beyond(x,best) else best; ns=best-sg*d0
                        if beyond(ns,stop): stop=ns
        last=c
    if rem>0:   # still open: mark at live price
        px=s['last_px'] if s['last_px']==s['last_px'] and s['last_px'] is not None else last
        r.update(status='filled',exit_ts=None,last_px=(got+rem*px) if px is not None else None,reason=('runner open' if stage else None))
    else: r['exit_px']=got   # blended exit price (linear in return)
    return r
def variants(X,P,now):
    """per-portfolio filters / execution variants; sets _why (skip reason) and may change fill/exit fields"""
    X=X.copy(); X['_why']=None
    if not len(X): return X
    V=CFG.get('variants',{})
    if P.get('min_confluence'):
        ok=(X.confluence.fillna(0)>=P['min_confluence'])&(X.timeframe.isin(['4h','1d'])|(X.htf=='yes'))
        X.loc[~ok,'_why']='skip_confluence'
    if P.get('require_live_tfs'):   # same-asset same-direction signals of ALL these timeframes must be live at fill time (the signal itself counts)
        G=pd.read_sql("select symbol,direction,timeframe,start_ts,exit_ts from signals where status!='malformed'",sqlite3.connect(DB)); G['end']=G.exit_ts.fillna(now+1)
        def has(r):
            g=G[(G.symbol==r.symbol)&(G.direction==r.direction)&(G.start_ts<=r.fill_ts)&(G.end>r.fill_ts)]; return set(P['require_live_tfs'])<=set(g.timeframe)|{r.timeframe}
        ok=X.apply(has,axis=1); X.loc[~ok&X._why.isna(),'_why']='skip_confluence'
    if P.get('btc_trend_filter'):
        bt=btc_trend()
        if bt is None: X['_why']=X['_why'].fillna('skip_btc_trend_nodata')
        else:
            def tr(t):
                i=np.searchsorted(bt[0],t,side='right')-1; return None if i<0 else bool(bt[1][i])
            up=X.fill_ts.map(tr); bad=((X.direction=='Long')&(up!=True))|((X.direction=='Short')&(up!=False))
            X.loc[bad&X._why.isna(),'_why']='skip_btc_trend'
    if P.get('max_stale_frac') is not None:
        def stale(r):
            if 'all_active' not in str(r.source): return False   # pending signals fill later at the range itself: fresh by construction
            a=cached(r.symbol)
            if a is None: return False
            i=np.searchsorted(a[:,0],r.start_ts,side='right')-1
            if i<0: return False
            sg=-1 if r.direction=='Short' else 1; edge=r.entry_low if sg<0 else r.entry_high
            return sg*(a[i,4]-edge)/abs(r.tp1-edge)>P['max_stale_frac']
        st=X.apply(stale,axis=1); X.loc[st&X._why.isna(),'_why']='skip_stale'
    if P.get('execution') in ('better_entry','runners','tp2','half_tp1','nostop'):
        for i,r in X.iterrows():
            if r._why: continue
            p=path(r.to_dict(),P['execution'])
            for k in ('fill_ts','entry','stop','exit_ts','exit_px','reason','last_px'): X.at[i,k]=p[k]
            X.at[i,'status']='closed' if p['status']=='closed' else 'filled'
            if p['why']: X.at[i,'_why']=p['why']
    return X
def portfolios(c):
    S=pd.read_sql("select * from signals where fill_ts is not null",c); C=cost(); now=int(time.time()*1000); M=hl_meta()
    c.execute('create table if not exists margin (portfolio,ts,used,equity)'); c.execute('delete from trades'); c.execute('delete from equity'); c.execute('delete from margin'); D=CFG['portfolio_defaults']
    for name,p in CFG['portfolios'].items():
        P={**D,**p}; A=assets(P.get('assets_file'))
        X=S[S.timeframe.isin(P['timeframes'])&S.direction.isin(P['directions'])].copy()
        X=X[(X.tp1-X.entry).abs()/X.entry>=P['min_tp1_dist']]
        if A is not None: X=X[X.symbol.map(lambda s:s.replace('/USD','')).isin(A)|X.symbol.isin(A)]
        # htf_confluent: a live same-asset same-direction 4h/1d signal exists at fill time
        H=pd.read_sql("select symbol,direction,start_ts,exit_ts from signals where status!='malformed' and timeframe in ('4h','1d')",c); H['end']=H.exit_ts.fillna(now+1)
        X['htf']=[('yes' if ((H.symbol==r.symbol)&(H.direction==r.direction)&(H.start_ts<=r.fill_ts)&(H.end>r.fill_ts)).any() else 'no') for r in X.itertuples()] if len(X) else []
        if P.get('require_htf_confluence'): X=X[X.htf=='yes']
        if P.get('start_after'): X=X[X.fill_ts>=int(pd.Timestamp(P['start_after']).tz_localize(TZ).timestamp()*1000)]   # account reset: fresh from this time
        X=variants(X,P,now)
        # prioritize_confluence: signals filling at the same time compete for cap slots -> HTF-confluent first, then highest confluence score
        X=X.assign(_h=(X.htf=='yes').astype(int),_c=X.confluence.fillna(0)).sort_values(['fill_ts','_h','_c'],ascending=[True,False,False]) if P.get('prioritize_confluence') else X.sort_values('fill_ts'); eq=P['start']; opn=[]; pause={}; L={}; curve=[(int(X.fill_ts.min()) if len(X) else now,eq)]; rows=[]
        def close(t):
            nonlocal eq
            for q in sorted([q for q in opn if q['status']=='closed' and q['exit_ts']<=t],key=lambda q:q['exit_ts']):
                opn.remove(q); eq+=q['pnl']; curve.append((q['exit_ts'],eq))
                if q['pnl']<0:
                    L.setdefault(q['timeframe'],[]).append((q['exit_ts'],-q['pnl']))
                    if sum(x for tt,x in L[q['timeframe']] if tt>q['exit_ts']-P['pause_hours']*3600e3)>P['pause_loss_frac']*eq: pause[q['timeframe']]=q['exit_ts']+P['pause_hours']*3600e3
        for _,s in X.iterrows():
            close(s.fill_ts); tf=s.timeframe; why=s['_why'] if isinstance(s['_why'],str) else None
            if not why and not hl_asset(s.symbol,M): why='not_on_hyperliquid'
            if why: pass
            elif pause.get(tf,0)>s.fill_ts: why='skip_paused'
            elif sum(q['timeframe']==tf for q in opn)>=((P.get('caps_confluent') or {}).get(tf,P['caps'].get(tf,99)) if s.htf=='yes' else P['caps'].get(tf,99)): why='skip_cap'   # caps_confluent: higher total-open cap for HTF-confluent trades
            elif P.get('max_open_risk') and sum(q['risk_frac'] for q in opn)+P['risk']>P['max_open_risk']+1e-9: why='skip_risk_cap'   # generic: total open risk cap (each trade = risk at entry)
            elif P.get('max_same_direction') and sum(q['direction']==s.direction for q in opn)>=P['max_same_direction']: why='skip_same_dir'   # generic: max open trades in one direction
            sg=-1 if s.direction=='Short' else 1; sd=abs(s.entry-s.stop)/s.entry
            lv,liq,mx,lsrc=leverage(s.symbol,s.direction,s.entry,s.stop,M)
            nostop=P.get('execution')=='nostop'; stop_out=None if nostop else s.stop   # nostop: sd = nominal stop distance (sizing + R only), no stop traded
            if nostop and lv is not None:   # fixed leverage (default 1x): margin = notional/lev; isolated HL liq at that leverage
                lv=P.get('fixed_leverage',1); mmr=1/(2*mx); liq=s.entry-sg*(s.entry/lv-s.entry*mmr)/(1-sg*mmr)
            if lv is None: why='not_on_hyperliquid'   # rule: only assets tradable on Hyperliquid, in every portfolio
            n=min(P['risk']*eq/(sd+C),P['max_notional_frac']*eq); mg=n/lv if lv else 0
            if not why and sum(q['margin'] for q in opn)+mg>eq: why='insufficient_margin'
            if why: rows.append(dict(px_src=s.px_src,leverage=None,margin=None,liq_px=None,max_lev=mx,lev_src=lsrc,portfolio=name,confluence=s.confluence,htf_confluent=s.htf,key=s.key,symbol=s.symbol,timeframe=tf,direction=s.direction,fill_ts=s.fill_ts,entry=s.entry,stop=stop_out,status=why,reason=why,notional=0,ret=None,pnl=0,r=None,exit_ts=None,exit_px=None));continue
            closed=s.status=='closed'; px=s.exit_px if closed else s.last_px
            ret=sg*(px-s.entry)/s.entry-C; q=dict(px_src=s.px_src,leverage=lv,margin=mg,liq_px=liq,max_lev=mx,lev_src=lsrc,portfolio=name,confluence=s.confluence,htf_confluent=s.htf,key=s.key,symbol=s.symbol,timeframe=tf,direction=s.direction,fill_ts=int(s.fill_ts),entry=s.entry,stop=stop_out,exit_ts=int(s.exit_ts) if closed else None,exit_px=px,reason=s.reason if closed else 'open (marked)',status='closed' if closed else 'open',notional=n,ret=ret,pnl=n*ret,r=ret/(sd+C))
            rows.append(q); q=dict(q,risk_frac=P['risk'])
            opn.append(q)
        close(float('inf'))
        unreal=sum(r['pnl'] for r in rows if r['status']=='open'); curve.append((now,eq+unreal))
        for r in rows: c.execute(f"insert into trades ({','.join(r)}) values ({','.join('?'*len(r))})",list(r.values()))
        c.execute('insert into margin values (?,?,?,?)',(name,now,sum(r['margin'] for r in rows if r['status']=='open'),eq+unreal))
        c.executemany('insert into equity values (?,?,?)',[(name,int(t),e) for t,e in curve])
    c.commit()
def journal(c):
    T=pd.read_sql("select t.*,s.raw_text,s.source,s.tp1 from trades t join signals s using(key) where t.status in ('open','closed') order by t.fill_ts",c)
    for k in['fill_ts','exit_ts']: T[k]=pd.to_datetime(T[k],unit='ms',utc=True).dt.tz_convert(TZ).dt.strftime('%Y-%m-%d %H:%M')
    T.to_csv('journal.csv',index=False); return T
def publisher_watchdog():
    """keep the GitHub Pages publisher loop alive (serve/run_publish.sh) unless serve/STOP_PUBLISH exists"""
    d='/workspace/thor/serve'
    if not os.path.exists(f'{d}/run_publish.sh') or os.path.exists(f'{d}/STOP_PUBLISH'): return
    try: os.kill(int(open(f'{d}/publish.pid').read()),0); return
    except Exception: pass
    import subprocess; subprocess.Popen(['setsid','nohup','./run_publish.sh'],cwd=d,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,start_new_session=True); log('publisher watchdog: restarted run_publish.sh')
ONCE='--once' in sys.argv
def cycle():
    c=db(); n=ingest(c); update_signals(c); confluence(c); portfolios(c); journal(c)
    import dashboard; dashboard.build(c)
    st=dict(c.execute('select status,count(*) from signals group by status').fetchall())
    en=c.execute("select count(*) from signals where live_state='still_enterable'").fetchone()[0]; c.close()
    hb=dict(ts=time.strftime('%F %T'),new_signals=n,status=st,still_enterable=en,sources=SRC); open('heartbeat','w').write(json.dumps(hb)); log('cycle',hb)
    if ONCE: return   # one-shot (GitHub Actions engine): never relaunch the box publisher
    try: publisher_watchdog()
    except Exception as e: log('watchdog error',e)
    try:   # box: mirror new check_*.json to the engine repo's inbox branch (no-op if nothing new)
        import subprocess; subprocess.Popen(['/workspace/thor/serve/push_inbox.sh'],stdout=open('/workspace/thor/serve/push_inbox.log','a'),stderr=subprocess.STDOUT,start_new_session=True)
    except Exception as e: log('push_inbox error',e)
if __name__=='__main__':
    if '--once' in sys.argv: cycle(); sys.exit()
    open('tracker.pid','w').write(str(os.getpid()))
    while True:
        if os.path.exists('STOP'): log('STOP file present, exiting'); break
        try: cycle()
        except Exception: log('cycle error',traceback.format_exc())
        time.sleep(CFG['poll_seconds'])
