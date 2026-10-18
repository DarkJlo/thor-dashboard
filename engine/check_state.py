#!/usr/bin/env python3
"""Gate before the engine force-pushes `state` (PAPER ONLY).
usage: check_state.py NEW_DB [PREV_DB]
Fails (exit 1) unless NEW_DB exists, opens, passes PRAGMA integrity_check, and its signal count and closed-trade
count are not lower than PREV_DB's. Set ALLOW_SHRINK=1 (workflow_dispatch input allow_shrink) to accept a deliberate
drop (e.g. a dedup or a rollback); the drop is still printed."""
import os,sys,sqlite3
def counts(p):
    c=sqlite3.connect(f'file:{p}?mode=ro',uri=True)
    ic=c.execute('pragma integrity_check').fetchone()[0]
    s=c.execute('select count(*) from signals').fetchone()[0]
    t=c.execute("select count(*) from trades where status='closed'").fetchone()[0]
    c.close(); return ic,s,t
def main(a):
    new=a[0]; prev=a[1] if len(a)>1 else None; allow=os.environ.get('ALLOW_SHRINK','').lower() in ('1','true','yes')
    if not os.path.isfile(new) or os.path.getsize(new)==0: print(f'::error::{new} missing or empty'); return 1
    try: ic,s,t=counts(new)
    except Exception as e: print(f'::error::{new} does not open as a tracker db: {e}'); return 1
    if ic!='ok': print(f'::error::{new} integrity_check: {ic}'); return 1
    print(f'new state: integrity ok, signals={s}, closed_trades={t}')
    if not prev or not os.path.isfile(prev): print('no previous state db to compare (first run?)'); return 0
    try: _,ps,pt=counts(prev)
    except Exception as e: print(f'::warning::previous state db unreadable ({e}); skipping count comparison'); return 0
    print(f'previous state: signals={ps}, closed_trades={pt}')
    bad=[f'{n} {v}<{pv}' for n,v,pv in (('signals',s,ps),('closed_trades',t,pt)) if v<pv]
    if bad:
        if allow: print(f'::warning::state shrank ({", ".join(bad)}) but ALLOW_SHRINK is set: saving anyway'); return 0
        print(f'::error::state shrank ({", ".join(bad)}): refusing to overwrite `state`. Re-run with allow_shrink=true if intended.'); return 1
    return 0
if __name__=='__main__': sys.exit(main(sys.argv[1:]))
