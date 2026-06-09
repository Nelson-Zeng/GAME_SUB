"""游戏订阅提醒插件 - AstrBot Plugin

提供游戏发售日期和版本更新的订阅与自动提醒功能。
结合 OneBot11 在 QQ 群中使用，通过 LLM 检索游戏发售/更新日期，
并在指定时间自动@提醒订阅用户。

指令:
    /订阅游戏 <游戏名>        - 订阅游戏发售日期提醒
    /订阅游戏 <游戏名> <延时> - 测试订阅（延时秒后执行检查并输出结果）
    /订阅更新 <游戏名>        - 订阅游戏版本更新提醒
    /订阅列表                 - 查看当前群的所有订阅
    /移除订阅 <游戏名>        - 移除指定游戏的所有订阅
"""

import asyncio
import json
import os
from datetime import datetime, timedelta

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp


# ---------------------------------------------------------------------------
# HTML 模板：订阅列表渲染（Jinja2 + CSS）
# ---------------------------------------------------------------------------
SUBSCRIPTION_LIST_HTML = """
<div style="font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
            padding: 24px 28px; min-width: 420px; max-width: 600px;
            background: #ffffff; color: #333;">
  <h1 style="font-size: 22px; color: #1a1a2e; margin: 0 0 18px 0;
             border-bottom: 2px solid #4361ee; padding-bottom: 10px;">
    🎮 游戏订阅列表
  </h1>

  <!-- 游戏发售订阅 -->
  <div style="margin-bottom: 22px;">
    <h2 style="font-size: 17px; color: #4361ee; margin: 0 0 10px 0;">
      🕹️ 游戏发售订阅
    </h2>
    {% if release_subs %}
    <ul style="list-style: none; padding: 0; margin: 0;">
      {% for game in release_subs %}
      <li style="padding: 10px 14px; margin-bottom: 6px;
                 background: #f0f4ff; border-radius: 6px;
                 border-left: 3px solid #4361ee; font-size: 14px;">
        <strong>{{ game.game_name }}</strong>
        <span style="color: #888; font-size: 12px; margin-left: 6px;">
          ({{ game.count }}人订阅)
        </span>
      </li>
      {% endfor %}
    </ul>
    {% else %}
    <p style="color: #aaa; font-size: 14px; padding: 8px 0;">暂无游戏发售订阅</p>
    {% endif %}
  </div>

  <!-- 游戏更新订阅 -->
  <div>
    <h2 style="font-size: 17px; color: #f72585; margin: 0 0 10px 0;">
      🔄 游戏更新订阅
    </h2>
    {% if update_subs %}
    <ul style="list-style: none; padding: 0; margin: 0;">
      {% for game in update_subs %}
      <li style="padding: 10px 14px; margin-bottom: 6px;
                 background: #fff0f6; border-radius: 6px;
                 border-left: 3px solid #f72585; font-size: 14px;">
        <strong>{{ game.game_name }}</strong>
        <span style="color: #888; font-size: 12px; margin-left: 6px;">
          ({{ game.count }}人订阅)
        </span>
      </li>
      {% endfor %}
    </ul>
    {% else %}
    <p style="color: #aaa; font-size: 14px; padding: 8px 0;">暂无游戏更新订阅</p>
    {% endif %}
  </div>

  <p style="font-size: 11px; color: #bbb; margin-top: 18px; text-align: right;">
    生成时间: {{ generated_at }}
  </p>
</div>
"""


