# py/wechat_bot_manager.py
import asyncio
import base64
import json
import random
import threading
from typing import Optional, List, Dict
import weakref
import logging
import re
import sys
import os
import glob
import shutil
import importlib
import io
import aiohttp
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

from py.get_setting import get_port, load_settings
from py.behavior_engine import BehaviorItem, global_behavior_engine, BehaviorSettings

try:
    import wechatbot
    from wechatbot import WeChatBot
except ImportError:
    WeChatBot = None
    wechatbot = None
    logging.warning("尚未安装 wechatbot-sdk，请执行 pip install wechatbot-sdk")

# 拦截控制台输出的工具，用于提取二维码
class StreamInterceptor:
    def __init__(self, original_stream, qr_callback, success_callback):
        self.original_stream = original_stream
        self.qr_callback = qr_callback
        self.success_callback = success_callback
        self.buffer = ""

    def write(self, text):
        self.original_stream.write(text)
        try:
            self.buffer += str(text)
            if "liteapp.weixin.qq.com/q/" in self.buffer:
                match = re.search(r'(https://liteapp\.weixin\.qq\.com/q/[a-zA-Z0-9_?=&]+)', self.buffer)
                if match:
                    self.qr_callback(match.group(1))
                    self.buffer = self.buffer.replace(match.group(0), "")
            
            lower_buf = self.buffer.lower()
            success_keywords =["login successfully", "log in successfully", "logged in as", "登录成功", "wechat login succeed"]
            if any(kw in lower_buf for kw in success_keywords):
                self.success_callback()
                self.buffer = ""
            if len(self.buffer) > 1000:
                self.buffer = self.buffer[-500:]
        except: pass

    def flush(self):
        self.original_stream.flush()
        
    def __getattr__(self, attr):
        return getattr(self.original_stream, attr)

class WeChatBotConfig(BaseModel):
    WeChatAgent: str = "super-model"
    memoryLimit: int = 30
    separators: List[str] =[]
    reasoningVisible: bool = False
    quickRestart: bool = True
    enableTTS: bool = False
    wakeWord: str = ""
    force_relogin: bool = False
    behaviorSettings: Optional[BehaviorSettings] = None
    behaviorTargetChatIds: List[str] = Field(default_factory=list)

