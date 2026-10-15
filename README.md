# EV Market Intelligence OS

面向新能源汽车市场研究的个人交互式作品。Python读取官方数据，SQLite保留独立来源批次，Streamlit与Plotly提供可追溯分析。

## 体验

应用入口为`public_app.py`。首页可进入Dashboard或AI Market Research演示。覆盖DE、FR、IT、ES、NL、SE、NO、BE、PL、DK、GB，年度2022—2025；具体采集和官方更新时间在首页显示。采用已验证快照，尚未启用自动更新。

- 注册趋势、同比、净增、BEV渗透率、动力结构、排名、地图、质量筛选和历史批次。
- 深浅主题、CSV/PNG及HTML摘要导出。
- AI模块提供真实数据输入、引用核验和外部JSON报告预览，不调用模型。演示/模拟内容不等于AI生成报告。

英国DfT/DVLA首次注册含进口二手车，TOTAL公布精度有限。11国可共同展示，严格可比分析排除英国；不能跨来源合计或严格排名，也不代表整个欧洲或欧盟市场。详情见[SOURCES.md](SOURCES.md)。

## 运行与部署

使用Python 3.12，安装`requirements.txt`后运行`streamlit run public_app.py`。Streamlit Community Cloud选择仓库根目录的`public_app.py`，高级设置选择Python 3.12；**无需配置任何Secrets或API密钥**。

本目录是发布候选包：包含验证所需的数据库、JSON/CSV、DfT官方附件、批次清单及本地地图。请保持目录结构；不要只上传app.py或删除来源证据。完整文件摘要在`snapshot_manifest.json`，数据库不接受访客写入。

`.public-demo`与公开入口双重启用只读模式：即使服务器误配密钥和授权变量，模型调用与服务器端报告保存仍被拒绝。访客报告仅驻留当前会话并可下载，不应粘贴敏感信息。

这是个人研究与技术演示，不代表任何官方机构背书。真实AI调用尚未在本作品公开版启用。公开部署需要维护者确认后进行；候选目录本身不代表已经上线。
