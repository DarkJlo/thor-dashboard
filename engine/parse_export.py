import json,re,csv,collections,pandas as pd
F='/home/box/agent-data/agents/018c2498-39bc-42e6-8b74-1a36a1dfd16a/attachments/b094db598effc8bca9e5f072669021a841f165fb2ddfe270f7005d92eb6500b2.json'
m=json.load(open(F))['messages']
def txt(x):
    t=x.get('text'); return t if isinstance(t,str) else ''.join(p if isinstance(p,str) else p.get('text','') for p in t)
num=lambda s:float(s.replace(',',''))
rows=[];legacy=0;bad=0
for x in m:
    if x.get('from')!='CyberFrogsThor': continue
    t=txt(x); mt=pd.Timestamp(int(x['date_unixtime']),unit='s',tz='UTC').tz_convert('-07:00').strftime('%Y-%m-%d %H:%M')
    if 'about to hit the zone' in t:
        r=re.search(r'(\S+) (\w+) (Long|Short) about to hit the zone!\s*([\d.,]+) to ([\d.,]+)\s*TP1: ([\d.,]+ \([\d.]+%\))\s*TP2: ([\d.,]+ \([\d.]+%\))\s*TP3: ([\d.,]+ \([\d.]+%\))\s*Invalid: ([\d.,]+)\s*Dt: (\S+)',t)
        if not r: bad+=1; continue
        s,tf,dr,a,b,t1,t2,t3,inv,dt=r.groups(); lo,hi=sorted([num(a),num(b)])
        rows.append([mt,s,tf,dr,lo,hi,t1.replace(',',''),t2.replace(',',''),t3.replace(',',''),num(inv),dt,' | '.join(l for l in t.split('\n') if l.strip()),'new',x['id']])
    elif 'Hitrate' in t:
        sym=tf=None
        for tok in re.finditer(r'(?m)^\s*(?P<h>(?P<hs>[^\s|]+)(?: (?P<htf>\d+[mhdw]))? \| Price)|^\s*(?P<tf>\d+[mhdw])\s*$|(?P<dr>Long|Short) (?P<a>[\d.,]+) - (?P<b>[\d.,]+)\s*TPs: (?P<t1>[\d.,]+), (?P<t2>[\d.,]+), (?P<t3>[\d.,]+)\s*Invalid: (?P<inv>[\d.,]+)\s*Dt: (?P<dt>[\d-]+(?: [\d:]+)?)',t):
            g=tok.groupdict()
            if g['tf']: tf=g['tf']; continue
            if g['h']:
                sym=g['hs']
                if g['htf']: tf=g['htf']
                continue
            if not sym or not tf: bad+=1; continue
            lo,hi=sorted([num(g['a']),num(g['b'])])
            rows.append([mt,sym,tf,g['dr'],lo,hi,num(g['t1']),num(g['t2']),num(g['t3']),num(g['inv']),g['dt'],tok.group(0).replace('\n',' | '),'old',x['id']])
    elif re.search(r'(Long|Short) [\d.]+ to',t): legacy+=1
D=pd.DataFrame(rows,columns='msg_time,symbol,timeframe,direction,entry_low,entry_high,tp1,tp2,tp3,invalid,dt,raw_text,format,msg_id'.split(','))
n0=len(D)
# old-format snapshots repeat the same zone; keep first appearance of an identical setup
D['key']=D[['symbol','timeframe','direction','entry_low','entry_high','invalid','dt']].astype(str).agg('|'.join,axis=1)
D['dup']=D.duplicated('key')&(D.format=='old')
D[~D.dup].drop(columns=['key','dup']).to_csv('signals_all.csv',index=False)
print('parsed blocks',n0,'unparsed',bad,'legacy-zone msgs (no TPs, skipped)',legacy,'dups removed',D.dup.sum())
E=D[~D.dup]; E['month']=E.msg_time.str[:7]
for c in['format','timeframe','direction','month']: print(E.groupby([c,'format']).size().unstack(fill_value=0) if c!='format' else E.format.value_counts())
# sanity vs signals.csv
S=pd.read_csv('signals.csv'); N=E[E.format=='new']
k=lambda d:d.msg_time+d.symbol+d.timeframe+d.direction+d.entry_low.astype(float).round(8).astype(str)
print('signals.csv matched:',k(S).isin(set(k(N))).sum(),'/',len(S))
print('new in Sep24-Oct11 window:',((N.msg_time>='2026-09-24')&(N.msg_time<'2026-10-12')).sum())
print('symbols:',sorted(E.symbol.unique()))
