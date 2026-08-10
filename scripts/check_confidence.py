"""统计历史置信度。"""
import sqlite3
import sys
from pathlib import Path

db = (
    sys.argv[1]
    if len(sys.argv) > 1
    else Path(__file__).resolve().parent.parent / "data" / "vspider.db"
)
con = sqlite3.connect(db)

print("== 置信度分布（成功的视频）==")
for conf, n in con.execute(
    "select confidence, count(*) from videos "
    "where error='' or error is null group by confidence order by confidence"
):
    print(f"  {conf}: {n} 条")

print("\n== 逐条明细（转写字数 / OCR字数 / 置信度 / 标题）==")
for row in con.execute(
    "select transcript_chars, ocr_chars, confidence, substr(title,1,18) "
    "from videos where error='' or error is null order by confidence"
):
    print(f"  转写{row[0]:>5}字 OCR{row[1]:>5}字 置信{row[2]}  {row[3]}")