class WeChatBotManager:
    def __init__(self):
        self.bot_thread: Optional[threading.Thread] = None
        self.bot_client: Optional['WeChatClient'] = None
        self.is_running = False
        self.config = None
        self.loop = None
        self._shutdown_event = threading.Event()
        self._startup_complete = threading.Event()
        self._ready_complete = threading.Event()
        self._startup_error = None
        self._stop_requested = False
        self.qr_url = None
        self.qr_base64 = None
        self.is_logged_in = False
        
    def start_bot(self, config: WeChatBotConfig):
        if self.is_running: raise Exception("微信机器人已在运行")
        if WeChatBot is None: raise Exception("尚未安装 wechatbot-sdk")
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
            name="WeChatBotThread"
        )
        self.bot_thread.start()
        if not self._startup_complete.wait(timeout=30):
            self.stop_bot()
            raise Exception("微信机器人连接超时")
        if self._startup_error:
            self.stop_bot()
            raise Exception(f"启动失败: {self._startup_error}")

    def _clear_wechat_cache(self):
        try:
            home_dir = os.path.expanduser("~")
            dirs_to_remove =[".wechatbot", "session", os.path.join(home_dir, ".wechatbot")]
            for d in dirs_to_remove:
                if os.path.exists(d): shutil.rmtree(d, ignore_errors=True)
            for f in glob.glob("*.pkl"): os.remove(f)
            logging.info("♻️ 强制清除微信登录缓存成功！")
        except: pass

    def _run_bot_thread(self, config):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            if config.force_relogin:
                self._clear_wechat_cache()
                config.force_relogin = False 
                if wechatbot: importlib.reload(wechatbot)
            
            bot = WeChatBot()
            self.bot_client = WeChatClient(bot)
            self.bot_client.WeChatAgent = config.WeChatAgent
            self.bot_client.memoryLimit = config.memoryLimit
            self.bot_client.separators = config.separators or[]
            self.bot_client.reasoningVisible = config.reasoningVisible
            self.bot_client.quickRestart = config.quickRestart
            self.bot_client.enableTTS = config.enableTTS
            self.bot_client.wakeWord = config.wakeWord
            self.bot_client._manager_ref = weakref.ref(self)

            try:
                settings = self.loop.run_until_complete(load_settings())
                behavior_data = settings.get("behaviorSettings", {})
                target_ids = config.behaviorTargetChatIds or settings.get("wechatBotConfig", {}).get("behaviorTargetChatIds",[])
                if behavior_data:
                    global_behavior_engine.update_config(behavior_data, {"wechat": target_ids})
            except: pass

            @bot.on_message
            async def handle_message_wrapper(msg):
                await self.bot_client.handle_message(msg)

            self.loop.run_until_complete(self._async_run_websocket())
        except Exception as e:
            self._startup_error = str(e)
            self._startup_complete.set()
        finally:
            self._cleanup()  

    async def _async_run_websocket(self):
        original_stdout, original_stderr = sys.stdout, sys.stderr
        try:
            self._startup_complete.set()
            self.is_running = True
            
            def _handle_qr(url):
                if self.qr_url: return  
                self.qr_url = url
                try:
                    import qrcode
                    qr = qrcode.QRCode(border=1)
                    qr.add_data(url)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buffered = io.BytesIO()
                    img.save(buffered, format="PNG")
                    self.qr_base64 = "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8")
                except: pass

            def _handle_success():
                self.is_logged_in = True
                self.qr_base64 = None  
                self.qr_url = None

            sys.stdout = StreamInterceptor(original_stdout, _handle_qr, _handle_success)
            sys.stderr = StreamInterceptor(original_stderr, _handle_qr, _handle_success)
            
            if global_behavior_engine.is_running: global_behavior_engine.stop()
            asyncio.create_task(global_behavior_engine.start())
            
            bot = self.bot_client.bot
            bot_task = asyncio.create_task(asyncio.to_thread(bot.run))
            await bot_task
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    def _cleanup(self):
        self.is_running = False
        self._shutdown_event.set()

    def stop_bot(self):
        self._stop_requested = True
        self.is_running = False
        if self.bot_client:
            bot = self.bot_client.bot
            # 取消所有活跃任务
            for task in self.bot_client.active_tasks.values():
                task.cancel()
            for m in['stop', 'logout', 'exit']:
                if hasattr(bot, m): getattr(bot, m)()
        if self.bot_thread: self.bot_thread.join(timeout=2)

    def get_status(self):
        return {
            "is_running": self.is_running,
            "startup_error": self._startup_error,
            "qr_url": self.qr_url,
            "qr_base64": getattr(self, 'qr_base64', None),
            "is_logged_in": getattr(self, 'is_logged_in', False)
        }

    def update_behavior_config(self, config: WeChatBotConfig):
        self.config = config
        global_behavior_engine.update_config(config.behaviorSettings, {"wechat": config.behaviorTargetChatIds})

