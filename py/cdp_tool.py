import json
import os
import time
import uuid
import requests
import asyncio
import websockets
from py.get_setting import UPLOAD_FILES_DIR, get_port, load_settings

# 全局变量，用于保持当前上下文
CURRENT_PAGE_INDEX = 0

async def get_cdp_port():
    settings = await load_settings()
    # 默认回退到 9222，或者你的配置值
    return settings.get('chromeMCPSettings', {}).get('CDPport', 3456) # 假设你主进程默认端口是3456

async def get_targets():
    """获取所有 CDP 目标"""
    port = await get_cdp_port()
    try:
        resp = requests.get(f'http://127.0.0.1:{port}/json/list')
        return resp.json()
    except Exception as e:
        print(f"CDP Connection Error: {e}")
        return []

async def get_main_window_ws():
    """获取主窗口（Vue 控制器）的 WebSocket URL"""
    targets = await get_targets()
    
    # 调试：打印所有目标，方便你看清楚当前有哪些窗口
    # print("Current CDP Targets:", json.dumps(targets, indent=2))
    
    for t in targets:
        url = t.get('url', '')
        title = t.get('title', '')
        target_type = t.get('type')

        # 1. 必须是 page 类型 (排除 webview 标签页, service_worker 等)
        if target_type != 'page':
            continue

        # 2. ★ 关键：排除 VRM 窗口
        # VRM 窗口的 URL 通常包含 'vrm.html'
        if 'vrm.html' in url:
            continue
            
        # 3. ★ 关键：排除开发者工具窗口 (如果打开了 DevTools)
        if 'devtools://' in url:
            continue
            
        # 4. (可选) 排除扩展程序窗口
        if 'ext' in url:
            continue

        # 5. 找到主窗口
        # 主窗口的特征通常是：
        # - URL 包含 'skeleton.html' (骨架屏阶段)
        # - 或者 URL 是 'http://127.0.0.1:端口/' (加载完成阶段)
        # - 只要不是上面排除的特定窗口，剩下的 page 通常就是主窗口
        return t.get('webSocketDebuggerUrl')
        
    print("Error: Could not find Main Window in CDP targets.")
    return None

async def get_webview_ws(index=None):
    """获取具体网页的 WebSocket URL"""
    targets = await get_targets()
    # 过滤出所有 webview (实际的网页标签)
    webviews = [t for t in targets if t['type'] == 'webview']
    
    target_idx = index if index is not None else CURRENT_PAGE_INDEX
    
    if 0 <= target_idx < len(webviews):
        return webviews[target_idx].get('webSocketDebuggerUrl')
    return None

async def cdp_command(ws_url, method, params=None):
    """发送 CDP 命令的通用函数"""
    if not ws_url:
        return {"error": "Target not found"}
    
    # 修改这里：增加 max_size 参数
    # 设置为 None 表示不限制大小，或者设置为 10 * 1024 * 1024 (10MB)
    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
        cmd_id = 1
        message = {
            "id": cmd_id,
            "method": method,
            "params": params or {}
        }
        await ws.send(json.dumps(message))
        
        while True:
            response = await ws.recv()
            data = json.loads(response)
            if data.get('id') == cmd_id:
                return data.get('result', {})

# ==========================================
# Input Automation (Via Vue Controller)
# ==========================================

async def call_vue_method(method_name, args_list=None):
    """
    通用函数：调用 window.aiBrowser 的方法 (带重试机制)
    """
    max_retries = 3
    retry_delay = 1.0 # 1秒等待

    ws_url = await get_main_window_ws()
    if not ws_url:
        return {"error": "Main window not found"}

    # 构造参数字符串
    if args_list:
        json_args = [json.dumps(arg) for arg in args_list]
        args_str = ", ".join(json_args)
    else:
        args_str = ""
    
    expression = f"window.aiBrowser.{method_name}({args_str})"

    for attempt in range(max_retries):
        # 每次重试都重新连接 WebSocket，防止 WS 链接本身断开
        try:
            res = await cdp_command(ws_url, "Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True, 
                "awaitPromise": True
            })
            
            # 1. 检查 CDP 协议本身的异常
            if 'exceptionDetails' in res:
                exc = res['exceptionDetails']
                msg = exc.get('text', 'Unknown Error')
                if 'exception' in exc and 'description' in exc['exception']:
                    msg = f"{msg}: {exc['exception']['description']}"
                
                # ★ 关键：如果遇到 Illegal invocation，抛出异常以触发重试
                if "Illegal invocation" in msg or "GUEST_VIEW_MANAGER_CALL" in msg:
                    print(f"[Warn] Retrying {method_name} due to Electron error: {msg}")
                    raise ValueError("Electron Webview Error") # 触发 except 重试
                
                return f"Error executing {method_name}: {msg}"

            # 2. 检查返回值是否包含错误信息 (因为你的 JS 代码里 try-catch 后返回了 "Fill Error: ...")
            remote_object = res.get('result', {})
            value = remote_object.get('value', "")
            
            # 如果返回值是字符串且包含 Electron 内部错误，也视为失败进行重试
            if isinstance(value, str) and ("Illegal invocation" in value or "GUEST_VIEW_MANAGER_CALL" in value):
                print(f"[Warn] Retrying {method_name} due to JS Result error: {value}")
                raise ValueError("Electron Webview Error")

            if 'value' in remote_object:
                return remote_object['value']
            
            if remote_object.get('type') == 'undefined':
                return "Success"
                
            return f"Operation completed (Type: {remote_object.get('type')})"

        except Exception as e:
            # 如果是最后一次尝试，则放弃
            if attempt == max_retries - 1:
                return f"Failed {method_name} after {max_retries} retries. Last error: {str(e)}"
            
            # 等待后重试
            await asyncio.sleep(retry_delay)
            # 有时候主窗口 WS URL 也会变，重新获取一下更稳妥
            ws_url = await get_main_window_ws() or ws_url

