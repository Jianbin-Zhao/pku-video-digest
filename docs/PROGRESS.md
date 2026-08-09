# 进度日志

按时间顺序追加，只记录已验证的事实和已完成的产出。
截止时间 2026-08-14，实际按 2026-08-07 至 08-08 两天推进。

---

## 2026-08-07（周五）

### 16:50 环境勘察

摸清本机与目录状况：Git 2.53、Python 3.13.12、Node 24 就位；
无 uv、无 ffmpeg、无 NVIDIA GPU（Intel Iris Xe 核显）；C 盘仅剩 24.4 GB。
结论是本机不适合放数据。

### 17:00 迁移到 D 盘

D 盘有 435 GB 可用。实际数据落在 `D:\pku_exam`，
桌面 `pku_exam` 改为指向它的目录联接（Junction），路径习惯不变。
同时把 HuggingFace、ModelScope、Playwright、pip、torch 的缓存目录
全部通过用户级环境变量重定向到 `D:\pku_exam\.cache`，避免模型往 C 盘塞。

### 17:05 克隆 MediaCrawler

GitHub 直连被连接重置。探测到本机 7890 端口有代理在跑，
用单条命令临时指定代理完成克隆（未改全局 git 配置）。
产出：`D:\pku_exam\MediaCrawler`，327 个文件。

### 17:10 通读上游代码，确认能力边界

- 已有：7 平台 search / detail / creator、视频下载实现、CDP 反检测模式、
  多种存储后端（csv / json / jsonl / sqlite / mysql / postgres / excel）
- 缺失：**无榜单能力**（`CRAWLER_TYPE` 只有三种）、除 B 站外无时间过滤、
  无 ASR、无 LLM 归纳、无编排调度；视频下载是整文件读入内存再落盘
- License：`NON-COMMERCIAL LEARNING LICENSE 1.1`，学术作业适用

由此确定：MediaCrawler 只读引用，另起独立工程 `vspider`。

### 17:25 技术调研（关键结论）

- ASR：SenseVoice-Small 在 CPU 上 17.2× 实时、中文 CER 7.81%，
  优于 Whisper-large-v3 在 H100 上的 13.4× / 20.02%。定为主力。
- 下载：yt-dlp 对 B 站维护良好；**抖音、快手内置 extractor 自 2025 年起失效**，
  社区改走分享页 SSR 数据解析。因此下载层必须分后端。
- 成本：Qwen-Flash ¥0.2/百万输入 token，两周开发期跑 2000 条视频约 ¥1.6，成本可忽略。
- 租卡：AutoDL RTX 4090 约 ¥1.88/小时，学生认证可免费拿会员折扣。

### 17:35 本地 Python 环境

pip 直连与清华镜像均 SSL EOF 握手中断，改走 7890 代理后正常。
装 uv 0.12.2 → 拉 CPython 3.11.15 → 建 `D:\pku_exam\.venv` →
装完 MediaCrawler 全部依赖。

### 17:45 服务器接入

用户租到 RTX 3080 Ti 12 GB（非预期的 24 GB，模型选型据此下调）。
写 `tools/remote.py`（paramiko）作为远程执行与文件同步通道，凭据只走环境变量不落盘。
实测服务器直连 B 站 API 仅 0.22 秒、直连 GitHub 0.9 秒，网络优于本机。

### 18:00 服务器环境搭建

发现并修正两个问题：一是 AutoDL 的 `network_turbo` 把国内流量绕去境外出口，
导致 ModelScope 下载从数十 MB/s 掉到不足 1 MB/s；二是镜像里 pip 已预置
`mirrors.aliyun.com`，本就无需加速。最终决定全程直连。

产出：ffmpeg 4.4.2 就位；funasr / modelscope / rapidocr / yt-dlp / fastapi 等依赖装完；
SenseVoiceSmall 与 FSMN-VAD 开始下载（受同地区共享带宽限制，约 30 分钟）。
因归纳走 API，取消 vLLM 与 Qwen3-8B-AWQ 下载，省约 14 GB。

### 18:10 第一个可运行闭环

写完数据模型、B 站榜单发现、多源融合、归纳后端，跑通冒烟测试：

- B 站全站排行榜前 5 全部拉到，标题 / 播放量 / 点赞 / 发布时间 / 时长均正确
- qwen-flash 归纳单条 **1.41 秒**，结构化 JSON 解析正常
- 自评置信度 0.55，如实反映了"只有元数据、无转写"的输入状态

### 18:20 实验 E1：模型档位对比

结论是**换更大的模型几乎没有收益，补齐输入信息才是质变**。
完整数据见 `docs/EXPERIMENTS.md` E1。据此确定 `qwen-flash` 为默认归纳后端，
工程投入转向"把输入喂饱"。

### 18:30 代码产出盘点

