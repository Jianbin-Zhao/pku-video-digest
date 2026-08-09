# vspider · 多平台短视频榜单发现 · 下载 · 内容归纳

支持 **B 站 / 抖音 / 快手 / 微博 / 小红书** 五个平台的视频搜索抓取，下载后对内容做
结构化文字归纳（一句话摘要、要点、话题、情感、推广识别）。覆盖题面两个验收场景：

1. **场景一**：今天排行榜前 N 的视频，下载并归纳。
2. **场景二**：指定用户今天发布的视频，下载并归纳。

以 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 为**只读库依赖**（不改其源码，
保留上游维护红利），在其之上自建榜单发现、语音识别、画面 OCR、多源融合归纳与任务编排。
设计理由与改进点见 [`docs/DESIGN.md`](docs/DESIGN.md)，全部实测数据见 [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md)，
逐日进度见 [`docs/PROGRESS.md`](docs/PROGRESS.md)。

---

## 架构

```
发现 ──► 下载 ──► 抽音频 ──► 语音识别(ASR) ─┐
                └► 关键帧 ──► 画面文字(OCR) ─┼─► 多源融合 ──► LLM 归纳 ──► SQLite 入库
                                             │
   平台元数据 / 高赞评论 / 互动数据 ─────────┘
```

- 采集层与理解层**解耦**：所有平台原始响应归一化成统一 `VideoItem`，后续阶段平台无关。
- 编排器按资源类型分别限流并发（下载 I/O、ffmpeg、ASR 独占、OCR 线程池、归纳 I/O），
  单条视频失败被隔离，不拖垮整批；同时对外发**事件流**，CLI 渲染成终端进度，
  Web 经 SSE 推给浏览器做实时可视化。

### 三档部署形态（`--profile`）

| profile | 归纳后端 | ASR / OCR | 适用 |
| --- | --- | --- | --- |
| `api` | 阿里云百炼（qwen-flash 等） | 本地 | 开发调试、演示、质量上限对照 |
| `gpu` | 服务器 vLLM 起 Qwen3-8B-AWQ | 本地（GPU/CPU 可选） | **完全本地、无外部依赖** |
| `cpu` | 本地 llama.cpp 起 Qwen2.5-3B-GGUF | 本地 CPU | **无显卡也能全本地跑** |

三者都是 OpenAI 兼容接口，业务代码零改动。归纳质量在三档间无实质差异（见 E1/E10/E11），
差别只在速度与成本。**"尽量本地部署"由 `gpu` / `cpu` 两档正面达成。**

---

## 安装

```bash
# 采集侧（本机，浏览器平台需要）
pip install -e ".[download]"
python -m playwright install chromium

# 理解侧（服务器）额外装
pip install -e ".[asr,ocr,serve]"
```

配置 `.env`（参考 `.env.example`）：`api` 档需 `DASHSCOPE_API_KEY`；
远程执行/同步需 `VSPIDER_SSH_*`。

---

## 最短可复现路径

### A. B 站（服务器可直连，无需浏览器）

```bash
# 场景一：B 站今日榜单前 5，本地 GPU 归纳
vspider rank --platform bili --limit 5 --profile gpu --device cpu

# 场景二：某 up 主今天发布的视频
vspider creator --platform bili --id <mid> --today --profile gpu --device cpu
```

### B. 抖音 / 快手 / 微博 / 小红书（混合部署：本机采集 + 服务器理解）

这些平台的接口带 JS 签名且要登录态，机房 IP 易触发风控，因此**采集下载在本机做**
（干净家庭 IP + 已登录），**内容理解在服务器算**。