# ------------------------------------------
# Interaction Tools (Complete List)
# ------------------------------------------

async def take_snapshot(filePath=None, verbose=False):
    """
    获取页面可交互元素的 DOM 树快照。
    """
    # 调用 Vue 方法生成快照字符串
    result = await call_vue_method('getWebviewSnapshot', [verbose])
    
    # 如果指定了 filePath，则保存到文件（模拟 Agent 行为）
    if filePath and result and isinstance(result, str):
        try:
            with open(filePath, 'w', encoding='utf-8') as f:
                f.write(result)
            return f"Snapshot saved to {filePath}"
        except Exception as e:
            return f"Error saving snapshot: {str(e)}"
            
    # 否则直接返回快照内容
    return result

async def click(uid, dblClick=False):
    """点击元素"""
    return await call_vue_method('webviewClick', [uid, dblClick])

async def fill(uid, value):
    """填写输入框"""
    return await call_vue_method('webviewFill', [uid, value])

async def fill_form(elements):
    """
    批量填写表单
    elements: [{'uid': '...', 'value': '...'}, ...]
    """
    return await call_vue_method('webviewFillForm', [elements])

async def drag(from_uid, to_uid):
    """拖拽元素"""
    return await call_vue_method('webviewDrag', [from_uid, to_uid])

async def handle_dialog(action, promptText=None):
    """处理弹窗 (alert/confirm/prompt)"""
    return await call_vue_method('webviewHandleDialog', [action, promptText])

async def hover(uid):
    """悬停"""
    return await call_vue_method('webviewHover', [uid])

async def press_key(key,uid):
    """按键"""
    return await call_vue_method('webviewPressKey', [key, uid])

# ------------------------------------------
# Navigation Tools
# ------------------------------------------

async def list_pages():
    """列出所有标签页"""
    return await call_vue_method('getPagesInfo')

async def new_page(url, timeout=0):
    """新建标签页"""
    return await call_vue_method('openUrlInNewTab', [url])

async def close_page(pageIdx):
    """关闭标签页"""
    return await call_vue_method('closeTabByIndex', [pageIdx])

async def select_page(pageIdx, bringToFront=True):
    """选择/切换标签页"""
    return await call_vue_method('switchTabByIndex', [pageIdx])

async def navigate_page(type="url", url=None, ignoreCache=False, timeout=0):
    """页面导航"""
    return await call_vue_method('browserNavigate', [type, url, ignoreCache])

async def wait_for(text, timeout=1000):
    """等待文本出现"""
    return await call_vue_method('webviewWaitFor', [text, timeout])

# ------------------------------------------
# Debugging Tools
# ------------------------------------------

async def evaluate_script(script_code, args=None):
    """执行 JS (极强容错版)"""
    
    # 1. 清理字符串前后的空白和反引号（AI有时候会自作聪明加上 ```javascript 的Markdown代码块）
    clean_code = script_code.strip().strip('`')
    if clean_code.startswith('javascript'):
        clean_code = clean_code[10:].strip()

    # 2. 兜底容错：如果 AI 还是只输出了函数体（比如包含 return 但没写 function）
    if not clean_code.startswith("function") and not clean_code.startswith("() =>") and not clean_code.startswith("async function"):
        print(f"[Agent Warning] AI forgot to wrap function, auto-wrapping it...")
        # 帮它包一层标准函数
        clean_code = f"function() {{\n{clean_code}\n}}"
        
    # 3. 导航安全拦截：防止执行页面跳转后，原页面上下文丢失导致 WebSocket 断开
    if "submit()" in clean_code or "location" in clean_code:
        safe_code = f"""
        function() {{
            setTimeout(function() {{
                ({clean_code})();
            }}, 100);
            return 'Command scheduled (Async execution for navigation safety)';
        }}
        """
        return await call_vue_method('executeInActiveWebview', [safe_code, args or []])
    
    # 4. 正常执行
    return await call_vue_method('executeInActiveWebview', [clean_code, args or []])

