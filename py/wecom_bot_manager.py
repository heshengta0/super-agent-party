# py/wecom_bot_manager.py
import asyncio
import json
import random
import threading
from typing import Optional, List, Dict
import weakref
import aiohttp
import io
import base64
import logging
import time
import re
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

from py.get_setting import convert_to_amr_simple, get_port, load_settings
from py.behavior_engine import BehaviorItem, global_behavior_engine, BehaviorSettings
from aibot import WSClient, WSClientOptions, generate_req_id

# 企业微信机器人配置模型
class WeComBotConfig(BaseModel):
    WeComAgent: str           # LLM模型名
    memoryLimit: int          # 记忆条数限制
    bot_id: str               # 企微机器人BotID (Webhook Key)
    secret: str               # 企微长连接专用Secret
    reasoningVisible: bool    # 是否显示推理过程
    quickRestart: bool        # 快速重启指令开关
    enableTTS: bool           # 是否启用TTS
    wakeWord: str              # 唤醒词
    behaviorSettings: Optional[BehaviorSettings] = None
    behaviorTargetChatIds: List[str] = Field(default_factory=list)

class WeComBotManager:
    def __init__(self):
        self.bot_thread: Optional[threading.Thread] = None
        self.bot_client: Optional['WeComClient'] = None
        self.is_running = False
        self.config = None
        self.loop = None
        self._shutdown_event = threading.Event()
        self._startup_complete = threading.Event()
        self._ready_complete = threading.Event()
        self._startup_error = None
        self._stop_requested = False
        
    def start_bot(self, config: WeComBotConfig):
        if self.is_running:
            raise Exception("企业微信机器人已在运行")
            
        self.config = config
        self._shutdown_event.clear()
        self._startup_complete.clear()
        self._ready_complete.clear()
        self._startup_error = None
        self._stop_requested = False
        
        self.bot_thread = threading.Thread(
            target=self._run_bot_thread,
            args=(config,),
            daemon=True,
            name="WeComBotThread"
        )
        self.bot_thread.start()
        
        if not self._startup_complete.wait(timeout=30):
            self.stop_bot()
            raise Exception("企业微信机器人连接超时")
            
        if self._startup_error:
            self.stop_bot()
            raise Exception(f"企业微信启动失败: {self._startup_error}")
        
    def _run_bot_thread(self, config):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            self.bot_client = WeComClient()
            self.bot_client.WeComAgent = config.WeComAgent
            self.bot_client.memoryLimit = config.memoryLimit
            self.bot_client.reasoningVisible = config.reasoningVisible
            self.bot_client.quickRestart = config.quickRestart
            self.bot_client.wakeWord = config.wakeWord
            self.bot_client._manager_ref = weakref.ref(self)
            
            # 初始加载时同步行为配置
            try:
                settings = self.loop.run_until_complete(load_settings())
                behavior_data = settings.get("behaviorSettings", {})
                target_ids = config.behaviorTargetChatIds or settings.get("weComBotConfig", {}).get("behaviorTargetChatIds", [])
                
                if behavior_data:
                    global_behavior_engine.update_config(behavior_data, {"wecom": target_ids})
                    logging.info(f"[WeComManager] 初始行为引擎配置已同步，目标数: {len(target_ids)}")
            except Exception as e:
                logging.error(f"企微初始同步行为配置失败: {e}")

            self.loop.run_until_complete(self._async_run_websocket())
            
        except Exception as e:
            if not self._stop_requested:
                logging.error(f"企业微信线程异常: {e}")
                self._startup_error = str(e)
            self._startup_complete.set()
            self._ready_complete.set()
        finally:
            self._cleanup()

    async def _async_run_websocket(self):
        try:
            options = WSClientOptions(bot_id=self.config.bot_id, secret=self.config.secret)
            self.bot_client.ws_client = WSClient(options)
            self.bot_client.register_events()
            
            if global_behavior_engine.is_running:
                global_behavior_engine.stop()
                await asyncio.sleep(0.5)
            asyncio.create_task(global_behavior_engine.start())
            
            # 标记状态
            self.is_running = True
            self._startup_complete.set()
            self._ready_complete.set()
            logging.info("[WeComManager] 状态已标记为 True，建立连接并进入保活...")

            # 建立连接并保活
            await self.bot_client.ws_client.connect()
            while not self._stop_requested:
                await asyncio.sleep(1.0)
                
        except Exception as e:
            self.is_running = False
            if not self._stop_requested:
                logging.error(f"企微长连接崩溃: {e}")
                self._startup_error = str(e)
            raise

    def _cleanup(self):
        self.is_running = False
        if self.bot_client:
            # 取消所有正在进行的任务
            for task in self.bot_client.active_tasks.values():
                task.cancel()
            if self.bot_client.ws_client:
                try: self.bot_client.ws_client.disconnect()
                except: pass
        if self.loop and not self.loop.is_closed():
            try: self.loop.close()
            except: pass
        self._shutdown_event.set()

    def stop_bot(self):
        self._stop_requested = True
        self.is_running = False
        if self.bot_thread and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=3)

    def get_status(self):
        return {
            "is_running": self.is_running,
            "thread_alive": self.bot_thread.is_alive() if self.bot_thread else False,
            "config": self.config.model_dump() if self.config else None,
            "startup_error": self._startup_error
        }

    def update_behavior_config(self, config: WeComBotConfig):
        self.config = config
        if self.bot_client:
            self.bot_client.WeComAgent = config.WeComAgent 
            self.bot_client.wakeWord = config.wakeWord
        global_behavior_engine.update_config(config.behaviorSettings, {"wecom": config.behaviorTargetChatIds})
        print(f"[WeComManager] 行为配置已成功热更新，目标数: {len(config.behaviorTargetChatIds)}")