class WeChatClient:
    def __init__(self, bot):
        self.bot = bot
        self.WeChatAgent = "super-model"
        self.memoryLimit = 10
        self.memoryList = {}
        self.separators =[]
        self.reasoningVisible = False
        self.quickRestart = True
        self.port = get_port()
        self._shutdown_requested = False
        self._manager_ref = None
        self.enableTTS = False
        self.wakeWord = None
        self.last_active_chat_id = None
        # 核心：追踪每个 chat_id 正在运行的任务
        self.active_tasks: Dict[str, asyncio.Task] = {}
        global_behavior_engine.register_handler("wechat", self.execute_behavior_event)

    async def handle_message(self, msg) -> None:
        """非阻塞消息入口：快速返回，让后续消息能立即处理"""
        if self._shutdown_requested: return
        chat_id = getattr(msg, 'user_id', 'unknown_user')
        self.last_active_chat_id = chat_id 
        global_behavior_engine.report_activity("wechat", chat_id)
        
        user_text = getattr(msg, 'text', '').strip()
        if not user_text: return

        # 1. 快捷指令与任务打断检查（统一快捷指令，受系统设置全局开关控制，非阻塞版）
        from py import shortcut_commands
        if await shortcut_commands.im_shortcuts_enabled():
            action = shortcut_commands.parse_im_action(user_text)
            if action == "stop":
                logging.info(f"收到停止指令: chat_id={chat_id}")
                if chat_id in self.active_tasks:
                    self.active_tasks[chat_id].cancel()
                asyncio.create_task(self._send_text(msg, shortcut_commands.STOP_MSG_ZH))
                return
            if action == "reset":
                logging.info(f"收到重启指令: chat_id={chat_id}")
                if chat_id in self.active_tasks:
                    self.active_tasks[chat_id].cancel()
                self.memoryList[chat_id] = []
                asyncio.create_task(self._send_text(msg, shortcut_commands.RESET_MSG_ZH))
                return
            if action == "help":
                asyncio.create_task(self._send_text(msg, shortcut_commands.build_help_text("zh")))
                return
            if action == "skills":
                _txt = await shortcut_commands.build_skills_text("zh")
                asyncio.create_task(self._send_text(msg, _txt))
                return
            if action == "model":
                _txt = await shortcut_commands.handle_model_command(user_text, "zh")
                asyncio.create_task(self._send_text(msg, _txt))
                return
            if action == "personality":
                _txt = await shortcut_commands.handle_personality_command(user_text, "zh")
                asyncio.create_task(self._send_text(msg, _txt))
                return
            if action == "retry":
                asyncio.create_task(self._send_text(msg, shortcut_commands.retry_hint("zh")))
                return
            if action == "mode":
                _txt = await shortcut_commands.build_mode_info_text("zh")
                asyncio.create_task(self._send_text(msg, _txt))
                return

        if "/id" in user_text.lower():
            asyncio.create_task(self._send_text(msg, f"🤖 当前 ChatID:\n`{chat_id}`"))
            return

        from py import shortcut_commands as _sc
        _sub = _sc.parse_subscribe_action(user_text)
        if _sub:
            _reply = await _sc.handle_subscribe_command("wechat", chat_id, _sub == "sub", "zh")
            if _reply:
                asyncio.create_task(self._send_text(msg, _reply))
            return

        # 2. 如果当前有正在处理的任务，则打断它
        if chat_id in self.active_tasks:
            logging.info(f"检测到新消息，正在打断会话 {chat_id} 的旧任务")
            self.active_tasks[chat_id].cancel()

        # 3. 创建并追踪新任务，但不等待（核心修改）
        task = asyncio.create_task(self._process_ai_logic(msg, chat_id, user_text))
        self.active_tasks[chat_id] = task
        task.add_done_callback(lambda t: self._task_done_callback(chat_id, t))
        # 立即返回，不做任何 await，彻底解除阻塞

    def _task_done_callback(self, chat_id: str, task: asyncio.Task):
        """任务结束时自动清理 active_tasks 中的引用"""
        if self.active_tasks.get(chat_id) == task:
            self.active_tasks.pop(chat_id, None)

    async def _process_ai_logic(self, msg, chat_id, user_text):
        """核心 AI 处理逻辑，支持流式和打断"""
        if chat_id not in self.memoryList: self.memoryList[chat_id] = []
        self.memoryList[chat_id].append({"role": "user", "content": user_text})

        client = AsyncOpenAI(api_key="super-secret-key", base_url=f"http://127.0.0.1:{self.port}/v1")
        state = {"text_buffer": "", "image_cache":[]}
        
        try:
            stream = await client.chat.completions.create(
                model=self.WeChatAgent,
                messages=self.memoryList[chat_id],
                stream=True,
                extra_body={"is_app_bot": True, "platform": "wechat"}
            )
            
            full_response =[]
            async for chunk in stream:
                if not chunk.choices: continue
                delta = chunk.choices[0].delta
                content = delta.content or ""
                
                if hasattr(delta, "reasoning_content") and delta.reasoning_content and self.reasoningVisible:
                    content = delta.reasoning_content
                
                full_response.append(content)
                state["text_buffer"] += content
                
                # 实时提取图片
                self._extract_images(state)
                
                # 分段发送文本
                buffer = state["text_buffer"]
                split_pos = -1
                for sep in self.separators:
                    pos = buffer.find(sep)
                    if pos != -1:
                        split_pos = pos + len(sep)
                        break
                
                if split_pos != -1:
                    current_chunk = buffer[:split_pos]
                    state["text_buffer"] = buffer[split_pos:]
                    clean_text = self._clean_text(current_chunk)
                    if clean_text: await self._send_text(msg, clean_text)

            # 发送剩余文本
            if state["text_buffer"]:
                clean_text = self._clean_text(state["text_buffer"])
                if clean_text: await self._send_text(msg, clean_text)
            
            # 发送缓存的图片
            for img_url in state["image_cache"]:
                await self._send_image(chat_id, img_url)

            # 更新记忆
            full_content = "".join(full_response)
            self.memoryList[chat_id].append({"role": "assistant", "content": full_content})
            
            # 限制记忆长度
            if self.memoryLimit > 0:
                while len(self.memoryList[chat_id]) > self.memoryLimit:
                    self.memoryList[chat_id].pop(0)

            if self.enableTTS: 
                await self._send_voice(msg, full_content)

        except asyncio.CancelledError:
            # 重要：重新抛出取消异常，让任务状态正确
            raise
        except Exception as e:
            logging.error(f"微信消息处理异常: {e}")

    def _extract_images(self, state):
        """从 text_buffer 中提取 Markdown 图片 URL"""
        buffer = state["text_buffer"]
        pattern = r'!\[.*?\]\((https?://[^\s\)]+)\)'
        matches = re.findall(pattern, buffer)
        for url in matches:
            if url not in state["image_cache"]:
                state["image_cache"].append(url)

    async def _send_image(self, target_id, image_url):
        """下载并发送图片"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url, timeout=30) as resp:
                    if resp.status != 200: return
                    img_data = await resp.read()
            
            content_dict = {"file": img_data, "file_name": "ai_image.png"}
            if hasattr(self.bot, 'send_media'):
                await self.bot.send_media(target_id, content_dict)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error(f"发送图片失败: {e}")

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        text = re.sub(r'<.*?>', '', text)
        return text.strip()
    
    async def _send_text(self, msg, text):
        if hasattr(self.bot, 'reply'): await self.bot.reply(msg, text)

    async def execute_behavior_event(self, chat_id: str, behavior_item: BehaviorItem):
        """主动行为逻辑（非阻塞版，支持被打断）"""
        target_id = chat_id or getattr(self, 'last_active_chat_id', None)
        if not target_id: return
        if not self.bot._context_tokens.get(target_id): return

        prompt_content = await self._resolve_behavior_prompt(behavior_item)
        if not prompt_content: return

        # 打断旧任务
        if target_id in self.active_tasks:
            logging.info(f"行为引擎触发，正在打断会话 {target_id} 的旧任务")
            self.active_tasks[target_id].cancel()

        # 创建后台任务，立即返回
        task = asyncio.create_task(self._process_behavior_logic(target_id, prompt_content))
        self.active_tasks[target_id] = task
        task.add_done_callback(lambda t: self._task_done_callback(target_id, t))

    async def _process_behavior_logic(self, target_id: str, prompt_content: str):
        """核心的主动行为 AI 请求子逻辑"""
        client = AsyncOpenAI(api_key="super-secret-key", base_url=f"http://127.0.0.1:{self.port}/v1")
        try:
            response = await client.chat.completions.create(
                model=self.WeChatAgent,
                messages=[{"role": "user", "content": f"[system]: {prompt_content}"}],
                stream=False,
                extra_body={"is_app_bot": True, "platform": "wechat", "behavior_trigger": True}
            )
            full_content = response.choices[0].message.content
            
            temp_state = {"text_buffer": full_content, "image_cache":[]}
            self._extract_images(temp_state)
            
            clean_content = self._clean_text(full_content)
            if clean_content:
                await self.bot.send(target_id, clean_content)
                for url in temp_state["image_cache"]:
                    await self._send_image(target_id, url)
                if self.enableTTS: await self._send_voice(target_id, full_content)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error(f"执行主动行为失败: {e}")

    async def _resolve_behavior_prompt(self, behavior: BehaviorItem) -> str:
        action = behavior.action
        if action.type == "prompt": return action.prompt
        elif action.type == "random": return random.choice(action.random.events)
        return None

    async def _send_voice(self, target, text):
        try:
            settings = await load_settings()
            tts_settings = settings.get("ttsSettings", {})
            clean_tts_text = self._clean_text(text)
            if not clean_tts_text: return
            
            async with aiohttp.ClientSession() as session:
                payload = {"text": clean_tts_text, "voice": "default", "ttsSettings": tts_settings, "index": 0, "format": "mp3"}
                async with session.post(f"http://127.0.0.1:{self.port}/tts", json=payload) as resp:
                    if resp.status != 200: return
                    audio_bytes = await resp.read()
                    
                    chat_id = getattr(target, 'user_id', target)
                    content_dict = {"file": audio_bytes, "file_name": "voice.mp3"}
                    if hasattr(self.bot, 'send_media'):
                        await self.bot.send_media(chat_id, content_dict)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error(f"语音生成/发送失败: {e}")

    def __del__(self):
        try:
            for task in self.active_tasks.values(): task.cancel()
        except: pass