import asyncio
import time
import platform
import json
import os
from typing import List, Optional, Tuple
from functools import wraps

# ================== 核心修复：安全导入 GUI 库 ==================
GUI_AVAILABLE = False
try:
    import pyautogui
    import pyperclip
    # 开启安全防故障机制
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    GUI_AVAILABLE = True
except (KeyError, ImportError, Exception) as e:
    # 如果在 Docker/无显示器环境中，忽略报错，只打印警告
    print(f"⚠️ [Warning] 桌面鼠标键盘工具已禁用 (缺少 DISPLAY): {e}")

# 拦截器：如果大模型在 Docker 里试图调用鼠标键盘，给它返回一句话，而不是让系统崩溃
def require_gui(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        if not GUI_AVAILABLE:
            return "执行失败：当前系统运行在无头环境(如Docker)中，没有物理显示器，无法执行鼠标和键盘操作。"
        return await func(*args, **kwargs)
    return wrapper
# ==============================================================


CURRENT_SCREEN_REGION = None

def set_screen_region(region: Optional[Tuple[int, int, int, int]]):
    """设置当前激活的屏幕映射区域"""
    global CURRENT_SCREEN_REGION
    CURRENT_SCREEN_REGION = region

def _percent_to_pixel(x_percent: float, y_percent: float) -> Tuple[int, int]:
    """内部辅助函数：将千分比 (0 到 1000) 转换为当前屏幕或指定区域的实际像素坐标。"""
    x_percent = max(0, min(1000, float(x_percent)))
    y_percent = max(0, min(1000, float(y_percent)))
    
    # 如果指定了局部屏幕区域，则基于局部区域计算坐标
    if CURRENT_SCREEN_REGION is not None:
        rx, ry, rw, rh = CURRENT_SCREEN_REGION
        px = rx + int(rw * (x_percent / 1000))
        py = ry + int(rh * (y_percent / 1000))
        
        # 确保不超出该区域的边界
        px = min(px, rx + rw - 1)
        py = min(py, ry + rh - 1)
        return px, py
        
    # 否则默认映射全屏坐标
    width, height = pyautogui.size()
    px = min(int(width * (x_percent / 1000)), width - 1)
    py = min(int(height * (y_percent / 1000)), height - 1)
    
    return px, py


@require_gui
async def mouse_move(x: float, y: float, duration: float = 0.5) -> str:
    """移动鼠标到屏幕千分比位置"""
    if x < 0 or x > 1000 or y < 0 or y > 1000:
        return "千分比坐标超出范围，请输入 0 到 1000 之间的值。"
    
    px, py = _percent_to_pixel(x, y)
    
    def _move():
        pyautogui.moveTo(px, py, duration=duration, tween=pyautogui.easeInOutQuad)
        time.sleep(0.02)
    
    await asyncio.to_thread(_move)
    return f"鼠标已成功移动到屏幕位置 ({x}‰, {y}‰)。 [LAST_ACTION: MOVE({x},{y})]"


@require_gui
async def mouse_click(button: str = "left", clicks: int = 1, x: Optional[float] = None, y: Optional[float] = None) -> str:
    """点击鼠标（支持千分比坐标）"""
    if x is not None and y is not None:
        if x < 0 or x > 1000 or y < 0 or y > 1000:    
            return "千分比坐标超出范围，请输入 0 到 1000 之间的值。"
        
        def _click_at():
            px, py = _percent_to_pixel(x, y)
            pyautogui.moveTo(px, py, duration=0.2)
            time.sleep(0.2) 
            pyautogui.click(x=px, y=py, clicks=clicks, button=button, interval=0.1)
            
        await asyncio.to_thread(_click_at)
        # 根据点击次数打上不同的标签
        tag = f"CLICK({x},{y})" if clicks == 1 else f"DOUBLE_CLICK({x},{y})"
        return f"鼠标已移动到 ({x}‰, {y}‰) 并使用 {button} 键点击了 {clicks} 次。 [LAST_ACTION: {tag}]"
    else:
        # 如果没有传入坐标（原地点击），我们无法在图片上准确标出位置，所以不带坐标标签
        await asyncio.to_thread(pyautogui.click, clicks=clicks, button=button, interval=0.1)
        return f"鼠标在当前位置使用 {button} 键点击了 {clicks} 次。[LAST_ACTION: CLICK_CURRENT]"


@require_gui
async def mouse_double_click(button: str = "left", x: Optional[float] = None, y: Optional[float] = None) -> str:
    """双击鼠标"""
    if x is not None and y is not None:
        if x < 0 or x > 1000 or y < 0 or y > 1000:    
            return "千分比坐标超出范围，请输入 0 到 1000 之间的值。"
        
        def _double_click():
            px, py = _percent_to_pixel(x, y)
            pyautogui.moveTo(px, py, duration=0.2)
            time.sleep(0.2)
            pyautogui.click(x=px, y=py, clicks=2, button=button, interval=0.1)
            
        await asyncio.to_thread(_double_click)
        return f"鼠标已移动到 ({x}‰, {y}‰) 并使用 {button} 键双击。 [LAST_ACTION: DOUBLE_CLICK({x},{y})]"
    else:
        await asyncio.to_thread(pyautogui.click, clicks=2, button=button, interval=0.1)
        return f"鼠标在当前位置使用 {button} 键双击。 [LAST_ACTION: CLICK_CURRENT]"


@require_gui
async def mouse_drag(x1: float, y1: float, x2: float, y2: float, duration: float = 1.0, button: str = "left") -> str:
    """从起始位置 (x1, y1) 拖拽到终点位置 (x2, y2)"""
    try:
        coords = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        for name, val in coords.items():
            if val < 0 or val > 1000:
                return f"错误：{name} 坐标 ({val}) 超出范围，请输入 0 到 1000 之间的值。"
        
        px1, py1 = _percent_to_pixel(x1, y1)
        px2, py2 = _percent_to_pixel(x2, y2)
        
        def _drag():
            pyautogui.moveTo(px1, py1, duration=0.2)
            time.sleep(0.2) 
            pyautogui.dragTo(x=px2, y=py2, duration=duration, button=button, tween=pyautogui.easeInOutQuad)
            time.sleep(0.1)
            
        await asyncio.to_thread(_drag)
        return f"已成功将鼠标从 ({x1}‰, {y1}‰) 拖拽到 ({x2}‰, {y2}‰)。[LAST_ACTION: DRAG({x1},{y1},{x2},{y2})]"
    except Exception as e:
        return f"拖拽失败：{e}"


@require_gui
async def mouse_scroll(clicks: int) -> str:
    """滚动鼠标"""
    def _scroll():
        chunk_size = 10 if abs(clicks) > 10 else abs(clicks)
        direction = 1 if clicks > 0 else -1
        remaining = abs(clicks)
        
        while remaining > 0:
            current_chunk = min(chunk_size, remaining)
            pyautogui.scroll(current_chunk * direction)
            remaining -= current_chunk
            if remaining > 0:
                time.sleep(0.01)
    
    await asyncio.to_thread(_scroll)
    direction = "向上" if clicks > 0 else "向下"
    # 滚动无法标点，仅返回状态
    return f"鼠标滚轮已{direction}滚动了 {abs(clicks)} 个单位。[LAST_ACTION: SCROLL]"


@require_gui
async def mouse_hold(button: str, duration: float) -> str:
    """长按鼠标按键"""
    if duration > 30: duration = 30
    
    def _hold_logic():
        try:
            pyautogui.mouseDown(button=button)
            time.sleep(duration)
        finally:
            pyautogui.mouseUp(button=button)
    
    await asyncio.to_thread(_hold_logic)
    return f"已成功按住鼠标 {button} 键持续 {duration} 秒。[LAST_ACTION: HOLD]"



@require_gui
async def copy_to_input_box(text: str) -> str:
    """输入文本 (优化版：解决偶发性只输入字符 'v' 的 Bug)"""
    def _type_text():
        old_clipboard = ""
        try:
            old_clipboard = pyperclip.paste()
        except Exception:
            pass
        
        sys_os = platform.system()
        
        try:
            pyperclip.copy("")
            pyperclip.copy(text)
            wait_time = 0.2 if sys_os == "Windows" else 0.15
            time.sleep(wait_time)
            
            for i in range(3):
                if pyperclip.paste() == text: break
                time.sleep(0.1)
                pyperclip.copy(text)
            
            modifier = 'command' if sys_os == "Darwin" else 'ctrl'
            
            # 🌟 修复核心：显式按下修饰键并等待，确保操作系统队列 100% 确认 Ctrl/Cmd 处于被按住状态 🌟
            pyautogui.keyDown(modifier)
            time.sleep(0.05)  # 50 毫秒的系统缓冲延迟，彻底阻断输入法或系统抢跑
            pyautogui.press('v')
            time.sleep(0.05)  # 释放前的短暂等待
            pyautogui.keyUp(modifier)
            
            time.sleep(0.15)
        finally:
            time.sleep(0.05)
            for _ in range(2):
                try:
                    if old_clipboard: pyperclip.copy(old_clipboard)
                    break
                except Exception:
                    time.sleep(0.05)

    await asyncio.to_thread(_type_text)
    return f"已复制文本到输入框：'{text}'"

@require_gui
async def keyboard_press(key: str, presses: int = 1) -> str:
    """按下单个按键多次"""
    def _press_logic():
        pyautogui.press(key, presses=presses, interval=0.05)
    
    await asyncio.to_thread(_press_logic)
    return f"已按下键盘按键 '{key}' {presses} 次。"


@require_gui
async def keyboard_sequence(keys: List[str]) -> str:
    """按顺序按下多个不同的按键，中间间隔 0.5 秒"""
    if not keys:
        return "错误：未提供按键列表。"

    def _sequence_logic():
        for i, key in enumerate(keys):
            pyautogui.press(key)
            # 如果不是最后一个按键，则等待 0.5 秒
            if i < len(keys) - 1:
                time.sleep(0.5)

    await asyncio.to_thread(_sequence_logic)
    return f"已按顺序执行按键序列：{', '.join(keys)}，按键间隔 0.5 秒。"

@require_gui
async def keyboard_hotkey(keys: List[str]) -> str:
    """按下组合快捷键"""
    if not keys: return "错误：未提供按键组合"
    
    def _hotkey():
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            modifier = keys[0]
            rest_keys = keys[1:]
            with pyautogui.hold(modifier):
                for k in rest_keys:
                    pyautogui.press(k)
                    time.sleep(0.02)
    
    await asyncio.to_thread(_hotkey)
    return f"已触发组合键：{' + '.join(keys)}。"


@require_gui
async def keyboard_hold(keys: List[str], duration: float) -> str:
    """长按按键"""
    if duration > 30: duration = 30
    
    def _hold_logic():
        start_time = time.time()
        try:
            for key in keys:
                pyautogui.keyDown(key)
                time.sleep(0.02)
            
            elapsed = 0
            while elapsed < duration:
                sleep_time = min(0.1, duration - elapsed)
                time.sleep(sleep_time)
                elapsed = time.time() - start_time
        except Exception as e:
            print(f"按住按键时出错: {e}")
        finally:
            for key in reversed(keys):
                try:
                    pyautogui.keyUp(key)
                    time.sleep(0.02)
                except Exception:
                    pass

    await asyncio.to_thread(_hold_logic)
    return f"已成功长按组合键 {keys} 持续 {duration} 秒。"


@require_gui
async def logical_click(id: int) -> str:
    """通过 UI 树节点 ID 执行无障碍逻辑点击（支持窗口被遮挡及熄屏/锁屏后台操作）"""
    # 动态引入 UI 树缓存查询方法
    from py.ui_tree_helper import get_cached_element
    
    cached = get_cached_element(id)
    if not cached:
        return f"错误：未找到 ID 为 {id} 的有效 UI 元素。页面可能已刷新，请重新获取截图后再试。"
        
    system, handle = cached
    
    try:
        if system == "Windows":
            def _win_click():
                # 尝试一：标准 Invoke 动作 (对应大多数 Button 按钮)
                try:
                    pattern = handle.GetInvokePattern()
                    if pattern:
                        pattern.Invoke()
                        return True
                except Exception:
                    pass
                
                # 尝试二：Toggle 动作 (对应复选框 Checkbox/单选框 Radio)
                try:
                    pattern = handle.GetTogglePattern()
                    if pattern:
                        pattern.Toggle()
                        return True
                except Exception:
                    pass
                
                # 尝试三：SelectionItem 动作 (对应列表项/页签 Tab)
                try:
                    pattern = handle.GetSelectionItemPattern()
                    if pattern:
                        pattern.Select()
                        return True
                except Exception:
                    pass
                
                # 尝试四：模拟无障碍点击 (不移动物理鼠标)
                try:
                    handle.Click(simulateMove=True)
                    return True
                except Exception:
                    pass
                
                raise Exception("当前 Windows UIA 节点不支持任何已知的无障碍点击动作。")
                
            await asyncio.to_thread(_win_click)
            return f"已成功通过 Windows UIA 模式对节点 ID {id} 执行后台逻辑点击。[LAST_ACTION: LOGICAL_CLICK({id})]"
            
        elif system == "Darwin":
            import ApplicationServices as AX
            
            def _mac_click():
                # 尝试一：AXPress (macOS 标准按钮按下动作)
                err = AX.AXUIElementPerformAction(handle, "AXPress")
                if err == 0:
                    return True
                
                # 尝试二：AXPick (菜单弹出项选择动作)
                err = AX.AXUIElementPerformAction(handle, "AXPick")
                if err == 0:
                    return True
                
                # 尝试三：AXShowMenu (触发右键/下拉菜单动作)
                err = AX.AXUIElementPerformAction(handle, "AXShowMenu")
                if err == 0:
                    return True
                
                raise Exception(f"AXUIElementPerformAction 返回无障碍错误码: {err}")
                
            await asyncio.to_thread(_mac_click)
            return f"已成功通过 macOS AXPress 模式对节点 ID {id} 执行后台逻辑点击。[LAST_ACTION: LOGICAL_CLICK({id})]"
            
        elif system == "Linux":
            import pyatspi
            
            def _linux_click():
                action = handle.queryAction()
                if action and action.nActions > 0:
                    # 默认执行该节点的第一个关联行为（通常为点击/激活）
                    action.doAction(0)
                    return True
                raise Exception("当前 Linux AT-SPI 节点不具备动作接口。")
                
            await asyncio.to_thread(_linux_click)
            return f"已成功通过 Linux AT-SPI 模式对节点 ID {id} 执行后台逻辑点击。[LAST_ACTION: LOGICAL_CLICK({id})]"
            
        else:
            return f"未知的操作系统类型 {system}。"
            
    except Exception as e:
        # 当无障碍接口调用遇到应用不配合等死角时，提示 AI 退化执行物理鼠标点击
        return f"逻辑点击 ID {id} 失败（原因: {str(e)}）。建议立刻使用原物理工具 mouse_click 传入该节点的 center 坐标进行兜底点击。"


@require_gui
async def logical_type(id: int, text: str) -> str:
    """通过无障碍节点 ID 在后台输入文本（无需物理移动鼠标或使用剪贴板，支持锁屏和后台输入）"""
    from py.ui_tree_helper import get_cached_element
    cached = get_cached_element(id)
    if not cached:
        return f"错误：未找到 ID 为 {id} 的有效输入框。页面可能已刷新，请重新截图。"
        
    system, handle = cached
    try:
        if system == "Windows":
            def _win_type():
                # 尝试一：UIA ValuePattern (最标准的输入框赋值方法)
                try:
                    pattern = handle.GetValuePattern()
                    if pattern:
                        pattern.SetValue(text)
                        return True
                except Exception:
                    pass
                # 尝试二：LegacyIAccessiblePattern 赋值
                try:
                    pattern = handle.GetLegacyIAccessiblePattern()
                    if pattern:
                        pattern.SetValue(text)
                        return True
                except Exception:
                    pass
                raise Exception("该组件不支持 Windows UIA Value 赋值模式。")
                
            await asyncio.to_thread(_win_type)
            return f"已成功通过 Windows UIA 后台向输入框 ID {id} 输入文本：'{text}'"
            
        elif system == "Darwin":
            import ApplicationServices as AX
            
            def _mac_type():
                # macOS 底层魔法：直接通过系统无障碍接口重写该节点的 AXValue 属性
                err = AX.AXUIElementSetAttributeValue(handle, "AXValue", text)
                if err == 0:
                    return True
                raise Exception(f"macOS AXValue 写入失败，无障碍错误码: {err}")
                
            await asyncio.to_thread(_mac_type)
            return f"已成功通过 macOS AXValue 后台向输入框 ID {id} 输入文本：'{text}'"
            
        else:
            return f"暂时不支持该系统平台后台逻辑输入。"
    except Exception as e:
        # 退化机制：如果逻辑输入失败，提示 AI 采用物理点击该输入框 + 粘贴的传统方式
        return f"后台逻辑输入失败（原因：{str(e)}）。请尝试先点击目标输入框，再调用 copy_to_input_box 粘贴输入。"

# 注意：wait 不需要 GUI，所以【不要】加 @require_gui
async def wait(seconds: float) -> str:
    """等待一段时间，让页面或程序加载"""
    seconds = min(max(0, seconds), 60)
    await asyncio.sleep(seconds)
    return f"已等待 {seconds} 秒。"

async def screenshot() -> str:
    """获取截图"""
    await asyncio.sleep(0.3)
    return "[Getting screenshot]"

# ================= 对应的 OpenAI 工具 Schema 定义 =================

mouse_move_tool = {
    "type": "function",
    "function": {
        "name": "mouse_move",
        "description": "将鼠标移动到屏幕上的指定位置。坐标使用千分比表示（0到1000）。(0,0)是屏幕左上角，(1000,1000)是右下角，(500,500)是屏幕正中心。",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "目标水平坐标(X轴)，范围 0 到 1000 的千分比。例如 500 表示宽度正中间","maximum": 1000, "minimum": 0},
                "y": {"type": "number", "description": "目标垂直坐标(Y轴)，范围 0 到 1000 的千分比。例如 500 表示高度正中间","maximum": 1000, "minimum": 0},
                "duration": {"type": "number", "description": "移动耗时（秒），默认为0.5秒。为了拟真，建议不要设为0", "default": 0.5}
            },
            "required": ["x", "y"]
        }
    }
}

