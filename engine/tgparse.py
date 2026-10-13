"""Reuses /workspace/thor/parse_export.py parsing logic on a Telegram Desktop export."""
import json,re,pandas as pd
SRC=open('/workspace/thor/parse_export.py').read()
BODY=SRC[SRC.index('rows=[];'):SRC.index('D=pd.DataFrame')]
COLS='msg_time,symbol,timeframe,direction,entry_low,entry_high,tp1,tp2,tp3,invalid,dt,raw_text,format,msg_id'.split(',')
def parse(path):
    g={'json':json,'re':re,'pd':pd,'m':json.load(open(path))['messages']}
    exec(SRC[SRC.index('def txt'):SRC.index('rows=[];')],g); exec(BODY,g)
    return pd.DataFrame(g['rows'],columns=COLS)
def parse_texts(texts,unixtime):
    """Parse bot screen texts (pair views etc.) with the same export parser by wrapping them as bot messages."""
    g={'json':json,'re':re,'pd':pd,'m':[{'from':'CyberFrogsThor','date_unixtime':str(int(unixtime)),'text':t,'id':i} for i,t in enumerate(texts)]}
    exec(SRC[SRC.index('def txt'):SRC.index('rows=[];')],g); exec(BODY,g)
    return pd.DataFrame(g['rows'],columns=COLS)
