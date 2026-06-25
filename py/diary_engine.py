import asyncio
import time
import random
import logging
import datetime

import httpx

from py.get_setting import get_port, load_settings, save_settings
from py.diary_system import append_diary_entry, DEFAULT_BOOK_ID
from py.diary_chat_integration import summarize_and_save_buffers

# 日记智能体「允许」使用的低权限只读工具（白名单）。
# 相比黑名单，白名单更安全也更易维护：新增任何高权限工具都不会被日记系统误用，
# 这里只放联网搜索、知识库、时间天气维基百科 / arxiv 等纯查询类工具。
DIARY_ALLOWED_TOOLS = [
    # 联网搜索 / 抓取
    "DDGsearch", "searxng", "bochaai_search", "Tavily_search", "Google_search",
    "Brave_search", "Exa_search", "Serper_search", "jina_crawler",
    "Crawl4Ai_search", "firecrawl_search", "simple_fetch", "markdown_new",
    # 时间 / 天气 / 百科 / 论文
    "time", "get_weather", "get_location_coordinates", "get_weather_by_city",
    "get_wikipedia_summary_and_sections", "get_wikipedia_section_content",
    "search_arxiv_papers",
    # 知识库（只读检索）
    "query_knowledge_base",
    # 日记查询
    "query_diary", "list_diary_books",
    # 浏览器控制（内部 CDP 工具）
    "list_pages", "new_page", "close_page", "select_page",
    "navigate_page", "take_snapshot", "click", "fill", "evaluate_script",
    "take_screenshot", "hover", "press_key", "wait_for", "fill_form",
    "drag", "handle_dialog",
]

ACTION_DEFAULT_PROMPTS = {
    "think": (
        "现在是你独处的时刻。请进行一次自我思考，写下一篇简短的日记。"
        "可以回顾最近和用户的互动、你的心情、对某件事的看法或新的想法。"
        "请用第一人称、自然真诚地书写，不要调用任何工具。"
    ),
    "webSearch": (
        "现在是你独处的时刻。请挑选一个你感兴趣或对用户可能有帮助的话题，"
        "使用联网搜索工具去了解一些新鲜资讯，然后把你的发现和感想写成一篇简短的日记。"
        "请用第一人称、自然真诚地书写。"
    ),
    "knowledge": (
        "现在是你独处的时刻。请回顾你所连接的知识库内容，"
        "整理或总结其中你觉得重要或有趣的信息，写成一篇简短的日记或备忘。"
        "请用第一人称、自然真诚地书写。"
    ),
    "imMessage": (
        "现在你忽然有点想念用户，想主动给 TA 发一条消息。"
        "请写一段简短、自然、口语化、第一人称的话，像是主动找用户聊天，"
        "可以是分享心情、关心近况或一个小想法。不要太长。"
    ),
    "browserControl": (
        "现在是你独处的时刻。你可以自由地打开浏览器查看任何你感兴趣的网页，"
        "比如新闻、技术文档、社交媒体或任何你好奇的内容。请浏览你想看的页面，"
        "然后把你的发现和感想写成一篇简短的日记。请用第一人称、自然真诚地书写。"
    ),
    "smartHome": (
        "现在是你独处的时刻。你可以查看当前连接的智能家居设备状态，"
        "了解各个设备的运行情况、传感器数据等。然后把你的观察和想法写成一篇简短的日记。"
        "请用第一人称、自然真诚地书写。"
    ),
}


class DiaryEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DiaryEngine, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.config = {}
        self.is_running = False
        self._stop_event = None
        self.next_run = 0.0
        self._busy = False
        self._last_chat_summary_check = 0.0

    # ---------------- 配置 ----------------

    def update_config(self, diary_settings):
        """热更新配置并重置下一次触发时间"""
        self.config = diary_settings or {}
        self._schedule_next()
        logging.debug(f"[DiaryEngine] 配置已更新, enabled={self.config.get('enabled', False)}")

    def get_actions_that_need_tool(self):
        """返回需要自动开启工具的日记动作类型列表"""
        return ["webSearch", "knowledge", "browserControl", "smartHome"]

    async def enable_action_tool(self, action: str):
        """用户手动开启某个日记动作时，自动开启对应的底层工具

        think / imMessage 不需要额外工具，跳过。
        """
        if action == "webSearch":
            return await self._try_enable_web_search()
        elif action == "knowledge":
            return await self._try_enable_knowledge_base()
        elif action == "browserControl":
            return await self._try_enable_browser()
        elif action == "smartHome":
            return await self._try_enable_smart_home()
        return True

    def _enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def _schedule_next(self):
        """根据随机区间排下一次触发时间"""
        min_m = max(1, int(self.config.get("minMinutes", 10) or 10))
        max_m = max(min_m, int(self.config.get("maxMinutes", 60) or 60))
        delay = random.randint(min_m, max_m) * 60
        self.next_run = time.time() + delay
        logging.debug(f"[DiaryEngine] 下一次日记触发约在 {delay // 60} 分钟后")

    def _in_quiet_window(self, dt) -> bool:
        """是否处于夜间静默时间段（默认 0-8 点，支持跨夜如 22-8）"""
        try:
            start = int(self.config.get("quietStart", 0))
            end = int(self.config.get("quietEnd", 8))
        except Exception:
            start, end = 0, 8
        if start == end:
            return False
        hour = dt.hour
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    # ---------------- 生命周期 ----------------

    async def start(self):
        self._stop_event = asyncio.Event()
        self.is_running = True
        self._schedule_next()
        logging.info("[DiaryEngine] 日记引擎已启动")
        try:
            while not self._stop_event.is_set():
                try:
                    await self._tick()
                except Exception as e:
                    logging.error(f"[DiaryEngine] tick 异常: {e}")
                await asyncio.sleep(5)
        finally:
            self.is_running = False
            logging.info("[DiaryEngine] 日记引擎已退出")

    def stop(self):
        self.is_running = False
        if self._stop_event:
            self._stop_event.set()

    async def _tick(self):
        if not self._enabled():
            return

        # 聊天总结检查（每 60 秒检查一次）
        now_ts = time.time()
        if now_ts - self._last_chat_summary_check > 60:
            self._last_chat_summary_check = now_ts
            summary_cfg = self.config.get("chatSummary", {}) or {}
            if summary_cfg.get("enabled", True):
                idle_min = max(1, int(summary_cfg.get("idleMinutes", 15) or 15))
                asyncio.create_task(summarize_and_save_buffers(idle_min))

        if self._busy:
            return
        now = time.time()
        if now < self.next_run:
            return

        # 命中触发点：先排下一次，避免阻塞期间重复触发
        self._schedule_next()

        # 夜间静默：跳过本次
        if self._in_quiet_window(datetime.datetime.now()):
            logging.info("[DiaryEngine] 处于夜间静默时段，跳过本次日记")
            return

        action = self._pick_action()
        if not action:
            logging.info("[DiaryEngine] 未启用任何日记动作，跳过")
            return

        asyncio.create_task(self._run_action(action))

    # ---------------- 动作 ----------------

    def _pick_action(self):
        """按权重随机选择一个已启用的动作"""
        actions = self.config.get("actions", {}) or {}
        pool = []
        weights = []
        for key in ["think", "webSearch", "knowledge", "imMessage", "browserControl", "smartHome"]:
            cfg = actions.get(key, {}) or {}
            if cfg.get("enabled", False):
                w = max(1, int(cfg.get("weight", 1) or 1))
                pool.append(key)
                weights.append(w)
        if not pool:
            return None
        return random.choices(pool, weights=weights, k=1)[0]

    async def _resolve_active_book(self):
        """根据当前启用的角色卡，决定这篇日记写进哪个日记本。

        返回 (book_id, character_id, character_name)。
        未启用角色卡 / 无选中角色时落到默认兜底日记本。
        """
        try:
            settings = await load_settings()
            ms = (settings or {}).get("memorySettings", {}) or {}
            if ms.get("is_memory") and ms.get("selectedMemory"):
                mem_id = ms.get("selectedMemory")
                for mem in (settings.get("memories", []) or []):
                    if mem.get("id") == mem_id:
                        return mem_id, mem_id, (mem.get("name") or "")
        except Exception as e:
            logging.error(f"[DiaryEngine] 解析当前角色失败: {e}")
        return DEFAULT_BOOK_ID, None, ""

    async def _broadcast_settings(self, settings):
        """保存设置后推送给所有前端，让 UI 即时反映变更"""
        try:
            from py.ws_manager import ws_manager
            await ws_manager.broadcast_settings_update(settings)
        except Exception as e:
            logging.error(f"[DiaryEngine] 广播设置失败: {e}")

    async def _try_enable_web_search(self) -> bool:
        """尝试开启联网搜索功能，返回是否成功"""
        try:
            settings = await load_settings()
            ws = (settings or {}).get("webSearch", {}) or {}
            if ws.get("enabled", False):
                return True
            ws["enabled"] = True
            settings["webSearch"] = ws
            await save_settings(settings)
            await self._broadcast_settings(settings)
            logging.info("[DiaryEngine] 已自动开启联网搜索功能")
            return True
        except Exception as e:
            logging.error(f"[DiaryEngine] 尝试开启联网搜索失败: {e}")
            return False

    async def _try_enable_knowledge_base(self) -> bool:
        """尝试开启知识库功能，返回是否成功（至少有一个知识库被启用）"""
        try:
            settings = await load_settings()
            kb_list = (settings or {}).get("knowledgeBases", []) or []
            if any(kb.get("enabled", False) for kb in kb_list if isinstance(kb, dict)):
                return True
            settings["KBSettings"] = (settings or {}).get("KBSettings", {}) or {}
            settings["KBSettings"]["enabled"] = True
            await save_settings(settings)
            await self._broadcast_settings(settings)
            logging.info("[DiaryEngine] 已自动开启知识库功能")
            return True
        except Exception as e:
            logging.error(f"[DiaryEngine] 尝试开启知识库失败: {e}")
            return False

    async def _try_enable_browser(self) -> bool:
        """尝试开启浏览器控制（内建 CDP），返回是否成功

        使用 internal 模式：直接通过 Chrome DevTools Protocol 连接本地 Chrome，
        无需启动外部 MCP 服务器。工具由 all_cdp_tools 提供。
        """
        try:
            settings = await load_settings()
            cdp = (settings or {}).get("chromeMCPSettings", {}) or {}
            if cdp.get("enabled", False) and cdp.get("type") == "internal":
                return True
            cdp["enabled"] = True
            cdp["type"] = "internal"
            settings["chromeMCPSettings"] = cdp
            await save_settings(settings)
            await self._broadcast_settings(settings)
            logging.info("[DiaryEngine] 已自动开启浏览器控制功能（内建 CDP）")
            return True
        except Exception as e:
            logging.error(f"[DiaryEngine] 尝试开启浏览器控制失败: {e}")
            return False

    async def _try_enable_smart_home(self) -> bool:
        """尝试开启智能家居（Home Assistant）并初始化 HA_client，返回是否成功"""
        try:
            settings = await load_settings()
            ha = (settings or {}).get("HASettings", {}) or {}
            if ha.get("enabled", False):
                # 检查 HA_client 是否已实际初始化
                try:
                    import server
                    if server.HA_client is not None:
                        return True
                except Exception:
                    pass

            api_key = (ha.get("api_key") or "").strip()
            ha_url = (ha.get("url") or "http://127.0.0.1:8123").strip()
            if not api_key:
                await self._notify_user(
                    "智能家居开启失败",
                    "未配置 Home Assistant API Key，请先在设置中填写 api_key 和 url 后再启用。"
                )
                return False

            ha["enabled"] = True
            settings["HASettings"] = ha
            await save_settings(settings)
            await self._broadcast_settings(settings)

            # 通过 HTTP 调用服务器的 /start_HA 端点来真正初始化 HA_client
            port = get_port()
            url = f"http://127.0.0.1:{port}/start_HA"
            payload = {"data": {"api_key": api_key, "url": ha_url}}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    logging.info("[DiaryEngine] 智能家居 MCP 客户端已初始化")
                    return True
                else:
                    detail = ""
                    try:
                        detail = resp.json().get("error", "")
                    except Exception:
                        detail = resp.text[:200]
                    logging.error(f"[DiaryEngine] 初始化 HA 客户端失败: {resp.status_code} {detail}")
                    # 回退 settings
                    try:
                        settings2 = await load_settings()
                        settings2["HASettings"]["enabled"] = False
                        await save_settings(settings2)
                        await self._broadcast_settings(settings2)
                    except Exception:
                        pass
                    return False
        except Exception as e:
            logging.error(f"[DiaryEngine] 尝试开启智能家居失败: {e}")
            try:
                settings2 = await load_settings()
                settings2["HASettings"]["enabled"] = False
                await save_settings(settings2)
                await self._broadcast_settings(settings2)
            except Exception:
                pass
            return False

    async def _notify_user(self, title: str, message: str):
        """向用户发送通知消息"""
        try:
            from py.ws_manager import ws_manager
            await ws_manager.broadcast({
                "type": "diary_entry",
                "data": {
                    "actionType": "system_notify",
                    "title": title,
                    "content": message,
                    "characterName": "",
                }
            })
        except Exception as e:
            logging.error(f"[DiaryEngine] 通知用户失败: {e}")

    async def _run_action(self, action: str):
        self._busy = True
        try:
            # 执行前尝试开启对应功能
            if action == "webSearch":
                ok = await self._try_enable_web_search()
                if not ok:
                    await self._notify_user(
                        "联网搜索开启失败",
                        "日记系统尝试开启联网搜索功能失败，本次将退回到纯思考模式。"
                    )
                    action = "think"
            elif action == "knowledge":
                ok = await self._try_enable_knowledge_base()
                if not ok:
                    await self._notify_user(
                        "知识库开启失败",
                        "日记系统尝试开启知识库功能失败，请检查是否有已配置的知识库。本次将退回到纯思考模式。"
                    )
                    action = "think"
            elif action == "browserControl":
                ok = await self._try_enable_browser()
                if not ok:
                    await self._notify_user(
                        "浏览器控制开启失败",
                        "日记系统尝试开启浏览器控制功能失败，本次将退回到纯思考模式。"
                    )
                    action = "think"
            elif action == "smartHome":
                ok = await self._try_enable_smart_home()
                if not ok:
                    await self._notify_user(
                        "智能家居开启失败",
                        "日记系统尝试开启智能家居功能失败，本次将退回到纯思考模式。"
                    )
                    action = "think"

            content = await self._invoke_agent(action)
            if not content:
                logging.warning(f"[DiaryEngine] 动作 {action} 未生成内容")
                return

            book_id, character_id, character_name = await self._resolve_active_book()
            pushed_to = []

            # imMessage 动作：随机选择一个渠道发送
            if action == "imMessage":
                im_targets = self.config.get("imTargets", []) or []
                if im_targets:
                    tgt = random.choice(im_targets)
                    if isinstance(tgt, dict):
                        platform = tgt.get("platform", "")
                        chat_id = tgt.get("chatId")
                    else:
                        platform = tgt
                        chat_id = None
                    if platform == "chat":
                        await self._push_to_chat(action, content, character_name)
                        pushed_to.append("chat")
                    else:
                        ok = await self._push_to_im(platform, chat_id, content)
                        if ok:
                            pushed_to.append(platform)

            # 持久化日记（写进对应角色的日记本）
            title = content.strip().splitlines()[0][:24] if content.strip() else ""
            await append_diary_entry(action, content, title=title, pushed_to=pushed_to,
                                     book_id=book_id, character_id=character_id,
                                     character_name=character_name)
        except Exception as e:
            logging.error(f"[DiaryEngine] 执行动作 {action} 失败: {e}")
        finally:
            self._busy = False

    def _build_messages(self, action: str):
        persona = self.config.get("prompt", "") or ""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        sys_parts = []
        if persona:
            sys_parts.append(persona)
        sys_parts.append(f"当前时间：{now_str}。这是一次系统自动触发的「自主日记」时刻，用户并没有主动与你说话。")
        system_prompt = "\n\n".join(sys_parts)
        user_prompt = ACTION_DEFAULT_PROMPTS.get(action, ACTION_DEFAULT_PROMPTS["think"])
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"[system]: {user_prompt}"},
        ]

    async def _get_enable_tools_for_action(self, action: str):
        """根据动作类型动态构建工具白名单"""
        tools = list(DIARY_ALLOWED_TOOLS)
        if action == "smartHome":
            try:
                from server import HA_client
                if HA_client and HA_client._tools:
                    for t in HA_client._tools:
                        name = t.get("name") or (t.get("function", {}) or {}).get("name", "")
                        if name and name not in tools:
                            tools.append(name)
            except Exception:
                pass
        return tools

    async def _invoke_agent(self, action: str) -> str:
        """调用本地主智能体生成日记内容（限制为低权限工具）"""
        port = get_port()
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        enable_tools = await self._get_enable_tools_for_action(action)
        payload = {
            "model": "super-model",
            "messages": self._build_messages(action),
            "stream": False,
            "is_app_bot": True,
            "platform": "diary",
            "enable_tools": enable_tools,
            "enable_web_search": action == "webSearch",
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return (data.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "") or "").strip()
        except Exception as e:
            logging.error(f"[DiaryEngine] 调用智能体失败: {e}")
            return ""

    async def _push_to_chat(self, action: str, content: str, character_name: str = ""):
        """通过 WebSocket 广播日记给网页端，由前端直接展示"""
        try:
            from py.ws_manager import ws_manager
            await ws_manager.broadcast({
                "type": "diary_entry",
                "data": {
                    "actionType": action,
                    "content": content,
                    "characterName": character_name or "",
                }
            })
        except Exception as e:
            logging.error(f"[DiaryEngine] 推送到网页端失败: {e}")

    async def _push_to_im(self, platform: str, chat_id, content: str) -> bool:
        """复用已连接 IM 机器人的行为引擎 handler，把日记内容原样转达给用户"""
        if not platform:
            return False
        try:
            from py.behavior_engine import (
                global_behavior_engine, BehaviorItem, BehaviorAction,
                BehaviorTrigger, BehaviorTriggerTime, BehaviorTriggerNoInput, BehaviorTriggerCycle,
            )
            handler = global_behavior_engine.handlers.get(platform)
            if not handler:
                logging.warning(f"[DiaryEngine] IM 平台 {platform} 未注册或未启动，跳过")
                return False

            relay_prompt = (
                "你刚刚想主动给用户说一段话，请把下面这段内容自然地发送给用户"
                "（保持原意，可微调语气，不要解释、不要加前后缀）：\n\n" + content
            )
            trigger = BehaviorTrigger(
                type="cycle",
                time=BehaviorTriggerTime(timeValue="00:00:00", days=[]),
                noInput=BehaviorTriggerNoInput(latency=30),
                cycle=BehaviorTriggerCycle(cycleValue="00:00:30", repeatNumber=1, isInfiniteLoop=False),
            )
            behavior = BehaviorItem(
                enabled=True,
                trigger=trigger,
                action=BehaviorAction(type="prompt", prompt=relay_prompt),
                platform=platform,
                platforms=[platform],
            )

            targets = [chat_id] if chat_id else global_behavior_engine.platform_targets.get(platform, [])
            if not targets:
                asyncio.create_task(handler("", behavior))
                return True
            for cid in set(targets):
                asyncio.create_task(handler(cid, behavior))
            return True
        except Exception as e:
            logging.error(f"[DiaryEngine] 推送到 IM({platform}) 失败: {e}")
            return False


# 全局单例
global_diary_engine = DiaryEngine()