mouse_click_tool = {
    "type": "function",
    "function": {
        "name": "mouse_click",
        "description": "点击鼠标。如果传入千分比坐标，则会先移动到该位置再点击；如果不传坐标则在当前位置点击。",
        "parameters": {
            "type": "object",
            "properties": {
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "点击的按键，左键/右键/中键"},
                "clicks": {"type": "integer", "description": "点击次数。1为单击，2为双击，当你需要打开链接或文件时，建议使用双击。如果单击某个图标没有任何反应，也要优先考虑双击。", "default": 1},
                "x": {"type": "number", "description": "点击前的目标水平坐标（0 到 1000 的千分比），可选","maximum": 1000, "minimum": 0},
                "y": {"type": "number", "description": "点击前的目标垂直坐标（0 到 1000 的千分比），可选","maximum": 1000, "minimum": 0}
            },
            "required": ["button"]
        }
    }
}

mouse_double_click_tool = {
    "type": "function",
    "function": {
        "name": "mouse_double_click",
        "description": "双击鼠标以快速打开链接、文件、应用等。如果传入千分比坐标，则会先移动到该位置再点击；如果不传坐标则在当前位置点击。",
        "parameters": {
            "type": "object",
            "properties": {
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "点击的按键，左键/右键/中键"},
                "x": {"type": "number", "description": "点击前的目标水平坐标（0 到 1000 的千分比），可选","maximum": 1000, "minimum": 0},
                "y": {"type": "number", "description": "点击前的目标垂直坐标（0 到 1000 的千分比），可选","maximum": 1000, "minimum": 0}
            },
            "required": ["button"]
        }
    }
}