# ---------------------------------------------------------------------------
# 插件主类
# ---------------------------------------------------------------------------
class GameSubscriptionPlugin(Star):
    """游戏订阅提醒插件

    支持功能:
    - 订阅游戏发售日期，发售前 7/3/1 天及当天自动提醒
    - 订阅游戏版本更新，更新当天自动提醒
    - 查看订阅列表（按游戏/更新分类显示）
    - 移除特定游戏订阅
    - 测试订阅（带延时参数）
    - 发售日过后自动清理发售订阅；更新订阅长期有效
    - 每日定时批量检索，一次性查询所有订阅游戏
    """

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 持久化数据目录（AstrBot 根目录下的 data/）
        self.data_dir = os.path.join("data", "astrbot_plugin_game_subscription")
        os.makedirs(self.data_dir, exist_ok=True)
        self.data_file = os.path.join(self.data_dir, "subscriptions.json")

        # 加载订阅数据
        self.subscriptions = self._load_data()

        # 解析提醒天数配置（兼容整数和字符串）
        self.reminder_days = self._parse_reminder_days(
            self.config.get("reminder_days", "7,3,1,0")
        )

        # 启动每日定时检查任务
        asyncio.create_task(self._schedule_loop())

    # ------------------------------------------------------------------
    # 配置解析
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_reminder_days(value) -> list:
        """解析 reminder_days 配置，支持列表和逗号分隔字符串"""
        if isinstance(value, list):
            return sorted([int(d) for d in value])
        try:
            return sorted([int(d.strip()) for d in str(value).split(",") if d.strip()])
        except (ValueError, AttributeError):
            return [0, 1, 3, 7]

    # ------------------------------------------------------------------
    # 数据持久化
    # ------------------------------------------------------------------
    def _load_data(self) -> dict:
        """从磁盘加载订阅数据"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as exc:
            logger.error(f"[GameSub] 加载订阅数据失败: {exc}")
        return {"release_subscriptions": {}, "update_subscriptions": {}}

    def _save_data(self):
        """将订阅数据持久化到磁盘"""
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.subscriptions, f, ensure_ascii=False, indent=2)
        except IOError as exc:
            logger.error(f"[GameSub] 保存订阅数据失败: {exc}")

    # ------------------------------------------------------------------
    # 订阅管理辅助方法
    # ------------------------------------------------------------------
    def _get_release_subs(self, game_name: str) -> list:
        """获取某游戏的所有发售订阅列表"""
        return self.subscriptions["release_subscriptions"].get(game_name, [])

    def _get_update_subs(self, game_name: str) -> list:
        """获取某游戏的所有更新订阅列表"""
        return self.subscriptions["update_subscriptions"].get(game_name, [])

    def _is_user_subscribed_release(self, game_name: str, user_id: str, group_id: str) -> bool:
        """检查用户是否已订阅某游戏的发售提醒"""
        return any(
            sub["user_id"] == user_id and sub["group_id"] == group_id
            for sub in self._get_release_subs(game_name)
        )

    def _is_user_subscribed_update(self, game_name: str, user_id: str, group_id: str) -> bool:
        """检查用户是否已订阅某游戏的更新提醒"""
        return any(
            sub["user_id"] == user_id and sub["group_id"] == group_id
            for sub in self._get_update_subs(game_name)
        )

    def _add_release_sub(self, game_name: str, user_id: str, group_id: str, umo: str):
        """添加一条发售订阅"""
        if game_name not in self.subscriptions["release_subscriptions"]:
            self.subscriptions["release_subscriptions"][game_name] = []
        self.subscriptions["release_subscriptions"][game_name].append(
            {
                "user_id": user_id,
                "group_id": group_id,
                "unified_msg_origin": umo,
                "subscribe_date": datetime.now().strftime("%Y-%m-%d"),
            }
        )
        self._save_data()

    def _add_update_sub(self, game_name: str, user_id: str, group_id: str, umo: str):
        """添加一条更新订阅"""
        if game_name not in self.subscriptions["update_subscriptions"]:
            self.subscriptions["update_subscriptions"][game_name] = []
        self.subscriptions["update_subscriptions"][game_name].append(
            {
                "user_id": user_id,
                "group_id": group_id,
                "unified_msg_origin": umo,
                "subscribe_date": datetime.now().strftime("%Y-%m-%d"),
            }
        )
        self._save_data()

    def _remove_all_subs_for_game(self, game_name: str) -> bool:
        """移除某游戏的所有订阅（发售 + 更新），返回是否有移除"""
        removed = False
        if game_name in self.subscriptions["release_subscriptions"]:
            del self.subscriptions["release_subscriptions"][game_name]
            removed = True
        if game_name in self.subscriptions["update_subscriptions"]:
            del self.subscriptions["update_subscriptions"][game_name]
            removed = True
        if removed:
            self._save_data()
        return removed

    def _cleanup_released_games(self, today_str: str):
        """清理已过发售日的游戏发售订阅（更新订阅不清理）"""
        to_remove = []
        for game_name, subs in self.subscriptions["release_subscriptions"].items():
            for sub in subs:
                reminder = sub.get("last_reminder")
                if reminder and reminder.get("release_date"):
                    try:
                        if datetime.strptime(
                            reminder["release_date"], "%Y-%m-%d"
                        ).date() <= datetime.strptime(today_str, "%Y-%m-%d").date():
                            to_remove.append(game_name)
                            break
                    except ValueError:
                        pass
        for game_name in set(to_remove):
            del self.subscriptions["release_subscriptions"][game_name]
            logger.info(f"[GameSub] 自动清理已过发售日的订阅: {game_name}")
        if to_remove:
            self._save_data()

    # ==================================================================
    # 指令处理器
    # ==================================================================

    # ------------------------------------------------------------------
    # /订阅游戏 <游戏名> [延时秒数]
    # ------------------------------------------------------------------
    @filter.command("订阅游戏")
    async def subscribe_game(
        self, event: AstrMessageEvent, game_name: str, delay: int = 0
    ):
        """订阅游戏发售日期提醒，可选传入延时秒数进行测试"""
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("⚠️ 请在QQ群内使用此指令")
            return

        umo = event.unified_msg_origin

        # 测试模式：带延时，不加入订阅列表，直接执行检索
        if delay > 0:
            yield event.plain_result(
                f"🧪 测试模式：正在检索《{game_name}》...\n"
                f"⏱️ 将在 {delay} 秒后输出结果"
            )
            asyncio.create_task(
                self._delayed_test(game_name, "release", umo, user_id, delay, event)
            )
            return

        # 防重复订阅
        if self._is_user_subscribed_release(game_name, user_id, group_id):
            yield event.plain_result(
                f"⚠️ 你已经在本群订阅了《{game_name}》的发售提醒"
            )
            return

        # 写入订阅
        self._add_release_sub(game_name, user_id, group_id, umo)
        logger.info(
            f"[GameSub] 用户 {user_id} 在群 {group_id} 订阅了游戏发售: {game_name}"
        )

        yield event.plain_result(
            f"✅ 已订阅《{game_name}》的发售提醒！\n"
            f"📅 将在发售前 {', '.join(str(d) for d in self.reminder_days if d > 0)} 天"
            f"及发售当天提醒您"
        )

    # ------------------------------------------------------------------
    # /订阅更新 <游戏名>
    # ------------------------------------------------------------------
    @filter.command("订阅更新")
    async def subscribe_update(self, event: AstrMessageEvent, game_name: str):
        """订阅游戏版本更新提醒"""
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("⚠️ 请在QQ群内使用此指令")
            return

        umo = event.unified_msg_origin

        if self._is_user_subscribed_update(game_name, user_id, group_id):
            yield event.plain_result(
                f"⚠️ 你已经在本群订阅了《{game_name}》的更新提醒"
            )
            return

        self._add_update_sub(game_name, user_id, group_id, umo)
        logger.info(
            f"[GameSub] 用户 {user_id} 在群 {group_id} 订阅了游戏更新: {game_name}"
        )
        yield event.plain_result(
            f"✅ 已订阅《{game_name}》的更新提醒！\n📅 游戏更新当天将自动提醒您"
        )

    # ------------------------------------------------------------------
    # /订阅列表
    # ------------------------------------------------------------------
    @filter.command("游戏订阅列表")
    async def list_subscriptions(self, event: AstrMessageEvent):
        """查看当前群的游戏订阅列表（按发售/更新分类）"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("⚠️ 请在QQ群内使用此指令")
            return

        # 收集本群的发售订阅
        release_subs = []
        for game_name, subs in self.subscriptions["release_subscriptions"].items():
            group_subs = [s for s in subs if s["group_id"] == group_id]
            if group_subs:
                release_subs.append(
                    {"game_name": game_name, "count": len(group_subs)}
                )

        # 收集本群的更新订阅
        update_subs = []
        for game_name, subs in self.subscriptions["update_subscriptions"].items():
            group_subs = [s for s in subs if s["group_id"] == group_id]
            if group_subs:
                update_subs.append(
                    {"game_name": game_name, "count": len(group_subs)}
                )

        if not release_subs and not update_subs:
            yield event.plain_result(
                "📋 当前群暂无任何游戏订阅\n"
                "使用 /订阅游戏 游戏名 来订阅游戏发售提醒\n"
                "使用 /订阅更新 游戏名 来订阅游戏更新提醒"
            )
            return

        # 优先使用图片渲染，失败时降级为纯文本
        try:
            url = await self.html_render(
                SUBSCRIPTION_LIST_HTML,
                {
                    "release_subs": release_subs,
                    "update_subs": update_subs,
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                },
            )
            yield event.image_result(url)
        except Exception as exc:
            logger.warning(f"[GameSub] 图片渲染失败，降级为纯文本: {exc}")
            lines = ["📋 游戏订阅列表\n"]
            lines.append("🕹️ === 游戏发售订阅 ===")
            if release_subs:
                for g in release_subs:
                    lines.append(f"  · {g['game_name']}（{g['count']}人订阅）")
            else:
                lines.append("  暂无")
            lines.append("\n🔄 === 游戏更新订阅 ===")
            if update_subs:
                for g in update_subs:
                    lines.append(f"  · {g['game_name']}（{g['count']}人订阅）")
            else:
                lines.append("  暂无")
            yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # /移除订阅 <游戏名>
    # ------------------------------------------------------------------
    @filter.command("移除订阅")
    async def remove_subscription(self, event: AstrMessageEvent, game_name: str):
        """移除特定游戏的所有订阅（发售+更新）"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("⚠️ 请在QQ群内使用此指令")
            return

        # 只移除本群的订阅
        removed_count = 0

        # 移除本群的发售订阅
        if game_name in self.subscriptions["release_subscriptions"]:
            before = len(self.subscriptions["release_subscriptions"][game_name])
            self.subscriptions["release_subscriptions"][game_name] = [
                s
                for s in self.subscriptions["release_subscriptions"][game_name]
                if s["group_id"] != group_id
            ]
            after = len(self.subscriptions["release_subscriptions"][game_name])
            removed_count += before - after
            if after == 0:
                del self.subscriptions["release_subscriptions"][game_name]

        # 移除本群的更新订阅
        if game_name in self.subscriptions["update_subscriptions"]:
            before = len(self.subscriptions["update_subscriptions"][game_name])
            self.subscriptions["update_subscriptions"][game_name] = [
                s
                for s in self.subscriptions["update_subscriptions"][game_name]
                if s["group_id"] != group_id
            ]
            after = len(self.subscriptions["update_subscriptions"][game_name])
            removed_count += before - after
            if after == 0:
                del self.subscriptions["update_subscriptions"][game_name]

        if removed_count > 0:
            self._save_data()
            logger.info(
                f"[GameSub] 群 {group_id} 移除了 {removed_count} 条《{game_name}》订阅"
            )
            yield event.plain_result(
                f"✅ 已移除本群对《{game_name}》的所有订阅（共 {removed_count} 条）"
            )
        else:
            yield event.plain_result(f"⚠️ 本群没有对《{game_name}》的任何订阅")

    # ==================================================================
    # 延时测试
    # ==================================================================
    async def _delayed_test(
        self,
        game_name: str,
        sub_type: str,
        umo: str,
        user_id: str,
        delay: int,
        event: AstrMessageEvent = None,
    ):
        """延时后执行一次检索并输出结果（用于测试）"""
        await asyncio.sleep(delay)
        try:
            today = datetime.now()
            today_str = today.strftime("%Y-%m-%d")

            if sub_type == "release":
                result = await self._batch_search_release([game_name], umo, event)
                if game_name in result:
                    info = result[game_name]
                    release_date_str = info.get("release_date", "未知")
                    msg = (
                        f"🧪 测试检索结果\n\n"
                        f"🎮 {game_name}\n"
                        f"📅 预计发售日期: {release_date_str}"
                    )
                    if info.get("status"):
                        msg += f"\n📌 状态: {info['status']}"
                    # 计算距今天数
                    try:
                        release_date = datetime.strptime(
                            release_date_str, "%Y-%m-%d"
                        ).date()
                        diff = (release_date - today.date()).days
                        if diff > 0:
                            msg += f"\n⏳ 距发售还有 {diff} 天"
                        elif diff == 0:
                            msg += "\n🎉 今天就是发售日！"
                        else:
                            msg += f"\n📦 已于 {abs(diff)} 天前发售"
                    except ValueError:
                        pass
                else:
                    msg = (
                        f"🧪 测试检索结果\n\n"
                        f"🎮 {game_name}\n"
                        f"❌ 未能获取到该游戏的发售日期信息"
                    )
            else:
                result = await self._batch_search_update([game_name], umo, event)
                if game_name in result:
                    info = result[game_name]
                    update_date_str = info.get("update_date", "未知")
                    msg = (
                        f"🧪 测试检索结果\n\n"
                        f"🎮 {game_name}\n"
                        f"📅 最近更新日期: {update_date_str}"
                    )
                    if info.get("version"):
                        msg += f"\n📌 最新版本: {info['version']}"
                else:
                    msg = (
                        f"🧪 测试检索结果\n\n"
                        f"🎮 {game_name}\n"
                        f"❌ 未能获取到该游戏的更新信息"
                    )

            message_chain = MessageChain()
            message_chain.chain = [Comp.At(qq=user_id), Comp.Plain(f" {msg}")]
            await self.context.send_message(umo, message_chain)

        except Exception as exc:
            logger.error(f"[GameSub] 延时测试执行失败: {exc}")
            try:
                message_chain = MessageChain()
                message_chain.chain = [
                    Comp.At(qq=user_id),
                    Comp.Plain(f" 🧪 测试执行失败: {exc}"),
                ]
                await self.context.send_message(umo, message_chain)
            except Exception:
                pass

    # ==================================================================
    # 每日定时调度
    # ==================================================================
    async def _schedule_loop(self):
        """每日定时任务主循环"""
        # 等待 AstrBot 完全初始化
        await asyncio.sleep(15)
        logger.info("[GameSub] 每日定时检查任务已启动")

        while True:
            try:
                now = datetime.now()
                check_hour = int(
                    self.config.get("check_time", {}).get("hour", 9)
                )
                check_minute = int(
                    self.config.get("check_time", {}).get("minute", 0)
                )

                # 计算下一个执行时间
                target = now.replace(
                    hour=check_hour, minute=check_minute, second=0, microsecond=0
                )
                if now >= target:
                    target += timedelta(days=1)

                wait_seconds = (target - now).total_seconds()
                logger.info(
                    f"[GameSub] 下次每日检查将在 {target.strftime('%Y-%m-%d %H:%M')} 执行"
                    f"（{wait_seconds:.0f}秒后）"
                )

                await asyncio.sleep(wait_seconds)

                # 执行每日检查
                logger.info("[GameSub] 开始执行每日检查...")
                await self._daily_check()

                # 执行完毕后等待 60 秒，防止同一时间重复触发
                await asyncio.sleep(60)

            except Exception as exc:
                logger.error(f"[GameSub] 定时任务循环异常: {exc}")
                await asyncio.sleep(300)  # 异常后等待 5 分钟再重试

    # ------------------------------------------------------------------
    # 每日检查核心逻辑
    # ------------------------------------------------------------------
    async def _daily_check(self):
        """每日检查：批量检索所有订阅游戏并发送提醒"""
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")

        # ---- 1. 处理发售订阅 ----
        release_game_names = list(
            self.subscriptions["release_subscriptions"].keys()
        )
        if release_game_names:
            logger.info(
                f"[GameSub] 批量检索 {len(release_game_names)} 个游戏的发售日期"
            )
            release_info = await self._batch_search_release(release_game_names)

            # 构建提醒列表 & 记录 last_reminder
            release_reminders = self._build_release_reminders(
                release_info, today
            )

            # 发送提醒通知
            await self._send_release_notifications(release_reminders)

            # 发售当天及之后自动清理
            self._auto_cleanup_release(release_reminders, today)

        # ---- 2. 处理更新订阅 ----
        update_game_names = list(
            self.subscriptions["update_subscriptions"].keys()
        )
        if update_game_names:
            logger.info(
                f"[GameSub] 批量检索 {len(update_game_names)} 个游戏的更新日期"
            )
            update_info = await self._batch_search_update(update_game_names)

            update_reminders = self._build_update_reminders(
                update_info, today_str
            )
            await self._send_update_notifications(update_reminders)

        logger.info("[GameSub] 每日检查完成")

    # ------------------------------------------------------------------
    # LLM 批量检索
    # ------------------------------------------------------------------
    async def _get_provider(self, umo: str = None):
        """获取 LLM 提供商

        - 若配置文件指定了 search_provider_id，优先使用该提供商
        - 若传入 umo，使用该会话的当前提供商
        - 否则从所有提供商中取第一个可用
        """
        provider_id = self.config.get("search_provider_id", "")
        if provider_id:
            return self.context.get_provider_by_id(provider_id)
        if umo:
            return self.context.get_using_provider(umo=umo)
        # 无 umo 时：遍历所有提供商取第一个
        all_providers = self.context.get_all_providers()
        if all_providers:
            return all_providers[0]
        return None

    async def _web_search(self, query: str, event: AstrMessageEvent = None) -> str:
        """使用 AstrBot 内置的 Web Search 工具搜索信息

        通过 LLM Tool Manager 获取已注册的 web_search 工具并调用。
        """
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            if not tool_mgr:
                return ""

            # 尝试多种可能的搜索工具名称
            search_tool = None
            for name in ["web_search_tavily", "web_search", "search_internet", "web_search_tool"]:
                search_tool = tool_mgr.get_func(name)
                if search_tool:
                    logger.info(f"[GameSub] 使用搜索工具: {name}")
                    break

            if not search_tool:
                logger.debug("[GameSub] 未找到可用的 Web Search 工具")
                return ""

            # 兼容不同的工具调用接口：遍历所有可能的调用方法名，对每个方法尝试多种调用风格
            logger.info(f"[GameSub] 使用搜索工具: {type(search_tool).__name__}")
            # 打印工具的所有可调用方法，方便排查
            tool_methods = [
                m for m in dir(search_tool)
                if not m.startswith('_') and callable(getattr(search_tool, m))
            ]
            logger.debug(f"[GameSub] 搜索工具的可调用方法: {tool_methods}")

            result = None

            for method_name in ['run', 'call', 'execute', 'invoke', 'search', 'query']:
                method = getattr(search_tool, method_name, None)
                if not method or not callable(method):
                    continue

                if result is None:
                    try:
                        result = await method(query=query)
                    except Exception as e:
                        logger.debug(f"[GameSub] 方法 {method_name}(query=query) 失败: {e}")
                if result is None and event:
                    try:
                        result = await method(event, query=query)
                    except Exception as e:
                        logger.debug(f"[GameSub] 方法 {method_name}(event, query=query) 失败: {e}")
                if result is None:
                    try:
                        result = await method({'query': query})
                    except Exception as e:
                        logger.debug(f"[GameSub] 方法 {method_name}({{\'query\': query}}) 失败: {e}")
                if result is not None:
                    logger.info(f"[GameSub] 搜索工具调用成功 (方法: {method_name})")
                    break

            # 如果上面的遍历都失败了，尝试直接作为 callable 调用
            if result is None and callable(search_tool):
                logger.debug(f"[GameSub] 尝试直接 callable 调用")
                for desc, call_args in [
                    ("search_tool(query=query)", lambda: search_tool(query=query)),
                    ("search_tool(event, query)", lambda: search_tool(event, query=query) if event else None),
                    ("search_tool({{'query': query}})", lambda: search_tool({'query': query})),
                ]:
                    try:
                        r = call_args()
                        if asyncio.iscoroutine(r):
                            r = await r
                        if r is not None:
                            logger.info(f"[GameSub] callable 调用成功: {desc}")
                            result = r
                            break
                    except Exception as e:
                        logger.debug(f"[GameSub] callable {desc} 失败: {e}")
                        continue

            if result is None:
                # 最后的兜底：将工具加入 func_tool 让 LLM 调用
                logger.info(f"[GameSub] 直接调用失败，尝试通过 LLM 调用搜索工具")
                from astrbot.core.agent.tool import ToolSet
                provider = await self._get_provider(event.unified_msg_origin if event else None)
                if provider:
                    ts = ToolSet()
                    ts.add_tool(search_tool)
                    try:
                        llm_resp = await provider.text_chat(
                            prompt=f"请搜索：{query}",
                            func_tool=ts,
                        )
                        result = llm_resp.completion_text if llm_resp else None
                    except Exception:
                        pass

            # 处理不同类型的返回值
            if result is None:
                return ""
            if hasattr(result, 'content'):
                content = result.content
                if isinstance(content, list):
                    return "\n".join(
                        str(c.text) for c in content if hasattr(c, 'text')
                    )
                return str(content)
            return str(result)
        except Exception as exc:
            logger.warning(f"[GameSub] Web Search 查询失败 [{query}]: {exc}")
            return ""

    async def _tavily_search(self, query: str) -> str:
        """使用配置的 Tavily API Key 直接调用 Tavily 搜索

        不依赖 AstrBot 内置搜索工具，插件自行管理搜索配置。
        """
        api_key = self.config.get("tavily_api_key", "")
        if not api_key:
            logger.debug("[GameSub] 未配置 tavily_api_key，跳过 Tavily 搜索")
            return ""

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 5,
                }
                async with session.post(
                    "https://api.tavily.com/search",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"[GameSub] Tavily 搜索失败 [{query}]: HTTP {resp.status}"
                        )
                        return ""
                    data = await resp.json()

            # 拼接搜索结果
            parts = []
            if data.get("answer"):
                parts.append(data["answer"])
            for r in data.get("results", []):
                title = r.get("title", "")
                content = r.get("content", "")
                if title:
                    parts.append(f"{title}: {content}")
                elif content:
                    parts.append(content)
            result = "\n".join(parts)
            logger.info(f"[GameSub] Tavily 搜索成功 [{query}]: 获取到 {len(result)} 字符")
            return result[:2000]  # 限制长度
        except Exception as exc:
            logger.warning(f"[GameSub] Tavily 搜索异常 [{query}]: {exc}")
            return ""

    async def _search_llm_generate(self, prompt: str) -> str:
        """使用独立的搜索 LLM 配置（OpenAI 兼容格式）生成文本

        如果配置了 search_llm_api_key/url/model，则使用独立的 LLM；
        否则返回空字符串，让调用方回退到 AstrBot 默认 LLM。
        """
        api_key = self.config.get("search_llm_api_key", "")
        base_url = self.config.get("search_llm_url", "")
        model = self.config.get("search_llm_model", "")

        if not api_key or not base_url or not model:
            logger.debug("[GameSub] 未配置完整的独立 LLM 参数，跳过")
            return ""

        url = f"{base_url.rstrip('/')}/chat/completions"
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是一个游戏信息助手，负责根据搜索结果提取游戏的发售日期或版本更新信息。请严格按照要求的格式返回。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                }
                async with session.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        logger.warning(
                            f"[GameSub] 搜索 LLM 请求失败: HTTP {resp.status} - {err_text[:200]}"
                        )
                        return ""
                    data = await resp.json()

            text = data["choices"][0]["message"]["content"]
            return text.strip()
        except Exception as exc:
            logger.warning(f"[GameSub] 搜索 LLM 生成异常: {exc}")
            return ""

    async def _batch_search_release(self, game_names: list, umo: str = None,
                                    event: AstrMessageEvent = None) -> dict:
        """批量查询游戏预计发售日期

        将所有游戏合并为一次 LLM 请求，减少 API 调用次数。

        Returns:
            {游戏名: {"release_date": "YYYY-MM-DD", "status": "..."}}
        """
        provider = await self._get_provider(umo)
        if not provider:
            logger.warning("[GameSub] 未找到可用的 LLM 提供商，跳过发售检索")
            return {}

        games_str = "、".join(game_names)

        # 第一步：搜索每个游戏的实际发售信息（优先使用 Tavily，兜底用 AstrBot 内置搜索）
        search_results = {}
        tavily_configured = bool(self.config.get("tavily_api_key", ""))

        for name in game_names:
            query = f"{name} 游戏 发售日期 发行日期"
            result_text = ""
            if tavily_configured:
                result_text = await self._tavily_search(query)
            if not result_text:
                # 兜底：尝试 AstrBot 内置的 Web Search 工具
                result_text = await self._web_search(query, event)
            if result_text:
                search_results[name] = result_text[:500]

        # 第二步：构建提取 prompt
        prompt_parts = [
            "请根据以下信息，提取游戏的发售日期。",
        ]
        if search_results:
            prompt_parts.append(
                "\n以下是网络搜索到的相关信息（优先使用这些信息）："
            )
            for name, ctx in search_results.items():
                prompt_parts.append(f"\n【{name}】\n{ctx}")
        prompt_parts.append(
            f"\n\n请从以上信息中提取以下游戏的预计发售日期（或实际发售日期）。"
            f"\n游戏列表：{games_str}"
            f"\n要求："
            f"\n1. 请仅返回纯 JSON 格式数据，不要包含任何其他文字、代码块标记。"
            f"\n2. 日期统一使用 YYYY-MM-DD 格式；如果只有年月则用该月最后一天代替。"
            f"\n3. status 可以是 \"已发售\"、\"未发售\"、\"未知\"。"
            f"\n4. 如果查不到某个游戏，release_date 填 \"未知\"。"
            f"\n\n返回格式示例："
            f'\n{{"游戏名": {{"release_date": "2025-06-15", "status": "未发售"}}}}'
        )
        prompt = "".join(prompt_parts)

        # 第三步：调用 LLM 提取结构化数据
        text = await self._search_llm_generate(prompt)
        if text:
            # 独立 LLM 成功
            logger.info("[GameSub] 使用独立配置的 LLM 提取发售信息成功")
        else:
            # 兜底：使用 AstrBot 默认 LLM 提供商
            logger.info("[GameSub] 使用 AstrBot 默认 LLM 提供商提取发售信息")
            try:
                resp = await provider.text_chat(prompt=prompt)
                text = resp.completion_text.strip()
            except Exception as exc:
                logger.error(f"[GameSub] AstrBot LLM 提取发售信息失败: {exc}")
                return {}

        # 第四步：解析 JSON
        try:
            if text.startswith("```"):
                text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error(
                f"[GameSub] LLM 返回的发售信息无法解析为 JSON:\n{text[:500]}"
            )
        except Exception as exc:
            logger.error(f"[GameSub] 批量查询发售日期失败: {exc}")
        return {}

    async def _batch_search_update(self, game_names: list, umo: str = None,
                                     event: AstrMessageEvent = None) -> dict:
        """批量查询游戏最近版本更新日期

        Returns:
            {游戏名: {"update_date": "YYYY-MM-DD", "version": "..."}}
        """
        provider = await self._get_provider(umo)
        if not provider:
            logger.warning("[GameSub] 未找到可用的 LLM 提供商，跳过更新检索")
            return {}

        games_str = "、".join(game_names)

        # 第一步：搜索（优先 Tavily，兜底 AstrBot 内置搜索）
        search_results = {}
        tavily_configured = bool(self.config.get("tavily_api_key", ""))

        for name in game_names:
            query = f"{name} 游戏 版本更新 最新版本"
            result_text = ""
            if tavily_configured:
                result_text = await self._tavily_search(query)
            if not result_text:
                result_text = await self._web_search(query, event)
            if result_text:
                search_results[name] = result_text[:500]

        prompt_parts = [
            "请根据以下信息，提取游戏的最近版本更新日期和版本号。",
        ]
        if search_results:
            prompt_parts.append("\n以下是网络搜索到的相关信息（优先使用这些信息）：")
            for name, ctx in search_results.items():
                prompt_parts.append(f"\n【{name}】\n{ctx}")
        prompt_parts.append(
            f"\n\n请从以上信息中提取以下游戏的最近一次版本更新日期和版本号。"
            f"\n游戏列表：{games_str}"
            f"\n要求："
            f"\n1. 请仅返回纯 JSON 格式数据，不要包含任何其他文字、代码块标记。"
            f"\n2. 日期使用 YYYY-MM-DD 格式。"
            f"\n3. 如果查不到，update_date 填 \"未知\"。"
            f"\n\n返回格式示例："
            f'\n{{"游戏名": {{"update_date": "2025-06-01", "version": "v1.5.0"}}}}'
        )
        prompt = "".join(prompt_parts)

        # 第二步：调用 LLM 提取（优先独立 LLM）
        text = await self._search_llm_generate(prompt)
        if text:
            logger.info("[GameSub] 使用独立配置的 LLM 提取更新信息成功")
        else:
            logger.info("[GameSub] 使用 AstrBot 默认 LLM 提供商提取更新信息")
            try:
                resp = await provider.text_chat(prompt=prompt)
                text = resp.completion_text.strip()
            except Exception as exc:
                logger.error(f"[GameSub] AstrBot LLM 提取更新信息失败: {exc}")
                return {}

        # 第三步：解析 JSON
        try:
            if text.startswith("```"):
                text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error(
                f"[GameSub] LLM 返回的更新信息无法解析为 JSON:\n{text[:500]}"
            )
        except Exception as exc:
            logger.error(f"[GameSub] 批量查询更新日期失败: {exc}")
        return {}

    # ------------------------------------------------------------------
    # 提醒构建
    # ------------------------------------------------------------------
    def _build_release_reminders(self, release_info: dict, today: datetime) -> list:
        """根据检索结果构建发售提醒列表

        Returns:
            [{game_name, release_date_str, days_until, subs}]
        """
        reminders = []
        for game_name, info in release_info.items():
            release_date_str = info.get("release_date", "未知")
            if release_date_str == "未知":
                continue

            try:
                release_date = datetime.strptime(release_date_str, "%Y-%m-%d").date()
                days_until = (release_date - today.date()).days

                if days_until in self.reminder_days:
                    subs = self.subscriptions["release_subscriptions"].get(
                        game_name, []
                    )
                    if subs:
                        reminders.append(
                            {
                                "game_name": game_name,
                                "release_date_str": release_date_str,
                                "days_until": days_until,
                                "subs": subs,
                            }
                        )

                # 记录 last_reminder，用于后续自动清理
                for sub in self.subscriptions["release_subscriptions"].get(
                    game_name, []
                ):
                    sub["last_reminder"] = {
                        "release_date": release_date_str,
                        "check_date": today.strftime("%Y-%m-%d"),
                    }
            except ValueError:
                logger.warning(
                    f"[GameSub] 无法解析 {game_name} 的发售日期: {release_date_str}"
                )
        return reminders

    def _build_update_reminders(self, update_info: dict, today_str: str) -> list:
        """根据检索结果构建更新提醒列表

        Returns:
            [{game_name, update_date_str, version, subs}]
        """
        reminders = []
        for game_name, info in update_info.items():
            update_date_str = info.get("update_date", "未知")
            if update_date_str == today_str:
                subs = self.subscriptions["update_subscriptions"].get(game_name, [])
                if subs:
                    reminders.append(
                        {
                            "game_name": game_name,
                            "update_date_str": update_date_str,
                            "version": info.get("version", ""),
                            "subs": subs,
                        }
                    )
        return reminders

    # ------------------------------------------------------------------
    # 通知发送
    # ------------------------------------------------------------------
    async def _send_release_notifications(self, reminders: list):
        """发送发售提醒通知（按 unified_msg_origin 聚合，一次性通知）"""
        # 按 umo 聚合提醒
        grouped: dict = {}
        for reminder in reminders:
            for sub in reminder["subs"]:
                umo = sub["unified_msg_origin"]
                user_id = sub["user_id"]
                if umo not in grouped:
                    grouped[umo] = []
                grouped[umo].append(
                    {
                        "user_id": user_id,
                        "game_name": reminder["game_name"],
                        "days_until": reminder["days_until"],
                        "release_date_str": reminder["release_date_str"],
                    }
                )

        for umo, items in grouped.items():
            try:
                chain = []
                for item in items:
                    days = item["days_until"]
                    if days == 0:
                        day_text = "🎉 今天发售！"
                    elif days > 0:
                        day_text = f"⏳ 还有 {days} 天"
                    else:
                        continue

                    chain.append(Comp.At(qq=item["user_id"]))
                    chain.append(
                        Comp.Plain(
                            f" 🎮《{item['game_name']}》{day_text}"
                            f"（发售日期: {item['release_date_str']}）\n"
                        )
                    )

                if chain:
                    header = [Comp.Plain("📢 游戏发售提醒\n")]
                    message_chain = MessageChain()
                    message_chain.chain = header + chain
                    await self.context.send_message(umo, message_chain)
                    logger.info(
                        f"[GameSub] 已发送 {len(items)} 条发售提醒到 {umo}"
                    )
            except Exception as exc:
                logger.error(f"[GameSub] 发送发售提醒失败 ({umo}): {exc}")

    async def _send_update_notifications(self, reminders: list):
        """发送更新提醒通知（按 unified_msg_origin 聚合，一次性通知）"""
        grouped: dict = {}
        for reminder in reminders:
            for sub in reminder["subs"]:
                umo = sub["unified_msg_origin"]
                user_id = sub["user_id"]
                if umo not in grouped:
                    grouped[umo] = []
                grouped[umo].append(
                    {
                        "user_id": user_id,
                        "game_name": reminder["game_name"],
                        "version": reminder.get("version", ""),
                    }
                )

        for umo, items in grouped.items():
            try:
                chain = []
                for item in items:
                    chain.append(Comp.At(qq=item["user_id"]))
                    version_text = (
                        f"（版本: {item['version']}）" if item["version"] else ""
                    )
                    chain.append(
                        Comp.Plain(
                            f" 🔄《{item['game_name']}》今日有更新！{version_text}\n"
                        )
                    )

                if chain:
                    header = [Comp.Plain("📢 游戏更新提醒\n")]
                    message_chain = MessageChain()
                    message_chain.chain = header + chain
                    await self.context.send_message(umo, message_chain)
                    logger.info(
                        f"[GameSub] 已发送 {len(items)} 条更新提醒到 {umo}"
                    )
            except Exception as exc:
                logger.error(f"[GameSub] 发送更新提醒失败 ({umo}): {exc}")

    # ------------------------------------------------------------------
    # 自动清理
    # ------------------------------------------------------------------
    def _auto_cleanup_release(self, reminders: list, today: datetime):
        """发售当天过后自动清理发售订阅（更新订阅不受影响）"""
        to_cleanup = set()
        for reminder in reminders:
            if reminder["days_until"] == 0:
                to_cleanup.add(reminder["game_name"])

        for game_name in to_cleanup:
            if game_name in self.subscriptions["release_subscriptions"]:
                del self.subscriptions["release_subscriptions"][game_name]
                logger.info(
                    f"[GameSub] 自动清理已过发售日的订阅: {game_name}"
                )

        if to_cleanup:
            self._save_data()

    # ------------------------------------------------------------------
    # 插件终止
    # ------------------------------------------------------------------
    async def terminate(self):
        """插件卸载/停用时保存数据"""
        self._save_data()
        logger.info("[GameSub] 插件已终止，订阅数据已保存")