```
vspider/models.py              数据模型（VideoItem / Transcript / OcrResult / Summary）
vspider/settings.py            .env 加载
vspider/discovery/base.py      RankingProvider 抽象 + 两级策略定义
vspider/discovery/bilibili.py  B 站官方排行榜 + 热门接口（已验证）
vspider/download/base.py       下载层抽象 + VIDEO / AUDIO_ONLY 两种模式
vspider/download/ytdlp_backend.py  yt-dlp 后端（B 站 / 微博 / 小红书）
vspider/media/audio.py         ffmpeg 抽音频 / 探时长 / 判断有无音轨
vspider/asr/base.py            ASR 抽象
vspider/asr/sensevoice.py      SenseVoice + VAD 分段（带时间戳）
vspider/fusion/context.py      五路信号融合 + 分预算 + 中间截断
vspider/summarize/prompts.py   结构化输出提示词 + 置信度校准标准
vspider/summarize/openai_compat.py  OpenAI 兼容后端（覆盖 API / vLLM / llama.cpp）
tools/remote.py                服务器远程执行与文件同步
scripts/server_setup.sh        服务器环境一次性搭建
scripts/smoke_api.py           榜单 + 归纳冒烟测试
scripts/compare_models.py      模型档位对照实验
scripts/probe_models.py        账号可用模型探测
```

### 18:37 定位并解决模型下载瓶颈（30 倍提速）

**问题**：SenseVoice 的 936 MB 权重通过 modelscope 单连接下载只有 410~750 kB/s，
且在 97% 处断连后不能续传、直接从头重下，两次尝试共耗时 35 分钟仍未完成。

**排查**：先排除了 AutoDL `network_turbo` 的干扰（关掉后仍慢），
判断为单连接被限速。用 aria2c 开 16 连接实测 **12 MiB/s**。

**改动**：新增 `scripts/fetch_models.sh`，大文件一律走 aria2c 16 连接并校验字节数，
小文件（配置、词表）仍交给 modelscope。

| 方式 | 速度 | 936 MB 实际耗时 |
| --- | --- | --- |
| modelscope 单连接 | 410~750 kB/s | 30 分钟以上且失败重来 |
| **aria2c 16 连接** | **12 MiB/s** | **75 秒** |

### 18:45 补齐 funasr 运行时依赖

`funasr/utils/load_utils.py` 顶层就 `import torchaudio`，绕不过去。
装的时候必须加 `--no-deps`，否则 pip 会按 torchaudio 的依赖声明把镜像自带的
`torch 2.8.0+cu128` 换成 PyPI 上的其他 CUDA 构建，白下 800 MB 还可能弄坏 CUDA。
新增 `scripts/fix_deps.sh`。最终 `torchaudio 2.8.0+cu128` 与 torch 完全匹配。

### 18:50 首次端到端跑通（E2E_OK）

`scripts/e2e_test.py` 六个阶段全通。详细数据见 `docs/EXPERIMENTS.md` 的 E3-a 与 E4。

**最重要的收获**：随机选中的样本《蒙德：高清重置》恰好是一条**完全没有人声**的视频，
ASR 只输出 3 个字（全是音乐符号 `🎼`），而 OCR 从 24 帧里抽出 663 字有效地名，
最终摘要准确、置信度 0.85。这条样本单独就证明了「只做 ASR 的方案会失效」，
为改进 2（多源融合）提供了真实证据。

### 18:58 OCR 并行化（3.35 倍提速）

**问题**：首版 OCR 串行执行 24 帧耗时 21.42 秒，占整条流水线 49.3%，是最大瓶颈。

**改动**：`RapidOcr` 改用线程池，worker 数取 `min(6, CPU核数/2)`，
每个 worker 持有独立引擎实例（共享单实例在并发下不可靠，
其内部 det/cls/rec 三个 session 之间有可变中间状态），
并把每个 onnxruntime session 的 `intra_op_num_threads` 限到 2，
避免多个 worker 各自开满线程互相抢核。

| 指标 | 优化前 | 优化后 |
| --- | --- | --- |
| OCR 耗时 | 21.42s | **6.39s（3.35×）** |
| 端到端合计 | 43.49s | **27.92s（缩短 36%）** |

### 19:00 关键帧时间戳修正

首版在场景切变模式下拿不到每帧时间戳（只有均匀采样模式有），
会让界面「点转写文稿跳转到视频位置」这个功能失效。
改为把 ffmpeg 日志级别提到 `info` 并解析 `showinfo` 滤镜输出的 `pts_time`，
单次执行即可同时拿到图片和时间轴。

### 19:05 流水线事件流定义

新增 `vspider/pipeline/events.py`。编排器只负责发事件，不关心谁在听：
CLI 把事件渲染成终端进度，Web 后端把同一批事件通过 SSE 推给前端做实时可视化。
两边共用一套事件定义，避免界面和命令行看到的进度对不上。

### 19:10 流水线编排器

新增 `vspider/pipeline/orchestrator.py`，两个入口 `run_ranking` / `run_creator`
直接对应题面的两个验收场景。

