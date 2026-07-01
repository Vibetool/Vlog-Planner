<h1 align="center">Vlog-Planner</h1>

<p align="center">
  一个 <b>Claude Code / Claude Agent Skill</b>：给它「目的地 + 大致路线 + 日期」，<br/>
  它结合<b>实时天气、日出日落与黄金时刻、太阳方位、海况、地图 POI</b>，<br/>
  和你<b>多轮对话</b>着，产出一份<b>逐日 Vlog 拍摄手册</b>——每个点拍什么、几点拍、出片指数多高、天气不好怎么办。
</p>

---

## 🙏 致敬「超 Carry 的柴西」

这个 Skill 的方法论，完全来自 B 站博主 **「超 Carry 的柴西」** 的一支 Vlog 制作教学视频。

她讲的不是玄乎的"感觉"，而是一套**可复制的功夫**：出发前认真考证 **天气、光照、潮汐、日出日落**，用 Windy / Lumy / Lumos 这些工具预判光线，把每天定性成「A-roll 拍摄日 / B-roll 拍摄日 / 坏天气赶路日」，再用「三镜头拍摄法」快速拍齐一组完整镜头。她那句 **"再美的风景，如果没有故事发生，也很难被人记住"**，是这个工具的灵魂。

Vlog-Planner 把她这套**选景 + 看天 + 排时段**的功夫自动化了。**谢谢柴西把压箱底的干货端出来。** 做成片时，也欢迎你在致谢里 @ 一下她。

> 本项目是粉丝向的开源实现，与博主本人无隶属或合作关系。

---

## ✨ 它能做什么

- 🗺️ **目的地展开**：你只说"雨崩"，它帮你研究出周边一串关键景点（神瀑、冰湖、大本营…），并给每个点标上 **A-roll 锚点 / B-roll 空镜 / 双修** 的角色，形成初步计划。
- 🌦️ **看天定路线**：拉取逐时天气（云量 / 降水概率 / 风 / 能见度），给每个点算一个 **"出片指数 ★"**。柴西说"好看日落很吃天气"——这里把它量化。
- 🌅 **算黄金时刻**：本地天文计算（NOAA）日出日落、黄金 / 蓝调时刻，以及**太阳逐时方位**（决定机位顺光逆光）。等于把 Lumy + Lumos 离线算了。
- 🌊 **海况（海边）**：浪高 / 涌浪 / 海温（Open-Meteo Marine）；潮汐涨落时刻提示你另查潮汐表。补上柴西说的"潮汐"这一环。
- 📅 **分日规划**：每天定性为 **A-roll 重点日 / B-roll 空镜日 / 赶路休整日**；坏天气自动给 **plan B**。
- 🎬 **套柴西拍法**：A-roll 叙事 + B-roll 空镜 + **三镜头法**（① 自拍 ② 你的眼睛 ③ 把你放进环境），并按"装备 → 场景"帮你挑镜头。
- 🗣️ **多轮对话，不是一次性出稿**：先提议、你确认、再推进；随时可加路线细节、增删景点、改日期，它只重跑受影响的部分。
- 🔍 **如实标注**：手册里说明每项数据的来源与局限（见下）。

一份产出长什么样，直接看范例 👉 [`examples/sample-manual-taishan.md`](examples/sample-manual-taishan.md)（泰山夜爬看日出）、[`examples/sample-manual-chuanxi.md`](examples/sample-manual-chuanxi.md)（川西 3 天）。

---

## 🔧 工作原理

把"算得准的部分"和"要创意的部分"分开：

- **`scripts/`（确定性计算）**：地理编码、天气、太阳黄金时刻 / 方位、海况、出片评分、坐标转换 → 输出结构化 JSON。**纯 Python 标准库，零依赖，零 key（默认数据源）。**
- **Claude（知识 + 创意）**：读这份 JSON + 柴西方法论，注入目的地的人文故事、A-roll / B-roll、三镜头法，写成逐日手册，并和你对话迭代。

它跑的是一套 6 步、带 4 个"确认门"的 SOP：**目的地展开 → 定逐日行程 → 算天气×光线 → 出片评分/分日角色 → 套拍法写手册 → 透明标注**。

---

## 📦 安装（Step by step）

### 0. 前置要求
- **Python 3**（macOS/Linux 自带；Windows 到 python.org 装）。**不需要 `pip install` 任何东西。**
- **Claude Code**（CLI / 桌面 / IDE 插件均可）。
- 联网（天气 / 地图默认走 Open-Meteo、OpenStreetMap）。

