import asyncio
import base64
import io
import json
import logging
import random
import re
import threading
import weakref
from typing import Dict, List, Optional, Any

import aiohttp
import discord
from discord.ext import commands, tasks
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from py.behavior_engine import BehaviorItem, BehaviorSettings, global_behavior_engine
from py.get_setting import convert_to_opus_simple, get_port, load_settings

# ------------------ 配置模型 ------------------
class DiscordBotConfig(BaseModel):
    token: str
    llm_model: str = "super-model"
    memory_limit: int = 10
    separators: List[str] = []
    reasoning_visible: bool = False
    quick_restart: bool = True
    enable_tts: bool = True
    wakeWord: str              # 唤醒词
    behaviorSettings: Optional[BehaviorSettings] = None
    behaviorTargetChatIds: List[str] = Field(default_factory=list)

# ------------------ 管理器 ------------------
class DiscordBotManager:
    def __init__(self):
        self.bot_thread: Optional[threading.Thread] = None
        self.bot_client: Optional["DiscordClient"] = None
        self.is_running = False
        self.config: Optional[DiscordBotConfig] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event = threading.Event()
        self._ready_complete = threading.Event()
        self._startup_error: Optional[str] = None
        self._stop_requested = False

    def start_bot(self, config: DiscordBotConfig):
        if self.is_running:
            raise RuntimeError("Discord 机器人已在运行")
        self.config = config
        self._shutdown_event.clear()
        self._ready_complete.clear()
        self._startup_error = None
        self._stop_requested = False

        self.bot_thread = threading.Thread(
            target=self._run_bot_thread, args=(config,), daemon=True, name="DiscordBotThread"
        )
        self.bot_thread.start()

        if not self._ready_complete.wait(timeout=30):
            self.stop_bot()
            raise RuntimeError("Discord 机器人就绪超时")

        if self._startup_error:
            self.stop_bot()
            raise RuntimeError(f"Discord 机器人启动失败: {self._startup_error}")

    def _run_bot_thread(self, config: DiscordBotConfig):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            async def main_startup():
                try:
                    settings = await load_settings()
                    behavior_data = settings.get("behaviorSettings", {})
                    target_ids = config.behaviorTargetChatIds or settings.get("discordBotConfig", {}).get("behaviorTargetChatIds", [])
                    
                    if behavior_data:
                        logging.info(f"Discord 线程: 同步行为配置... 目标数: {len(target_ids)}")
                        global_behavior_engine.update_config(behavior_data, {"discord": target_ids})
                        config.behaviorSettings = behavior_data if isinstance(behavior_data, BehaviorSettings) else BehaviorSettings(**behavior_data)
                        config.behaviorTargetChatIds = target_ids

                    self.bot_client = DiscordClient(config, manager=self)

                    if not global_behavior_engine.is_running:
                        asyncio.create_task(global_behavior_engine.start())

                    await self.bot_client.start(config.token)
                except Exception as e:
                    self._startup_error = str(e)
                    logging.exception("Discord 机器人启动出错")

            self.loop.run_until_complete(main_startup())
        except Exception as e:
            if not self._stop_requested:
                self._startup_error = str(e)
        finally:
            self._cleanup()

    def stop_bot(self):
        if not self.is_running and not self.bot_thread:
            return
        self._stop_requested = True
        self._shutdown_event.set()
        self.is_running = False
        if self.bot_client:
            # 取消所有活跃任务
            for task in self.bot_client.active_tasks.values():
                task.cancel()
            asyncio.run_coroutine_threadsafe(self.bot_client.close(), self.loop)
        if self.bot_thread and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=5)

    def _cleanup(self):
        self.is_running = False
        if self.loop and not self.loop.is_closed():
            try:
                pending = asyncio.all_tasks(self.loop)
                for task in pending: task.cancel()
                self.loop.close()
            except Exception: pass

    def get_status(self):
        return {
            "is_running": self.is_running,
            "thread_alive": self.bot_thread.is_alive() if self.bot_thread else False,
            "ready_completed": self._ready_complete.is_set(),
            "startup_error": self._startup_error,
        }

    def update_behavior_config(self, config: DiscordBotConfig):
        self.config = config
        if self.bot_client:
            self.bot_client.config.llm_model = config.llm_model 
            self.bot_client.config.enable_tts = config.enable_tts
            self.bot_client.config.wakeWord = config.wakeWord
        global_behavior_engine.update_config(config.behaviorSettings, {"discord": config.behaviorTargetChatIds})