并发策略不是一刀切，而是按资源类型分别限流，因为各阶段的瓶颈完全不同：
下载是网络 I/O（并发 3）、抽音频是 ffmpeg（并发 3）、
语音识别是 GPU 且 funasr 的 generate 不可并发调用（全局串行）、
OCR 内部已有线程池（视频级串行，避免线程数翻倍抢核）、归纳是 HTTP（并发 4）。

每条视频作为独立任务流过全部阶段，单条失败被隔离——榜单里总会有因地区限制或
临时下架而拿不到的，不能让一条拖垮整批。

### 19:12 组件装配与命令行

- `vspider/registry.py`：按平台选采集器/下载器，按部署形态（api / gpu / cpu）选推理后端。
  CLI 和之后的 Web 后端共用，避免两边配置漂移。
- `vspider/cli.py`：`vspider rank` 和 `vspider creator` 两条命令。
- `vspider/pipeline/console.py`：事件渲染。并发下事件是交错到达的，
  所以每行都带视频短序号，否则日志读不出是哪条在动。
- `tools/remote.py` 改为自己读 `.env`，省掉每次手动导环境变量。

### 19:19 场景一完整跑通（E5）

`vspider rank --platform bili --limit 5 --category tech -j 3`

| 指标 | 结果 |
| --- | --- |
| 成功率 | **5/5（100%）** |
| 墙钟耗时 | **78.3s** |
| 各阶段耗时之和 | 266.5s（并发加速 **3.4×**） |
| 处理的视频内容 | 2906 秒 = **48.4 分钟** |
| 端到端速度 | **37× 实时** |
| 置信度 | 0.85 / 0.92 ×4 |

编排器复用 ASR 实例的收益兑现：单条转写实测 57.9~96.7× 实时，
一条 1190 秒的视频出 7507 字只用 26.67 秒。完整分析见 `docs/EXPERIMENTS.md` E5。

### 19:24 两处体验修复

- funasr 每次 generate 都打一条 tqdm 进度条，多条视频并发时互相覆盖，
  把流水线自己的日志彻底冲掉。给 `AutoModel` 和每次 `generate` 都加上 `disable_pbar`。
- 时长过滤把榜单第 1 名（超过 30 分钟）默默丢掉了，结果从第 2 名开始。
  改为把淘汰项单独返回并发一条日志说明原因，而不是无声消失。

### 19:30 B 站 WBI 签名（场景二的前置）

创作者投稿接口 `/x/space/wbi/arc/search` 需要 WBI 签名，
之前直接抛的 `NotImplementedError` 让场景二完全跑不了。

新增 `vspider/discovery/wbi.py` 自己实现：从 `/x/web-interface/nav` 取两张图片
URL，文件名即 img_key 与 sub_key，拼接后按一张固定的 64 位表重排取前 32 位得到
mixin_key，再对排序后的 query 加时间戳做 md5。

**没有引 MediaCrawler 来做这件事**：这段逻辑只有几十行且无状态，
为它引入一个需要启动真实浏览器的依赖不划算。那个依赖留给小红书、
抖音这些真正需要浏览器环境的平台。

### 19:35 风控排查：一个被推翻的实验（E6）

签名做对之后仍然 -352。第一反应是逐项加手段做组合矩阵，
每个组合试一次，结果找到「buvid + bili_ticket」这一组能成功。

**但第二轮复跑，结果几乎完全相反**——同一个「裸请求」第一轮失败第二轮却成功。
这说明第一版实验设计本身是错的：它默认结果由参数组合决定，
而两轮的矛盾暴露出存在未被控制的变量。

改成测成功率（每配置 6 次、间隔 3 秒、配置间冷却 25 秒）后结论清楚了：

- 主导变量是**按 IP 的请求频率**，不是指纹参数。没有任何配置能稳定通过，
  成功率在 0/6 到 3/6 之间波动。
- `dm_img_*` 那组 WebGL 指纹参数**不但不必需，加上反而更容易触发 412**。
  这与网上大量教程的说法相反，已从实现中移除。
- 服务器（宁夏联通）与本机（甘肃电信）表现一致，**不是 IP 归属地问题**。

据此实现退避重试：最多 5 次、从 2 秒起指数退避，每次重试换一份新的匿名身份。
触发风控后同一个 buvid 会被短暂拉黑，沿用它重试只是白等。
同时支持 `.env` 里的 `BILI_COOKIE`，带登录态时跳过身份轮换。

这一条记下来主要是记那个方法论错误：**当两次实验结果矛盾时，
要怀疑的是实验设计遗漏了变量，而不是去挑一个「看起来能用」的组合。**

### 19:50 场景二跑通（E7）

`vspider creator --platform bili --id 25876945 --limit 2`

成功 1/1（第 1 条时长超上限被跳过），墙钟 78.6 秒，置信度 0.92，
语音 4303 字 + 画面文字 4010 字。其中约 17 秒花在风控退避上。

**新瓶颈浮现**：OCR 并行化之后，关键帧抽取升为最大单项（23.37s，37.8%）。
原因是 scene 滤镜要把整条视频完整解码一遍，开销随时长线性增长。
下一步：先按固定间隔粗采样，只在候选区间内做切变检测。