### 1. 下载本仓库
```bash
git clone https://github.com/Vibetool/Vlog-Planner.git
```
（或点右上角 **Code → Download ZIP** 解压。）

### 2. 放进 Claude Code 的 skills 目录
Skill 目录名必须叫 **`vlog-planner`**（与 `SKILL.md` 里的 `name:` 一致）。

```bash
# 用户级（所有项目都能用）——在仓库根目录执行
mkdir -p ~/.claude/skills/vlog-planner && cp -r ./* ~/.claude/skills/vlog-planner/

# 或项目级（只在某个项目里用）
mkdir -p <你的项目>/.claude/skills/vlog-planner && cp -r ./* <你的项目>/.claude/skills/vlog-planner/
```

### 3. 验证装好了
```bash
python3 ~/.claude/skills/vlog-planner/scripts/sun.py --selftest   # 应输出 8/8 passed
```

### 4. 在 Claude Code 里用
直接说话就行，比如：

> 「帮我规划下周去稻城亚丁的 vlog 拍摄，7 月 2 号到 4 号，成都出发」

Claude 会自动调用本 Skill。**这是一个对话式过程**：它先研究目的地、提议关键景点和逐日安排，**和你来回确认**（你随时可以补充更明确的路线、增删景点、改日期），坏天气日一起商量备选，最后写出逐日手册，还能按你的反馈继续调整。给得越细、问得越少。

---

## 🔌 数据源：零配置起步，需要再升级

**默认全部免费、零 key、全球可用**，装好即用：

| 能力 | 默认（免费无 key） | 可升级为（需注册 key） |
|---|---|---|
| 天气 | Open-Meteo | 和风天气 QWeather |
| 选址 / 地理编码 | OpenStreetMap Nominatim | 高德 Amap |
| 周边 POI | OpenStreetMap Overpass | 高德 Amap |
| 海况 | Open-Meteo Marine | —（免费无 key） |
| 日出日落 / 太阳方位 | 内置天文计算（NOAA） | —（始终本地，无需 key） |

**要不要换成高德 / 和风？** 看你拍哪儿：

- **国内旅行、想要更全的景点库和更准的路网/天气** → 建议升级到**高德 + 和风**。OSM 在国内景点覆盖稀疏，高德明显更全。
- **只是先跑通、或主要拍国外** → **默认就够**，不用折腾 key。

升级不影响回退：**没填 key 会自动回退到免费源**，Skill 照常工作。

---

## 🔑 升级教程：注册高德 / 和风并填进配置

先复制一份配置：
```bash
cd ~/.claude/skills/vlog-planner
cp config.example.json config.json      # config.json 已被 .gitignore，不会外泄你的 key
```

### A. 高德地图（选址 + POI）

> 控制台 <https://console.amap.com/> ｜ 文档 <https://lbs.amap.com/>

1. 打开 <https://console.amap.com/> 用**手机号注册**并登录（国内手机号）。
2. 完成**实名认证（个人认证即可，用支付宝授权，免费、通常秒过）**。⚠️ **不实名就没有免费额度**，key 建了也调不通。
3. 左侧 **应用管理 → 我的应用 → 创建新应用**，填个名字（如 `vlog-planner`）。
4. 在这个应用上点 **添加 Key**：**「服务平台」务必选「Web 服务」**。⚠️ 选成「Web端(JS)/Android/iOS」都**无法**用于我们的服务端 REST 调用。
5. 复制生成的 **Key**（32 位字符串）。geocode 和 POI **共用这一个 Web 服务 key**。
6. 浏览器自测：`https://restapi.amap.com/v3/geocode/geo?address=杭州西湖&key=你的KEY`，返回 `status:1` 即可用。
7. 填进 `config.json`：把 `providers.geocode.provider` 和 `providers.poi.provider` 改成 `"amap"`，并把 key 填进两处 `amap.key`：

```jsonc
"geocode": { "provider": "amap", "amap": { "key": "你的高德KEY" } },
"poi":     { "provider": "amap", "radius_m": 8000, "amap": { "key": "你的高德KEY" } }
```

> 免费额度：个人实名后，地理编码 / 周边搜索属"基础服务"，日调用量以万计、约 3 QPS（额度按账号共享，不是按 key）。超额只会返回失败不会自动扣费。具体数字以你控制台为准。坐标为 GCJ-02，Skill 已自动处理转换。

### B. 和风天气 QWeather（天气）

> 控制台 <https://console.qweather.com/> ｜ 文档 <https://dev.qweather.com/>

