"""检查 Web 路由。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vspider.web.server as s

print("import ok, routes:", len(s.app.routes))
for r in s.app.routes:
    path = getattr(r, "path", "")
    if path.startswith("/api") or path == "/":
        print("  route:", path)
