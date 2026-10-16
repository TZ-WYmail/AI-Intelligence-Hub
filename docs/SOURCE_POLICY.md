# 来源核验与使用范围

2026-10-16首批核验、2026-10-17新增核验结果见 source-validation.json。累计26个候选中22个成功并启用；每个启用来源均记录实际地址、格式、条目数、最近发布时间、免鉴权、官方订阅目录、轮询周期与限制备注。所有来源未来都可能调整地址/条款。

- [NVIDIA 官方 RSS 目录与使用说明](https://www.nvidia.com/en-us/about-nvidia/rss/)明确限定个人、信息用途和非商业使用。当前仅订阅官方列出的 Newsroom/Developer feed；Blog 超时禁用。商业化部署前须另行确认许可。
- [arXiv 官方 RSS 说明](https://info.arxiv.org/help/rss.html)给出分类 URL 规则；[API 使用条款](https://info.arxiv.org/help/api/tou.html)适用于 RSS。仅使用论文描述性元数据，保留指向原文摘要页的链接，不抓 PDF。所有 arXiv feed 共享每 3.1 秒最多一个请求、单连接约束；默认 1 小时轮询。arXiv 按投稿发布批次更新，并非即时新闻。
- [NASA 官方订阅目录](https://www.nasa.gov/rss-feeds/)列出实际 feed；[NASA 使用说明](https://www.nasa.gov/nasa-brand-center/images-and-media/)列明素材/第三方内容/标志的边界。应用不使用其品牌标志、不下载图片，只保存订阅本身提供的标题和摘要。默认 30 分钟轮询；未找到可承诺的固定刷新 SLA 或明确数字频率上限。
- [EIA 订阅目录](https://www.eia.gov/tools/rssfeeds/)提供能源新闻/统计订阅；[EIA 再利用说明](https://www.eia.gov/about/copyrights_reuse.php)要求保留来源与发布时间，第三方授权素材另有边界。本项目不抓图片、全文或 PDF。默认 30 分钟轮询。
- [ECB 官方订阅目录](https://www.ecb.europa.eu/home/html/rss.en.html)提供实际 RSS 地址；[版权说明](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html)要求准确引用来源、标示修改，并对署名研究文档再出版另有限制。此处仅用于个人订阅信息发现，保存 feed 元数据和摘要，阅读研究文档跳转官网，不重新发布论文正文。默认 30 分钟轮询。
- [FDA官方订阅目录](https://www.fda.gov/about-fda/contact-fda/subscribe-podcasts-and-news-feeds)列出新闻发布和药品动态的实际RSS地址，2026-10-17均解析20条。采用每小时轮询，未找到明确数字限额；只作个人订阅与来源链接展示，不推断全文再分发权。
- [36氪官方订阅目录](https://www.36kr.com/rss-center)的快讯订阅当前不可解析；[NIH NIDCR目录](https://www.nidcr.nih.gov/about-us/nidcr-rss-feeds)的Science News地址请求失败，两者均禁用。
- [GDELT 官方 DOC API 说明](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)确认 JSON ArtList 协议；当前实网返回错误，禁用。仅适配发现元数据，不代表获得原出版者全文转载权。

订阅可访问并不等于无限制转载许可，单次解析成功也不等于持续可用。未知数字限额明确记为未知，不作虚构。后续需记录同一来源多次采集的更新间隔/发现延迟，依照源站实际限制调整周期。
