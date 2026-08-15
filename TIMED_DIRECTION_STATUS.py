from __future__ import annotations
from collections import defaultdict
from jupiterdegenbot.config import Settings
from jupiterdegenbot.storage import DB

s=Settings(); db=DB(s.database_path)
print('=== TIMED DIRECTION V2 STATUS ===')
print('DB:', db.path)
print('Model enabled:', bool(getattr(s,'timed_direction_model_enabled',False)))
print('LIVE enabled :', bool(getattr(s,'timed_direction_live_enabled',False)))
print('Required settled:', int(getattr(s,'timed_direction_live_min_settled',20)))
print('Max Brier:', float(getattr(s,'timed_direction_live_max_brier',0.22)))
print('Max log-loss:', float(getattr(s,'timed_direction_live_max_log_loss',0.68)))
with db.connect(readonly=True) as c:
    rows=c.execute("""SELECT asset,
      COUNT(*) total,
      SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open,
      SUM(CASE WHEN status='RESOLVED' THEN 1 ELSE 0 END) resolved,
      AVG(CASE WHEN status='RESOLVED' THEN brier_score END) brier,
      AVG(CASE WHEN status='RESOLVED' THEN log_loss END) logloss
      FROM shadow_predictions
      WHERE settlement_kind='timed_direction' AND model_name LIKE '%TIMED_DIRECTION_V2%'
      GROUP BY asset ORDER BY asset""").fetchall()
    for r in rows:
      n=int(r['resolved'] or 0); b=r['brier']; ll=r['logloss']
      ready=(n>=int(getattr(s,'timed_direction_live_min_settled',20)) and b is not None and b<=float(getattr(s,'timed_direction_live_max_brier',.22)) and ll is not None and ll<=float(getattr(s,'timed_direction_live_max_log_loss',.68)))
      print(f"{str(r['asset']):5} total={int(r['total'] or 0):4} open={int(r['open'] or 0):4} resolved={n:4} brier={b if b is not None else '-'} logloss={ll if ll is not None else '-'} live_ready={ready}")
    runs=c.execute("SELECT id,started_at,finished_at,status FROM runs WHERE kind='timed_fast' ORDER BY id DESC LIMIT 5").fetchall()
    print('\nLatest timed_fast runs:')
    for r in runs: print(dict(r))
