"""Web 界面：把同一套流水线事件流通过 SSE 推给浏览器做实时可视化。

CLI 和 Web 共用 orchestrator 与 registry，唯一区别是事件的消费方式：
CLI 渲染成终端进度，Web 经 SSE 推给前端画流水线时间线 + 结果卡片墙。
"""
