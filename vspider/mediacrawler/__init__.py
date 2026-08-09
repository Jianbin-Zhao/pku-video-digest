"""MediaCrawler 适配层。

老师给的参考实现就是 MediaCrawler，这里把它当**依赖库**用，而不是抄或改它的代码。

它值得复用的核心资产只有一个：四个平台的反爬对抗。
抖音的 a_bogus、小红书的 x-s/x-t、快手的 GraphQL 会话、微博的移动端 cookie，
这些签名算法既复杂又会随平台更新失效，自己手写是纯粹的重复劳动，
而且维护成本会一直压在身上。

它没有的东西同样明确，也正是本项目要补的：
  - **没有任何榜单能力**。抓取类型只有 search / detail / creator 三种，
    而题面第一个场景要的是「今天排行榜前 5」。
  - 没有下载后的内容理解（语音识别、画面文字、归纳）。
  - 没有统一的数据模型，各平台的存储字段各写各的。

因此分工是：MediaCrawler 负责「能拿到数据」，本项目负责「拿什么数据、拿到之后做什么」。
"""

from vspider.mediacrawler.session import (
    MediaCrawlerSession,
    PlatformSpec,
    SPECS,
)

__all__ = ["MediaCrawlerSession", "PlatformSpec", "SPECS"]
