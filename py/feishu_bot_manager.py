# feishu_bot_manager.py
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
import re
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

from py.get_setting import convert_to_opus_simple, get_port, load_settings
from py.behavior_engine import BehaviorItem, global_behavior_engine, BehaviorSettings

# 飞书机器人配置模型
class FeishuBotConfig(BaseModel):
    FeishuAgent: str          # LLM模型名
    memoryLimit: int          # 记忆条数限制
    appid: str                # 飞书APP_ID
    secret: str               # 飞书APP_SECRET
    separators: List[str]     # 消息分段符
    reasoningVisible: bool    # 是否显示推理过程
    quickRestart: bool        # 快速重启指令开关
    enableTTS: bool           # 是否启用TTS
    wakeWord: str             # 唤醒词
    behaviorSettings: Optional[BehaviorSettings] = None
    behaviorTargetChatIds: List[str] = Field(default_factory=list)

class FeishuBotManager:
    def __init__(self):
        self.bot_thread: Optional[threading.Thread] = None
        self.bot_client: Optional[FeishuClient] = None
        self.is_running = False
        self.config = None
        self.loop = None
        self._shutdown_event = threading.Event()
        self._startup_complete = threading.Event()
        self._ready_complete = threading.Event()
        self._startup_error = None
        self.ws = None  
        self._stop_requested = False  
        
    def start_bot(self, config):
        if self.is_running:
            raise Exception("飞书机器人已在运行")
            
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
            name="FeishuBotThread"
        )
        self.bot_thread.start()
        
        if not self._startup_complete.wait(timeout=30):
            self.stop_bot()
            raise Exception("飞书机器人连接超时")
            
        if self._startup_error:
            self.stop_bot()
            raise Exception(f"飞书机器人启动失败: {self._startup_error}")
        
        if not self._ready_complete.wait(timeout=30):
            self.stop_bot()
            raise Exception("飞书机器人就绪超时，请检查网络连接和配置")
            
        if not self.is_running:
            self.stop_bot()
            raise Exception("飞书机器人未能正常运行")
    
    def _run_bot_thread(self, config):
        self.loop = None
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            self.bot_client = FeishuClient()
            self.bot_client.FeishuAgent = config.FeishuAgent
            self.bot_client.memoryLimit = config.memoryLimit
            self.bot_client.separators = config.separators if config.separators else []
            self.bot_client.reasoningVisible = config.reasoningVisible
            self.bot_client.quickRestart = config.quickRestart
            self.bot_client.appid = config.appid
            self.bot_client.secret = config.secret
            self.bot_client.enableTTS = config.enableTTS
            self.bot_client.wakeWord = config.wakeWord
            self.bot_client._manager_ref = weakref.ref(self)
            self.bot_client._ready_callback = self._on_bot_ready

            try:
                settings = asyncio.run(load_settings())
                behavior_data = settings.get("behaviorSettings", {})
                target_ids = config.behaviorTargetChatIds or settings.get("feishuBotConfig", {}).get("behaviorTargetChatIds", [])
                
                if behavior_data:
                    logging.info(f"飞书同步行为配置中... 目标: {len(target_ids)}")
                    global_behavior_engine.update_config(behavior_data, {"feishu": target_ids})
            except Exception as e:
                logging.error(f"飞书行为配置同步失败: {e}")

            import lark_oapi as lark
            lark_client = lark.Client.builder().app_id(config.appid).app_secret(config.secret).build()
            self.bot_client.lark_client = lark_client
            
            event_dispatcher = lark.EventDispatcherHandler.builder("", "")\
                .register_p2_im_message_receive_v1(self.bot_client.sync_handle_message)\
                .build()
                
            self.ws = lark.ws.Client(
                config.appid, 
                config.secret,
                event_handler=event_dispatcher,
                auto_reconnect=False
            )
            
            self.loop.run_until_complete(self._async_run_websocket())
            
        except Exception as e:
            if not self._stop_requested:
                self._startup_error = str(e)
            if not self._startup_complete.is_set(): self._startup_complete.set()
            if not self._ready_complete.is_set(): self._ready_complete.set()
        finally:
            self._cleanup()  

    async def _async_run_websocket(self):
        try:
            await self.ws._connect()
            self._startup_complete.set()
            self._ready_complete.set()
            self.is_running = True
            
            ping_task = asyncio.create_task(self.ws._ping_loop())
            receive_task = asyncio.create_task(self._message_receive_loop())
            
            if global_behavior_engine.is_running:
                global_behavior_engine.stop()
                await asyncio.sleep(0.5)

            behavior_task = asyncio.create_task(global_behavior_engine.start())
            await asyncio.gather(ping_task, receive_task, behavior_task, return_exceptions=True)
        except Exception as e:
            if not self._stop_requested: self._startup_error = str(e)
            raise

    async def _message_receive_loop(self):
        while not self._stop_requested and not self._shutdown_event.is_set():
            if self.ws._conn is None: break
            try:
                msg = await asyncio.wait_for(self.ws._conn.recv(), timeout=1.0)
                asyncio.create_task(self.ws._handle_message(msg))
            except asyncio.TimeoutError: continue
            except: break
    
    def _on_bot_ready(self):
        self.is_running = True
        self._ready_complete.set()

    def _cleanup(self):
        self.is_running = False
        if global_behavior_engine.is_running: global_behavior_engine.stop()
        if self.ws and self.loop and not self.loop.is_closed():
            try:
                if asyncio.iscoroutinefunction(self.ws._disconnect):
                    self.loop.run_until_complete(self.ws._disconnect())
            except: pass
        if self.loop and not self.loop.is_closed():
            try:
                for task in asyncio.all_tasks(self.loop): task.cancel()
                self.loop.close()
            except: pass
        self._shutdown_event.set()

    def stop_bot(self):
        if not self.is_running and not self.bot_thread: return
        self._stop_requested = True
        self._shutdown_event.set()
        self.is_running = False
        
        # 取消所有正在进行的对话任务
        if self.bot_client:
            for task in self.bot_client.active_tasks.values():
                task.cancel()

        if self.loop and not self.loop.is_closed():
            try:
                if self.ws and hasattr(self.ws, '_disconnect'):
                    asyncio.run_coroutine_threadsafe(self.ws._disconnect(), self.loop)
            except: pass
        
        if self.bot_thread and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=5)
        self._stop_requested = False

    def get_status(self):
        return {
            "is_running": self.is_running,
            "thread_alive": self.bot_thread.is_alive() if self.bot_thread else False,
            "config": self.config.model_dump() if self.config else None,
            "startup_error": self._startup_error
        }

    def update_behavior_config(self, config: FeishuBotConfig):
        self.config = config
        if self.bot_client:
            self.bot_client.FeishuAgent = config.FeishuAgent 
            self.bot_client.enableTTS = config.enableTTS
            self.bot_client.wakeWord = config.wakeWord
        global_behavior_engine.update_config(config.behaviorSettings, {"feishu": config.behaviorTargetChatIds})

