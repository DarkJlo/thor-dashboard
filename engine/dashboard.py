# THOR paper tracker dashboard: dumps tracker.db state as JSON and injects it into
# dashboard_template.html (self-contained, offline). A hosted version can serve the
# same template and replace the embedded JSON with a fetch of payload(c).
import pandas as pd,numpy as np,json,time,os,math
TZ='-07:00'
D=os.path.dirname(os.path.abspath(__file__))
def ext_candles(D,syms):
    """View-only charts for assets not on Hyperliquid (stocks, SOL/BTC...): Yahoo Finance candles fetched by the engine
    (the browser can't call Yahoo directly). Cached in ext_candles.json, refreshed at most every 30 min. Never used for trading."""
    import requests
    F=os.path.join(D,'ext_candles.json')
    try: C=json.load(open(F))
    except Exception: C={}
    out={}
    for sym in syms:
        b=str(sym).replace('T0:','').replace('T:','')
        cands=[b[:-4]+'-USD'] if b.endswith('USDT') else ([b.replace('/','-')] if '/' in b else [b,b+'-USD'])
        e=C.get(sym)
        if not e or time.time()-e.get('t',0)>1800:
            e={'t':time.time(),'src':None,'bars':{}}
            for y in cands:
                got={}
                for tf,iv,rg in (('15m','15m','20d'),('1h','60m','180d'),('1d','1d','2y')):
                    try:
                        r=requests.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{y}',params=dict(interval=iv,range=rg),headers={'User-Agent':'Mozilla/5.0'},timeout=15).json()['chart']['result'][0]
                        q=r['indicators']['quote'][0]
                        got[tf]=[[t*1000,o,h,l,c] for t,o,h,l,c in zip(r['timestamp'],q['open'],q['high'],q['low'],q['close']) if None not in (o,h,l,c)]
                    except Exception: pass
                if got.get('1d'):
                    h1=got.get('1h',[]);h4={}
                    for t,o,h,l,c in h1:
                        k=t//14400000*14400000;x=h4.get(k)
                        h4[k]=[k,o,h,l,c] if not x else [k,x[1],max(x[2],h),min(x[3],l),c]
                    got['4h']=[h4[k] for k in sorted(h4)]
                    e={'t':time.time(),'src':'Yahoo '+y,'bars':got};break
            C[sym]=e
        if e.get('bars'): out[sym]=e
    try: json.dump(C,open(F,'w'))
    except Exception: pass
    return out

def _clean(v):
    if v is None: return None
    if isinstance(v,(np.integer,)): return int(v)
    if isinstance(v,(float,np.floating)): return None if math.isnan(v) else float(f'{float(v):.8g}')
    return v
def _rows(df,cols): return [[_clean(x) for x in r] for r in df[cols].itertuples(index=False)] if len(df) else []
def payload(c):
    import yaml; cfg=yaml.safe_load(open(os.path.join(D,'config.yaml')))
    S=pd.read_sql('select * from signals',c); T=pd.read_sql('select * from trades',c); E=pd.read_sql('select * from equity order by ts',c)
    for k in ['fill_ts','exit_ts']: T[k]=pd.to_numeric(T[k],errors='coerce')
    sc=['key','symbol','timeframe','direction','entry_low','entry_high','tp1','tp2','tp3','invalid','stop','entry','status','live_state','confluence','start_ts','dt','first_seen_ts','fill_ts','exit_ts','exit_px','reason','last_ts','last_px','source','note','raw_text','px_src']
    tc=['leverage','margin','liq_px','max_lev','lev_src','portfolio','key','symbol','timeframe','direction','confluence','htf_confluent','status','reason','fill_ts','entry','stop','exit_ts','exit_px','notional','ret','pnl','r','px_src','risk','boosted']
    hb={}
    try: hb=json.load(open(os.path.join(D,'heartbeat')))
    except Exception: pass
    pd_=cfg.get('portfolio_defaults',{})
    ports={p:dict(v,start=v.get('start',pd_.get('start',1000)),risk=v.get('risk',pd_.get('risk'))) for p,v in cfg['portfolios'].items()}
    for p,v in ports.items():
        if v.get('assets_file'):
            f=os.path.join(D,v['assets_file']); v['assets']=[l.strip() for l in open(f) if l.strip() and not l.startswith('#')] if os.path.exists(f) else []
    try: MG={r[0]:dict(used=r[1],equity=r[2]) for r in c.execute('select portfolio,used,equity from margin')}
    except Exception: MG={}
    try: hm=json.load(open(os.path.join(D,'hl_meta.json'))); hlm=dict(fetched_at=hm.get('fetched_at'),source=hm.get('source'))
    except Exception: hlm={}
    hlmax={}; hlcoin={}   # hlcoin: symbol -> [HL coin, price scale] for client-side candle charts (kPEPE etc. quote per 1000)
    try:
        HA=json.load(open(os.path.join(D,'hl_meta.json')))['assets']
        for sym in S.symbol.unique():
            if '/' in sym and not sym.endswith('/USD'): continue
            b=(sym[2:].replace('USDT','') if sym.startswith('T:') else sym.replace('/USD','')).replace('/','')   # same as tracker.base()
            for k in (b,'k'+b,b+'COIN'):
                if k in HA and not HA[k].get('delisted'): hlmax[sym]=HA[k]['max_lev']; hlcoin[sym]=[k,1e-3 if k=='k'+b else 1]; break
    except Exception: pass
    extc=ext_candles(D,[x for x in S.symbol.unique() if x not in hlcoin])
    return dict(engine=os.environ.get('THOR_ENGINE','box'),hlmax=hlmax,hlcoin=hlcoin,extc=extc,variants=cfg.get('variants',{}),margin=MG,hl_meta=hlm,leverage_cfg=cfg.get('leverage',{}),defaults=pd_,stop_rr=cfg.get('stop_rr'),timeouts=cfg.get('timeouts_days',{}),confluence_weights=cfg.get('confluence_weights',{}),generated=(G:=int(time.time()*1000)),gen=G,heartbeat=hb,poll_seconds=cfg.get('poll_seconds',180),costs=cfg.get('costs',{}),
        buckets=cfg.get('confluence_buckets',[0,2,4,7,100]),portfolios=ports,
        signal_cols=sc,signals=_rows(S,sc),trade_cols=tc,trades=_rows(T,tc),
        equity={p:_rows(g,['ts','equity']) for p,g in E.groupby('portfolio',sort=False)})
def build(c,out=None):
    P=payload(c); data=json.dumps(P,separators=(',',':')).replace('</','<\\/')
    tpl=open(os.path.join(D,'dashboard_template.html')).read()
    out=out or os.path.join(D,'dashboard.html'); tmp=out+'.tmp'
    open(tmp,'w').write(tpl.replace('/*__DATA__*/null',data,1)); os.replace(tmp,out)
    # version.json next to the page: the browser polls it and reloads only when gen changes
    vf=os.path.join(os.path.dirname(os.path.abspath(out)),'version.json'); open(vf+'.tmp','w').write(json.dumps({'gen':P['gen']})); os.replace(vf+'.tmp',vf)
if __name__=='__main__':
    import sqlite3,sys; c=sqlite3.connect(os.path.join(D,'tracker.db')); build(c,sys.argv[1] if len(sys.argv)>1 else None); c.close()
