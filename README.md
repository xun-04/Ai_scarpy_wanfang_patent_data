# 万方境外专利爬虫

基于万方数据 gRPC-web API 的境外专利批量爬取工具。输入 IPC 分类号，按国家和年份筛选，自动翻页抓取专利数据并导出为 Excel，支持从 Espacenet 补充 IPC/CPC 分类号。

## 功能特性

- **双阶段流程**：Phase 1 从万方搜索爬取专利列表，Phase 2 从 Espacenet 补充主分类号和分类号
- **断点续爬**：每 N 页自动保存进度（JSON checkpoint），网络中断后可从上次位置继续，不丢数据
- **Excel 断点修复**：即使 checkpoint 文件丢失，也能从已保存的 Excel 最后一条成功记录恢复进度
- **反爬对抗**：随机请求间隔、冷却期、Cookie 持久化复用、IP 被封时自动弹出浏览器让用户手动完成滑块验证
- **gRPC-web 协议逆向**：手动实现 protobuf 编解码，不依赖 .proto 文件
- **多国家支持**：覆盖 49 个国家/地区（美国、欧洲专利局、日本、韩国、加拿大等）
- **半年拆分**：通过公开日日期范围将每年拆为上半年/下半年两次爬取，提升 121 页上限
- **分 Sheet 存储**：每年一个 Excel 文件，上半年和下半年数据分 Sheet 存放
- **自动保存**：每 N 页自动写入 Excel，防止中途数据丢失

## 环境要求

- Python 3.7+
- Windows / macOS / Linux

## 安装

```bash
# 安装核心依赖
pip install requests pandas blackboxprotobuf

# 安装浏览器自动化支持（用于手动滑块验证 + Espacenet 分类号补充）
pip install playwright
playwright install chromium

# Espacenet 分类号补充还需要
pip install beautifulsoup4
```

## 快速开始

### GUI 模式（默认）

```bash
python Ai_scrapy.py
```

运行后弹出窗口，选择年份和运行模式，点击确认即可开始。

对于数据量较大的年份，推荐先搜索爬取后再补充分类号。

## 配置说明

编辑 `Ai_scrapy.py` 文件顶部的配置常量：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `TARGET_CLASS_CODE` | `"B64U10/10"` | 目标 IPC 分类号 |
| `RESULT_DIR` | `"./cc/result/data"` | 结果输出目录(相对路径) |
| `PAGE_SIZE` | `50` | 每页请求条数 |
| `REQUEST_DELAY_MIN` / `MAX` | `2` / `5` | 每页间隔秒数（随机防反爬） |
| `COOLDOWN_EVERY` | `20` | 每 N 页触发冷却 |
| `COOLDOWN_SECONDS` | `5` | 冷却等待秒数 |
| `AUTO_SAVE_EVERY` | `5` | 每 N 页自动写入 Excel |
| `MAX_API_PAGES` | `121` | 最大翻页数 |
| `ESPACENET_ENRICH_DELAY` | `(0.3, 0.8)` | Espacenet 请求间隔（秒） |
| `ESPACENET_ENRICH_SAVE_EVERY` | `20` | 每 N 条补充保存一次 |

## 输出文件

| 文件 | 说明 |
|---|---|
| `境外专利结果_{年份}.xlsx` | 爬取结果，内含 1-6月 / 7-12月 两个 Sheet |
| `checkpoint.json` | Phase 1 断点续爬进度，程序正常结束后自动删除 |
| `checkpoint_enrich.json` | Phase 2 断点续补进度，程序正常结束后自动删除 |
| `cookies.json` | 万方会话 Cookie，复用可减少验证码触发 |
| `browser_profile/` | Espacenet 浏览器配置文件副本（绕过 Cloudflare验证） |

## Excel 输出字段

| 字段 | 说明 |
|---|---|
| 国家 | 筛选国家 |
| 年份 | 筛选年份 |
| 检索分类号 | IPC 分类号 |
| 全局序号 | 累计序号 |
| 页码 | 所在页码 |
| 页内序号 | 页内序号 |
| 专利名称 | 专利标题 |
| 专利摘要 | 摘要文本 |
| 申请/专利号 | 申请号或专利号 |
| 申请日期 | 申请日期 |
| 公开/公告号 | 公开号或公告号 |
| 公开/公告日 | 公开日期 |
| 主分类号 | 主 IPC 分类号（Phase 2 从 Espacenet 填入） |
| 分类号 | 其他分类号（Phase 2 从 Espacenet 填入） |
| 申请/专利权人 | 申请人或专利权人 |
| 发明/设计人 | 发明人 |
| 国别省市代码 | 国家代码 |
| 状态 | 成功 / 失败原因 |
| 爬取时间 | 数据抓取时间 |

## 工作原理

**Phase 1 — 搜索爬取：**
1. 构造 gRPC-web protobuf 搜索请求（分类号 + 公开日日期范围 + 国家 + 分页参数）
2. 每年拆分为 1-6月 / 7-12月 两次查询，避免超过 API 121 页上限
3. 发送 HTTP POST 到万方搜索接口，接收 protobuf 格式响应
4. 解析 protobuf 外层获取总数和条目列表，再递归解析内层专利详情
5. 字段映射 → 追加到 pandas DataFrame → 按半年分 Sheet 写入 Excel

**Phase 2 — 分类号补充：**
1. 读取 Phase 1 生成的 Excel，找到有公开号的行
2. 使用 Playwright + 真实浏览器配置文件调 Espacenet API（绕过 Cloudflare）
3. 从返回 HTML 中提取 IPC（International Patent Classification）和 CPC（Cooperative Patent Classification）
4. 回填到 Excel 的主分类号和分类号列

## 目录结构

```
.
├── Ai_scrapy.py              # 主入口（配置 + 编排逻辑）
├── ai_scrapy/                # 功能模块包
│   ├── proto_utils.py        #   protobuf 编解码与字段解析
│   ├── api_client.py         #   HTTP 会话管理 + API 调用 + 验证码处理
│   ├── espacenet.py          #   浏览器检测 + Espacenet 客户端
│   ├── persistence.py        #   Excel 读写 + checkpoint 管理
│   └── gui_app.py            #   Tkinter GUI + 国家选择
├── result/data/              # 输出目录（Excel、checkpoint、cookie）
├── .gitignore
└── README.md
```

## 常见问题

**Q: 爬取过程中弹出浏览器窗口？**

说明 IP 被万方限流，需要手动完成滑块验证。完成验证后关闭浏览器窗口，程序会自动提取 Cookie 继续爬取。（有校园网ip好像不会触发）

**Q: 如何在断网后恢复？**

直接重新运行相同年份，程序会自动从 checkpoint 恢复进度。如果 checkpoint 丢失，会尝试从 Excel 最后一条成功记录恢复。

**Q: 为什么不直接用 requests 解析网页？**

万方专利搜索使用 gRPC-web 协议传输数据，不通过 HTML 渲染。本项目逆向实现了其 protobuf 通信协议。

**Q: 能否爬取所有数据？**

API 有 121 页上限（约 6000 条），通过年份 + 半年 + 国家拆分可以覆盖更多数据。

**Q: 分类号获取失败？**

先试试看能否打开`https://worldwide.espacenet.com/`，如果不行的话试试科学上网或者校园网。

## License

MIT