mouse_drag_tool = {
    "type": "function",
    "function": {
        "name": "mouse_drag",
        "description": "按下鼠标按键从起始坐标拖动到终点坐标。常用于拖动窗口、滑块、移动文件或框选一段区域。",
        "parameters": {
            "type": "object",
            "properties": {
                "x1": {"type": "number", "description": "起始点水平坐标 (0-1000)","maximum": 1000, "minimum": 0},
                "y1": {"type": "number", "description": "起始点垂直坐标 (0-1000)","maximum": 1000, "minimum": 0},
                "x2": {"type": "number", "description": "终点水平坐标 (0-1000)","maximum": 1000, "minimum": 0},
                "y2": {"type": "number", "description": "终点垂直坐标 (0-1000)","maximum": 1000, "minimum": 0},
                "duration": {"type": "number", "description": "拖拽过程耗时（秒），默认为 1.0 秒", "default": 1.0},
                "button": {"type": "string", "enum": ["left", "right"], "description": "按住哪个键拖拽，默认左键", "default": "left"}
            },
            "required": ["x1", "y1", "x2", "y2"]
        }
    }
}

mouse_hold_tool = {
    "type": "function",
    "function": {
        "name": "mouse_hold",
        "description": "长按鼠标某个按键一段时间。适用于游戏中的蓄力、持续开火或某些 UI 的长按菜单。",
        "parameters": {
            "type": "object",
            "properties": {
                "button": {
                    "type": "string", 
                    "enum": ["left", "right", "middle"],
                    "description": "要按住的鼠标按键。"
                },
                "duration": {
                    "type": "number", 
                    "description": "按住的时长（秒）。"
                }
            },
            "required": ["button", "duration"]
        }
    }
}


