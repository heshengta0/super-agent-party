import asyncio
from typing import Optional, List
from py.task_center import get_task_center, TaskStatus
from py.sub_agent import run_subtask_in_background

# --- Tool Definitions ---

create_subtask_tool = {
    "type": "function",
    "function": {
        "name": "create_subtask",
        "description": "创建一个子任务并在后台异步执行。支持多渠道发布结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "子任务标题，不要体现发到哪个平台，而是在platforms中指定。"},
                "description": {"type": "string", "description": "任务详细目标，目标中不要体现发到哪个平台，而是在platforms中指定。这会导致子智能体无法完成任务。"},
                "task_type": {
                    "type": "string",
                    "enum": ["once", "time", "cycle"],
                    "default": "once"
                },
                "platforms": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["chat", "wechat", "feishu", "dingtalk", "telegram", "discord", "slack", "wecom"]
                    },
                    "description": "如果希望在任务完成后主动推送到聊天软件，请在此处指定",
                    "default": []
                },
                "trigger_config": {
                    "type": "object",
                    "properties": {
                        "timeValue": {"type": "string"},
                        "days": {"type": "array", "items": {"type": "integer"}},
                        "cycleValue": {"type": "string"},
                        "repeatNumber": {"type": "integer", "default": 1},
                        "isInfiniteLoop": {"type": "boolean", "default": True}
                    }
                },
                "agent_type": {"type": "string", "default": "default"}
            },
            "required": ["title", "description", "task_type"]
        }
    }
}

# (query_tasks_tool, cancel_subtask_tool, finish_task_tool 保持不变...)
query_tasks_tool = {
    "type": "function",
    "function": {
        "name": "query_task_progress",
        "description": "查询任务进度与结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "parent_task_id": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "running", "completed", "failed", "cancelled"]},
                "verbose": {"type": "boolean", "default": False},
                "history_index": {"type": "integer", "default": -1}
            }
        }
    }
}

cancel_subtask_tool = {
    "type": "function",
    "function": {
        "name": "cancel_subtask",
        "description": "取消任务",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"}
            },
            "required": ["task_id"]
        }
    }
}

finish_task_tool = {
    "type": "function",
    "function": {
        "name": "finish_task",
        "description": "任务完成确认工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "result": {"type": "string"}
            },
            "required": ["task_id", "result"]
        }
    }
}

# --- Tool Implementations ---

async def create_subtask(
    title: str,
    description: str,
    task_type: str = "once",
    trigger_config: dict = None,
    agent_type: str = "default",
    workspace_dir: str = None,
    settings: dict = None,
    parent_task_id: Optional[str] = None,
    consensus_content: Optional[str] = None,
    platforms: List[str] = []
) -> str:
    try:
        task_center = await get_task_center(workspace_dir)
        actual_parent_id = parent_task_id or "MAIN_AGENT"
        context = {
            "task_type": task_type,
            "trigger_config": trigger_config or {},
            "platforms": platforms,
            "history": [],
            "results_history": [],
            "ran_count": 0
        }
        
        task = await task_center.create_task(
            title=title,
            description=description,
            parent_task_id=actual_parent_id,
            agent_type=agent_type,
            context=context,
            platforms=platforms # 传入平台列表
        )
        
        if task_type == "once":
            asyncio.create_task(
                run_subtask_in_background(
                    task_id=task.task_id,
                    workspace_dir=workspace_dir,
                    settings=settings, 
                    consensus_content=consensus_content
                )
            )
            mode_msg = "已立即开始执行。"
        else:
            mode_msg = f"已进入计划清单，等待调度触发 (模式: {task_type})。"
            
    except Exception as e:
        return f"❌ 创建子任务失败: {str(e)}"
    
    return (f"✅ 子任务创建成功！\n\n"
            f"任务ID: {task.task_id}\n"
            f"标题: {task.title}\n"
            f"类型: {task_type}\n"
            f"渠道: {', '.join(platforms)}\n"
            f"状态: {mode_msg}")

