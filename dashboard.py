from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from typing import Any
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env')
DB=Path(os.getenv('DATABASE_PATH','data/jupiter_degen.db'))
if not DB.is_absolute(): DB=ROOT/DB
ASSETS=[x.strip().upper() for x in os.getenv('CRYPTO_ASSETS','BTC,ETH,SOL,XRP,HYPE,DOGE,BNB').split(',') if x.strip()]
REFRESH=max(60,int(os.getenv('DASHBOARD_AUTO_REFRESH_SECONDS','60') or 60))
MODE=os.getenv('TRADING_MODE','paper').upper()
MODEL=os.getenv('OLLAMA_MODEL','qwen2.5:1.5b-instruct-q4_K_M')

st.set_page_config(page_title='Jupiter Degen Edge V1.0',page_icon='⚡',layout='wide')
st.markdown('''<style>
:root{--n:#39ff14;--c:#00ffc8;--r:#ff426d;--a:#ffb020;--bg:#020603;--p:#061009;--line:rgba(57,255,20,.25)}
html,body,[data-testid="stAppViewContainer"]{background:radial-gradient(circle at 50% -20%,#08230f 0,#020603 40%,#010301 100%);color:var(--n)}
.block-container{max-width:1800px;padding-top:1rem}.hero{border:1px solid rgba(57,255,20,.42);background:#061009;padding:20px;border-radius:16px}.title{font-size:34px;font-weight:900}.cyan{color:var(--c)!important}.bad{color:var(--r)!important}.warn{color:var(--a)!important}.muted{color:#92ad98!important}
.card{border:1px solid var(--line);background:#051008;padding:14px;border-radius:12px;min-height:88px}.lab{font-size:10px;color:#78a180;letter-spacing:.1em}.val{font-size:21px;font-weight:800;margin-top:8px}.assetgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:12px 0 18px}.asset{border:1px solid var(--line);background:#041006;border-radius:10px;padding:12px}.sym{font-weight:900}.price{font-size:18px;color:#e7ffe9;font-weight:800}.meta{font-size:10px;color:#7fa387}.section{font-size:20px;font-weight:900;margin:15px 0 10px}.callout{border:1px solid var(--line);background:rgba(57,255,20,.04);border-radius:10px;padding:12px;color:#bcd4c0}.stDataFrame{border:1px solid var(--line)}
</style>''',unsafe_allow_html=True)

def conn():
    c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row; return c

