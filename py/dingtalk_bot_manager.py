import asyncio
import json
import random
import threading
import os
import time
import logging
import aiohttp
import re
import base64
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

# 钉钉官方 SDK
import dingtalk_stream
from dingtalk_stream import AckMessage, ChatbotMessage

# 假设这些模块在你的环境中定义
from py.behavior_engine import BehaviorItem, BehaviorSettings, global_behavior_engine
from py.get_setting import get_port, load_settings

# 配置模型
class DingtalkBotConfig(BaseModel):
    DingtalkAgent: str
    memoryLimit: int
    appKey: str
    appSecret: str
    separators: List[str]
    reasoningVisible: bool
    quickRestart: bool
    enableTTS: bool 
    wakeWord: str
    behaviorSettings: Optional[BehaviorSettings] = None
    behaviorTargetChatIds: List[str] = Field(default_factory=list)

class DingtalkBotManager:
    def __init__(self):
        self.bot_thread: Optional[threading.Thread] = None
        self.is_running = False
        self.config = None
        self._startup_error = None
        self.client = None
        self.bot_logic = None
        
    def start_bot(self, config: DingtalkBotConfig):
        if self.is_running:
            raise Exception("钉钉机器人已在运行")
        self.config = config
        self._startup_error = None
        self.bot_thread = threading.Thread(target=self._run_bot_thread, args=(config,), daemon=True)
        self.bot_thread.start()
        self.is_running = True

    def _run_bot_thread(self, config):
        async def main_loop():
            try:
                self.bot_logic = DingtalkClientLogic(config)
                
                from py.get_setting import load_settings
                settings = await load_settings()
                behavior_data = settings.get("behaviorSettings", {})
                target_ids = config.behaviorTargetChatIds or []
                
                if behavior_data:
                    logging.info(f"[Dingtalk] 同步行为配置中... 目标数: {len(target_ids)}")
                    global_behavior_engine.update_config(behavior_data, {"dingtalk": target_ids})

                credential = dingtalk_stream.Credential(config.appKey, config.appSecret)
                self.client = dingtalk_stream.DingTalkStreamClient(credential)
                
                handler = DingtalkInternalHandler(self.bot_logic)
                self.client.register_callback_handler(ChatbotMessage.TOPIC, handler)
                
                logging.info("[Dingtalk] 正在并发启动：行为引擎 + 钉钉长连接...")

                await asyncio.gather(
                    global_behavior_engine.start(),
                    self.client.start()
                )
                
            except Exception as e:
                self._startup_error = str(e)
                logging.error(f"[Dingtalk] 异步循环异常: {e}")
            finally:
                self.is_running = False
                global_behavior_engine.stop()

        try:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            new_loop.run_until_complete(main_loop())
        except Exception as e:
            logging.error(f"[Dingtalk] 线程退出: {e}")

    def stop_bot(self):
        if self.client:
            try: self.client.stop()
            except: pass
        # 取消所有逻辑任务
        if self.bot_logic:
            for task in self.bot_logic.active_tasks.values():
                task.cancel()
        self.is_running = False

    def get_status(self):
        return {
            "is_running": self.is_running,
            "has_error": self._startup_error is not None,
            "error_message": self._startup_error,
            "config_loaded": self.config is not None
        }

    def update_behavior_config(self, config: DingtalkBotConfig):
        self.config = config
        if self.bot_logic:
            self.bot_logic.config = config
        global_behavior_engine.update_config(config.behaviorSettings, {"dingtalk": config.behaviorTargetChatIds})

class DingtalkInternalHandler(dingtalk_stream.ChatbotHandler):
    def __init__(self, bot_logic):
        super(DingtalkInternalHandler, self).__init__()
        self.bot_logic = bot_logic

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        try:
            incoming_message = ChatbotMessage.from_dict(callback.data)
            await self.bot_logic.on_message(callback.data, incoming_message, self)
        except Exception as e:
            logging.error(f"消息处理异常: {e}")
        return AckMessage.STATUS_OK, 'OK'