至此题面要求的两个场景在 B 站上都已完整跑通。

### 20:10 抽帧策略优化（10.5 倍）与一次自纠（E8）

E7 暴露出关键帧抽取是新瓶颈（23.37s，占单条 37.8%）。新增
`scripts/bench_keyframe.py` 对比三种策略，**同时测耗时和 OCR 字数**——
只比速度会选错，一个快但漏字的策略是负收益。

**第一轮发现自己写的 iframe 实现是坏的**：它比要优化掉的 scene 还慢，
且字数与 interval 一模一样（都是 2857）。这个巧合说明它压根没抽到帧、
静默退化到了兜底路径。根因是 `-skip_frame nokey` 对 AV1 无效——
libdav1d 不理会这个参数，把整条视频 18948 帧全解码了一遍。
改成读**包**的 flags（只解复用不解码）后：

| 策略 | 抽帧 | 有效字数 |
| --- | --- | --- |
| **iframe（修复后）** | **2.22s** | **4378** |
| scene | 22.97s | 4010 |
| interval | 1.77s | 2857 |

iframe 在两个维度上同时胜出，比 scene 快 10.3 倍且字数还多 9%。
默认策略改为 iframe，关键帧抽取 23.37s → 2.22s。

**中途还改了下载格式优先 H.264，随后被数据推翻并撤回**。
理由本来是 AV1 软解慢（整片解码 22.97s vs 5.95s），单条 A/B 测下载耗时
几乎不变（16.68s vs 16.94s），看着是白拿。但放回完整流水线跑五条就现原形了：

| 阶段 | E5 基线（AV1） | 改 H.264 | 撤回（AV1） |
| --- | --- | --- | --- |
| 视频下载 | 49.19s | **80.07s** | 70.70s |
| 关键帧抽取 | 41.95s | 7.81s | **7.77s** |
| 墙钟 | 78.3s | 75.4s | **68.2s** |

三路并发下载共享带宽时，多出的 61% 体积是要还的。而「关键帧抽取」两栏
几乎一样（7.81 vs 7.77），直接证明 iframe 只解复用不解码、根本不吃编码格式，
H.264 的解码优势在主路径上兑现不了。已撤回。

教训：**单条 A/B 会漏掉资源争抢，优化必须在真实并发下复测。**

### 20:41 多样本复验，推翻了自己的质量结论

前面所有轮次都只用了一条视频，这不足以支撑「改默认策略」这种决定。
新增 `scripts/fetch_corpus.py` 跨六个分区（科技/音乐/鬼畜/生活/游戏/美食，
刻意选镜头节奏差异最大的）建了 12 条语料库，随机抽 8 条复跑：

| 策略 | 抽帧均值 | 字数均值 | 单样本夺冠 |
| --- | --- | --- | --- |
| **iframe** | **0.83s** | 982 | 2/8 |
| scene | 5.74s | 773 | 4/8 |
| interval | 1.36s | 947 | 2/8 |

- **速度优势稳健**：iframe 比 scene 快 6.9 倍，8 条样本无一例外。
- **「iframe 字数更多」不成立**：三者字数均值落在噪声范围内，
  按单样本夺冠次数算 scene 反而领先。之前那个「多 9% / 多 53%」
  纯粹是样本特性，换一批就消失了。

**选型结果不变（仍用 iframe），但理由从「又快又准」修正为「快得多，且质量无显著差异」。**
E8 已按统计口径重写。

这条教训最贵：结论碰巧对了，但方法是错的——如果当初 scene 在那条样本上
恰好胜出，就会选错。**幸存的正确结论不等于可靠的方法。**
后续所有涉及质量的对比一律走多样本。

### 22:00-23:10 复用 MediaCrawler 接入其余四平台（不手写签名）

思路定调：老师给的参考实现 MediaCrawler 已经把四个平台的反爬全解决了，
所以把它当**依赖库**用，只复用它唯一值得复用的资产——签名对抗
（抖音 a_bogus、小红书 x-s、快手 __NS_hxfalcon、微博移动端会话）。
它没有的能力（榜单、内容理解、统一模型）由本项目补。分工清晰。

**适配层**（`vspider/mediacrawler/`）：
- `bootstrap.py` 把 MediaCrawler 挂到 import 路径，并处理它的相对路径依赖。
  踩到三个坑：抖音 help.py 在 import 时就 `open('libs/douyin.js')`、
  快手 GraphQL 写死相对目录、多处 init 脚本按相对路径读。
  解法是预导入时临时 chdir 到仓库根 + 给快手 GraphQL 打绝对路径补丁。
- `session.py` 共享一个浏览器、按平台懒加载页面。签名必须有活的页面
  （msToken 从 localStorage 取、a_bogus 在页面里跑 JS），所以绕不开浏览器。