mouse_scroll_tool = {
    "type": "function",
    "function": {
        "name": "mouse_scroll",
        "description": "滚动鼠标滚轮以浏览网页或文档。正数表示向上滚动，负数表示向下滚动。",
        "parameters": {
            "type": "object",
            "properties": {
                "clicks": {"type": "integer", "description": "滚动单位。大于0为向上滚，小于0为向下滚。如 500 或 -500。一般网页滚动一次可以尝试 300 到 800 的数值。"}
            },
            "required": ["clicks"]
        }
    }
}

keyboard_type_tool = {
    "type": "function",
    "function": {
        "name": "copy_to_input_box",
        "description": "在当前焦点输入框中复制你给的一段文本。支持输入中文和英文字符。注意：调用前请确保已经点击了正确的输入框使之获得了焦点！这个输入只是复制粘贴，与键盘控制无关，不是真的按键交互",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "需要输入的具体文本内容"}
            },
            "required": ["text"]
        }
    }
}

keyboard_press_tool = {
    "type": "function",
    "function": {
        "name": "keyboard_press",
        "description": "按下单个按键。适用于需要连续按下同一个键的情况，例如删除多个字符或连续下移。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string", 
                    "description": "按键名称，例如: 'enter', 'backspace', 'tab', 'down', 'esc'。"
                },
                "presses": {
                    "type": "integer", 
                    "description": "按下该按键的次数，默认为 1。", 
                    "default": 1
                }
            },
            "required": ["key"]
        }
    }
}