# ------------------ Discord Client ------------------
class DiscordClient(discord.Client):
    def __init__(self, config: DiscordBotConfig, manager: DiscordBotManager):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.manager = manager
        self.memory: Dict[int, List[dict]] = {} 
        self.async_tools: Dict[int, List[str]] = {}
        self.file_links: Dict[int, List[str]] = {}
        self._shutdown_requested = False
        # 核心：追踪每个频道正在运行的任务
        self.active_tasks: Dict[int, asyncio.Task] = {}
        
        global_behavior_engine.register_handler("discord", self.execute_behavior_event)

    async def on_ready(self):
        self.manager.is_running = True
        self.manager._ready_complete.set()
        logging.info(f"✅ Discord 机器人已上线：{self.user}")

    async def on_message(self, msg: discord.Message):
        if self._shutdown_requested or msg.author == self.user:
            return

        cid = msg.channel.id
        
        # 1. 快捷指令与任务打断检查（统一快捷指令，受系统设置全局开关控制）
        if msg.content:
            from py import shortcut_commands
            content_strip = msg.content.strip()
            if await shortcut_commands.im_shortcuts_enabled():
                action = shortcut_commands.parse_im_action(content_strip)
                if action == "stop":
                    if cid in self.active_tasks:
                        self.active_tasks[cid].cancel()
                        await msg.reply(shortcut_commands.STOP_MSG_EN)
                    return
                if action == "reset":
                    if cid in self.active_tasks:
                        self.active_tasks[cid].cancel()
                    self.memory[cid] = []
                    await msg.reply(shortcut_commands.RESET_MSG_EN)
                    return
                if action == "help":
                    await msg.reply(shortcut_commands.build_help_text("en"))
                    return
                if action == "skills":
                    await msg.reply(await shortcut_commands.build_skills_text("en"))
                    return
                if action == "model":
                    await msg.reply(await shortcut_commands.handle_model_command(content_strip, "en"))
                    return
                if action == "personality":
                    await msg.reply(await shortcut_commands.handle_personality_command(content_strip, "en"))
                    return
                if action == "retry":
                    await msg.reply(shortcut_commands.retry_hint("en"))
                    return
                if action == "mode":
                    await msg.reply(await shortcut_commands.build_mode_info_text("en"))
                    return

        # 2. 如果当前有正在处理的任务，则打断它
        if cid in self.active_tasks:
            logging.info(f"检测到新消息，正在打断频道 {cid} 的旧任务")
            self.active_tasks[cid].cancel()

        # 3. 创建并追踪新任务
        task = asyncio.create_task(self._process_ai_logic(msg))
        self.active_tasks[cid] = task
        
        try:
            await task
        except asyncio.CancelledError:
            logging.info(f"频道 {cid} 的任务已被打断/取消")
        except Exception as e:
            logging.exception(f"处理 Discord 消息异常: {e}")
        finally:
            if self.active_tasks.get(cid) == task:
                self.active_tasks.pop(cid, None)

    async def _process_ai_logic(self, msg: discord.Message):
        """核心 AI 处理逻辑，支持流式分段和打断"""
        cid = msg.channel.id
        if cid not in self.memory:
            self.memory[cid] = []
            self.async_tools[cid] = []
            self.file_links[cid] = []

        global_behavior_engine.report_activity("discord", str(cid))

        if msg.content.strip().lower() == "/id":
            await msg.reply(f"🤖 **Channel ID**\n`{cid}`")
            return

        from py import shortcut_commands as _sc
        _sub = _sc.parse_subscribe_action(msg.content)
        if _sub:
            _reply = await _sc.handle_subscribe_command("discord", str(cid), _sub == "sub", "en")
            if _reply:
                await msg.reply(_reply)
            return

        user_content = []
        user_text = msg.content or ""
        has_media = False

        # 多模态解析
        for att in msg.attachments:
            if att.content_type and att.content_type.startswith("image"):
                b64data = base64.b64encode(await att.read()).decode()
                user_content.append({"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{b64data}"}})
                has_media = True
            elif att.content_type and att.content_type.startswith("audio"):
                asr_text = await self._transcribe_audio(await att.read(), att.filename)
                user_text += f"\n[语音转写] {asr_text}" if asr_text else "\n[语音转写失败]"

        if self.config.wakeWord and self.config.wakeWord not in user_text:
            return

        if has_media and user_text:
            user_content.append({"type": "text", "text": user_text})
        
        final_input = user_content or user_text
        if not final_input: return
        self.memory[cid].append({"role": "user", "content": final_input})

        # 请求 LLM
        settings = await load_settings()
        client = AsyncOpenAI(api_key="sk", base_url=f"http://127.0.0.1:{get_port()}/v1")
        
        state = {"text_buffer": "", "image_buffer": "", "image_cache": [], "audio_buffer": []}
        full_response = []

        try:
            stream = await client.chat.completions.create(
                model=self.config.llm_model,
                messages=self.memory[cid],
                stream=True,
                extra_body={
                    "asyncToolsID": self.async_tools[cid],
                    "fileLinks": self.file_links[cid],
                    "is_app_bot": True,
                    "platform": "discord",
                },
            )

            async for chunk in stream:
                if not chunk.choices: continue
                delta = chunk.choices[0].delta
                
                # Omni 音频捕获
                if hasattr(delta, "audio") and delta.audio and "data" in delta.audio:
                    state["audio_buffer"].append(delta.audio["data"])
                
                # Tool 逻辑
                at_id = getattr(delta, "async_tool_id", None)
                if at_id:
                    if at_id not in self.async_tools[cid]: self.async_tools[cid].append(at_id)
                    else: self.async_tools[cid].remove(at_id)
                
                t_link = getattr(delta, "tool_link", None)
                if t_link and settings.get("tools", {}).get("toolMemorandum", {}).get("enabled"):
                    if t_link not in self.file_links[cid]: self.file_links[cid].append(t_link)

                content = delta.content or ""
                reasoning = getattr(delta, "reasoning_content", "") or ""
                display_content = reasoning if (self.config.reasoning_visible and reasoning) else content
                
                full_response.append(content)
                state["text_buffer"] += display_content
                state["image_buffer"] += display_content

                # 流式分段发送
                if state["text_buffer"] and not self.config.enable_tts:
                    buffer = state["text_buffer"]
                    split_pos = -1
                    in_code_block = False
                    
                    # 检查分隔符（复杂逻辑保留）
                    i = 0
                    while i < len(buffer):
                        if buffer[i:].startswith("```"): in_code_block = not in_code_block; i += 3; continue
                        if not in_code_block:
                            for sep in self.config.separators:
                                if buffer[i:].startswith(sep):
                                    split_pos = i + len(sep)
                                    break
                            if split_pos != -1: break
                        i += 1
                    
                    # 强制分段
                    if len(buffer) > 1800: split_pos = 1800

                    if split_pos != -1:
                        seg = buffer[:split_pos]
                        state["text_buffer"] = buffer[split_pos:]
                        clean = self._clean_text(seg)
                        if clean: await msg.channel.send(clean)

            # 扫尾
            if state["text_buffer"]:
                clean = self._clean_text(state["text_buffer"])
                if clean and not self.config.enable_tts: await msg.channel.send(clean)
            
            # 图片提取与发送
            self._extract_images(state)
            for url in state["image_cache"]: await self._send_image(msg, url)
            
            # Omni 音频
            has_omni = False
            if state["audio_buffer"]:
                final_audio, is_opus = await asyncio.to_thread(convert_to_opus_simple, base64.b64decode("".join(state["audio_buffer"])))
                await self._send_omni_voice(msg, final_audio, is_opus)
                has_omni = True

            # TTS 与 记忆
            final_text = "".join(full_response)
            if self.config.enable_tts and not has_omni:
                await self._send_voice(msg, final_text)
                
            self.memory[cid].append({"role": "assistant", "content": final_text})
            if self.config.memory_limit > 0:
                while len(self.memory[cid]) > self.config.memory_limit * 2: self.memory[cid].pop(0)

        except Exception as e:
            if not isinstance(e, asyncio.CancelledError):
                logging.error(f"Discord 逻辑处理异常: {e}")

    # ---------- 功能函数 (保持原样) ----------

    async def _send_omni_voice(self, msg: discord.Message, audio_data: bytes, is_opus: bool):
        try:
            ext = "opus" if is_opus else "wav"
            file = discord.File(io.BytesIO(audio_data), filename=f"voice.{ext}")
            await msg.reply(file=file, mention_author=False)
        except: pass

    async def _transcribe_audio(self, audio_bytes: bytes, filename: str) -> Optional[str]:
        form = aiohttp.FormData()
        form.add_field("audio", io.BytesIO(audio_bytes), filename=filename, content_type="audio/ogg")
        async with aiohttp.ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{get_port()}/asr", data=form) as r:
                if r.status == 200:
                    res = await r.json()
                    return res.get("text") if res.get("success") else None
        return None

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        text = re.sub(r'<.*?>', '', text)
        return text.strip()

    def clean_markdown(self, buffer):
        buffer = re.sub(r'#{1,6}\s', '', buffer, flags=re.MULTILINE)
        buffer = re.sub(r'[*_~`]+', '', buffer)
        buffer = re.sub(r'^\s*[-*]\s', '', buffer, flags=re.MULTILINE)
        buffer = re.sub(r'[\u2600-\u27BF\U0001F300-\U0001F9FF]', '', buffer)
        buffer = re.sub(r'!\[.*?\]\(.*?\)', '', buffer)
        buffer = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', buffer)
        return buffer.strip()

    async def _send_voice(self, msg, text: str):
        settings = await load_settings()
        clean_t = self.clean_markdown(text)
        payload = {"text": clean_t, "voice": "default", "ttsSettings": settings.get("ttsSettings", {}), "index": 0, "mobile_optimized": True, "format": "opus"}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{get_port()}/tts", json=payload) as r:
                if r.status == 200:
                    file = discord.File(io.BytesIO(await r.read()), filename="voice.opus")
                    await (msg.channel.send(file=file) if hasattr(msg, 'channel') else msg.send(file=file))

    def _extract_images(self, state: Dict[str, Any]):
        pattern = r'!\[.*?\]\((https?://[^\s)]+)'
        for m in re.finditer(pattern, state["image_buffer"]): state["image_cache"].append(m.group(1))

    async def _send_image(self, msg, img_url: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url) as resp:
                    if resp.status == 200:
                        file = discord.File(io.BytesIO(await resp.read()), filename="image.png")
                        await msg.channel.send(file=file)
        except: pass

    async def execute_behavior_event(self, chat_id: str, behavior_item: BehaviorItem):
        prompt = await self._resolve_behavior_prompt(behavior_item)
        if not prompt: return
        cid = int(chat_id)
        if cid not in self.memory: self.memory[cid] = []
        messages = self.memory[cid] + [{"role": "user", "content": f"[system]: {prompt}"}]
        try:
            client = AsyncOpenAI(api_key="sk", base_url=f"http://127.0.0.1:{get_port()}/v1")
            response = await client.chat.completions.create(model=self.config.llm_model, messages=messages, stream=False, extra_body={"is_app_bot": True, "platform": "discord", "behavior_trigger": True})
            reply = response.choices[0].message.content
            if reply:
                channel = self.get_channel(cid)
                if channel:
                    await channel.send(reply)
                    self.memory[cid].append({"role": "user", "content": f"[system]: {prompt}"})
                    self.memory[cid].append({"role": "assistant", "content": reply})
                    if self.config.enable_tts: await self._send_voice(channel, reply)
        except Exception as e:
            logging.error(f"Discord 行为推送失败: {e}")   

    async def _resolve_behavior_prompt(self, behavior: BehaviorItem) -> str:
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

    async def close(self):
        self._shutdown_requested = True
        await super().close()

    def __del__(self):
        try:
            for task in self.active_tasks.values(): task.cancel()
        except: pass