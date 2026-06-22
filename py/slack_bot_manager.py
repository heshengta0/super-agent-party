import asyncio
import base64
import io
import logging
import re
import threading
import time
from typing import Dict, List, Optional, Any

import aiohttp
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from openai import AsyncOpenAI
from pydantic import BaseModel
from py.get_setting import get_port, load_settings

# ------------------ 配置模型 ------------------
class SlackBotConfig(BaseModel):
    bot_token: str
    app_token: str
    llm_model: str = "super-model"
    memory_limit: int = 30
    separators: List[str] = []
    reasoning_visible: bool = True
    quick_restart: bool = True
    enable_tts: bool = False
    wakeWord: str = ""
    behaviorSettings: Optional[Any] = None 
    behaviorTargetChatIds: List[str] = []

# ------------------ Slack 机器人管理器 ------------------
class SlackBotManager:
    def __init__(self):
        self.bot_thread: Optional[threading.Thread] = None
        self.socket_client: Optional[SocketModeClient] = None
        self.is_running = False
        self.config: Optional[SlackBotConfig] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready_complete = threading.Event()
        
        self.bot_user_id: Optional[str] = None
        
        # 状态存储
        self.memory: Dict[str, List[dict]] = {}      
        self.async_tools: Dict[str, List[str]] = {}  
        self.file_links: Dict[str, List[str]] = {}
        # 核心：追踪每个频道正在运行的任务，实现打断功能
        self.active_tasks: Dict[str, asyncio.Task] = {}

    def start_bot(self, config: SlackBotConfig):
        if self.is_running:
            raise RuntimeError("Slack 机器人已在运行")
        self.config = config
        self._ready_complete.clear()

        self.bot_thread = threading.Thread(
            target=self._run_bot_thread, args=(config,), daemon=True, name="SlackBotThread"
        )
        self.bot_thread.start()

        if not self._ready_complete.wait(timeout=30):
            self.stop_bot()
            raise RuntimeError("Slack 机器人启动超时")

    def _run_bot_thread(self, config: SlackBotConfig):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def main_startup():
            try:
                from py.get_setting import load_settings
                from py.behavior_engine import global_behavior_engine, BehaviorSettings
                
                global_behavior_engine.register_handler("slack", self.execute_behavior_event)
                settings = await load_settings()
                behavior_data = settings.get("behaviorSettings", {})
                
                target_ids = config.behaviorTargetChatIds or settings.get("slackBotConfig", {}).get("behaviorTargetChatIds", [])
                
                if behavior_data:
                    logging.info(f"Slack 线程: 同步行为配置... 目标频道数: {len(target_ids)}")
                    global_behavior_engine.update_config(behavior_data, {"slack": target_ids})
                    config.behaviorSettings = behavior_data if isinstance(behavior_data, BehaviorSettings) else BehaviorSettings(**behavior_data)
                    config.behaviorTargetChatIds = target_ids

                if not global_behavior_engine.is_running:
                    asyncio.create_task(global_behavior_engine.start())

                await self._async_start(config)

            except Exception as e:
                logging.exception(f"Slack 启动过程异常: {e}")
                self.is_running = False 
                self._ready_complete.set()

        try:
            self.loop.run_until_complete(main_startup())
        except Exception as e:
            logging.error(f"Slack 线程 Loop 异常: {e}")
        finally:
            self._cleanup()

    async def _async_start(self, config: SlackBotConfig):
        web_client = AsyncWebClient(token=config.bot_token)
        auth = await web_client.auth_test()
        self.bot_user_id = auth["user_id"]

        self.socket_client = SocketModeClient(app_token=config.app_token, web_client=web_client)

        async def process_listener(client, req: SocketModeRequest):
            if req.type == "events_api":
                await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
                event = req.payload.get("event", {})
                if event.get("user") == self.bot_user_id or event.get("bot_id") or "subtype" in event:
                    return
                if event.get("type") in ["message", "app_mention"]:
                    # 调度消息处理入口
                    await self._dispatch_message(event, web_client)

        self.socket_client.socket_mode_request_listeners.append(process_listener)
        await self.socket_client.connect()
        self.is_running = True
        self._ready_complete.set()
        while self.is_running: await asyncio.sleep(1)

    def _cleanup(self):
        self.is_running = False
        if self.loop and not self.loop.is_closed():
            try:
                for task in asyncio.all_tasks(self.loop): task.cancel()
                self.loop.close()
            except: pass

    def stop_bot(self):
        self.is_running = False
        if self.socket_client:
            asyncio.run_coroutine_threadsafe(self.socket_client.close(), self.loop)
        # 取消所有活跃任务
        for task in self.active_tasks.values():
            task.cancel()
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

    def get_status(self):
        return {"is_running": self.is_running}

    # ---------- 消息调度入口（含打断逻辑） ----------
    async def _dispatch_message(self, event: dict, web_client: AsyncWebClient):
        cid = event["channel"]
        text = event.get("text", "").strip()

        # 1. 快捷指令检查（统一快捷指令，受系统设置全局开关控制）
        from py import shortcut_commands
        if await shortcut_commands.im_shortcuts_enabled():
            action = shortcut_commands.parse_im_action(text)
            if action == "stop":
                if cid in self.active_tasks:
                    self.active_tasks[cid].cancel()
                    await web_client.chat_postMessage(channel=cid, text=shortcut_commands.STOP_MSG_EN)
                return
            if action == "reset":
                if cid in self.active_tasks:
                    self.active_tasks[cid].cancel()
                self.memory[cid] = []
                await web_client.chat_postMessage(channel=cid, text=shortcut_commands.RESET_MSG_EN)
                return
            if action == "help":
                await web_client.chat_postMessage(channel=cid, text=shortcut_commands.build_help_text("en"))
                return
            if action == "skills":
                await web_client.chat_postMessage(channel=cid, text=await shortcut_commands.build_skills_text("en"))
                return
            if action == "model":
                await web_client.chat_postMessage(channel=cid, text=await shortcut_commands.handle_model_command(text, "en"))
                return
            if action == "personality":
                await web_client.chat_postMessage(channel=cid, text=await shortcut_commands.handle_personality_command(text, "en"))
                return
            if action == "retry":
                await web_client.chat_postMessage(channel=cid, text=shortcut_commands.retry_hint("en"))
                return
            if action == "mode":
                await web_client.chat_postMessage(channel=cid, text=await shortcut_commands.build_mode_info_text("en"))
                return

        # 2. 如果该频道有正在运行的任务，打断它
        if cid in self.active_tasks:
            logging.info(f"Slack: 检测到新消息，打断频道 {cid} 的旧任务")
            self.active_tasks[cid].cancel()

        # 3. 创建新任务
        new_task = asyncio.create_task(self._handle_message_logic(event, web_client))
        self.active_tasks[cid] = new_task
        
        try:
            await new_task
        except asyncio.CancelledError:
            logging.info(f"Slack: 频道 {cid} 的任务已被打断/取消")
        finally:
            if self.active_tasks.get(cid) == new_task:
                self.active_tasks.pop(cid, None)

    async def _handle_message_logic(self, event: dict, web_client: AsyncWebClient):
        """核心处理：AI 逻辑部分"""
        cid = event["channel"]
        text = event.get("text", "").strip()

        if cid not in self.memory:
            self.memory[cid], self.async_tools[cid], self.file_links[cid] = [], [], []

        from py.behavior_engine import global_behavior_engine
        global_behavior_engine.report_activity("slack", cid)

        if text.lower() == "/id":
            await web_client.chat_postMessage(channel=cid, text=f"🤖 *Session ID*\n`{cid}`")
            return

        from py import shortcut_commands as _sc
        _sub = _sc.parse_subscribe_action(text)
        if _sub:
            _reply = await _sc.handle_subscribe_command("slack", cid, _sub == "sub", "en")
            if _reply:
                await web_client.chat_postMessage(channel=cid, text=_reply)
            return

        if self.config.wakeWord and self.config.wakeWord not in text: return

        self.memory[cid].append({"role": "user", "content": text})

        state = {"text_buffer": "", "image_buffer": "", "image_cache": []}
        
        # 发送占位
        initial_resp = await web_client.chat_postMessage(channel=cid, text="...")
        reply_ts = initial_resp["ts"]

        settings = await load_settings()
        client_ai = AsyncOpenAI(api_key="sk", base_url=f"http://127.0.0.1:{get_port()}/v1")

        try:
            stream = await client_ai.chat.completions.create(
                model=self.config.llm_model,
                messages=self.memory[cid],
                stream=True,
                extra_body={
                    "asyncToolsID": self.async_tools[cid],
                    "fileLinks": self.file_links[cid],
                    "is_app_bot": True,
                    "platform": "slack",
                },
            )

            full_response = []
            last_update_time = time.time()

            async for chunk in stream:
                if not chunk.choices: continue
                delta = chunk.choices[0].delta

                # 工具逻辑
                if getattr(delta, "tool_link", None) and settings.get("tools", {}).get("toolMemorandum", {}).get("enabled"):
                    if delta.tool_link not in self.file_links[cid]: self.file_links[cid].append(delta.tool_link)
                
                at_id = getattr(delta, "async_tool_id", None)
                if at_id:
                    if at_id not in self.async_tools[cid]: self.async_tools[cid].append(at_id)
                    else: self.async_tools[cid].remove(at_id)

                content = delta.content or ""
                reasoning = getattr(delta, "reasoning_content", None) or ""
                display_content = reasoning if (self.config.reasoning_visible and reasoning) else content

                full_response.append(content)
                state["text_buffer"] += display_content
                state["image_buffer"] += display_content

                # 控制更新频率
                now = time.time()
                if (now - last_update_time > 1.2) or any(sep in content for sep in self.config.separators):
                    seg = self._clean_text(state["text_buffer"])
                    if seg:
                        await web_client.chat_update(channel=cid, ts=reply_ts, text=seg + " ▌")
                        last_update_time = now

            full_content = "".join(full_response)
            await web_client.chat_update(channel=cid, ts=reply_ts, text=self._clean_text(full_content) or "Reply complete.")

            # 图片提取与发送
            self._extract_images(state)
            for img_url in state["image_cache"]:
                await self._send_image(cid, img_url, web_client)

            if self.config.enable_tts:
                await self._send_voice(cid, full_content, web_client)

            self.memory[cid].append({"role": "assistant", "content": full_content})
            if self.config.memory_limit > 0:
                while len(self.memory[cid]) > self.config.memory_limit * 2: self.memory[cid].pop(0)

        except Exception as e:
            if not isinstance(e, asyncio.CancelledError):
                logging.error(f"Slack Bot Logic Error: {e}")
                await web_client.chat_update(channel=cid, ts=reply_ts, text=f"❌ Error: {e}")

    # ---------- 工具函数 (保持原样) ----------
    def _extract_images(self, state: Dict[str, Any]):
        pattern = r'!\[.*?\]\((https?://[^\s)]+)'
        for m in re.finditer(pattern, state["image_buffer"]):
            state["image_cache"].append(m.group(1))

    def _clean_text(self, text: str) -> str:
        text = re.sub(r'<.*?>', '', text)
        return re.sub(r"!\[.*?\]\(.*?\)", "", text).strip()

    async def _send_image(self, cid: str, url: str, web_client: AsyncWebClient):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as r:
                    if r.status == 200:
                        await web_client.files_upload_v2(channel=cid, file=await r.read(), filename="image.png")
        except: pass

    async def _send_voice(self, cid: str, text: str, web_client: AsyncWebClient):
        try:
            settings = await load_settings()
            clean_text = re.sub(r'[*_~`#]|!\[.*?\]\(.*?\)', '', text)
            if not clean_text.strip(): return
            payload = {"text": clean_text[:300], "voice": "default", "ttsSettings": settings.get("ttsSettings", {}), "index": 0, "mobile_optimized": False, "format": "mp3"}
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{get_port()}/tts", json=payload) as r:
                    if r.status == 200:
                        await web_client.files_upload_v2(channel=cid, file=await r.read(), filename="voice.mp3", title="语音回复")
        except: pass

    def update_behavior_config(self, config: SlackBotConfig):
        self.config = config
        from py.behavior_engine import global_behavior_engine
        global_behavior_engine.update_config(config.behaviorSettings, {"slack": config.behaviorTargetChatIds})

    async def execute_behavior_event(self, chat_id: str, behavior_item: Any):
        if not self.socket_client: return
        prompt = await self._resolve_behavior_prompt(behavior_item)
        if not prompt: return
        cid = chat_id
        if cid not in self.memory: self.memory[cid] = []
        messages = self.memory[cid] + [{"role": "user", "content": f"[system]: {prompt}"}]
        try:
            client_ai = AsyncOpenAI(api_key="sk", base_url=f"http://127.0.0.1:{get_port()}/v1")
            response = await client_ai.chat.completions.create(model=self.config.llm_model, messages=messages, stream=False, extra_body={"is_app_bot": True, "platform": "slack", "behavior_trigger": True})
            reply = response.choices[0].message.content
            if reply:
                await self.socket_client.web_client.chat_postMessage(channel=cid, text=reply)
                self.memory[cid].append({"role": "assistant", "content": reply})
                if self.config.enable_tts: await self._send_voice(cid, reply, self.socket_client.web_client)
        except: pass

    async def _resolve_behavior_prompt(self, behavior: Any) -> Optional[str]:
        import random
        action = behavior.action
        if action.type == "prompt": return action.prompt
        elif action.type == "random" and action.random and action.random.events:
            events = action.random.events
            if action.random.type == "random": return random.choice(events)
            idx = action.random.orderIndex
            selected = events[idx % len(events)]
            action.random.orderIndex = (idx + 1) % len(events)
            return selected
        return None

    def __del__(self):
        try:
            for t in self.active_tasks.values(): t.cancel()
        except: pass