- 关键教训（判据踩坑两次）：登录态**不能靠间接信号推断**。
  先用 cookie 判 → 小红书 web_session 匿名也有，误判成已登录；
  改用官方 pong → IP 被风控时对着已登录账号返回 False，误判成未登录。
  最终结论写进 `verify.py`：**唯一可靠判据是真去取一次数据**。

**四平台榜单发现**（都没有官方视频榜，策略如实标注 RankSource）：
- 抖音：热榜只给热词不给视频，走热词搜索重排。
- 快手：必须用 V2 签名接口 `/rest/v/search/feed`，旧 GraphQL 已废弃
  （未签名返回 result:50）。签名依赖页面注入的捕获脚本。
- 微博：视频频道 containerid 直接给视频流，最接近官方榜。
- 小红书：无任何榜单接口，纯热词重排；搜索结果不含直链，下载前需补拉详情。

**下载**：B 站走 yt-dlp（DASH 分片合流成熟），其余四家走直链
（`download/direct.py`，采集阶段已拿到地址，复用浏览器 cookie 过 Referer 校验）。

### 23:00 修「卡」的根因：热词重排缺熔断

现象：反复验证时单个平台能跑到 200 多秒，抖音还老返回 0 条。
根因不是频控本身，是**我的循环在失败时会把 6 个种子词全打一遍**——
成功时第一个词凑够就跳出，失败时计数永远到不了阈值于是硬顶到底，
被限流还继续加压，快手叠上签名请求的 3 次指数退避直接爆炸。

抽出 `discovery/keyword_rerank.py`，核心是熔断器：连续被拒 2 次立刻收手。
抖音的 2483、快手的 result≠1、小红书的 success=false 都翻译成
`KeywordRejected` 触发熔断。改完抖音从 200s+ 降到 23s 就停。

### 23:10 五平台可用性实测（判据：真取到数据）

`scripts/verify_all.py` 一次性验证，不再反复探测（反复探测正是把自己
刷进频控的原因）：

| 平台 | 发现 | 下载 | 说明 |
| --- | --- | --- | --- |
| B 站 | 可用 | 可用 | 官方排行榜 |
| 快手 | 可用 | 可用 | V2 签名，13MB/5s |
| 微博 | 可用 | 可用 | 视频频道 |
| 小红书 | 可用 | 部分 | 发现可用；下载需补拉详情取直链 |
| 抖音 | 受限 | — | 登录/签名/token 都正常，但搜索接口对自动化请求返回 2483，是平台风控 |

### 23:10 场景一端到端首次跑通（服务器，全链路）

`vspider rank --platform bili --limit 2 --profile api`，服务器 3080 Ti：
下载→语音转写→关键帧 OCR→归纳全通，2/2 成功，29.7s。
归纳质量扎实：每条给出一句话概括、5 条要点、话题标签、情感、置信度 0.85，
并标注依据（语音 N 字 + 画面文字 N 字）。结果落 JSON。
这是把今天所有组件串起来的第一个完整交付。

### 23:30 混合部署跑通：浏览器平台的端到端（快手）

浏览器平台的采集必须在本机（干净家庭 IP + 登录态），理解要在 GPU 服务器，
所以补了跨机衔接：
- 编排器加 `run_prefetched`：跳过发现/下载，对已下载文件从 ASR 阶段接手。
- `scripts/fetch_local.py`（本机）：采集+下载，导出 mp4 + items.json。
- `scripts/understand.py`（服务器）：读清单重建 VideoItem，跑理解。

实测快手：本机下载 → 传服务器 → 理解，2/2 成功，38.8s。
质量：第一条准确列出盘点的四首歌名；第二条「快手粉条」广告被
**正确判定为推广**（is_promotion）并提炼出卖点，置信度 0.92。

至此两条路线都验证过：B 站服务器直连全程一次跑通；
浏览器平台走「本机采集 + 服务器理解」。内容理解阶段平台无关，
微博/小红书与快手同路径。

### 23:30 修下载卡死：细粒度超时

fetch_local 首跑耗时 15 分钟，根因是快手一条视频连接被对端掐断，
而下载器用 120s 整体超时 × 3 次重试，一条卡住的连接吃掉好几分钟。
改成 `httpx.Timeout(60, connect=10, read=20)`：卡住的读取 20s 就失败去重试，
不再干等。

---

## 2026-08-08（周六）

### 21:30 重建先验、恢复服务器连接

上一轮聊天记录丢失，从三份文档（DESIGN / EXPERIMENTS / PROGRESS）与现有代码重建
上下文。服务器换了新实例，更新 `.env` 里的 SSH 端口与密码。核对服务器环境仍完好：
RTX 3080 Ti（驱动 580 / CUDA 13）、torch 2.8.0+cu128、funasr / rapidocr / yt-dlp
就位，SenseVoiceSmall 与 fsmn-vad 两个模型都在。`tools/remote.py` 补两处：
跳过 `.browser` 目录不上传、Windows 控制台强制 UTF-8 输出（远端中文进度条之前会
UnicodeEncodeError 打断）。