1. 打开 <https://console.qweather.com/> 用**邮箱注册**并登录。**免费 key 不需要实名。**
2. **创建项目（Project）**：项目页右上角 Create Project，起个名字。
3. **添加凭据（Credential）**：进项目 → Add Credential → 认证方式选 **API KEY** → 保存，复制生成的 **KEY**。
4. **拿到你的专属 API Host**：控制台 → 设置 <https://console.qweather.com/setting>，会给你一个**专属域名**（形如 `abcxyz.qweatherapi.com`）。⚠️ **2026 起老的共享域名 `devapi.qweather.com` 停用**，一定用你自己的 Host。
5. 浏览器自测：`https://你的HOST/v7/weather/7d?location=116.41,39.92&key=你的KEY`（注意 `location` 是**经度在前**）。
6. 填进 `config.json`：把 `providers.weather.provider` 改成 `"qweather"`，填 key 和你的 Host：

```jsonc
"weather": {
  "provider": "qweather",
  "qweather": { "key": "你的和风KEY", "base": "https://你的HOST.qweatherapi.com" }
}
```

> 免费额度：无需付款即可用，当前约"每月前 5 万次免费"（历史上变动多次，以你控制台为准）。免费版覆盖 7 天日预报 + 24 小时逐时。注意：和风免费版逐时只有未来 24h，多日逐时不如 Open-Meteo 全，Skill 会自动按日级降级处理。

### 出片"口味"也能调
`config.json` 里 `planning.shootable_threshold`：`lenient`（只要不是完全没法拍都拍）/ `balanced`（默认）/ `strict`（只推荐强光晴朗窗口）。

---

## ⚠️ 老实说的局限（手册里也会标注）

- **国内 POI**：OSM 在中国覆盖**稀疏**，自动发现只作补充；想要全量请配高德 key。
- **天气精度**：Open-Meteo 在中国由全球模式驱动，**复杂山地**（高原午后对流、河谷雾）精度有限，仅供参考。
- **坐标基准**：OSM/GPS 是 WGS-84，国内地图是 GCJ-02，**差 100–300m**。脚本同时给 `gcj02` 坐标，国内地图核对位置请用它。
- **光线计算**：不含**地形遮挡**（峡谷 / 高山会让实际日出更晚），按海平面地平线估算。
- **潮汐**：`marine.py` 只给海况（浪 / 涌 / 海温），**潮汐涨落时刻要另查潮汐表**。
- **预报会变**：出发前 1–2 天请**重跑**，并用 `WebSearch` 查目的地"景区直播 / webcam"核对实况。

---

## 🛠️ 脚本（也可单独命令行运行）

```bash
python3 scripts/sun.py --lat 28.50 --lon 100.26 --date 2026-07-02 --tz 8   # 日出日落+黄金时刻+太阳方位
python3 scripts/geocode.py "冰湖" --near 28.39,98.79                         # 地名→坐标(+gcj02)，就近消歧
python3 scripts/weather.py --lat 28.50 --lon 100.26 --start 2026-07-02 --end 2026-07-04
python3 scripts/marine.py --lat 36.06 --lon 120.38 --start 2026-07-03 --end 2026-07-04   # 海况(海边)
python3 scripts/poi.py --lat 28.50 --lon 100.26 --radius 8000               # 周边机位
echo '{"anchor":"稻城","days":[{"date":"2026-07-02","spots":["稻城亚丁"]}]}' | python3 scripts/plan.py
python3 scripts/sun.py --selftest                                          # 天文算法自检
```

## 📁 目录结构

```
Vlog-Planner/
├─ SKILL.md                  # 给 Claude 的编排说明（触发 + 6 步流程 + 手册模板）
├─ config.example.json       # 可配置：数据源 / 出片口味 / 风格 / 装备
├─ references/methodology.md # 柴西方法论（规划时注入）
├─ scripts/                  # 零依赖 Python：sun/geocode/weather/marine/poi/plan + _common
└─ examples/                 # 范例手册（泰山夜爬、川西 3 天）
```

## 🙌 致谢与许可

- **方法论灵感**：[超 Carry 的柴西](https://space.bilibili.com/)（B 站）。
- **数据**：天气 [Open-Meteo](https://open-meteo.com/)（CC BY 4.0）；地理 / POI © **OpenStreetMap** 贡献者（ODbL，展示时请署名）；太阳位置基于 **NOAA** 公式。
- **代码许可**：**MIT**（见 [`LICENSE`](LICENSE)）。各数据源使用须遵守其各自条款与限频。

> Made with ❤️ for people who climb a mountain all night just for one sunrise.
