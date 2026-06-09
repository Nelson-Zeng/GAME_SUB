# 🎮 游戏订阅提醒插件

> AstrBot 插件 · 结合 OneBot11 在 QQ 群中使用

订阅游戏发售日期与版本更新，插件每日定时通过 LLM 批量检索游戏信息，结果渲染为高清图片发送，并在发售前 7/3/1 天及当天自动 @提醒订阅用户。

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🕹️ 发售订阅 | 订阅游戏发售日期，发售前自动提醒 |
| 🔄 更新订阅 | 订阅游戏版本更新，更新当天自动提醒 |
| 📋 订阅列表 | 查看自己的订阅（按发售/更新分类，渲染为图片） |
| 🗑️ 移除订阅 | 取消自己对特定游戏的订阅 |
| 🧪 测试模式 | 带延时参数，快速验证检索结果（发售/更新均支持） |
| 🧹 自动清理 | 发售日过后自动清除发售订阅（更新订阅长期有效） |
| ⚡ 批量检索 | 所有订阅游戏合并为一次 LLM 请求，节省 API 调用 |
| 🖼️ 图片渲染 | 订阅结果通过 Playwright + Chromium 渲染为精美图片 |
| 🔌 独立搜索 | 可选配置独立的 Tavily API Key + LLM，不依赖 AstrBot 内置搜索 |

---

## 📦 安装方式

### 方式一：AstrBot 插件市场（推荐）

在 AstrBot WebUI 的插件市场中搜索 `astrbot_plugin_game_subscription`，点击安装即可。

### 方式二：手动安装

```bash
# 将插件克隆到 AstrBot 的 data/plugins/ 目录下
cd AstrBot/data/plugins
git clone https://github.com/Nelson-Zeng/GAME_SUB.git astrbot_plugin_game_subscription
```

安装完成后，在 AstrBot WebUI 中重载插件或重启 AstrBot。

### ⚠️ Playwright Chromium 安装

插件依赖 Playwright + Chromium 进行图片渲染。Docker 部署需额外安装：

```dockerfile
# 在 Dockerfile 或容器内执行
RUN playwright install chromium
```

若浏览器不可用，插件会自动降级为纯文本模式。

---

## 🚀 指令说明

所有指令需在 **QQ 群内** 使用。

### `/订阅发售 <游戏名>`

订阅某游戏的发售日期提醒。

```
用户：/订阅发售 黑神话悟空
机器人：✅ 已订阅《黑神话悟空》的发售提醒！
       📅 将在发售前 7, 3, 1 天及发售当天提醒您
```

### `/订阅发售 <游戏名> <延时秒数>`

测试模式：不加入订阅列表，延时后直接执行检索并输出结果（渲染为图片）。

```
用户：/订阅发售 黑神话悟空 10
机器人：🧪 测试模式：正在检索《黑神话悟空》...
       ⏱️ 将在 10 秒后输出结果

（10秒后）
机器人：@用户 [图片：测试检索结果，含游戏名、发售日期、距今天数]
```

### `/订阅更新 <游戏名> [延时秒数]`

订阅某游戏的版本更新提醒。可选传入延时秒数进入测试模式。

```
用户：/订阅更新 原神
机器人：✅ 已订阅《原神》的更新提醒！
       📅 游戏更新当天将自动提醒您
```

```
用户：/订阅更新 原神 10
机器人：🧪 测试模式：正在检索《原神》...

（10秒后）
机器人：@用户 [图片：测试检索结果，含游戏名、最近更新日期、最新版本]
```

### `/游戏订阅列表`

查看 **你自己** 订阅的所有游戏，按发售/更新分类显示（渲染为图片）。

```
用户：/游戏订阅列表
机器人：[图片：我的游戏订阅，含发售订阅和更新订阅两个分区]
```

### `/移除订阅 <游戏名>`

取消 **你自己** 对某游戏的订阅（发售 + 更新同时取消）。

```
用户：/移除订阅 原神
机器人：✅ 已取消对《原神》的订阅（共 1 条）
```

