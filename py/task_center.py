import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum
import aiofiles
import aiofiles.os
from pydantic import BaseModel, Field

class TaskCreateRequest(BaseModel):
    title: str
    description: str
    agent_type: str = "default"
    task_type: str = "once"           # once, scheduled, recurring
    run_at_time: Optional[str] = None  # 定时任务的时间点 (ISO格式)
    interval_minutes: int = 60         # 周期任务的间隔
    platforms: List[str] = []    # 新增：多渠道支持
    trigger_config: Optional[dict] = {}

# 1. 增加任务类型枚举
class TaskType(str, Enum):
    ONCE = "once"           # 单次任务 (立即执行)
    SCHEDULED = "scheduled" # 定时任务 (未来某个时间点执行一次)
    RECURRING = "recurring" # 周期任务 (每隔一段时间执行)

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class SubTask(BaseModel):
    task_id: str
    parent_task_id: Optional[str] = None
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0  # 0-100
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    agent_type: str = "default"
    # 使用 Field(default_factory=dict) 确保每个实例有独立的字典
    context: Dict[str, Any] = Field(default_factory=dict)
    
    task_type: TaskType = TaskType.ONCE
    platforms: List[str] = Field(default_factory=list) 
    
    # 时间配置
    schedule_config: Optional[Dict[str, Any]] = None 
    
    # 状态跟踪
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    occurrence_count: int = 0  # 已执行次数

class TaskCenter:
    """任务中心 - 管理所有主任务和子任务"""
    
    def __init__(self, workspace_dir: str):
        self.workspace_dir = Path(workspace_dir)
        self.task_dir = self.workspace_dir / ".agents" / "tasks"
        self._lock = asyncio.Lock()
        self._ensure_task_dir()
    
    def _ensure_task_dir(self):
        """确保任务目录存在"""
        self.task_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_task_file(self, task_id: str) -> Path:
        """获取任务文件路径"""
        return self.task_dir / f"{task_id}.json"
    
    async def create_task(
        self,
        title: str,
        description: str,
        parent_task_id: Optional[str] = None,
        agent_type: str = "default",
        context: Optional[Dict[str, Any]] = None,
        platforms: List[str] = None # 新增参数
    ) -> SubTask:
        """创建新任务"""
        async with self._lock:
            task_id = str(uuid.uuid4())[:8]
            now = datetime.now().isoformat()
            
            task = SubTask(
                task_id=task_id,
                parent_task_id=parent_task_id,
                title=title,
                description=description,
                created_at=now,
                updated_at=now,
                agent_type=agent_type,
                context=context or {},
                platforms=platforms or [] # 赋值
            )
            
            await self._save_task(task)
            return task
    
    async def _save_task(self, task: SubTask):
        """保存任务到文件"""
        task_file = self._get_task_file(task.task_id)
        async with aiofiles.open(task_file, 'w', encoding='utf-8') as f:
            await f.write(task.model_dump_json(indent=2))
    
    async def get_task(self, task_id: str) -> Optional[SubTask]:
        """获取任务详情"""
        task_file = self._get_task_file(task_id)
        if not task_file.exists():
            return None
        
        try:
            async with aiofiles.open(task_file, 'r', encoding='utf-8') as f:
                data = await f.read()
                return SubTask.model_validate_json(data)
        except Exception as e:
            print(f"Error loading task {task_id}: {e}")
            return None
    
    async def update_task_progress(
        self,
        task_id: str,
        progress: int,
        status: Optional[TaskStatus] = None,
        result: Optional[str] = None,
        error: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """更新任务进度和上下文"""
        async with self._lock:
            task = await self.get_task(task_id)
            if not task:
                return False
            
            if task.status == TaskStatus.CANCELLED and status is not None and status != TaskStatus.CANCELLED:
                print(f"[TaskCenter] 任务 {task_id} 已被取消，拒绝状态更新为 {status}")
                return False
            
            safe_progress = max(0, min(100, progress))
            target_status = status if status else task.status
            
            if target_status == TaskStatus.COMPLETED:
                final_progress = 100
            elif target_status == TaskStatus.FAILED:
                final_progress = max(task.progress, safe_progress)
            elif target_status == TaskStatus.CANCELLED:
                final_progress = task.progress 
            else:
                final_progress = max(task.progress, safe_progress)
                final_progress = min(99, final_progress)
            
            task.progress = final_progress
            task.updated_at = datetime.now().isoformat()
            
            if status:
                task.status = status
                if status == TaskStatus.RUNNING and not task.started_at:
                    task.started_at = datetime.now().isoformat()
                elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                    task.completed_at = datetime.now().isoformat()
            
            if result is not None:
                task.result = result
            
            if error is not None:
                task.error = error
                task.status = TaskStatus.FAILED

            if context is not None:
                task.context.update(context)
            
            await self._save_task(task)
            return True

    async def list_tasks(
        self,
        parent_task_id: Optional[str] = None,
        status: Optional[TaskStatus] = None
    ) -> List[SubTask]:
        """列出任务"""
        tasks = []
        if not self.task_dir.exists():
            return tasks
        files = list(self.task_dir.glob("*.json"))
        for task_file in files:
            try:
                async with aiofiles.open(task_file, 'r', encoding='utf-8') as f:
                    data = await f.read()
                    task = SubTask.model_validate_json(data)
                    if parent_task_id is not None and task.parent_task_id != parent_task_id:
                        continue
                    if status is not None and task.status != status:
                        continue
                    tasks.append(task)
            except Exception as e:
                print(f"Error loading task file {task_file}: {e}")
                continue
        tasks.sort(key=lambda x: x.created_at, reverse=True)
        return tasks
    
    async def cancel_task(self, task_id: str) -> bool:
        return await self.update_task_progress(
            task_id=task_id,
            progress=0,
            status=TaskStatus.CANCELLED
        )

    async def delete_task(self, task_id: str) -> bool:
        async with self._lock:
            task_file = self._get_task_file(task_id)
            if task_file.exists():
                try:
                    await aiofiles.os.remove(task_file)
                    return True
                except Exception as e:
                    print(f"Error deleting task {task_id}: {e}")
                    return False
            return False

_task_centers: Dict[str, TaskCenter] = {}

async def get_task_center(workspace_dir: str) -> TaskCenter:
    if workspace_dir not in _task_centers:
        _task_centers[workspace_dir] = TaskCenter(workspace_dir)
    return _task_centers[workspace_dir]