keyboard_sequence_tool = {
    "type": "function",
    "function": {
        "name": "keyboard_sequence",
        "description": "按顺序按下多个不同的按键。程序会在每个按键之间自动停顿 0.5 秒。适用于流程化的按键操作，例如 '先按 Tab 切换焦点，再按 Enter 确认'。",
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "按键名称的列表。例如 ['tab', 'enter'] 或 ['up', 'up', 'space']。"
                }
            },
            "required": ["keys"]
        }
    }
}

keyboard_hotkey_tool = {
    "type": "function",
    "function": {
        "name": "keyboard_hotkey",
        "description": "按下键盘组合快捷键。例如复制是['ctrl', 'c']，切换窗口是['alt', 'tab']。如果是mac系统请使用'command'代替'ctrl'。",
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "快捷键组合数组，必须按照按下的先后顺序排列。例如: ['ctrl', 'shift', 'esc']"
                }
            },
            "required": ["keys"]
        }
    }
}

keyboard_hold_tool = {
    "type": "function",
    "function": {
        "name": "keyboard_hold",
        "description": "长按键盘上的一个或多个按键一段时间。这对于控制游戏角色移动或执行需要按住的操作非常有用。",
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要按住的按键列表。例如 ['w'] 或 ['w', 'shift']。"
                },
                "duration": {
                    "type": "number", 
                    "description": "按住的时长（秒）。"
                }
            },
            "required": ["keys", "duration"]
        }
    }
}