### 21:34 B 站两场景回归（无退化）

同步最新代码后在服务器复跑，确认此前结论仍成立：
- `rank --platform bili --limit 2`：2/2，墙钟 37.8s，置信度 0.92。
- `creator --id 25876945 --limit 2`：1/1（第 1 条超时长上限跳过），墙钟 85.7s，
  置信度 0.92，语音 4303 字 + 画面文字 4378 字。

### 21:40 修 Playwright 浏览器缺失（本机）

本机跑浏览器平台采集时报 `Executable doesn't exist ...chrome-headless-shell.exe`。
根因：`PLAYWRIGHT_BROWSERS_PATH` 用户级环境变量指到 `D:\pku_exam\.cache\ms-playwright`，
但浏览器实际被装到了默认的 `%LOCALAPPDATA%\ms-playwright`（装的时候环境变量还没设）。
用 robocopy 把 611 个文件 / 701 MB 搬到 D 盘缓存目录，路径对齐后即恢复。

### 21:50 修小红书下载：详情接口流结构变了（E9）

场景痛点复现：小红书搜索能发现视频笔记，但下载全部失败——`extract_video_url`
返回空。写 `scripts/probe_xhs_detail.py` 把 `/api/sns/web/v1/feed` 的原始 JSON
落盘，定位到 2026-08 的改版：详情不再返回 `consumer.origin_video_key`，
`media.stream` 的分键也从编码名（`h264`/`h265`）换成了档位名
（`EF4`=X264、`EF5`=X265，`EF6`/`EF7` 常为空）。旧实现只认 `h264` 一个键，
自然取不到。改为遍历全部档位、按 H.264 优先 + `default_stream` 优先选流，
`master_url` 缺失时退回 `backup_urls`。改完小红书发现+下载 3/3。详见 EXPERIMENTS E9。

### 22:00 四平台混合部署端到端全部跑通

浏览器平台走「本机采集下载 → 传服务器 → `understand.py` 理解」。三平台各取榜单
前 3 实测（服务器归纳走 api 档 qwen-flash）：

| 平台 | 结果 | 墙钟 | 备注 |
| --- | --- | --- | --- |
| 快手 | **3/3** | 30.9s | 含一条被正确判为运营干货的短视频 |
| 微博 | **3/3** | 27.1s | 视频频道直给视频流 |
| 小红书 | **3/3** | 32.9s | 直链修复后首次全通；一条拼豆测评被判 is_promotion |

加上 B 站服务器直连，题面五平台里 **4/5 端到端可用**。

### 22:10 抖音：确认匿名态已被平台彻底封死

写 `scripts/probe_dy_hotlist.py` 逐一验证三条匿名路径，全部走死：
- 一级策略依赖的热榜 `detail_list=1`：`word_list` 有 49~51 条，但**每条的
  `aweme_infos` 全为 0**——平台已不再随热榜下发代表作品，一级策略永远产出空。
- 视频榜子接口 `/hot/search/video/list/`：直接 `account blocked`。
- 二级策略的搜索：`status_code=2483 请先登录`（pong=True 但 sessionid 缺失，
  是匿名会话）。

结论：抖音不是代码问题，是**平台对匿名/自动化流量的强制登录墙**。唯一出路是
真实登录态（`scripts/login.py dy` 扫码，或 `.env` 配 `DY_COOKIE`）。这一步需要
用户扫码/贴 cookie，已作为交付前的待办上报。

### 22:20 起步本地 GPU 归纳（vLLM + Qwen3-8B-AWQ）

按「先 GPU 后 CPU」推进本地部署答卷。归纳当前走 api（qwen-flash），要正面回应
「尽量本地部署」需把归纳也搬到本地。新增 `scripts/vllm_setup.sh`：单独建 conda
环境 `vllm`（隔离，避免 vLLM 钉的 torch 覆盖 funasr 依赖的 2.8.0+cu128），
装 `vllm>=0.8.5`，从 ModelScope 下 `Qwen/Qwen3-8B-AWQ`（约 5.5 GB）。
归纳后端相应改造：关思考模式的传参 DashScope 走顶层 `enable_thinking`、
vLLM 走 `chat_template_kwargs`，按 profile 自动切换（api/gpu/cpu）。

### 23:00 本地 GPU 归纳跑通（大阶段完成）

vLLM 0.26 起在独立 venv 里，Qwen3-8B-AWQ 加载完成。踩了三个坑并解决：
conda 建环境卡死改用 venv；`pkill -f api_server` 因命令行自含该字面量把自己杀掉，
改用 `api[_]server` 括号写法；EngineCore 子进程被 setproctitle 改名、旧写法杀不掉
会一直占显存，改按 venv 路径匹配（`scripts/vllm_restart.sh` 固化这套清理）。
显存取舍见 EXPERIMENTS E10：12 GB 卡放不下 LLM+ASR 同卡，定为 LLM 独占 GPU
（util 0.85、16K 上下文）、ASR 让给 CPU。

