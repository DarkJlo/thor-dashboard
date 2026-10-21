"""Schema + validator for signal-check inbox files (check_*.json) written by the THOR signal-check routine. PAPER ONLY.
File:  {"checked_at": "YYYY-MM-DD HH:MM[:SS]" (Phoenix time unless an offset is given), "active": [item...], "pair_views": {label: view} | [view...], ...}
Item:  symbol (str), timeframe (15m|1h|4h|1d), direction (Long|Short), entry_low, entry_high, tp1, tp2, tp3, invalid (numbers or numeric strings),
       source: all_active | ask_thor | alert. Missing source is accepted only when status_text is exactly "All active" (the All active screen);
       otherwise the item is REJECTED (never defaulted to all_active). dt (Thor 'Dt:' stamp, UTC) optional.
View:  either the raw screen text (old format: accepted, marked 'no captured_at') or {"text": str, "captured_at": "YYYY-MM-DD HH:MM[:SS]"}.
       A view captured more than MAX_VIEW_AGE_MIN before checked_at is rejected. A label like 'HBAR/USD (3)' whose menu count differs from
       the number of 'Dt:' lines parsed from the text gives a warning (T: buttons count requests, not signals, so it's not an error).
usage: python3 inbox_schema.py FILE...   (prints a report, exit 1 if any file is invalid)"""
import json,re,sys
import pandas as pd
SOURCES=('all_active','ask_thor','alert'); TFS=('15m','1h','4h','1d'); LEVELS=('entry_low','entry_high','tp1','tp2','tp3','invalid')
MAX_VIEW_AGE_MIN=30; TZ='-07:00'
def ts_ms(t):
    x=pd.Timestamp(str(t)); return int((x.tz_localize(TZ) if x.tzinfo is None else x).timestamp()*1000)
def _num(v):
    """same rules as tracker.num()"""
    if isinstance(v,(int,float)) and not isinstance(v,bool): return float(v)
    t=str(v).replace(',','').replace('\u2212','-').strip()
    if ':' in t: t=t.rsplit(':',1)[1]
    if re.search(r'\d+\.\d+\.\d',t): return None
    m=re.search(r'(?<![\d.])[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?![\d.])',t); return float(m.group()) if m else None
def item_source(r):
    s=r.get('source')
    if s in SOURCES: return s,None
    if s is None and str(r.get('status_text','')).strip().lower()=='all active': return 'all_active',None
    return None,('unknown source '+repr(s)) if s is not None else 'missing source (and status_text is not "All active")'
def check_item(r):
    """-> (normalised item or None, error or None)"""
    if not isinstance(r,dict): return None,'item is not an object'
    miss=[k for k in ('symbol','timeframe','direction')+LEVELS if r.get(k) in (None,'')]
    if miss: return None,'missing '+','.join(miss)
    if r['timeframe'] not in TFS: return None,f"timeframe {r['timeframe']!r} not in {TFS}"
    if str(r['direction']).capitalize() not in ('Long','Short'): return None,f"direction {r['direction']!r}"
    bad=[k for k in LEVELS if _num(r[k]) is None]
    if bad: return None,'non-numeric '+','.join(bad)
    src,err=item_source(r)
    if err: return None,err
    return dict(r,source=src),None
def menu_count(label):
    m=re.search(r'\((\d+)\)',str(label)); return int(m.group(1)) if m else None
def check_views(pv,checked_ms):
    """-> (list of (label,text,captured_ms or None)), warnings, rejected"""
    out=[];warn=[];rej=[]
    items=list(pv.items()) if isinstance(pv,dict) else [(f'view{i}',v) for i,v in enumerate(pv or [])]
    for lab,v in items:
        cap=None
        if isinstance(v,dict):
            txt=v.get('text')
            if not isinstance(txt,str): rej.append(f'{lab}: view object without text'); continue
            if v.get('captured_at'):
                try: cap=ts_ms(v['captured_at'])
                except Exception: rej.append(f'{lab}: unparseable captured_at {v.get("captured_at")!r}'); continue
                age=(checked_ms-cap)/60000
                if age>MAX_VIEW_AGE_MIN: rej.append(f'{lab}: captured {age:.0f} min before checked_at (max {MAX_VIEW_AGE_MIN})'); continue
                if age<-10: rej.append(f'{lab}: captured_at {-age:.0f} min AFTER checked_at'); continue
            else: warn.append(f'{lab}: no captured_at (view time unverified)')
        elif isinstance(v,str): txt=v; warn.append(f'{lab}: old format, no captured_at (view time unverified)')
        else: rej.append(f'{lab}: view is neither text nor object'); continue
        n=menu_count(lab); k=len(re.findall(r'\bDt:',txt))
        if n is not None and n!=k: warn.append(f'{lab}: menu count {n} vs {k} parsed Dt line(s)')
        out.append((lab,txt,cap))
    return out,warn,rej