class DingtalkClientLogic:
    def __init__(self, config):
        self.config = config
        self.memoryList = {}
        self.port = get_port()
        self.separators = config.separators if config.separators else []
        # 核心：追踪每个 conversation_id 正在运行的任务
        self.active_tasks: Dict[str, asyncio.Task] = {}
        
        global_behavior_engine.register_handler("dingtalk", self.execute_behavior_event)

    async def _get_image_base64(self, url: str) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.read()
                        return base64.b64encode(data).decode('utf-8')
        except: pass
        return None

    async def on_message(self, raw_data: dict, incoming_message: ChatbotMessage, handler: DingtalkInternalHandler):
        cid = incoming_message.conversation_id
        global_behavior_engine.report_activity("dingtalk", cid)
        
        # 1. 基础解析 (获取用户文本用于指令判断)
        user_text = ""
        if incoming_message.message_type == "text":
            user_text = incoming_message.text.content.strip()
        elif incoming_message.message_type == "richText":
             # 尝试从富文本提取第一段文字
             if hasattr(incoming_message, 'rich_text_content') and incoming_message.rich_text_content.rich_text_list:
                 for item in incoming_message.rich_text_content.rich_text_list:
                     if 'text' in item: 
                         user_text = item['text'].strip()
                         break

        # 2. 快捷指令与任务打断逻辑（统一快捷指令，受系统设置全局开关控制）
        if user_text:
            from py import shortcut_commands
            content_clean = user_text.strip()
            if await shortcut_commands.im_shortcuts_enabled():
                action = shortcut_commands.parse_im_action(content_clean)
                if action == "stop":
                    if cid in self.active_tasks:
                        self.active_tasks[cid].cancel()
                        handler.reply_text(shortcut_commands.STOP_MSG_ZH, incoming_message)
                    return
                if action == "reset":
                    if cid in self.active_tasks:
                        self.active_tasks[cid].cancel()
                    self.memoryList[cid] = []
                    handler.reply_text(shortcut_commands.RESET_MSG_ZH, incoming_message)
                    return
                if action == "help":
                    handler.reply_text(shortcut_commands.build_help_text("zh"), incoming_message)
                    return
                if action == "skills":
                    handler.reply_text(await shortcut_commands.build_skills_text("zh"), incoming_message)
                    return
                if action == "model":
                    handler.reply_text(await shortcut_commands.handle_model_command(content_clean, "zh"), incoming_message)
                    return
                if action == "personality":
                    handler.reply_text(await shortcut_commands.handle_personality_command(content_clean, "zh"), incoming_message)
                    return
                if action == "retry":
                    handler.reply_text(shortcut_commands.retry_hint("zh"), incoming_message)
                    return
                if action == "mode":
                    handler.reply_text(await shortcut_commands.build_mode_info_text("zh"), incoming_message)
                    return

        # 3. 如果当前会话有任务在跑，直接打断
        if cid in self.active_tasks:
            logging.info(f"[Dingtalk] 检测到新消息，打断会话 {cid} 的旧任务")
            self.active_tasks[cid].cancel()

        # 4. 创建新任务并记录
        new_task = asyncio.create_task(self._process_ai_logic(raw_data, incoming_message, handler, cid))
        self.active_tasks[cid] = new_task
        
        try:
            await new_task
        except asyncio.CancelledError:
            logging.info(f"[Dingtalk] 会话 {cid} 的任务被取消")
        finally:
            if self.active_tasks.get(cid) == new_task:
                self.active_tasks.pop(cid, None)

    async def _process_ai_logic(self, raw_data: dict, incoming_message: ChatbotMessage, handler: DingtalkInternalHandler, cid: str):
        """实际的 AI 处理逻辑"""
        msg_type = incoming_message.message_type
        user_text_parts = []
        user_content_items = []
        has_image = False
        
        # --- A. 增强型消息解析 (完整保留) ---
        if msg_type == "text":
            if hasattr(incoming_message, 'text') and incoming_message.text:
                user_text_parts.append(incoming_message.text.content.strip())
        elif msg_type == "picture":
            download_code = incoming_message.image_content.download_code
            img_url = handler.get_image_download_url(download_code)
            if img_url:
                base64_str = await self._get_image_base64(img_url)
                if base64_str:
                    has_image = True
                    user_content_items.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_str}"}})
            if hasattr(incoming_message, 'text') and incoming_message.text:
                user_text_parts.append(incoming_message.text.content.strip())
        elif msg_type == "richText":
            if hasattr(incoming_message, 'rich_text_content') and incoming_message.rich_text_content:
                for item in incoming_message.rich_text_content.rich_text_list:
                    if 'text' in item and item['text']: user_text_parts.append(item['text'])
                    if 'downloadCode' in item and item['downloadCode']:
                        img_url = handler.get_image_download_url(item['downloadCode'])
                        b64 = await self._get_image_base64(img_url)
                        if b64:
                            has_image = True
                            user_content_items.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        user_text = "\n".join(user_text_parts).strip()
        if not user_text and not has_image: return

        # /id 指令保留
        if "/id" in user_text.lower():
            final_id = raw_data.get("senderStaffId") or incoming_message.sender_id
            msg = f"【会话信息】\nID: `{cid}`\n用户ID: `{final_id}`" if cid.startswith("cid") else f"【个人信息】\nUserID: `{final_id}`"
            handler.reply_markdown("ID 助手", msg, incoming_message)
            return

        from py import shortcut_commands as _sc
        _sub = _sc.parse_subscribe_action(user_text)
        if _sub:
            _reply = await _sc.handle_subscribe_command("dingtalk", cid, _sub == "sub", "zh")
            if _reply:
                handler.reply_text(_reply, incoming_message)
            return

        if self.config.wakeWord and self.config.wakeWord not in user_text and not has_image: return

        # B. 构造记忆
        if cid not in self.memoryList: self.memoryList[cid] = []
        current_content = []
        if user_text: current_content.append({"type": "text", "text": user_text})
        if has_image:
            current_content.extend(user_content_items)
            if not user_text: current_content.insert(0, {"type": "text", "text": "请分析这张图片"})
        self.memoryList[cid].append({"role": "user", "content": current_content})

        # C. AI 调用
        ai_client = AsyncOpenAI(api_key="none", base_url=f"http://127.0.0.1:{self.port}/v1")
        state = {"text_buffer": "", "full_response": ""}
        try:
            stream = await ai_client.chat.completions.create(
                model=self.config.DingtalkAgent,
                messages=self.memoryList[cid],
                stream=True,
                extra_body={"is_app_bot": True, "platform": "dingtalk"},
            )
            async for chunk in stream:
                if not chunk.choices: continue
                delta = chunk.choices[0].delta
                content = delta.content or ""
                reasoning = getattr(delta, "reasoning_content", "") if self.config.reasoningVisible else ""
                
                combined = reasoning + content
                if not combined: continue
                state["text_buffer"] += combined
                state["full_response"] += content

                # 流式分段
                if any(sep in state["text_buffer"] for sep in self.separators):
                    if state["text_buffer"].strip():
                        handler.reply_markdown("AI 助手", state["text_buffer"], incoming_message)
                        state["text_buffer"] = ""

            if state["text_buffer"].strip():
                handler.reply_markdown("AI 助手", state["text_buffer"], incoming_message)

            self.memoryList[cid].append({"role": "assistant", "content": state["full_response"]})
            if self.config.memoryLimit > 0:
                while len(self.memoryList[cid]) > self.config.memoryLimit * 2: self.memoryList[cid].pop(0)
        except Exception as e:
            if not isinstance(e, asyncio.CancelledError):
                logging.error(f"钉钉生成异常: {e}")
                handler.reply_text(f"处理出错: {str(e)}", incoming_message)

    async def _get_access_token(self) -> Optional[str]:
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"appKey": self.config.appKey, "appSecret": self.config.appSecret}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("accessToken")
        except: pass
        return None
    
    async def execute_behavior_event(self, chat_id: str, behavior_item: BehaviorItem):
        """主动推送逻辑 (完整保留)"""
        target_id = str(chat_id).strip()
        if not target_id: return
        
        # 解析 Prompt
        action = behavior_item.action
        prompt_content = action.prompt if action.type == "prompt" else (random.choice(action.random.events) if action.random.type == "random" else action.random.events[action.random.orderIndex % len(action.random.events)])
        if not prompt_content: return

        try:
            ai_client = AsyncOpenAI(api_key="none", base_url=f"http://127.0.0.1:{self.port}/v1")
            response = await ai_client.chat.completions.create(
                model=self.config.DingtalkAgent,
                messages=[{"role": "user", "content": "[system]: "+prompt_content}],
                stream=False
            )
            reply = response.choices[0].message.content
            if not reply: return

            token = await self._get_access_token()
            if not token: return
            
            headers = {"x-acs-dingtalk-access-token": token, "Content-Type": "application/json"}
            if target_id.startswith("cid"):
                url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
                payload = {"msgKey": "sampleMarkdown", "msgParam": json.dumps({"title": "AI 助手", "text": reply}), "openConversationId": target_id, "robotCode": self.config.appKey}
            else:
                url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
                payload = {"robotCode": self.config.appKey, "userIds": [target_id], "msgKey": "sampleMarkdown", "msgParam": json.dumps({"title": "AI 助手", "text": reply})}

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        if target_id not in self.memoryList: self.memoryList[target_id] = []
                        self.memoryList[target_id].append({"role": "assistant", "content": reply})
        except Exception as e:
            logging.error(f"[Dingtalk] 主动行为异常: {e}")

    def __del__(self):
        try:
            for t in self.active_tasks.values(): t.cancel()
        except: pass