两处实测（归纳全部由本地 Qwen3-8B 产出，零外部 API）：
- 快手 3 条（`understand.py --profile gpu --device cpu`）：3/3，墙钟 79.6s，
  内容归纳累计仅 3.81s。
- B 站榜单 2 条（`rank --profile gpu --device cpu`，服务器全链路）：2/2，
  两条 7~11 分钟长视频，归纳准确，置信度 0.70/0.80。瓶颈是 CPU 上的 ASR
  （342.84s，92.4%）。

质量对照证实 E2 假设：本地 8B 与云 qwen-flash 的摘要在信息量/准确度上无实质差异，
仅置信度自评偏保守（0.6~0.8 vs 0.85）。**「尽量本地部署」已正面达成。**

### 23:45 抖音登录脚本（可靠版）

通用 `scripts/login.py` 对抖音失效：它靠 pong 判登录，而抖音匿名态 pong 也返回 True，
会误判「已登录」直接退出、不给扫码机会。新增 `scripts/login_douyin.py`，
只认真正的身份 cookie（sessionid / sessionid_ss / sid_tt，匿名会话不会有），
轮询到即视为成功、再多等 6 秒收齐风控 cookie 后落盘。

### 23:50 抖音接通，五平台全部端到端跑通（5/5）

用户扫码登录成功（命中 sessionid 等身份 cookie，落盘 62 项到 `.browser`）。
登录态一到位，之前的 2483 登录墙立刻解除：
- 采集下载：`fetch_local.py dy --limit 3` → 3/3（9.4 / 52.9 / 74.2 MB）。
- 本地归纳：`understand.py --profile gpu --device cpu` → 3/3，本地 Qwen3-8B
  把「台风白海豚」系列报道准确归纳（突破 24 小时警戒线、维持 14 级等），
  置信度 0.70~0.80。

**至此题面五平台 B 站 / 快手 / 微博 / 小红书 / 抖音全部端到端跑通（5/5）**，
两个验收场景在 B 站上完整验证、内容理解阶段平台无关。抖音的前提是本机
`.browser` 里保有登录态（扫码得来，长期复用）。

## 2026-08-09（CPU 纯本地档 + 场景二多平台）

### 00:20 CPU 纯本地档跑通（无显卡路径成立）

新增 `scripts/cpu_setup.sh` / `cpu_serve.sh`：独立 venv 装 llama-cpp-python[server]，
起 Qwen2.5-3B-Instruct GGUF(Q4_K_M, 2.0 GB)，OpenAI 兼容服务监听 127.0.0.1:8080，
与 registry 的 cpu 档约定对齐（模型别名 `local`）。踩两个坑：预编译 CPU wheel 挂在
github.io、境内拉不动（pip 卡死 8 分钟），改走阿里云镜像源码现场编译（gcc 11.4，
几分钟成）；选 Qwen2.5-Instruct 而非 Qwen3 是为了省掉关思考的麻烦（非思考模型，
thinking_via=none 直接干净出 JSON）。冒烟：JSON 模式可用，5.7 tok/s。

关键结论：**这条路全程不碰 GPU**——ASR 走 CPU、OCR 本就 CPU、归纳走 llama.cpp CPU。
实测见 EXPERIMENTS E11。老师「无显卡也能本地部署」的要求正面达成。

### 00:40 场景二在微博 / 小红书跑通（各 2/2，走 CPU 档）

`fetch_local.py` 加 `--out-dir`，让场景二产物与场景一分目录，互不覆盖。
两处均「本机采集下载 → 传服务器 → understand.py --profile cpu」全 CPU 跑通：
- 微博（光明日报 uid=1402977920）：2/2。火把节、侯明昊无伴奏两条摘要准确，
  第二条正确判为推广。
- 小红书（明明爱养狗）：2/2。养狗品种、缺维生素补钙两条准确，补钙那条正确判为推广。

加上此前 B 站场景二（E7）已验证，**场景二在 3/5 平台实测通过**。

### 00:55 场景二在快手 / 抖音受平台风控限制（如实记录）

- 快手：今晚 landing 页直接返回 `{"result":2}`（IP 级限流），JS 不完整加载 →
  签名环境 `window.__ks_realm` 注入不出来 → 作品列表/搜索全超时。白天场景一同一路径
  是好的（E9），说明**代码无误，是出口 IP 被临时限流**，需较长冷却。诊断脚本
  `scripts/probe_ks_page.py`。
- 抖音：作品列表接口 `/aweme/post/` 返回 `Blocked by ArgusSecurityPlugin Validate
  Error`。这是比场景一搜索更硬的风控：除 a_bogus 外还要 X-Argus/X-Gorgon 头签名，
  MediaCrawler 现有签名不覆盖。抖音场景一（搜索）此前 3/3 正常。

结论：场景二在无强风控的平台（B 站/微博/小红书）稳定；快手/抖音属平台侧
反爬强度问题，非本项目逻辑缺陷，文档如实标注。

