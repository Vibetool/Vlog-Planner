<h1 align="center">Vlog-Planner</h1>

<p align="center">
一个 <b>Agent Skill</b>：给它「目的地 + 路线 + 日期」，它结合<b>实时天气、日出日落黄金时刻、太阳方位、海况与地图</b>，<br/>
和你多轮对话着产出一份<b>逐日 Vlog 拍摄手册</b>——每个点拍什么、几点拍、出片指数多高、天气不好怎么办。
</p>

> 方法论灵感来自 B 站博主「超 Carry 的柴西」的 Vlog 教学：看天定路线、把每天分成 A-roll / B-roll / 赶路日、用三镜头法快速拍齐。

## 它能做什么

- 🗺️ **目的地展开**：你只说"雨崩"，它研究出周边关键景点（神瀑 / 冰湖 / 大本营…），并给每个点标 **A-roll 锚点 / B-roll 空镜 / 双修** 的角色。
- 🌦️ **看天定路线**：逐时天气 → 每个点算一个 **「出片指数 ★」**。
- 🌅 **算黄金时刻**：本地计算日出日落、黄金 / 蓝调时刻、**太阳逐时方位**（定顺光逆光）。
- 🌊 **海况**：海边浪高 / 涌浪 / 海温（潮汐涨落时刻另查潮汐表）。
- 📅 **分日规划 + 抢窗**：A-roll 重点拍摄日 / **抢窗日（雨季好窗）** / B-roll 空镜日 / 赶路·休整日；并算出**当天最干拍摄窗口 + 雨起时刻**（日级"雨概率高"常藏住清晨晴窗），坏天气自动 plan B。
- 📍 **真实机位**：`--poi` 给每个点挂 OSM 命名机位/观景台（带坐标、来源），B-roll 打卡不靠脑补。
- 🎬 **套拍法**：A-roll 叙事 + B-roll 空镜 + **三镜头法**（① 自拍 ② 你的眼睛 ③ 把你放进环境）。
- 🗣️ **多轮对话**：随时可加路线、增删景点、改日期，它只重跑受影响的部分。

范例产出 👉 [`examples/sample-manual-taishan.md`](examples/sample-manual-taishan.md)（泰山夜爬看日出）。

## 安装

**最简单——直接让你的 Agent 读这个仓库自己装：**

> 读取 https://github.com/Vibetool/Vlog-Planner ，把它作为 skill 安装到我的 skills 目录（`vlog-planner`）。

手动装也行（需要 Python 3，**零 pip 依赖**）：

```bash
git clone https://github.com/Vibetool/Vlog-Planner.git
mkdir -p ~/.claude/skills/vlog-planner && cp -r Vlog-Planner/* ~/.claude/skills/vlog-planner/
```

装好后直接对 Agent 说：「**帮我规划下周去稻城的 vlog 拍摄，7/2–7/4，成都出发**」。它会先研究、提议、和你确认，再出手册——给得越细、问得越少。

## 数据源：零配置起步，可选升级

默认**全部免费、零 key、全球可用**：天气 `Open-Meteo`、选址 / POI `OpenStreetMap`、海况 `Open-Meteo Marine`、日出日落 / 太阳方位为本地天文计算（NOAA）。

国内想要更全的景点库和更准的天气，可在 `config.json` 换成**高德 + 和风**（没填 key 会自动回退到免费源）。

<details>
<summary>👉 升级到高德 / 和风（点开看注册步骤）</summary>

先 `cp config.example.json config.json`（`config.json` 已 gitignore，不外泄 key）。

**高德**（选址 + POI，控制台 <https://console.amap.com/>）
1. 手机号注册 → **实名认证**（个人认证 / 支付宝，免费秒过；⚠️ 不实名无免费额度）。
2. 应用管理 → 创建应用 → 添加 Key，**「服务平台」务必选「Web 服务」**（选 JS / Android / iOS 都用不了）。
3. `config.json` 里 `geocode` 和 `poi` 都改 `"provider":"amap"`，填同一个 key。坐标为 GCJ-02，Skill 自动转换。

**和风天气**（天气，控制台 <https://console.qweather.com/>）
1. 邮箱注册（免费不需实名）→ 建项目 → 加凭据（选 **API KEY**）→ 复制 KEY。
2. 在「设置」拿你的**专属 API Host**（形如 `abcxyz.qweatherapi.com`；⚠️ 老的 `devapi.qweather.com` 2026 起停用）。
3. `config.json` 里 `weather` 改 `"provider":"qweather"`，填 `key` 和 `base`（你的 Host）。免费版覆盖 7 天日预报 + 24h 逐时。

出片"口味"也能调：`planning.shootable_threshold` = `lenient` / `balanced`（默认）/ `strict`。

**一开始没配、之后想加 key？** 任何时候都行、**无需重装**：直接跟你的 Agent 说一句「**我要配高德 / 和风 key**」，它会带你走上面几步并帮你改好 `config.json`；或你自己编辑 `config.json` 保存，下次规划自动生效。（安装后首次规划时，Skill 也会主动问你一次用免费默认还是配 key。）
</details>

## 老实说的局限（手册里也会标注）

- 国内 OSM 景点覆盖**稀疏**，自动发现只作补充（想要全量配高德 key）。
- Open-Meteo 在中国复杂山地精度有限，仅供参考；**预报每天在变，出发前请重跑**并查景区直播核对。
- OSM/GPS 是 WGS-84、国内地图是 GCJ-02（差 100–300m），脚本同时给 `gcj02` 坐标，核对位置用它。
- 太阳计算不含**地形遮挡**；`marine.py` 只给海况，潮汐涨落时刻要另查潮汐表。

## 致谢与许可

方法论灵感：**超 Carry 的柴西**（B 站）。数据：[Open-Meteo](https://open-meteo.com/)（CC BY 4.0）、© **OpenStreetMap** 贡献者（ODbL）、**NOAA**。代码 **MIT**（见 [`LICENSE`](LICENSE)）。

> Made with ❤️ for people who climb a mountain all night just for one sunrise.