def exists(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def read(c,q,p=()):
    try:return pd.read_sql_query(q,c,params=p)
    except Exception:return pd.DataFrame()

def num(v,default=0.0):
    try:return float(v)
    except:return default

def card(label,value,cls='',sub=''):
    st.markdown(f"<div class='card'><div class='lab'>{label}</div><div class='val {cls}'>{value}</div><div class='meta'>{sub}</div></div>",unsafe_allow_html=True)

def payload(x):
    try:return json.loads(str(x or '{}'))
    except:return {}

st.markdown(f"<div class='hero'><div class='title'>JUPITER <span class='cyan'>DEGEN EDGE</span> V1.0</div><div class='muted'>Prediction scanner · PAPER/shadow learning · TIMED 5m/15m · local Ollama guard · fail-closed LIVE</div><div style='margin-top:8px'>MODE <b>{MODE}</b> · ASSETS {' / '.join(ASSETS)} · OLLAMA {MODEL}</div></div>",unsafe_allow_html=True)

@st.fragment(run_every=REFRESH)
def render():
    if not DB.exists():
        st.warning('Database not created yet. Run INSTALL.ps1, then SCAN_ONCE.ps1 or START_BOT.ps1.'); return
    with conn() as c:
        runs=read(c,"SELECT * FROM runs ORDER BY id DESC LIMIT 500") if exists(c,'runs') else pd.DataFrame()
        prices=read(c,"SELECT * FROM crypto_prices ORDER BY id DESC LIMIT 3000") if exists(c,'crypto_prices') else pd.DataFrame()
        orders=read(c,"SELECT * FROM orders ORDER BY id DESC LIMIT 1500") if exists(c,'orders') else pd.DataFrame()
        positions=read(c,"SELECT * FROM positions ORDER BY updated_at DESC LIMIT 1000") if exists(c,'positions') else pd.DataFrame()
        markets=read(c,"SELECT * FROM markets ORDER BY last_seen DESC LIMIT 1500") if exists(c,'markets') else pd.DataFrame()
        preds=read(c,"SELECT * FROM model_predictions ORDER BY id DESC LIMIT 1500") if exists(c,'model_predictions') else pd.DataFrame()
        signals=read(c,"SELECT * FROM signals ORDER BY id DESC LIMIT 1000") if exists(c,'signals') else pd.DataFrame()
        shadow=read(c,"SELECT * FROM shadow_predictions ORDER BY id DESC LIMIT 5000") if exists(c,'shadow_predictions') else pd.DataFrame()
        profiles=read(c,"SELECT * FROM learning_profiles ORDER BY active DESC,asset,horizon,comparator") if exists(c,'learning_profiles') else pd.DataFrame()
        models=read(c,"SELECT * FROM neural_models ORDER BY id DESC LIMIT 100") if exists(c,'neural_models') else pd.DataFrame()
        validations=read(c,"SELECT * FROM validation_runs ORDER BY id DESC LIMIT 100") if exists(c,'validation_runs') else pd.DataFrame()
        incidents=read(c,"SELECT * FROM incidents ORDER BY id DESC LIMIT 300") if exists(c,'incidents') else pd.DataFrame()
        life=read(c,"SELECT * FROM lifecycle_log ORDER BY id DESC LIMIT 1000") if exists(c,'lifecycle_log') else pd.DataFrame()
    last=runs.iloc[0].to_dict() if not runs.empty else {}
    msg=payload(last.get('message'))
    active_pos=positions
    if not positions.empty and 'status' in positions.columns: active_pos=positions[positions.status.astype(str).str.lower().isin(['open','active','pending','claimable','closing','closing_unknown'])]
    cost=pd.to_numeric(active_pos.get('cost_usd',pd.Series(dtype=float)),errors='coerce').fillna(0).sum() if not active_pos.empty else 0
    value=pd.to_numeric(active_pos.get('value_usd',pd.Series(dtype=float)),errors='coerce').fillna(0).sum() if not active_pos.empty else 0
    pnl=pd.to_numeric(active_pos.get('pnl_after_fees_usd',pd.Series(dtype=float)),errors='coerce').fillna(0).sum() if not active_pos.empty else 0
    paper=orders[orders.get('mode',pd.Series(index=orders.index,dtype=str)).astype(str).str.lower().eq('paper')] if not orders.empty else pd.DataFrame()
    live=orders[orders.get('mode',pd.Series(index=orders.index,dtype=str)).astype(str).str.lower().eq('live')] if not orders.empty else pd.DataFrame()
    c1,c2,c3,c4,c5=st.columns(5)
    with c1:card('LAST RUN',str(last.get('id') or '—'),sub=str(last.get('status') or 'waiting'))
    with c2:card('PAPER ORDERS',str(len(paper)))
    with c3:card('LIVE ORDERS',str(len(live)),'warn' if len(live) else '')
    with c4:card('OPEN LIVE',str(len(active_pos)))
    with c5:card('LIVE P&L',f'{pnl:+.2f} $','bad' if pnl<0 else 'cyan',sub=f'cost {cost:.2f}$ · value {value:.2f}$')
    latest={}
    if not prices.empty and {'asset','price'}.issubset(prices.columns):
        for a,g in prices.groupby(prices.asset.astype(str).str.upper(),sort=False): latest[a]=num(g.iloc[0].price,None)
    settled={a:0 for a in ASSETS}
    if not shadow.empty and {'asset','status'}.issubset(shadow.columns):
        rr=shadow[shadow.status.astype(str).str.upper().eq('RESOLVED')]
        for a,g in rr.groupby(rr.asset.astype(str).str.upper()): settled[a]=len(g)
    blocks=[]
    for a in ASSETS:
        p=latest.get(a); pt='WAITING' if p is None else (f'${p:,.4f}' if p<100 else f'${p:,.2f}')
        blocks.append(f"<div class='asset'><div class='sym'>{a}</div><div class='price'>{pt}</div><div class='meta'>RESOLVED {settled.get(a,0)}</div></div>")
    st.markdown("<div class='assetgrid'>"+''.join(blocks)+"</div>",unsafe_allow_html=True)
    section=st.radio('Section',['COMMAND CENTER','PAPER POSITIONS','DEGEN MARKETS','CRYPTO DATA','SIGNALS / AI','LEARNING','RESEARCH / VALIDATION','LIVE','SYSTEM'],horizontal=True,label_visibility='collapsed')
    if section=='COMMAND CENTER':
        st.markdown("<div class='section'>CURRENT CYCLE</div>",unsafe_allow_html=True)
        cols=st.columns(6); vals=[('RUN',last.get('id','—')),('MARKETS',msg.get('jupiter_degen_markets',0)),('PREDICTIONS',msg.get('degen_supported',0)),('SIGNALS',msg.get('signals',0)),('ORDERS',msg.get('orders',0)),('AI REVIEWS',msg.get('ai_reviews',0))]
        for col,(k,v) in zip(cols,vals):
            with col:card(k,str(v))
        st.markdown(f"<div class='callout'>STATE: <b>{msg.get('cycle_outcome',last.get('status','waiting'))}</b> · TIMED FAST is independent and continues SHADOW/PAPER learning even when no asset is LIVE-ready.</div>",unsafe_allow_html=True)
        st.markdown("<div class='section'>LATEST ORDERS</div>",unsafe_allow_html=True); st.dataframe(orders.head(30),use_container_width=True,hide_index=True)
    elif section=='PAPER POSITIONS':
        st.markdown("<div class='section'>PAPER</div>",unsafe_allow_html=True); st.dataframe(paper,use_container_width=True,hide_index=True)
    elif section=='DEGEN MARKETS': st.dataframe(markets,use_container_width=True,hide_index=True)
    elif section=='CRYPTO DATA':
        st.markdown("<div class='section'>PRICES</div>",unsafe_allow_html=True); st.dataframe(prices,use_container_width=True,hide_index=True)
        st.markdown("<div class='section'>MODEL PREDICTIONS</div>",unsafe_allow_html=True); st.dataframe(preds,use_container_width=True,hide_index=True)
    elif section=='SIGNALS / AI': st.dataframe(signals,use_container_width=True,hide_index=True)
    elif section=='LEARNING':
        active=int(pd.to_numeric(profiles.get('active',pd.Series(dtype=float)),errors='coerce').fillna(0).sum()) if not profiles.empty else 0
        timed=shadow[(shadow.get('settlement_kind',pd.Series(index=shadow.index,dtype=str)).astype(str)=='timed_direction')] if not shadow.empty else pd.DataFrame()
        a,b,c=st.columns(3)
        with a:card('ACTIVE PROFILES',str(active))
        with b:card('SHADOW TOTAL',str(len(shadow)))
        with c:card('TIMED V2',str(len(timed)))
        st.markdown("<div class='section'>TIMED / SHADOW</div>",unsafe_allow_html=True); st.dataframe(timed.head(1000),use_container_width=True,hide_index=True)
        st.markdown("<div class='section'>ADAPTIVE PROFILES</div>",unsafe_allow_html=True); st.dataframe(profiles,use_container_width=True,hide_index=True)
    elif section=='RESEARCH / VALIDATION':
        st.markdown("<div class='section'>NEURAL MODELS</div>",unsafe_allow_html=True); st.dataframe(models,use_container_width=True,hide_index=True)
        st.markdown("<div class='section'>VALIDATIONS</div>",unsafe_allow_html=True); st.dataframe(validations,use_container_width=True,hide_index=True)
    elif section=='LIVE':
        st.warning('Real-money LIVE is optional and disabled in the public .env.example. Read docs/LIVE_TRADING.md before changing it.')
        st.dataframe(active_pos,use_container_width=True,hide_index=True)
    else:
        st.markdown("<div class='section'>RUNS</div>",unsafe_allow_html=True); st.dataframe(runs.head(100),use_container_width=True,hide_index=True)
        st.markdown("<div class='section'>INCIDENTS</div>",unsafe_allow_html=True); st.dataframe(incidents,use_container_width=True,hide_index=True)
        st.markdown("<div class='section'>LIFECYCLE</div>",unsafe_allow_html=True); st.dataframe(life.head(200),use_container_width=True,hide_index=True)
render()