wait_tool = {
    "type": "function",
    "function": {
        "name": "wait",
        "description": "让操作暂停并等待一段时间。在点击了加载页面的链接、启动软件、或者输入内容后，必须调用此工具等待 UI 刷新完成，否则下一步操作可能会因为找不到目标而失败。",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "需要等待的秒数，如 1, 2.5, 5等。如果网速慢或程序加载慢，请适当延长。"}
            },
            "required": ["seconds"]
        }
    }
}
screenshot_tool = {
    "type": "function",
    "function": {
        "name": "screenshot",
        "description": "截取带有千分比辅助网格的当前桌面的图像"
    }
}

# 逻辑点击的工具配置声明
logical_click_tool = {
    "type": "function",
    "function": {
        "name": "logical_click",
        "description": "通过当前网页/窗口 UI 树的节点 ID 在后台执行逻辑点击（无障碍点击），不需要物理移动鼠标，支持窗口遮挡和熄屏操作。如果你能拿到有效的节点 ID，请优先使用此工具代替物理鼠标点击。",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer", 
                    "description": "要点击的 UI 元素的 ID（对应当前 UI 树 JSON 中提供的 id 字段）。"
                }
            },
            "required": ["id"]
        }
    }
}


logical_type_tool = {
    "type": "function",
    "function": {
        "name": "logical_type",
        "description": "通过当前网页/窗口 UI 树的节点 ID 在后台向输入框直接输入文本（无障碍输入），不需要物理移动鼠标，支持窗口遮挡和熄屏操作。如果你能拿到有效的输入框节点 ID，请优先使用此工具代替 copy_to_input_box 输入文字。",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer", 
                    "description": "要输入文本的输入框或文本域元素的 ID（对应当前 UI 树 JSON 中提供的 id 字段）。"
                },
                "text": {
                    "type": "string",
                    "description": "需要输入的具体文本内容。"
                }
            },
            "required": ["id", "text"]
        }
    }
}

# 导出所有工具到列表，方便主程序统一挂载
computer_use_tools = [
    wait_tool
    
]

desktopVision_use_tools = [
    screenshot_tool
]

mouse_use_tools = [
    mouse_move_tool,
    mouse_click_tool,
    mouse_double_click_tool,
    mouse_drag_tool,
    mouse_scroll_tool,
    mouse_hold_tool,
    logical_click_tool,
]

keyboard_use_tools = [
    keyboard_type_tool,
    keyboard_press_tool,
    keyboard_sequence_tool,
    keyboard_hotkey_tool,
    keyboard_hold_tool,
    logical_type_tool,
]