---

## ⚙️ 配置项

在 AstrBot WebUI → 插件 → 游戏订阅提醒 中进行配置。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `admin_sids` | 字符串 | 留空 | 管理员 QQ 号列表（逗号分隔），仅管理员可使用测试模式，留空则所有用户均可测试 |
| `check_time.hour` | 整数 | `9` | 每日检索执行小时（24小时制，0-23） |
| `check_time.minute` | 整数 | `0` | 每日检索执行分钟（0-59） |
| `reminder_days` | 字符串 | `7,3,1,0` | 发售前提醒天数，逗号分隔，0 表示发售当天 |
| `tavily_api_key` | 字符串 | 留空 | Tavily Search API Key（配置后将优先使用独立搜索） |
| `search_llm_api_key` | 字符串 | 留空 | 独立搜索用 LLM 的 API Key（OpenAI 兼容格式） |
| `search_llm_url` | 字符串 | 留空 | 独立搜索用 LLM 的请求地址，如 `https://api.openai.com/v1` |
| `search_llm_model` | 字符串 | 留空 | 独立搜索用 LLM 的模型名，如 `gpt-4o-mini` |
| `search_provider_id` | 字符串 | 留空 | 指定 AstrBot LLM 提供商 ID，留空则使用当前默认提供商 |

> **调用优先级**：
> 1. 若配置了 `tavily_api_key` → 优先使用 Tavily 独立搜索
> 2. 否则 → 尝试调用 AstrBot 内置 Web Search 工具
> 3. LLM 提取：配置了独立 `search_llm_*` → 优先使用独立 LLM；否则回退 AstrBot 提供商

---

## 🔔 自动提醒逻辑

```
每日 09:00（可配置）
    │
    ├─ 收集所有「发售订阅」的游戏名
    │   └─ 批量检索发售日期（Tavily / AstrBot 搜索 → LLM 提取）
    │       └─ 若距今 ∈ {7, 3, 1, 0} 天
    │           ├─ 发送图片通知 + @订阅用户
    │           └─ 若为发售当天 → 自动清除该游戏的发售订阅
    │
    └─ 收集所有「更新订阅」的游戏名
        └─ 批量检索更新日期
            └─ 若更新日期 == 今天
                ├─ 发送图片通知 + @订阅用户
                └─ （更新订阅不会被清除，长期有效）
```

---

## 📁 文件结构

```
astrbot_plugin_game_subscription/
├── main.py              # 插件主文件（核心逻辑）
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 Schema
├── requirements.txt     # Python 依赖
└── README.md            # 说明文档
```

---

## 📂 数据存储

订阅数据持久化在 AstrBot 根目录的 `data/astrbot_plugin_game_subscription/subscriptions.json`，格式如下：

```json
{
  "release_subscriptions": {
    "游戏名": [
      {
        "user_id": "QQ号",
        "group_id": "群号",
        "unified_msg_origin": "会话标识",
        "subscribe_date": "2025-01-01"
      }
    ]
  },
  "update_subscriptions": {
    "游戏名": [...]
  }
}
```

---

## ⚠️ 注意事项

- 插件依赖 **LLM 提供商** 进行游戏信息检索，确保至少配置一个可用的 LLM。
- 推荐配置独立的 `tavily_api_key` + `search_llm_*`，避免依赖 AstrBot 内部搜索工具。
- `search_provider_id` 可用于指定独立的 AstrBot LLM 提供商（如成本更低的模型）。
- 同一用户对同一游戏只能订阅一次（防重复，跨群也视为已订阅）。
- 发售订阅在游戏发售当天提醒后自动清除；更新订阅长期有效，需手动移除。
- 插件仅支持在 **QQ 群内** 使用（需配合 OneBot11 协议端，如 NapCat、Lagrange 等）。
- 图片渲染依赖 Playwright + Chromium，首次部署后执行 `playwright install chromium`。

---

## 📄 许可证

MIT License
