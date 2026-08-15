$ErrorActionPreference='Stop'; Set-Location $PSScriptRoot
@'
import sqlite3
from datetime import datetime, timezone, timedelta
from jupiterdegenbot.config import Settings
s=Settings(); since=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
c=sqlite3.connect(f"file:{s.database_path}?mode=ro",uri=True); c.row_factory=sqlite3.Row
print('=== ACTIVITY - LAST 24H ===')
orders=c.execute("SELECT mode,status,COUNT(*) n FROM orders WHERE created_at>=? GROUP BY mode,status ORDER BY mode,status",(since,)).fetchall()
print('Orders:'); [print(dict(r)) for r in orders]
print('\nTIMED V2 by asset/status:')
rows=c.execute("""SELECT asset,status,COUNT(*) n,MAX(first_seen_at) latest FROM shadow_predictions
 WHERE settlement_kind='timed_direction' AND model_name LIKE '%TIMED_DIRECTION_V2%' AND first_seen_at>=?
 GROUP BY asset,status ORDER BY asset,status""",(since,)).fetchall()
[print(dict(r)) for r in rows] or print('none')
print('\nLatest FAST runs:')
for r in c.execute("SELECT id,started_at,finished_at,status FROM runs WHERE kind='timed_fast' ORDER BY id DESC LIMIT 5"): print(dict(r))
print('\nLatest main runs:')
for r in c.execute("SELECT id,kind,started_at,finished_at,status FROM runs ORDER BY id DESC LIMIT 5"): print(dict(r))
c.close()
'@ | .\.venv\Scripts\python.exe -
