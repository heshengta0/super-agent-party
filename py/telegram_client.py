import asyncio, aiohttp, io, base64, json, logging, re, time
from typing import Dict, List, Any, Optional
from openai import AsyncOpenAI
from py.behavior_engine import BehaviorItem
from py.get_setting import convert_to_opus_simple, get_port, load_settings

class TelegramClient:
    def __init__(self):
        self.TelegramAgent = "super-model"
        self.memoryLimit = 10
        self.memoryList: Dict[int, List[Dict]] = {}  # chat_id -> messages
        self.asyncToolsID: Dict[int, List[str]] = {}
        self.fileLinks: Dict[int, List[str]] = {}
        self.separators = []
        self.reasoningVisible = False
        self.quickRestart = True
        self.enableTTS = False
        self.wakeWord = None
        self.bot_token: str = ""
        self.config = None 
        self._is_ready = False
        self._manager_ref = None
        self._ready_callback = None
        self._shutdown_requested = False
        self.offset = 0
        self.session: Optional[aiohttp.ClientSession] = None
        self.port = get_port()
        # 核心：用于管理每个 chat_id 的当前任务，实现打断功能
        self.active_tasks: Dict[int, asyncio.Task] = {}
        
        # --- 注册到行为引擎 ---
        from py.behavior_engine import global_behavior_engine
        global_behavior_engine.register_handler("telegram", self.execute_behavior_event)

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=35) 
        self.session = aiohttp.ClientSession(timeout=timeout)
        
        self._is_ready = True
        if self._manager_ref:
            manager = self._manager_ref()
            if manager:
                manager._ready_complete.set()
                manager.is_running = True

        logging.info("Telegram 轮询开始")
        try:
            while not self._shutdown_requested:
                try:
                    updates = await self._get_updates()
                    for u in updates:
                        await self._handle_update(u)
                except asyncio.TimeoutError:
                    pass
                if not updates:
                    await asyncio.sleep(0.1)
        finally:
            await self.session.close()

    async def _get_updates(self):
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        try:
            async with self.session.get(url, params={"offset": self.offset, "timeout": 5}) as resp:
                if resp.status != 200: return []
                data = await resp.json()
                if not data.get("ok"): return []
                return data["result"]
        except: return []

    async def _handle_update(self, u: dict):
        if "message" not in u: return
        msg = u["message"]
        self.offset = u["update_id"] + 1
        chat_id = msg["chat"]["id"]

        # 快捷指令检查与打断（统一快捷指令，受系统设置全局开关控制）
        if "text" in msg:
            from py import shortcut_commands
            text = msg["text"].strip()
            if await shortcut_commands.im_shortcuts_enabled():
                action = shortcut_commands.parse_im_action(text)
                if action == "stop":
                    if chat_id in self.active_tasks:
                        self.active_tasks[chat_id].cancel()
                        await self._send_text(chat_id, shortcut_commands.STOP_MSG_EN)
                    return
                if action == "reset":
                    if chat_id in self.active_tasks:
                        self.active_tasks[chat_id].cancel()
                    self.memoryList[chat_id] = []
                    await self._send_text(chat_id, shortcut_commands.RESET_MSG_EN)
                    return
                if action == "help":
                    await self._send_text(chat_id, shortcut_commands.build_help_text("en"))
                    return
                if action == "skills":
                    await self._send_text(chat_id, await shortcut_commands.build_skills_text("en"))
                    return
                if action == "model":
                    await self._send_text(chat_id, await shortcut_commands.handle_model_command(text, "en"))
                    return
                if action == "personality":
                    await self._send_text(chat_id, await shortcut_commands.handle_personality_command(text, "en"))
                    return
                if action == "retry":
                    await self._send_text(chat_id, shortcut_commands.retry_hint("en"))
                    return
                if action == "mode":
                    await self._send_text(chat_id, await shortcut_commands.build_mode_info_text("en"))
                    return

        # 自动打断旧任务
        if chat_id in self.active_tasks:
            logging.info(f"Telegram: 检测到新消息，打断会话 {chat_id} 的旧任务")
            self.active_tasks[chat_id].cancel()

        # 创建新处理任务
        task = asyncio.create_task(self._dispatch_message(chat_id, msg))
        self.active_tasks[chat_id] = task
        
        # 任务清理
        def _on_finish(_):
            if self.active_tasks.get(chat_id) == task:
                self.active_tasks.pop(chat_id, None)
        task.add_done_callback(_on_finish)

    async def _dispatch_message(self, chat_id: int, msg: dict):
        """异步分发消息处理"""
        try:
            if "text" in msg:
                await self._handle_text(chat_id, msg)
            elif "photo" in msg:
                await self._handle_photo(chat_id, msg)
            elif "voice" in msg or "audio" in msg:
                await self._handle_voice(chat_id, msg)
        except asyncio.CancelledError:
            logging.info(f"Telegram 会话 {chat_id} 的任务已被打断")
        except Exception as e:
            logging.error(f"Telegram 消息处理异常: {e}")

    async def _handle_text(self, chat_id: int, msg: dict):
        text = msg["text"]
        from py.behavior_engine import global_behavior_engine
        global_behavior_engine.report_activity("telegram", str(chat_id))

        if text.strip().lower() == "/id":
            await self._send_text(chat_id, f"🤖 **Session ID**\n`{chat_id}`")
            return

        from py import shortcut_commands as _sc
        _sub = _sc.parse_subscribe_action(text)
        if _sub:
            _reply = await _sc.handle_subscribe_command("telegram", str(chat_id), _sub == "sub", "en")
            if _reply:
                await self._send_text(chat_id, _reply)
            return

        if self.wakeWord and self.wakeWord not in text: return
        await self._process_llm(chat_id, text, [], msg.get("message_id"))

    async def _handle_photo(self, chat_id: int, msg: dict):
        from py.behavior_engine import global_behavior_engine
        global_behavior_engine.report_activity("telegram", str(chat_id))
        photos = msg["photo"]
        file_id = photos[-1]["file_id"]
        file_info = await self._get_file(file_id)
        if not file_info: return
        
        url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_info['file_path']}"
        async with self.session.get(url) as resp:
            if resp.status != 200: return
            img_bytes = await resp.read()
            
        base64_data = base64.b64encode(img_bytes).decode()
        user_content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"}},
            {"type": "text", "text": "用户发送了一张图片"}
        ]
        await self._process_llm(chat_id, "", user_content, msg.get("message_id"))

    async def _handle_voice(self, chat_id: int, msg: dict):
        from py.behavior_engine import global_behavior_engine
        global_behavior_engine.report_activity("telegram", str(chat_id))
        voice = msg.get("voice") or msg.get("audio")
        file_info = await self._get_file(voice["file_id"])
        if not file_info: return
        
        url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_info['file_path']}"
        async with self.session.get(url) as resp:
            if resp.status != 200: return
            text = await self._transcribe(await resp.read())
            
        if not text: return
        if self.wakeWord and self.wakeWord not in text: return
        await self._process_llm(chat_id, text, [], msg.get("message_id"))

    async def _process_llm(self, chat_id: int, text: str, extra_content: List[dict], reply_to_msg_id: Optional[int]):
        self.memoryList.setdefault(chat_id, [])
        self.asyncToolsID.setdefault(chat_id, [])
        self.fileLinks.setdefault(chat_id, [])

        user_msg = {"role": "user", "content": extra_content if extra_content else text}
        self.memoryList[chat_id].append(user_msg)

        settings = await load_settings()
        client = AsyncOpenAI(api_key="super-secret-key", base_url=f"http://127.0.0.1:{get_port()}/v1")
        state = {"text_buffer": "", "image_cache": [], "audio_buffer": []}
        full_response = []

        try:
            stream = await client.chat.completions.create(
                model=self.TelegramAgent,
                messages=self.memoryList[chat_id],
                stream=True,
                extra_body={
                    "asyncToolsID": self.asyncToolsID[chat_id],
                    "fileLinks": self.fileLinks[chat_id],
                    "is_app_bot": True,
                    "platform": "telegram",
                },
            )
            
            async for chunk in stream:
                if not chunk.choices: continue
                delta = chunk.choices[0].delta
                content = getattr(delta, 'content', '') or ""
                reasoning = getattr(delta, 'reasoning_content', '') or ""
                
                # 捕获 Omni 音频
                if hasattr(delta, "audio") and delta.audio and "data" in delta.audio:
                    state["audio_buffer"].append(delta.audio["data"])

                # 处理 Tool 逻辑
                if getattr(delta, 'tool_link', '') and settings["tools"]["toolMemorandum"]["enabled"]:
                    self.fileLinks[chat_id].append(delta.tool_link)
                if getattr(delta, 'async_tool_id', ''):
                    lst = self.asyncToolsID[chat_id]
                    if delta.async_tool_id not in lst: lst.append(delta.async_tool_id)
                    else: lst.remove(delta.async_tool_id)

                seg = reasoning if self.reasoningVisible and reasoning else content
                state["text_buffer"] += seg
                full_response.append(content)

                # 分段发送文本 (非 TTS 模式)
                if state["text_buffer"] and not self.enableTTS:
                    buffer = state["text_buffer"]
                    split_pos = -1
                    for sep in self.separators:
                        pos = buffer.find(sep)
                        if pos != -1:
                            split_pos = pos + len(sep)
                            break
                    
                    if split_pos != -1:
                        chunk_to_send = buffer[:split_pos]
                        state["text_buffer"] = buffer[split_pos:]
                        clean = self._clean_text(chunk_to_send)
                        if clean: await self._send_text(chat_id, clean)

            # 扫尾
            if state["text_buffer"] and not self.enableTTS:
                clean = self._clean_text(state["text_buffer"])
                if clean: await self._send_text(chat_id, clean)

            self._extract_images("".join(full_response), state)
            for img_url in state["image_cache"]: await self._send_photo(chat_id, img_url)

            # 处理 Omni 音频
            has_omni = False
            if state["audio_buffer"]:
                final_audio, is_opus = await asyncio.to_thread(convert_to_opus_simple, base64.b64decode("".join(state["audio_buffer"])))
                await self._send_omni_voice(chat_id, final_audio, is_opus)
                has_omni = True

            assistant_text = "".join(full_response)
            self.memoryList[chat_id].append({"role": "assistant", "content": assistant_text})
            if self.memoryLimit > 0:
                while len(self.memoryList[chat_id]) > self.memoryLimit * 2: self.memoryList[chat_id].pop(0)

            if self.enableTTS and assistant_text and not has_omni:
                await self._send_voice(chat_id, assistant_text)
                
        except Exception as e:
            if not isinstance(e, asyncio.CancelledError):
                logging.error(f"LLM 处理异常: {e}")
                await self._send_text(chat_id, f"处理出错: {e}")

    async def _send_omni_voice(self, chat_id: int, audio_data: bytes, is_opus: bool):
        try:
            data = aiohttp.FormData()
            data.add_field("chat_id", str(chat_id))
            if is_opus:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendVoice"
                data.add_field("voice", io.BytesIO(audio_data), filename="voice.ogg", content_type="audio/ogg")
            else:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
                data.add_field("document", io.BytesIO(audio_data), filename="reply.wav")
            await self.session.post(url, data=data)
        except: pass

    async def _send_text(self, chat_id: int, text: str, reply_to_msg_id: Optional[int] = None):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_to_msg_id: payload["reply_to_message_id"] = reply_to_msg_id
        async with self.session.post(url, json=payload) as resp:
            if resp.status != 200:
                payload.pop("parse_mode")
                await self.session.post(url, json=payload)

    async def _send_photo(self, chat_id: int, image_url: str):
        async with self.session.get(image_url) as resp:
            if resp.status != 200: return
            img_bytes = await resp.read()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        data = aiohttp.FormData()
        data.add_field("chat_id", str(chat_id))
        data.add_field("photo", io.BytesIO(img_bytes), filename="image.jpg")
        await self.session.post(url, data=data)

    def clean_markdown(self, buffer):
        buffer = re.sub(r'#{1,6}\s', '', buffer, flags=re.MULTILINE)
        buffer = re.sub(r'[*_~`]+', '', buffer)
        buffer = re.sub(r'^\s*[-*]\s', '', buffer, flags=re.MULTILINE)
        buffer = re.sub(r'[\u2600-\u27BF\U0001F300-\U0001F9FF]', '', buffer)
        buffer = re.sub(r'!\[.*?\]\(.*?\)', '', buffer)
        buffer = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', buffer)
        return buffer.strip()

    async def _send_voice(self, chat_id: int, text: str):
        settings = await load_settings()
        clean_t = self.clean_markdown(text)
        payload = {"text": clean_t, "voice": "default", "ttsSettings": settings.get("ttsSettings", {}), "index": 0, "mobile_optimized": True, "format": "opus"}
        async with aiohttp.ClientSession() as sess:
            async with sess.post(f"http://127.0.0.1:{self.port}/tts", json=payload) as resp:
                if resp.status != 200: return
                opus_data = await resp.read()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendVoice"
        data = aiohttp.FormData()
        data.add_field("chat_id", str(chat_id))
        data.add_field("voice", io.BytesIO(opus_data), filename="voice.opus")
        await self.session.post(url, data=data)

    async def _get_file(self, file_id: str) -> Optional[dict]:
        url = f"https://api.telegram.org/bot{self.bot_token}/getFile"
        async with self.session.get(url, params={"file_id": file_id}) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("result")
        return None

    async def _transcribe(self, audio_bytes: bytes) -> Optional[str]:
        form = aiohttp.FormData()
        form.add_field("audio", io.BytesIO(audio_bytes), filename="v.ogg")
        async with self.session.post(f"http://127.0.0.1:{self.port}/asr", data=form) as resp:
            if resp.status == 200:
                res = await resp.json()
                return res.get("text") if res.get("success") else None
        return None

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        text = re.sub(r'<.*?>', '', text)
        return text.strip()

    def _extract_images(self, full_text: str, state: dict):
        for m in re.finditer(r"!\[.*?\]\((https?://[^\s)]+)", full_text):
            state["image_cache"].append(m.group(1))

    async def execute_behavior_event(self, chat_id: str, behavior_item: BehaviorItem):
        prompt = await self._resolve_behavior_prompt(behavior_item)
        if not prompt: return
        cid = int(chat_id)
        self.memoryList.setdefault(cid, [])
        messages = self.memoryList[cid] + [{"role": "user", "content": f"[system]: {prompt}"}]
        try:
            client = AsyncOpenAI(api_key="sk", base_url=f"http://127.0.0.1:{self.port}/v1")
            resp = await client.chat.completions.create(model=self.TelegramAgent, messages=messages, stream=False, extra_body={"is_app_bot": True, "platform": "telegram", "behavior_trigger": True})
            reply = resp.choices[0].message.content
            if reply:
                await self._send_text(cid, reply)
                self.memoryList[cid].append({"role": "user", "content": f"[system]: {prompt}"})
                self.memoryList[cid].append({"role": "assistant", "content": reply})
                if self.enableTTS: await self._send_voice(cid, reply)
        except: pass

    async def _resolve_behavior_prompt(self, behavior: BehaviorItem) -> Optional[str]:
        import random
        action = behavior.action
        if action.type == "prompt": return action.prompt
        if action.type == "random" and action.random and action.random.events:
            events = action.random.events
            if action.random.type == "random": return random.choice(events)
            idx = action.random.orderIndex
            selected = events[idx % len(events)]
            action.random.orderIndex = idx + 1
            return selected
        return None