class FeishuClient:
    def __init__(self):
        self.FeishuAgent = "super-model"
        self.memoryLimit = 10
        self.memoryList = {}
        self.asyncToolsID = {}
        self.fileLinks = {}
        self.separators = []
        self.reasoningVisible = False
        self.quickRestart = True
        self._is_ready = False
        self.appid = None
        self.secret = None
        self.lark_client = None
        self.port = get_port()
        self._shutdown_requested = False
        self._manager_ref = None
        self._ready_callback = None
        self.enableTTS = False
        self.wakeWord = None
        # 核心：追踪当前任务，实现打断功能
        self.active_tasks: Dict[str, asyncio.Task] = {}
        global_behavior_engine.register_handler("feishu", self.execute_behavior_event)
        
    def sync_handle_message(self, data) -> None:
        if self._shutdown_requested: return
        if self._manager_ref and self._manager_ref()._stop_requested: return
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed(): return
            # 在协程中处理消息以便支持 cancel
            asyncio.run_coroutine_threadsafe(self.handle_message(data), loop)
        except Exception as e:
            logging.error(f"同步处理异常: {e}")

    async def handle_message(self, data) -> None:
        """接收并调度消息处理任务（包含打断逻辑）"""
        if self._shutdown_requested: return
        if not self._is_ready:
            self._is_ready = True
            if self._ready_callback: self._ready_callback()
        
        chat_id = data.event.message.chat_id
        msg_type = data.event.message.message_type
        global_behavior_engine.report_activity("feishu", chat_id)

        # 1. 快捷指令与任务打断检查（统一快捷指令，受系统设置全局开关控制）
        if msg_type == "text":
            try:
                from py import shortcut_commands
                text = json.loads(data.event.message.content).get("text", "").strip()
                if await shortcut_commands.im_shortcuts_enabled():
                    action = shortcut_commands.parse_im_action(text)
                    if action == "stop":
                        if chat_id in self.active_tasks:
                            self.active_tasks[chat_id].cancel()
                            await self._send_text(data.event.message, shortcut_commands.STOP_MSG_ZH)
                        return
                    if action == "reset":
                        if chat_id in self.active_tasks:
                            self.active_tasks[chat_id].cancel()
                        self.memoryList[chat_id] = []
                        await self._send_text(data.event.message, shortcut_commands.RESET_MSG_ZH)
                        return
                    if action == "help":
                        await self._send_text(data.event.message, shortcut_commands.build_help_text("zh"))
                        return
                    if action == "skills":
                        await self._send_text(data.event.message, await shortcut_commands.build_skills_text("zh"))
                        return
                    if action == "model":
                        await self._send_text(data.event.message, await shortcut_commands.handle_model_command(text, "zh"))
                        return
                    if action == "personality":
                        await self._send_text(data.event.message, await shortcut_commands.handle_personality_command(text, "zh"))
                        return
                    if action == "retry":
                        await self._send_text(data.event.message, shortcut_commands.retry_hint("zh"))
                        return
                    if action == "mode":
                        await self._send_text(data.event.message, await shortcut_commands.build_mode_info_text("zh"))
                        return
            except: pass

        # 2. 如果当前有任务正在运行，直接打断
        if chat_id in self.active_tasks:
            logging.info(f"检测到新消息，打断会话 {chat_id} 的旧任务")
            self.active_tasks[chat_id].cancel()

        # 3. 创建处理任务并记录
        current_task = asyncio.create_task(self._do_handle_message(data))
        self.active_tasks[chat_id] = current_task
        
        try:
            await current_task
        except asyncio.CancelledError:
            logging.info(f"会话 {chat_id} 的旧任务已安全退出")
        finally:
            if self.active_tasks.get(chat_id) == current_task:
                self.active_tasks.pop(chat_id, None)

    async def _do_handle_message(self, data) -> None:
        """实际的消息处理逻辑（原 handle_message 的全部内容）"""
        msg = data.event.message
        msg_type = msg.message_type
        chat_id = msg.chat_id
        
        client = AsyncOpenAI(api_key="super-secret-key", base_url=f"http://127.0.0.1:{self.port}/v1")
        settings = await load_settings()

        if chat_id not in self.memoryList: self.memoryList[chat_id] = []
            
        user_content = []
        user_text = ""
        has_image = False
        
        # --- 解析逻辑保持不变 ---
        if msg_type == "text":
            text = json.loads(msg.content).get("text", "")
            if "/id" in text.lower():
                await self._send_text(msg, f"🤖 **会话信息**\n\nChatID:\n`{chat_id}`")
                return
            from py import shortcut_commands as _sc
            _sub = _sc.parse_subscribe_action(text)
            if _sub:
                _reply = await _sc.handle_subscribe_command("feishu", chat_id, _sub == "sub", "zh")
                if _reply:
                    await self._send_text(msg, _reply)
                return
            user_text = text
            if self.wakeWord and self.wakeWord not in user_text: return
        elif msg_type == "image":
            image_key = json.loads(msg.content).get("image_key", "")
            if image_key:
                from lark_oapi.api.im.v1 import GetMessageResourceRequest as ResReq
                res_resp = self.lark_client.im.v1.message_resource.get(ResReq.builder().message_id(msg.message_id).file_key(image_key).type("image").build())
                if res_resp.success():
                    img_bin = res_resp.file.read()
                    base64_data = base64.b64encode(img_bin).decode("utf-8")
                    has_image = True
                    user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"}})
        elif msg_type == "post":
            content_json = json.loads(msg.content)
            user_text = self._extract_text_from_post(content_json)
            for image_key in self._extract_images_from_post(content_json):
                from lark_oapi.api.im.v1 import GetMessageResourceRequest as ResReq
                res_resp = self.lark_client.im.v1.message_resource.get(ResReq.builder().message_id(msg.message_id).file_key(image_key).type("image").build())
                if res_resp.success():
                    img_bin = res_resp.file.read()
                    base64_data = base64.b64encode(img_bin).decode("utf-8")
                    has_image = True
                    user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"}})
        elif msg_type == "audio":
            file_key = json.loads(msg.content).get("file_key", "")
            if file_key:
                from lark_oapi.api.im.v1 import GetMessageResourceRequest as ResReq
                res_resp = self.lark_client.im.v1.message_resource.get(ResReq.builder().message_id(msg.message_id).file_key(file_key).type("file").build())
                if res_resp.success():
                    user_text = await self._transcribe_audio(res_resp.file.read(), file_key)
                    if not user_text or (self.wakeWord and self.wakeWord not in user_text): return
        else: return

        if has_image:
            if user_text: user_content.append({"type": "text", "text": user_text})
            self.memoryList[chat_id].append({"role": "user", "content": user_content})
        else:
            if user_text: self.memoryList[chat_id].append({"role": "user", "content": user_text})
            else: return

        # AI 请求逻辑
        state = {"text_buffer": "", "image_buffer": "", "image_cache": [], "audio_buffer": []}
        try:
            asyncToolsID = self.asyncToolsID.setdefault(chat_id, [])
            fileLinks = self.fileLinks.setdefault(chat_id, [])
            
            stream = await client.chat.completions.create(
                model=self.FeishuAgent,
                messages=self.memoryList[chat_id],
                stream=True,
                extra_body={"asyncToolsID": asyncToolsID, "fileLinks": fileLinks, "is_app_bot": True, "platform": "feishu"}
            )
            
            full_response = []
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, "audio") and delta.audio and "data" in delta.audio:
                        state["audio_buffer"].append(delta.audio["data"])
                    if hasattr(delta, "async_tool_id") and delta.async_tool_id:
                        tid = delta.async_tool_id
                        if tid not in self.asyncToolsID[chat_id]: self.asyncToolsID[chat_id].append(tid)
                        else: self.asyncToolsID[chat_id].remove(tid)
                    if hasattr(delta, "tool_link") and delta.tool_link:
                        if settings["tools"]["toolMemorandum"]["enabled"]: self.fileLinks[chat_id].append(delta.tool_link)

                    content = delta.content or ""
                    if self.reasoningVisible and hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        content = delta.reasoning_content
                    
                    full_response.append(delta.content or "")
                    state["text_buffer"] += content
                    state["image_buffer"] += content
                    
                    # 分段发送（结构感知：不切断代码块/表格）
                    if state["text_buffer"]:
                        buffer = state["text_buffer"]
                        while True:
                            chunk, buffer = self._take_sendable_unit(buffer)
                            if chunk is None:
                                break
                            clean = self._clean_text(chunk)
                            if clean: await self._send_text(msg, clean)
                        state["text_buffer"] = buffer
            
            # 处理收尾
            self._extract_images(state)
            if state["text_buffer"]:
                leftover = state["text_buffer"]
                # 收尾时若存在未闭合的代码块围栏，补上闭合围栏以保证卡片正确渲染
                if leftover.count("```") % 2 == 1:
                    leftover = leftover.rstrip() + "\n```"
                clean = self._clean_text(leftover)
                if clean: await self._send_text(msg, clean)
            for img_url in state["image_cache"]: await self._send_image(msg, img_url)
            
            # Omni音频处理
            has_omni = False
            if state["audio_buffer"]:
                final_audio, is_opus = await asyncio.to_thread(convert_to_opus_simple, base64.b64decode("".join(state["audio_buffer"])))
                await self._send_omni_response(msg, final_audio, is_opus)
                has_omni = True

            full_content = "".join(full_response)
            if self.enableTTS and not has_omni: await self._send_voice(msg, full_content)
            self.memoryList[chat_id].append({"role": "assistant", "content": full_content})
            
            if self.memoryLimit > 0:
                while len(self.memoryList[chat_id]) > self.memoryLimit * 2:
                    self.memoryList[chat_id].pop(0)

        except Exception as e:
            if not isinstance(e, asyncio.CancelledError):
                logging.error(f"对话异常: {e}")
                await self._send_text(msg, f"对话中断: {str(e)}")

    # --- 后续所有辅助工具方法均保持原样（Omni, TTS, ASR, Upload等） ---

    async def _send_omni_response(self, original_msg, audio_data: bytes, is_opus: bool):
        try:
            file_type = "opus" if is_opus else "wav"
            file_name = f"reply.{file_type}"
            msg_type = "audio" if is_opus else "file"
            
            from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody
            upload_resp = self.lark_client.im.v1.file.create(CreateFileRequest.builder().request_body(CreateFileRequestBody.builder().file_type(file_type).file_name(file_name).file(io.BytesIO(audio_data)).build()).build())
            if not upload_resp.success(): return
            
            content_str = json.dumps({"file_key": upload_resp.data.file_key})
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, ReplyMessageRequest, ReplyMessageRequestBody
            if original_msg.chat_type == "p2p":
                req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(CreateMessageRequestBody.builder().receive_id(original_msg.chat_id).msg_type(msg_type).content(content_str).build()).build()
                self.lark_client.im.v1.message.create(req)
            else:
                req = ReplyMessageRequest.builder().message_id(original_msg.message_id).request_body(ReplyMessageRequestBody.builder().msg_type(msg_type).content(content_str).build()).build()
                self.lark_client.im.v1.message.reply(req)
        except: pass

    async def _transcribe_audio(self, audio_data: bytes, file_key: str) -> str:
        try:
            form_data = aiohttp.FormData()
            form_data.add_field('audio', io.BytesIO(audio_data), filename=f"{file_key}.ogg", content_type='audio/ogg')
            form_data.add_field('format', 'auto')
            async with aiohttp.ClientSession() as session:
                async with session.post(f"http://127.0.0.1:{self.port}/asr", data=form_data, timeout=60) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        return res.get("text", "").strip() if res.get("success") else None
        except: return None

    def clean_markdown(self, buffer):
        buffer = re.sub(r'#{1,6}\s', '', buffer, flags=re.MULTILINE)
        buffer = re.sub(r'[*_~`]+', '', buffer)
        buffer = re.sub(r'^\s*[-*]\s', '', buffer, flags=re.MULTILINE)
        buffer = re.sub(r'[\u2600-\u27BF\U0001F300-\U0001F9FF]', '', buffer)
        buffer = re.sub(r'!\[.*?\]\(.*?\)', '', buffer)
        buffer = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', buffer)
        return buffer.strip()

    async def _send_voice(self, original_msg, text):
        try:
            settings = await load_settings()
            clean_t = self.clean_markdown(text)
            payload = {"text": clean_t, "voice": "default", "ttsSettings": settings.get("ttsSettings", {}), "index": 0, "mobile_optimized": True, "format": "opus"}
            async with aiohttp.ClientSession() as session:
                async with session.post(f"http://127.0.0.1:{self.port}/tts", json=payload, timeout=90) as resp:
                    if resp.status != 200: return
                    opus_data = await resp.read()
                    from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody
                    upload_resp = self.lark_client.im.v1.file.create(CreateFileRequest.builder().request_body(CreateFileRequestBody.builder().file_type("opus").file_name("v.opus").file(io.BytesIO(opus_data)).build()).build())
                    if not upload_resp.success(): return
                    key = upload_resp.data.file_key
                    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, ReplyMessageRequest, ReplyMessageRequestBody
                    if original_msg.chat_type == "p2p":
                        self.lark_client.im.v1.message.create(CreateMessageRequest.builder().receive_id_type("chat_id").request_body(CreateMessageRequestBody.builder().receive_id(original_msg.chat_id).msg_type("audio").content(json.dumps({"file_key": key})).build()).build())
                    else:
                        self.lark_client.im.v1.message.reply(ReplyMessageRequest.builder().message_id(original_msg.message_id).request_body(ReplyMessageRequestBody.builder().msg_type("audio").content(json.dumps({"file_key": key})).build()).build())
        except: pass

    def _extract_text_from_post(self, post_content):
        res = []
        try:
            if isinstance(post_content, dict):
                if post_content.get("title"): res.append(post_content["title"])
                if "content" in post_content:
                    for para in post_content["content"]:
                        for el in para:
                            if el.get("tag") in ["text", "a"]: res.append(el.get("text", ""))
                            elif el.get("tag") == "at": res.append(f"@{el.get('user_name', '')}")
        except: pass
        return "\n".join(res)

    def _extract_images_from_post(self, post_content):
        keys = []
        try:
            if isinstance(post_content, dict) and "content" in post_content:
                for para in post_content["content"]:
                    for el in para:
                        if el.get("tag") in ["img", "media"] and el.get("image_key"): keys.append(el["image_key"])
        except: pass
        return keys

    def _extract_images(self, state):
        pattern = r'!\[.*?\]\((https?://[^\s\)]+)'
        for match in re.finditer(pattern, state["image_buffer"]): state["image_cache"].append(match.group(1))
    
    def _is_table_line(self, line: str) -> bool:
        s = line.strip()
        return s.startswith("|") and s.count("|") >= 2

    def _take_sendable_unit(self, buffer: str):
        """从缓冲区头部取出一个"完整且可安全发送"的单元。
        永远不会切断围栏代码块或 Markdown 表格。
        返回 (chunk, remaining)；若暂时没有完整单元可发送，返回 (None, buffer) 等待更多内容。"""
        if not buffer:
            return None, buffer

        nl = buffer.find("\n")
        first_line = buffer if nl == -1 else buffer[:nl]

        # 1) 代码块：缓冲区开头是围栏，必须等到闭合围栏后整体发送
        if first_line.lstrip().startswith("```"):
            open_end = (nl + 1) if nl != -1 else len(buffer)
            close = buffer.find("```", open_end)
            if close == -1:
                return None, buffer
            close_nl = buffer.find("\n", close)
            if close_nl == -1:
                return None, buffer
            return buffer[:close_nl + 1], buffer[close_nl + 1:]

        # 2) 表格：累积连续的表格行，等出现"完整的"非表格行确认表格结束后整体发送
        if self._is_table_line(first_line):
            ends_nl = buffer.endswith("\n")
            lines = buffer.split("\n")
            # buffer 末尾若没有换行符，最后一个分片是尚未接收完整的行，不能用于判断
            last_complete = len(lines) if ends_nl else len(lines) - 1
            i = 0
            while i < len(lines) and self._is_table_line(lines[i]):
                i += 1
            # 终止行必须是一个"完整且非空"的非表格行，否则可能是流式分片边界，需继续等待
            if i >= last_complete:
                return None, buffer
            if lines[i] == "" and i == len(lines) - 1:
                return None, buffer
            chunk = "\n".join(lines[:i]) + "\n"
            remaining = "\n".join(lines[i:])
            return chunk, remaining

        # 3) 普通文本：按配置的分隔符在最早位置切分，但不得越过后续代码块的起始
        split_pos = -1
        for sep in self.separators:
            pos = buffer.find(sep)
            if pos != -1:
                end = pos + len(sep)
                if split_pos == -1 or end < split_pos:
                    split_pos = end
        fence = buffer.find("```")
        if fence > 0 and (split_pos == -1 or fence < split_pos):
            return buffer[:fence], buffer[fence:]
        if split_pos == -1:
            return None, buffer
        return buffer[:split_pos], buffer[split_pos:]

    def _clean_text(self, text: str) -> str:
        # 按代码块分段，代码块内部原样保留（避免误删 <T> 等内容），仅清理代码块之外的图片/HTML 标签与独立分割线
        parts = re.split(r'(```[\s\S]*?```)', text)
        out = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                out.append(part)
            else:
                part = re.sub(r"!\[.*?\]\(.*?\)", "", part)
                part = re.sub(r'<[^>]+>', '', part)
                part = re.sub(r'(?m)^[ \t]*([-*_])\1{2,}[ \t]*$', '', part)
                out.append(part)
        return "".join(out).strip()
    
    async def _send_text(self, original_msg, text):
        try:
            if not text: return
            # 使用交互卡片(card 2.0)的 markdown 组件发送，支持代码块/表格/分割线等完整 Markdown，
            # 避免飞书 post 富文本的 md 标签把代码块渲染成 [代码块]。
            card = {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": text}]}}
            content = json.dumps(card)
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, ReplyMessageRequest, ReplyMessageRequestBody
            if original_msg.chat_type == "p2p":
                req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(CreateMessageRequestBody.builder().receive_id(original_msg.chat_id).msg_type("interactive").content(content).build()).build()
                self.lark_client.im.v1.message.create(req)
            else:
                req = ReplyMessageRequest.builder().message_id(original_msg.message_id).request_body(ReplyMessageRequestBody.builder().msg_type("interactive").content(content).build()).build()
                self.lark_client.im.v1.message.reply(req)
        except: pass
                
    async def _send_image(self, original_msg, image_url):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as r:
                    if r.status != 200: return
                    data = await r.read()
            from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
            up_resp = self.lark_client.im.v1.image.create(CreateImageRequest.builder().request_body(CreateImageRequestBody.builder().image_type("message").image(io.BytesIO(data)).build()).build())
            if not up_resp.success(): return
            key = up_resp.data.image_key
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, ReplyMessageRequest, ReplyMessageRequestBody
            if original_msg.chat_type == "p2p":
                self.lark_client.im.v1.message.create(CreateMessageRequest.builder().receive_id_type("chat_id").request_body(CreateMessageRequestBody.builder().receive_id(original_msg.chat_id).msg_type("image").content(json.dumps({"image_key": key})).build()).build())
            else:
                self.lark_client.im.v1.message.reply(ReplyMessageRequest.builder().message_id(original_msg.message_id).request_body(ReplyMessageRequestBody.builder().msg_type("image").content(json.dumps({"image_key": key})).build()).build())
        except: pass

    async def execute_behavior_event(self, chat_id: str, behavior_item: BehaviorItem):
        prompt = await self._resolve_behavior_prompt(behavior_item)
        if not prompt: return
        class Mock:
            def __init__(self, cid): self.chat_id = cid; self.message_id = None; self.chat_type = "p2p"
        mock = Mock(chat_id)
        if chat_id not in self.memoryList: self.memoryList[chat_id] = []
        messages = self.memoryList[chat_id].copy()
        messages.append({"role": "user", "content": f"[system]: {prompt}"})
        try:
            client = AsyncOpenAI(api_key="super-secret-key", base_url=f"http://127.0.0.1:{self.port}/v1")
            resp = await client.chat.completions.create(model=self.FeishuAgent, messages=messages, stream=False, extra_body={"is_app_bot": True, "platform": "feishu", "behavior_trigger": True})
            reply = resp.choices[0].message.content
            if reply:
                await self._send_text(mock, reply)
                self.memoryList[chat_id].append({"role": "user", "content": f"[system]: {prompt}"})
                self.memoryList[chat_id].append({"role": "assistant", "content": reply})
                if self.enableTTS: await self._send_voice(mock, reply)
        except: pass

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

    def __del__(self):
        try:
            for t in self.active_tasks.values(): t.cancel()
        except: pass