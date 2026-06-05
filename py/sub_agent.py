import asyncio
import json
import httpx
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from py.task_center import get_task_center, TaskStatus
from py.get_setting import load_settings, get_port
# 关键导入：必须包含所有相关的 Pydantic 模型
from py.behavior_engine import (
    global_behavior_engine, 
    BehaviorItem, 
    BehaviorAction, 
    BehaviorTrigger, 
    BehaviorTriggerTime,
    BehaviorTriggerNoInput,
    BehaviorTriggerCycle
)
from py.ws_manager import ws_manager

class SubAgentExecutor:
    """子智能体执行器 - 完美处理周期与定时任务结束逻辑"""
    
    def __init__(self, workspace_dir: str, settings: Dict):
        self.workspace_dir = workspace_dir
        self.settings = settings
        self.port = get_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.chat_endpoint = f"{self.base_url}/v1/chat/completions"
        self.simple_chat_endpoint = f"{self.base_url}/simple_chat"
    
    async def execute_subtask(
        self,
        task_id: str,
        consensus_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        task_center = await get_task_center(self.workspace_dir)
        task = await task_center.get_task(task_id)
        max_iterations = self.settings.get("CLISettings", {}).get("max_iterations", 100)
        
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}
        
        # 标记开始
        await task_center.update_task_progress(task.task_id, 0, status=TaskStatus.RUNNING)
        
        iteration = 0
        conversation_history =[]
        assistant_only_history = task.context.get("history",[])
        
        system_prompt = self._build_system_prompt(task, consensus_content)
        conversation_history.append({"role": "system", "content": system_prompt})
        conversation_history.append({"role": "user", "content": f"请执行任务：\n\n{task.description}\n\n完成后整理结果并提交。"})
        
        try:
            async with httpx.AsyncClient(timeout=600.0) as http_client:
                while iteration < max_iterations:

                    latest_task = await task_center.get_task(task_id)
                    if latest_task and latest_task.status == TaskStatus.CANCELLED:
                        print(f"[SubAgent] Task {task_id} was cancelled, stopping.")
                        return {"success": False, "error": "Task cancelled by user"}

                    iteration += 1
                    current_progress = 10 + int((iteration / max_iterations) * 80)
                    
                    assistant_response = await self._call_llm_stream_only(
                        http_client, conversation_history, 'super-model',
                        task.task_id, task_center, current_progress, assistant_only_history
                    )
                    
                    # 避免在遇到网络断连时造成高频请求死循环
                    if not assistant_response.strip():
                        print(f"[SubAgent] 警告: 第 {iteration} 次迭代收到空回复(可能是API连接失败)，休眠3秒...")
                        await asyncio.sleep(3)

                    conversation_history.append({"role": "assistant", "content": assistant_response})
                    
                    latest_task = await task_center.get_task(task_id)
                    # 检查是否通过工具完成（此时必定不会再被意外覆盖了）
                    if latest_task.status == TaskStatus.COMPLETED:
                        return await self._finalize_task_record(task_id, task_center, latest_task.result, assistant_only_history, iteration)

                    # 更新进度
                    await task_center.update_task_progress(
                        task.task_id, current_progress, 
                        status=TaskStatus.RUNNING,
                        context={"history": assistant_only_history, "current_iteration": iteration}
                    )

                    # 智能检查完成状态
                    is_complete = await self._check_task_completion_smart(task, conversation_history, http_client)
                    if is_complete:
                        final_res = await self._extract_final_result(task, conversation_history, http_client)
                        return await self._finalize_task_record(task_id, task_center, final_res["full"], assistant_only_history, iteration)
                    
                    conversation_history.append({"role": "user", "content": "请继续执行。完成后请明确总结并提交。"})
                
                return {"success": False, "error": "Max iterations reached"}

        except Exception as e:
            await task_center.update_task_progress(task.task_id, 0, status=TaskStatus.FAILED, error=str(e))
            return {"success": False, "error": str(e)}

    async def _finalize_task_record(self, task_id, task_center, result, history, iteration):
        """核心逻辑：决定任务是进入 COMPLETED 还是回到 PENDING，并推送结果"""
        task = await task_center.get_task(task_id)
        t_type = task.context.get("task_type", "once")
        config = task.context.get("trigger_config", {})
        
        # 1. 结果存档
        results_history = task.context.get("results_history",[])
        results_history.append({
            "time": datetime.now().isoformat(),
            "result": result,
            "iteration": iteration
        })
        results_history = results_history[-20:] # 仅保留最近20次

        # 2. 日志截断
        trimmed_history = history[-30:] if len(history) > 30 else history
        
        # 3. 状态判定
        final_status = TaskStatus.COMPLETED
        final_progress = 100
        next_run_at = None
        ran_count = task.context.get("ran_count", 0)

        if t_type == "cycle":
            is_infinite = config.get("isInfiniteLoop", True)
            repeat_num = config.get("repeatNumber", 1)
            if is_infinite or ran_count < repeat_num:
                try:
                    h, m, s = map(int, config.get("cycleValue", "01:00:00").split(':'))
                    next_run = datetime.now() + timedelta(hours=h, minutes=m, seconds=s)
                    final_status = TaskStatus.PENDING
                    final_progress = 0
                    next_run_at = next_run.isoformat()
                except: pass
        elif t_type == "time":
            days = config.get("days",[])
            if days and len(days) > 0:
                final_status = TaskStatus.PENDING
                final_progress = 0

        # 4. 更新数据库
        new_ctx = {
            "history": trimmed_history,
            "results_history": results_history,
            "last_run_at": datetime.now().isoformat(),
            "summary": (result[:200] + "...") if result else ""
        }
        if next_run_at:
            new_ctx["next_run_at"] = next_run_at

        await task_center.update_task_progress(
            task_id, 
            final_progress, 
            status=final_status, 
            result=result, 
            context=new_ctx
        )

        # 5. 多渠道推送逻辑
        target_platforms = task.platforms if task.platforms else []

        # B. 推送到外部平台 (Wechat, Feishu, etc.)
        for platform in target_platforms:
            if platform == "chat":
                print(f"[TaskExecutor] 正在广播任务完成信号到网页端并触发自动对话: {task.title}")
                
                # 跟发给其他平台一样的 prompt 内容
                chat_prompt = f"【自主任务汇报】\n任务名称：{task.title}\n任务ID：{task_id}\n\n执行结果：\n{result}\n\n请你作为助手，对上述任务结果进行简要总结并回复给用户。"
                
                await ws_manager.broadcast({
                    "type": "task_notification",
                    "data": {
                        "title": f"Task completed: {task.title}",
                        "message": result[:150] + ("..." if len(result) > 150 else ""),
                        "task_id": task_id,
                        # 这里构造与前端 runBehavior 匹配的数据结构
                        "behavior": {
                            "enabled": True,
                            "action": {
                                "type": "prompt",
                                "prompt": chat_prompt
                            }
                        }
                    }
                })
                continue


            handler = global_behavior_engine.handlers.get(platform)
            if not handler:
                print(f"[TaskExecutor] 平台 {platform} 尚未注册 handler，跳过推送")
                continue

            trigger_obj = BehaviorTrigger(
                type="time",
                time=BehaviorTriggerTime(timeValue="00:00:00", days=[]),
                noInput=BehaviorTriggerNoInput(latency=30),
                cycle=BehaviorTriggerCycle(cycleValue="00:00:30", repeatNumber=1, isInfiniteLoop=False)
            )
            action_obj = BehaviorAction(
                type="prompt",
                prompt=f"【自主任务汇报】\n任务名称：{task.title}\n任务ID：{task.task_id}\n\n执行结果：\n{result}\n\n请你作为助手，对上述任务结果进行简要总结并回复给用户。",
            )
            fake_behavior = BehaviorItem(
                enabled=True,
                trigger=trigger_obj,
                action=action_obj,
                platform=platform,
                platforms=[platform]
            )

            targets = global_behavior_engine.platform_targets.get(platform, [])
            if not targets:
                print(f"[TaskExecutor] 平台 {platform} 未配置目标 ChatID，尝试使用空 ID 触发 fallback")
                asyncio.create_task(handler("", fake_behavior))
                continue

            for chat_id in set(targets):
                if chat_id:
                    print(f"[TaskExecutor] 正在触发平台动作 -> {platform}:{chat_id}")
                    asyncio.create_task(handler(chat_id, fake_behavior))

        return {"success": True, "task_id": task_id, "result": result}


    async def _call_llm_stream_only(
        self, http_client, messages, model, task_id, task_center, 
        base_progress, display_history
    ) -> str:
        payload = {
            "messages": messages, "model": model, "stream": True, "is_sub_agent": True,
            "disable_tools":["create_subtask", "query_tasks_tool", "cancel_subtask"]
        }
        
        full_content = ""
        current_text_buffer = ""
        stream_buffer = ""
        stream_title = ""
        last_update_time = asyncio.get_event_loop().time()
        UPDATE_INTERVAL = 2.0
        
        def _push_line(line: str):
            stripped = line.strip()
            if not stripped:
                return
            display_history.append(stripped)
        
        def _flush_buffer():
            nonlocal stream_buffer, stream_title
            if not stream_buffer:
                return
            
            if stream_title:
                display_history.append(f"📡 [{stream_title}]")
                stream_title = ""
            
            for part in stream_buffer.split("\n"):
                _push_line(part)
            
            stream_buffer = ""
        
        try:
            async with http_client.stream("POST", self.chat_endpoint, json=payload) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "): continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]": break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        
                        if delta.get("content"):
                            _flush_buffer()
                            full_content += delta["content"]
                            current_text_buffer += delta["content"]
                        
                        if delta.get("tool_calls"):
                            _flush_buffer()
                            if current_text_buffer.strip():
                                display_history.append(current_text_buffer.strip())
                                current_text_buffer = ""
                            for tc in delta["tool_calls"]:
                                display_history.append(f"🔧 Calling: {tc.get('function',{}).get('name','?')}")
                        
                        tool = delta.get("tool_content")
                        if tool:
                            ttype = tool.get("type", "")
                            title = tool.get("title", "tool")
                            content = str(tool.get("content", ""))
                            
                            if "finish_task" in str(title):
                                continue
                            
                            if ttype == "tool_result_stream":
                                if not stream_title:
                                    stream_title = title
                                stream_buffer += content
                                while "\n" in stream_buffer:
                                    line, stream_buffer = stream_buffer.split("\n", 1)
                                    if not stream_title:
                                        _push_line(line)
                                    else:
                                        display_history.append(f"📡 [{stream_title}]")
                                        stream_title = ""
                                        _push_line(line)
                            
                            elif ttype == "tool_result":
                                _flush_buffer()
                                display_history.append(f"✅ [{title}]\n{content[:500]}")
                            
                            elif ttype == "error":
                                _flush_buffer()
                                display_history.append(f"❌ [{title}]\n{content[:300]}")
                            
                            else:
                                _flush_buffer()
                                display_history.append(f"📋 [{title}]\n{content[:300]}")
                        
                        now = asyncio.get_event_loop().time()
                        if now - last_update_time >= UPDATE_INTERVAL:
                            current_task = await task_center.get_task(task_id)
                            if current_task and current_task.status == TaskStatus.CANCELLED:
                                print(f"[SubAgent] Stream aborted due to cancellation of {task_id}")
                                return ""

                            # 修复核心点1：将 status=TaskStatus.RUNNING 删去或改为 status=None，防止抹去已触发的完成状态
                            await task_center.update_task_progress(
                                task_id, base_progress, status=None,
                                context={"history": display_history, "live_content": full_content[-800:] if full_content else ""}
                            )
                            last_update_time = now
                            
                    except: continue
        except Exception as e:
            print(f"[SubAgent] Stream error: {e}")
        
        _flush_buffer()
        if current_text_buffer.strip():
            display_history.append(current_text_buffer.strip())
        
        # 修复核心点2：同样将结尾强制覆盖状态的逻辑移除
        await task_center.update_task_progress(
            task_id, base_progress + 10, status=None,
            context={"history": display_history, "live_content": full_content[-1000:] if full_content else ""}
        )
        
        return full_content

    def _build_system_prompt(self, task, consensus_content):
        p = f"你是一个专业的任务执行助手。\n【任务】ID: {task.task_id} | 标题: {task.title}"
        if consensus_content: p += f"\n\n【共识规范】\n{consensus_content}"
        return p

    async def _check_task_completion_smart(self, task, conversation_history, http_client):
        msgs =[{"role": "system", "content": "判断任务目标是否已达成？只回YES/NO"}, {"role": "user", "content": f"目标:{task.description}\n历史:{str(conversation_history)[-2000:]}"}]
        try:
            resp = await http_client.post(self.simple_chat_endpoint, json={"messages": msgs, "model": "super-model"})
            return resp.json()["choices"][0]["message"]["content"].strip().upper().startswith("YES")
        except Exception as e:
            # 加入日志，不要悄悄失败
            print(f"[_check_task_completion_smart] 兜底检查请求失败: {e}")
            return False

    async def _extract_final_result(self, task, conversation_history, http_client):
        msgs =[{"role": "system", "content": "请从对话中提取出任务的最终执行产出。"}, {"role": "user", "content": f"历史:{str(conversation_history)[-4000:]}"}]
        try:
            resp = await http_client.post(self.simple_chat_endpoint, json={"messages": msgs, "model": "super-model"})
            return {"full": resp.json()["choices"][0]["message"]["content"].strip()}
        except Exception as e:
            print(f"[_extract_final_result] 结果提取请求失败: {e}")
            return {"full": "任务执行完成，未提取到明确结果。"}

async def run_subtask_in_background(task_id: str, workspace_dir: str, settings: Dict, consensus_content: Optional[str] = None):
    executor = SubAgentExecutor(workspace_dir, settings)
    await executor.execute_subtask(task_id, consensus_content)