class WeComClient:
    def __init__(self):
        self.WeComAgent = "super-model"
        self.memoryLimit = 10
        self.memoryList = {}
        self.reasoningVisible = False
        self.quickRestart = True
        self.port = get_port()
        self.ws_client: WSClient = None
        self._manager_ref = None
        self.wakeWord = None
        # 核心：追踪每个 chat_id 正在运行的任务
        self.active_tasks: Dict[str, asyncio.Task] = {}
        
        global_behavior_engine.register_handler("wecom", self.execute_behavior_event)

    def register_events(self):
        @self.ws_client.on('message.text')
        async def on_text(frame): await self.handle_message(frame, "text")
        @self.ws_client.on('message.image')
        async def on_image(frame): await self.handle_message(frame, "image")
        @self.ws_client.on('message.voice')
        async def on_voice(frame): await self.handle_message(frame, "voice")

    async def handle_message(self, frame, msg_type) -> None:
        """接收并调度消息处理任务（包含打断与指令逻辑）"""
        body = frame.get('body', {})
        chat_id = body.get('chatid', body.get('from', {}).get('userid', ''))
        global_behavior_engine.report_activity("wecom", chat_id)
        
        # 1. 快捷指令与打断检查（统一快捷指令，受系统设置全局开关控制）
        if msg_type == "text":
            from py import shortcut_commands
            user_text = body.get('text', {}).get('content', '').strip()
            if await shortcut_commands.im_shortcuts_enabled():
                action = shortcut_commands.parse_im_action(user_text)
                if action == "stop":
                    if chat_id in self.active_tasks:
                        self.active_tasks[chat_id].cancel()
                        await self.ws_client.reply_stream(frame, generate_req_id('stream'), shortcut_commands.STOP_MSG_ZH, True)
                    return
                if action == "reset":
                    if chat_id in self.active_tasks:
                        self.active_tasks[chat_id].cancel()
                    self.memoryList[chat_id] = []
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), shortcut_commands.RESET_MSG_ZH, True)
                    return
                if action == "help":
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), shortcut_commands.build_help_text("zh"), True)
                    return
                if action == "skills":
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), await shortcut_commands.build_skills_text("zh"), True)
                    return
                if action == "model":
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), await shortcut_commands.handle_model_command(user_text, "zh"), True)
                    return
                if action == "personality":
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), await shortcut_commands.handle_personality_command(user_text, "zh"), True)
                    return
                if action == "retry":
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), shortcut_commands.retry_hint("zh"), True)
                    return
                if action == "mode":
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), await shortcut_commands.build_mode_info_text("zh"), True)
                    return

        # 2. 如果当前有正在处理的任务，则打断它
        if chat_id in self.active_tasks:
            logging.info(f"[WeCom] 检测到新消息，正在打断会话 {chat_id} 的旧任务")
            self.active_tasks[chat_id].cancel()

        # 3. 创建新任务并记录
        current_task = asyncio.create_task(self._do_handle_message(frame, msg_type, chat_id))
        self.active_tasks[chat_id] = current_task
        
        try:
            await current_task
        except asyncio.CancelledError:
            logging.info(f"[WeCom] 会话 {chat_id} 的任务已被打断/取消")
        finally:
            if self.active_tasks.get(chat_id) == current_task:
                self.active_tasks.pop(chat_id, None)

    async def _do_handle_message(self, frame, msg_type, chat_id) -> None:
        """实际的 AI 处理逻辑，包含多模态解析与流式响应"""
        body = frame.get('body', {})
        client = AsyncOpenAI(api_key="sk", base_url=f"http://127.0.0.1:{self.port}/v1")
        
        if chat_id not in self.memoryList: self.memoryList[chat_id] = []
        
        user_text = ""
        user_content = []
        has_image = False
        
        # --- 解析逻辑保持不变 ---
        if msg_type == "text":
            user_text = body.get('text', {}).get('content', '')
            if "/id" in user_text.lower():
                await self.ws_client.reply_stream(frame, generate_req_id('stream'), f"ID: `{chat_id}`", True)
                return
            from py import shortcut_commands as _sc
            _sub = _sc.parse_subscribe_action(user_text)
            if _sub:
                _reply = await _sc.handle_subscribe_command("wecom", chat_id, _sub == "sub", "zh")
                if _reply:
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), _reply, True)
                return
            if self.wakeWord and self.wakeWord not in user_text: return

        elif msg_type == "image":
            image = body.get('image', {})
            if image.get('url'):
                buffer, _ = await self.ws_client.download_file(image.get('url'), image.get('aeskey'))
                base64_data = base64.b64encode(buffer).decode("utf-8")
                has_image = True
                user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"}})

        elif msg_type == "voice":
            voice = body.get('voice', {})
            if voice.get('url'):
                buffer, _ = await self.ws_client.download_file(voice.get('url'), voice.get('aeskey'))
                transcribed = await self._transcribe_audio(buffer)
                if transcribed:
                    user_text = transcribed
                    if self.wakeWord and self.wakeWord not in user_text: return
                else:
                    await self.ws_client.reply_stream(frame, generate_req_id('stream'), "语音识别失败", True)
                    return

        # 构造 OpenAI 格式
        if has_image:
            if user_text: user_content.append({"type": "text", "text": user_text})
            self.memoryList[chat_id].append({"role": "user", "content": user_content})
        else:
            if user_text: self.memoryList[chat_id].append({"role": "user", "content": user_text})
            else: return

        # LLM 响应
        stream_id = generate_req_id('stream')
        full_response_text = ""
        try:
            stream = await client.chat.completions.create(
                model=self.WeComAgent,
                messages=self.memoryList[chat_id],
                stream=True,
                extra_body={"is_app_bot": True, "platform": "wecom"}
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                content = delta.content or ""
                reasoning = getattr(delta, "reasoning_content", "") or ""
                
                display_content = (reasoning if self.reasoningVisible and reasoning else content)
                full_response_text += display_content
                
                if display_content:
                    # 企微 SDK 负责流式分段发送
                    await self.ws_client.reply_stream(frame, stream_id, full_response_text, False)
            
            # 发送最后一段
            await self.ws_client.reply_stream(frame, stream_id, full_response_text, True)
            self.memoryList[chat_id].append({"role": "assistant", "content": full_response_text})
            
            # 记忆长度限制
            if self.memoryLimit > 0:
                while len(self.memoryList[chat_id]) > self.memoryLimit:
                    self.memoryList[chat_id].pop(0)

            # TTS 语音逻辑可以在此通过异步任务触发 (保持现状)
            # manager = self._manager_ref()
            # if manager and manager.config.enableTTS:
            #     asyncio.create_task(self._send_voice(chat_id, full_response_text))
                
        except Exception as e:
            if not isinstance(e, asyncio.CancelledError):
                logging.error(f"企微回复异常: {e}")
                await self.ws_client.reply_stream(frame, stream_id, f"机器人异常: {e}", True)

    async def execute_behavior_event(self, chat_id: str, behavior_item: BehaviorItem):
        """行为引擎触发主动推送 (保持原有非流式逻辑)"""
        logging.info(f"[WeComClient] 行为触发 -> {chat_id}")
        prompt = behavior_item.action.prompt
        if behavior_item.action.type == "random":
            prompt = random.choice(behavior_item.action.random.events)
        if not prompt: return

        if chat_id not in self.memoryList: self.memoryList[chat_id] = []
        messages = self.memoryList[chat_id] + [{"role": "user", "content": f"[system]: {prompt}"}]

        try:
            client = AsyncOpenAI(api_key="sk", base_url=f"http://127.0.0.1:{self.port}/v1")
            response = await client.chat.completions.create(
                model=self.WeComAgent, messages=messages, stream=False, 
                extra_body={"is_app_bot": True, "platform": "wecom", "behavior_trigger": True}
            )
            reply = response.choices[0].message.content
            if reply:
                await self.ws_client.send_message(chat_id, {
                    'msgtype': 'markdown',
                    'markdown': {'content': reply}
                })
                self.memoryList[chat_id].append({"role": "assistant", "content": reply})
        except Exception as e:
            logging.error(f"企微主动推送失败: {e}")

    # ------------------ 工具方法 ------------------

    async def _transcribe_audio(self, audio_data: bytes) -> str:
        """ASR识别 (原有逻辑)"""
        try:
            form = aiohttp.FormData()
            form.add_field('audio', io.BytesIO(audio_data), filename="voice.amr", content_type='audio/amr')
            async with aiohttp.ClientSession() as session:
                async with session.post(f"http://127.0.0.1:{self.port}/asr", data=form) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        return res.get("text", "")
            return ""
        except: return ""

    async def _send_voice(self, chat_id: str, text: str):
        """发送语音气泡 (原有逻辑)"""
        try:
            manager = self._manager_ref()
            bot_key = manager.config.bot_id if manager and manager.config else None
            if not bot_key: return

            clean_text = re.sub(r'[*_#`~>]', '', text)
            if not clean_text.strip(): return
            
            async with aiohttp.ClientSession() as session:
                payload = {"text": clean_text, "voice": "default", "format": "mp3"} 
                async with session.post(f"http://127.0.0.1:{self.port}/tts", json=payload) as r:
                    if r.status != 200: return
                    mp3_data = await r.read()

                amr_data = await asyncio.to_thread(convert_to_amr_simple, mp3_data)
                if not amr_data: return

                upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key={bot_key}&type=voice"
                form = aiohttp.FormData()
                form.add_field('media', amr_data, filename='voice.amr', content_type='audio/amr')
                
                async with session.post(upload_url, data=form) as up_resp:
                    up_data = await up_resp.json()
                    media_id = up_data.get("media_id")
                
                if media_id:
                    send_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={bot_key}"
                    await session.post(send_url, json={"msgtype": "voice", "voice": {"media_id": media_id}})
        except: pass

    def __del__(self):
        try:
            for task in self.active_tasks.values(): task.cancel()
        except: pass