async def query_task_progress(
    workspace_dir: str,
    task_id: Optional[str] = None,
    parent_task_id: Optional[str] = None,
    status: Optional[str] = None,
    verbose: bool = False,
    history_index: int = -1
) -> str:
    """查询任务进度 - 增强版：支持周期/定时任务的深度回溯"""
    try:
        from py.task_center import get_task_center, TaskStatus
        task_center = await get_task_center(workspace_dir)
        
        tasks = []
        if task_id:
            single_task = await task_center.get_task(task_id)
            if single_task: tasks = [single_task]
            else: return f"❌ 未找到 ID 为 {task_id} 的任务。"
        else:
            status_enum = TaskStatus(status) if status else None
            tasks = await task_center.list_tasks(parent_task_id=parent_task_id, status=status_enum)

        if not tasks:
            return "📋 任务中心当前没有相关任务。"

        result_lines = [f"📋 任务中心状态报告 (共 {len(tasks)} 个任务)"]
        result_lines.append("-" * 40)

        for task in tasks:
            ctx = task.context or {}
            t_type = ctx.get("task_type", "once").upper()
            ran_count = ctx.get("ran_count", 0)
            
            # 1. 标题与基本状态行
            icon = "🔁" if t_type == "CYCLE" else "⏰" if t_type == "TIME" else "📄"
            status_icon = "✅" if task.status == TaskStatus.COMPLETED else "🔄" if task.status == TaskStatus.RUNNING else "⏳"
            result_lines.append(f"{status_icon} [{task.task_id}] {icon} {task.title}")
            
            display_platforms = task.platforms or ctx.get("platforms", [])
            platform_str = ", ".join(display_platforms) if display_platforms else "无"
            result_lines.append(f"   推送渠道: {platform_str}")

            # 2. 类型与频率信息
            type_info = f"   类型: {t_type}"
            if t_type == "CYCLE":
                type_info += f" (间隔: {ctx.get('trigger_config', {}).get('cycleValue', 'N/A')})"
            elif t_type == "TIME":
                days = ctx.get('trigger_config', {}).get('days', [])
                type_info += f" (时间: {ctx.get('trigger_config', {}).get('timeValue', 'N/A')} 周几: {days if days else '单次'})"
            result_lines.append(type_info)

            # 3. 调度统计
            schedule_info = f"   运行统计: 已触发 {ran_count} 次"
            if ctx.get("next_run_at"):
                next_run = ctx.get("next_run_at").replace("T", " ")[:16]
                schedule_info += f" | 下次运行: {next_run}"
            result_lines.append(schedule_info)
            result_lines.append(f"   当前状态: {task.status.value.upper()} | 总进度: {task.progress}%")

            # 4. 结果内容处理 (核心改进)
            results_history = ctx.get("results_history", [])
            
            if task.status == TaskStatus.RUNNING:
                history = ctx.get("history", [])
                if history:
                    result_lines.append(f"   ⚡ 实时动态: {history[-1][:120]}...")

            elif task.status in [TaskStatus.COMPLETED, TaskStatus.PENDING] and ran_count > 0:
                if verbose:
                    # 尝试从历史中提取特定结果
                    try:
                        target_record = results_history[history_index]
                        record_time = target_record.get("time", "").replace("T", " ")[:16]
                        result_lines.append(f"\n   🎯 第 {results_history.index(target_record)+1} 次执行产出 ({record_time}):")
                        result_lines.append(f"--- CONTENT START ---\n{target_record.get('result', '无结果内容')}\n--- CONTENT END ---")
                    except (IndexError, TypeError):
                        result_lines.append(f"   ⚠️ 无法找到索引为 {history_index} 的历史记录。")
                    
                    # 如果有多次历史，展示简易索引列表
                    if len(results_history) > 1:
                        history_list = ", ".join([f"#{i}" for i in range(len(results_history))])
                        result_lines.append(f"   📜 可回溯记录索引: {history_list} (总计 {len(results_history)} 条)")
                else:
                    # 非 verbose 模式显示最新摘要
                    last_res = results_history[-1].get("result", "") if results_history else task.result
                    summary = (last_res[:150] + "...") if last_res else "无内容"
                    result_lines.append(f"   📝 最新结果摘要: {summary}")
                    if len(results_history) > 1:
                        result_lines.append(f"   💡 (该任务有 {len(results_history)} 条历史记录，使用 verbose=true 和 history_index 查询详情)")

            elif task.status == TaskStatus.FAILED:
                result_lines.append(f"   ❌ 错误信息: {task.error}")

            result_lines.append("") # 换行分隔任务

    except Exception as e:
        return f"❌ 查询任务进度失败: {str(e)}"

    return "\n".join(result_lines)

async def cancel_subtask(workspace_dir: str, task_id: str) -> str:
    """取消子任务"""
    try:
        task_center = await get_task_center(workspace_dir)
        success = await task_center.cancel_task(task_id)
    except Exception as e:
        return f"❌ 取消任务失败: {str(e)}"
    return f"✅ 任务 {task_id} 已取消" if success else f"❌ 取消任务 {task_id} 失败"

# ⭐ 新增实现：finish_task
async def finish_task(
    workspace_dir: str,
    task_id: str,
    result: str
) -> str:
    try:
        """子智能体调用此函数来标记任务完成"""
        task_center = await get_task_center(workspace_dir)
        
        # 强制更新为 COMPLETED，进度 100，并保存最终结果
        success = await task_center.update_task_progress(
            task_id=task_id,
            progress=100,
            status=TaskStatus.COMPLETED,
            result=result
        )
    except Exception as e:
        return f"❌ 标记任务完成失败: {str(e)}"
    
    if success:
        return f"🎉 任务 {task_id} 已成功标记为完成！结果已保存。请停止后续操作。"
    else:
        return f"❌ 任务 {task_id} 状态更新失败（可能任务ID错误）。"