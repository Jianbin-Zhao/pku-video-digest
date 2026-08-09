"""导入自检:确认 web 服务模块可加载、路由已注册。"""
import vspider.web.server as s

print("import ok, routes:", len(s.app.routes))
for r in s.app.routes:
    path = getattr(r, "path", "")
    if path.startswith("/api") or path == "/":
        print("  route:", path)