### 01:15 Web 界面 + SQLite 入库 + 断点续跑（一次做完）

新增 `vspider/web/`（FastAPI + 原生前端单页）与 `vspider/storage.py`（标准库 sqlite3）：
- **实时可视化**：编排器的事件流经 SSE（`text/event-stream`）推给前端，
  每条视频一张卡片，各阶段 chip 随 stage_start/done 亮起；run 维护事件重放缓冲，
  中途打开页面也能补齐历史。三种模式：rank / creator（B 站直连）、
  understand（对 handoff 目录做理解，覆盖四个浏览器平台）。浏览器平台的直连 rank/creator
  在服务器上会被 400 拦下并指路到混合流程。
- **落库**：每个 run 结束写 `runs` + `videos` 两表（data/vspider.db），CLI 与 Web 共用同一份，
  历史可回看。用标准库 sqlite3 而非 sqlalchemy/aiosqlite，少一个安装依赖。
- **断点续跑**：`PipelineConfig.skip_uids` 由 `storage.processed_uids()` 填入，
  编排器在 `_process` / `run_prefetched` 里滤掉已成功归纳的 uid 并发 LOG。
  CLI 加 `--resume`，Web 请求体加 `resume`。

自测（`scripts/probe_web_run.py`，全在服务器 localhost）：
- understand + cpu 档：POST 启动 → SSE 收到 run_start / video_start×2 / stage×20 /
  video_done×2 / run_done / end，2/2 成功 85s，落库 1 条。
- 加 `resume`：两条 wb 视频命中已归纳集合，发 2 条跳过 LOG，处理 0/0、0.0s。
Web 首页 `curl` 200，路由 6 个 API 全注册。**Web / SQLite / 续跑三项全部实测通过。**

---

## 待办（按优先级）

已完成：

- [x] 关键帧抽取 + RapidOCR，打通「无人声视频」路径
- [x] 流水线编排器：分资源限流的并发、单条失败隔离、事件流
- [x] 场景 1「今日榜单前 N」（B 站，E5 实测 5/5）
- [x] 场景 2「指定用户今日发布视频」（B 站，E7 实测通过）
- [x] 复用 MediaCrawler 接入抖音/快手/微博/小红书（发现层，不手写签名）
- [x] 直链下载后端（四平台）
- [x] 热词重排熔断器，修掉「卡」的根因
- [x] 五平台可用性一次性实测（4/5 发现可用，判据为真取到数据）
- [x] **小红书下载修复**（详情接口流结构 EF4/EF5 改版）+ 混合部署 3/3（E9）
- [x] **场景一在快手/微博/小红书端到端跑通**（各 3/3，E9）
- [x] **本地 GPU 归纳落地**：vLLM + Qwen3-8B-AWQ，快手 3/3、B 站 2/2 全本地（E10）
- [x] **抖音扫码登录接通，五平台端到端全通（5/5）**
- [x] **CPU 纯本地档落地**：llama.cpp + Qwen2.5-3B-GGUF，微博/小红书场景二各 2/2 全 CPU（E11）
- [x] **场景二在 B 站/微博/小红书实测通过**（3/5；快手/抖音受平台风控限制，已如实记录）
- [x] **Web 界面（FastAPI + SSE 实时流水线 + 结果卡片墙 + 历史）**（E12）
- [x] **SQLite 入库 + 断点续跑**（CLI/Web 共用，`--resume`）（E12）
- [x] **README + 打包**：最短可复现路径、脚本清单、五平台状态表

### 已全部完成（用户 2026-08-08 指定的清单）

1. ~~CPU 纯本地档~~ ✅（E11）。
2. ~~场景二多平台~~ ✅ 3/5（B 站/微博/小红书）；快手今晚 IP 限流、抖音 Argus 风控，
   均平台侧问题、非逻辑缺陷，快手待 IP 冷却可复跑。
3. ~~FastAPI + 前端界面~~ ✅（E12）。
4. ~~SQLite 入库 + 断点续跑~~ ✅（E12）。
5. ~~README + 一键脚本~~ ✅（本次）。

### 仍可继续打磨（非阻塞，提交不依赖）

- 置信度驱动的按需升级 A/B 实测（代码已就位，`--escalate`）。
- 关键帧抽取在超长视频上的进一步提速（当前非瓶颈，长视频瓶颈是 CPU-ASR）。
- 快手场景二待 IP 冷却后复跑取证；抖音作品列表若要通需补 X-Argus 头签名。

### 环境备注（下次开工前）

- 服务器 SSH 端口/密码见 `.env`（本次已更新为 41351）。
- 本地 GPU 归纳需先在服务器起 vLLM：`bash scripts/vllm_restart.sh`（约 90 秒就绪）。
  跑本地档时 ASR 要走 CPU：`--profile gpu --device cpu`（12 GB 卡无法 LLM+ASR 同卡）。
- vLLM 空转也按小时计费，不用时 `pkill -9 -f 'vllm[-]venv'` 释放显存。