```bash
# 1) 本机：登录（抖音需扫码，其余平台同理，登录态持久化在 .browser/）
python scripts/login_douyin.py            # 抖音专用（只认真实身份 cookie）
python scripts/login.py ks                # 其余平台通用

# 2) 本机：采集并下载（场景一用 --limit；场景二用 --creator [--today]）
python scripts/fetch_local.py wb --limit 3
python scripts/fetch_local.py wb --creator <uid> --today --out-dir data/handoff/scene2/wb
#   产物：data/handoff/<平台>/ 下若干 mp4 + items.json

# 3) 把该目录同步到服务器
python tools/remote.py put data/handoff/wb /root/autodl-tmp/data/handoff/wb

# 4) 服务器：接手理解（--profile 选 gpu / cpu / api；--device cpu 走纯 CPU）
python scripts/understand.py /root/autodl-tmp/data/handoff/wb --profile gpu --device cpu
```

### C. Web 界面（实时流水线 + 结果卡片墙 + 历史）

```bash
# 服务器：先起归纳后端（二选一），再起 Web
bash scripts/vllm_restart.sh     # gpu 档：vLLM + Qwen3-8B-AWQ（约 90s 就绪）
bash scripts/cpu_serve.sh        # cpu 档：llama.cpp + Qwen2.5-3B-GGUF
bash scripts/serve_web.sh 6006   # 起 FastAPI（0.0.0.0:6006）

# 本机用 SSH 隧道访问，浏览器打开 http://127.0.0.1:6006
ssh -p <port> -L 6006:127.0.0.1:6006 root@<host>
```

Web 支持三种模式：`rank`（B 站直连）、`creator`（B 站直连）、
`understand`（对已同步到服务器的 handoff 目录做理解，覆盖四个浏览器平台）。
运行结果自动入 SQLite，支持历史回看与**断点续跑**（`--resume` / 界面勾选，跳过已归纳的视频）。

---

## 五平台落地状态

| 平台 | 场景一 | 场景二 | 说明 |
| --- | --- | --- | --- |
| B 站 | ✅ | ✅ | 官方接口，服务器直连全链路 |
| 微博 | ✅ | ✅ | 混合部署 |
| 小红书 | ✅ | ✅ | 混合部署（详情接口流结构改版已适配） |
| 快手 | ✅ | ⚠️ | 依赖 `__NS_hxfalcon` 签名；出口 IP 被限流时（`result:2`）需冷却重试 |
| 抖音 | ✅ | ⚠️ | 搜索/榜单需登录态；作品列表接口有 Argus 强风控，非本项目逻辑问题 |

⚠️ 为平台侧反爬强度，非代码缺陷，文档如实标注（详见 E9/E11）。

---

## 目录

```
vspider/            主工程
  discovery/        五平台榜单/创作者发现（两级策略）
  download/         直链下载 + B 站 yt-dlp
  asr/ ocr/         SenseVoice / RapidOCR
  fusion/           多源信息融合
  summarize/        OpenAI 兼容归纳后端（api/gpu/cpu 通吃）
  pipeline/         编排器 + 事件流 + 终端渲染
  web/              FastAPI + SSE 前端
  storage.py        SQLite 入库 + 断点续跑
  registry.py       组件装配（平台×profile）
  cli.py            命令行入口（rank / creator）
scripts/            登录、本机采集、服务器理解、起服务、各类探针
tools/remote.py     SSH 执行 / 文件同步
MediaCrawler/       只读库依赖（不改）
docs/               DESIGN / EXPERIMENTS / PROGRESS
```

## 常用脚本

| 脚本 | 作用 |
| --- | --- |
| `scripts/login.py` / `login_douyin.py` | 本机登录，持久化会话到 `.browser/` |
| `scripts/fetch_local.py` | 本机采集下载（场景一 `--limit`；场景二 `--creator [--today] --out-dir`） |
| `scripts/understand.py` | 服务器对 handoff 目录做理解 |
| `scripts/vllm_restart.sh` | 起/重启 vLLM（gpu 档） |
| `scripts/cpu_setup.sh` / `cpu_serve.sh` | 装/起 llama.cpp（cpu 档） |
| `scripts/serve_web.sh` | 起 Web 界面 |
| `tools/remote.py` | `run` 远程执行 / `put`·`get` 文件同步 |