async def take_screenshot(fullPage=False, uid=None):
    """
    截图
    Vue 端已将图片保存到 uploaded_files 目录，并返回了 URL。
    """
    # 直接调用，返回值就是 URL (例如: http://127.0.0.1:3456/uploaded_files/xxx.jpg)
    result = await call_vue_method('captureWebviewScreenshot', [fullPage, uid])
    
    # 简单的错误检查
    if not result or result.startswith("Error") or result.startswith("Screenshot Error"):
        return f"Failed to capture screenshot: {result}"
        
    return f"[Getting browser screenshot] {result}"

# ==========================================
# Tool Definitions (JSON Schemas)
# ==========================================

all_cdp_tools = [
    # --- Navigation ---
    {
        "type": "function",
        "function": {
            "name": "list_pages",
            "description": "Get a list of pages open in the browser.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "new_page",
            "description": "Creates a new page in the browser tab bar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to load"},
                    "timeout": {"type": "integer"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_page",
            "description": "Closes the page by its index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pageIdx": {"type": "integer", "description": "Index of the page to close"}
                },
                "required": ["pageIdx"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "select_page",
            "description": "Switch tab to the specified page index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pageIdx": {"type": "integer"},
                    "bringToFront": {"type": "boolean"}
                },
                "required": ["pageIdx"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_page",
            "description": "Navigates the currently selected page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["url", "back", "forward", "reload"]},
                    "url": {"type": "string"},
                    "ignoreCache": {"type": "boolean"}
                },
                "required": ["type"]
            }
        }
    },
    
    # --- Debugging & Input ---
    {
        "type": "function",
        "function": {
            "name": "take_snapshot",
            "description": "Get the accessibility tree of the current page to find UIDs for interaction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verbose": {"type": "boolean"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Clicks an element identified by UID from take_snapshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "The BackendNodeId from snapshot"},
                    "dblClick": {"type": "boolean"}
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fill",
            "description": "Type text into an input element.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string"},
                    "value": {"type": "string"}
                },
                "required": ["uid", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_script",
            "description": "Run JS in the current page. You MUST wrap your code in a valid Javascript function expression.\n\nGOOD Example:\n`function() { return document.title; }`\n\nBAD Example (DO NOT DO THIS):\n`return document.title;`",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_code": {
                        "type": "string", 
                        "description": "The complete JS function expression to execute."
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Arguments to pass into the function."
                    }
                },
                "required": ["script_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hover",
            "description": "Hover over an element identified by UID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "The BackendNodeId from snapshot"}
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a key or key combination (e.g. 'Enter', 'Control+a', 'ArrowDown').",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The key combination to press."},
                    "uid": {"type": "string", "description": "The BackendNodeId from snapshot"}
                },
                "required": ["key","uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for",
            "description": "Wait for specific text to appear on the page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to wait for."},
                    "timeout": {"type": "integer", "description": "Timeout in milliseconds (default 1000).","minimum": 100,"default": 1000,"maximum": 10000}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Take a screenshot of the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fullPage": {"type": "boolean", "description": "If set to true takes a screenshot of the full page instead of the currently visible viewport. Incompatible with uid."},
                    "uid": {"type": "string", "description": "The uid of an element on the page from the page content snapshot. If omitted takes a pages screenshot."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fill_form",
            "description": "Fill multiple form fields at once efficiently.",
            "parameters": {
                "type": "object",
                "properties": {
                    "elements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "uid": {"type": "string", "description": "The UID of the input element."},
                                "value": {"type": "string", "description": "The value to fill."}
                            },
                            "required": ["uid", "value"]
                        },
                        "description": "List of elements to fill, e.g., [{'uid': 'ai-1', 'value': 'john'}, ...]"
                    }
                },
                "required": ["elements"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "drag",
            "description": "Drag an element from one position to another using UIDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_uid": {"type": "string", "description": "The UID of the element to drag."},
                    "to_uid": {"type": "string", "description": "The UID of the target element to drop onto."}
                },
                "required": ["from_uid", "to_uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "handle_dialog",
            "description": "Handle JavaScript dialogs (alert, confirm, prompt).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["accept", "dismiss"], "description": "Whether to accept or dismiss the dialog."},
                    "promptText": {"type": "string", "description": "Text to enter if the dialog is a prompt."}
                },
                "required": ["action"]
            }
        }
    }
]