"""按阶段统计历史运行的耗时，回答"到底慢在哪"。"""
import json
import sqlite3
import sys
from collections import defaultdict

db = sys.argv[1] if len(sys.argv) > 1 else r"D:\pku_exam_plus\data\vspider.db"
con = sqlite3.connect(db)

print("run_id | 档位 | 视频时长 | 各阶段耗时")
rows = con.execute(
    "select v.run_id, r.profile, v.duration_sec, v.timings, substr(v.title,1,14) "
    "from videos v join runs r on r.run_id=v.run_id "
    "where v.error='' or v.error is null order by r.created_at"
).fetchall()

stage_sum: dict[tuple[str, str], list[float]] = defaultdict(list)
for run_id, profile, dur, timings, title in rows:
    t = json.loads(timings or "{}")
    parts = " ".join(f"{k}={v:.1f}s" for k, v in t.items())
    print(f"  {run_id[:8]} | {profile} | 时长{dur}s | {parts} | {title}")
    for k, v in t.items():
        stage_sum[(profile, k)].append(v)

print("\n== 各阶段耗时汇总（按归纳档位分组）==")
for (profile, k), vals in sorted(stage_sum.items()):
    print(
        f"  [{profile}] {k:12s} 均值 {sum(vals)/len(vals):6.1f}s"
        f"   最大 {max(vals):6.1f}s   共{len(vals)}条"
    )