# ---------- ladder age filter (2026-10-20) ----------
# A pair view lists every ladder Thor still shows for that pair, including ones posted days or months ago (each block has its own
# 'Dt:' stamp, UTC). Only ladders that are new since this pair was last read, and young enough for their timeframe, are new signals.
# Shared by the engine ingest (tracker.py), the box inbox builder (tg/build_check.py) and backtest/append_signals.py.
MAX_LADDER_AGE_H={'15m':12,'1h':24,'4h':48,'1d':48}   # <= engine max_backfill_hours, so a kept ladder starts at its own Dt
PREV_VIEW_GRACE_MIN=60   # Dt within this many minutes before the previous read still counts as new (bot posting lag / clock skew)
def dt_ms(dt):
    """Thor 'Dt:' stamp (UTC unless it carries an offset) -> epoch ms; None if missing or unparseable."""
    if dt is None or str(dt).strip() in ('','None','nan','NaT'): return None
    try: x=pd.Timestamp(str(dt)); return int((x if x.tzinfo else x.tz_localize('UTC')).timestamp()*1000)
    except Exception: return None
def stale_reason(dt,tf,view_ms,prev_view_ms=None,max_age_h=None):
    """None if a pair-view ladder can be a new signal, else why it is old history:
    its Dt is before the previous read of the same pair view (it was already on screen then), or older than MAX_LADDER_AGE_H[tf] at view time.
    A ladder without a parseable Dt is never dropped here (the caller falls back to the view time)."""
    t=dt_ms(dt)
    if t is None or view_ms is None: return None
    age=(view_ms-t)/3.6e6; lim=(max_age_h or MAX_LADDER_AGE_H).get(tf)
    if prev_view_ms and t<prev_view_ms-PREV_VIEW_GRACE_MIN*60_000:
        return f'Dt {dt} UTC is before the previous read of this pair view ({(view_ms-prev_view_ms)/3.6e6:.1f}h earlier); age {age:.1f}h'
    if lim is not None and age>lim: return f'Dt {dt} UTC is {age:.1f}h old at view time (max {lim}h for {tf})'
    return None
def check_file(d):
    """-> dict(ok, error, checked_ms, items, rejected_items, views, view_warnings, rejected_views)"""
    R=dict(ok=False,error=None,items=[],rejected_items=[],views=[],view_warnings=[],rejected_views=[])
    if not isinstance(d,dict): R['error']='top level is not an object'; return R
    if 'messages' in d: R.update(ok=True,kind='telegram_export'); return R
    if not d.get('checked_at'): R['error']='missing checked_at'; return R
    try: R['checked_ms']=ts_ms(d['checked_at'])
    except Exception: R['error']=f"unparseable checked_at {d.get('checked_at')!r}"; return R
    a=d.get('active',[])
    if not isinstance(a,list): R['error']='active is not a list'; return R
    pv=d.get('pair_views') or {}
    if not isinstance(pv,(dict,list)): R['error']='pair_views is neither an object nor a list'; return R
    for i,r in enumerate(a):
        it,err=check_item(r)
        if err: R['rejected_items'].append(f"active[{i}] {r.get('symbol') if isinstance(r,dict) else ''} {r.get('timeframe') if isinstance(r,dict) else ''}: {err}")
        else: R['items'].append(it)
    R['views'],R['view_warnings'],R['rejected_views']=check_views(pv,R['checked_ms'])
    R.update(ok=True,kind='check'); return R
if __name__=='__main__':
    bad=0
    for f in sys.argv[1:]:
        try: R=check_file(json.load(open(f)))
        except Exception as e: R=dict(ok=False,error=f'not valid JSON: {e}')
        bad+=not R['ok']
        print(f"{f}: {'OK' if R['ok'] else 'INVALID: '+str(R['error'])}" + (f" | items {len(R['items'])} ok, {len(R['rejected_items'])} rejected | views {len(R['views'])} ok, {len(R['rejected_views'])} rejected, {len(R['view_warnings'])} warnings" if R['ok'] and R.get('kind')=='check' else ''))
        for k in ('rejected_items','rejected_views','view_warnings'):
            for x in R.get(k,[]): print(f'  {k[:-1] if k.endswith("s") else k}: {x}')
    sys.exit(1 if bad else 0)
