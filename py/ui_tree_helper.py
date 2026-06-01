# py/ui_tree_helper.py
import platform
import asyncio
import json
import re
import os

# 强行激活本机 Qt 和 GTK 框架的无障碍树
os.environ["QT_ACCESSIBILITY"] = "1"
os.environ["GTK_A11Y"] = "1"

# ==========================================
# 🌟 全局句柄映射缓存池 🌟
# ==========================================
UI_ELEMENT_CACHE = {}
_id_counter = 0

def reset_ui_cache():
    """每次截图重构 UI 树前，清空旧缓存以防止内存泄露和句柄失效"""
    global _id_counter, UI_ELEMENT_CACHE
    UI_ELEMENT_CACHE.clear()
    _id_counter = 0

def cache_element(system_str: str, native_handle) -> int:
    """向本地缓存注入一个原生的无障碍句柄，并返回给 AI 一个整型标识符 ID"""
    global _id_counter, UI_ELEMENT_CACHE
    _id_counter += 1
    UI_ELEMENT_CACHE[_id_counter] = (system_str, native_handle)
    return _id_counter

def get_cached_element(id_int: int):
    """根据 AI 传入的逻辑 ID，从本地字典中查出底层物理句柄"""
    global UI_ELEMENT_CACHE
    return UI_ELEMENT_CACHE.get(id_int)


# ==========================================
# 核心入口与辅助定位
# ==========================================
async def get_desktop_ui_tree(logical_width: int, logical_height: int, offset_x: int = 0, offset_y: int = 0) -> str:
    """
    异步跨平台获取当前前台窗口的精简 UI 树。
    所有节点的中心点坐标均会被转化为与截图视口完全对齐的 0-1000 千分比相对坐标。
    """
    # 强制在第一步重置缓存，保证该步骤产生的 ID 从 1 开始，且不留存旧会话脏数据
    reset_ui_cache()
    
    system = platform.system()
    logical_width = logical_width if logical_width > 0 else 1920
    logical_height = logical_height if logical_height > 0 else 1080
    
    try:
        if system == "Windows":
            return await asyncio.to_thread(_get_windows_ui_tree, logical_width, logical_height, offset_x, offset_y)
        elif system == "Darwin":
            return await asyncio.to_thread(_get_mac_ui_tree, logical_width, logical_height, offset_x, offset_y)
        elif system == "Linux":
            return await asyncio.to_thread(_get_linux_ui_tree, logical_width, logical_height, offset_x, offset_y)
        else:
            return "[]"
    except Exception:
        return "[]"

def _normalize_coords(absolute_x: float, absolute_y: float, logical_width: int, logical_height: int, offset_x: int, offset_y: int):
    logical_x = absolute_x - offset_x
    logical_y = absolute_y - offset_y
    
    grid_x = int((logical_x / logical_width) * 1000)
    grid_y = int((logical_y / logical_height) * 1000)
    
    grid_x = max(0, min(1000, grid_x))
    grid_y = max(0, min(1000, grid_y))
    return grid_x, grid_y


# ==========================================
# Windows 平台实现 (包含 UIA-Chromium 强力激活)
# ==========================================
def _get_windows_ui_tree(logical_width: int, logical_height: int, offset_x: int, offset_y: int) -> str:
    try:
        import uiautomation as auto
        import ctypes
    except ImportError:
        return "[]"
    
    # 强制开启系统的 DPI 感知 (防止 150% 缩放导致截图和点击不一致)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    foreground_window = auto.GetForegroundControl()
    if not foreground_window:
        return "[]"
        
    # 主动唤醒 Windows 下的 Chromium 引擎
    try:
        doc = foreground_window.DocumentControl(searchDepth=4)
        if doc.Exists(0.1):
            _ = doc.Name
            _ = doc.GetLegacyIAccessiblePattern()
    except Exception:
        pass
        
    elements = []
    max_elements = 60
    interactive_types = {
        "ButtonControl", "EditControl", "HyperlinkControl", "MenuItemControl", 
        "TabItemControl", "CheckBoxControl", "ComboBoxControl", "ListItemControl"
    }
    
    for control, depth in auto.WalkControl(foreground_window, includeTop=True):
        if len(elements) >= max_elements:
            break
            
        ctrl_type = control.ControlTypeName or ""
        name = control.Name or ""
        rect = control.BoundingRectangle
        is_interactive = ctrl_type in interactive_types
        
        if not rect or (not name and not is_interactive):
            continue
            
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 5 or height <= 5:
            continue
            
        abs_x = rect.left + width // 2
        abs_y = rect.top + height // 2
        grid_x, grid_y = _normalize_coords(abs_x, abs_y, logical_width, logical_height, offset_x, offset_y)
        
        # 缓存当前元素并获得本地唯一 ID
        node_id = cache_element("Windows", control)
        
        clean_type = ctrl_type.replace("Control", "")
        elements.append({
            "id": node_id,
            "type": clean_type,
            "name": name if name else "[Actionable]",
            "center": [grid_x, grid_y]
        })
        
    class_name = foreground_window.ClassName or ""
    if "SunAwt" in class_name and len(elements) == 0:
        elements.append({
            "type": "Warning",
            "name": "[Java Access Bridge (JAB) is disabled. Run 'jabswitch -enable' in Admin Cmd to wake up Java elements.]",
            "center": [500, 500]
        })
        
    return json.dumps(elements, ensure_ascii=False, indent=2)


# ==========================================
# macOS 平台实现 (终极修复：CoreFoundation 强类型布尔、双重注入、多属性文本)
# ==========================================
def _get_mac_ui_tree(logical_width: int, logical_height: int, offset_x: int, offset_y: int) -> str:
    try:
        import AppKit
        import ApplicationServices as AX
        import time
        # 🌟 1. 核心修复：直接从 Mac 官方 CoreFoundation 导入原生的 CFBooleanRef 🌟
        # 这能确保传递给 C 语言 API 的是绝对纯净的 kCFBooleanTrue，彻底解决 NSNumber 类型不匹配被 Chromium 拒绝的 Bug
        from CoreFoundation import kCFBooleanTrue
    except ImportError:
        # 兼容性回退
        try:
            import objc
            kCFBooleanTrue = objc.YES
        except Exception:
            kCFBooleanTrue = True

    workspace = AppKit.NSWorkspace.sharedWorkspace()
    active_app = workspace.frontmostApplication()
    if not active_app:
        return "[]"
        
    pid = active_app.processIdentifier()
    app_element = AX.AXUIElementCreateApplication(pid)
    
    # 🌟 2. 系统级强力唤醒广播 (利用原生 kCFBooleanTrue) 🌟
    try:
        system_wide = AX.AXUIElementCreateSystemWide()
        AX.AXUIElementSetAttributeValue(system_wide, "AXEnhancedUserInterface", kCFBooleanTrue)
    except Exception as e:
        print(f"[Warning] System-wide accessibility wake-up failed: {e}")
    
    # 🌟 3. 应用级双通道唤醒 🌟
    AX.AXUIElementSetAttributeValue(app_element, "AXEnhancedUserInterface", kCFBooleanTrue)
    AX.AXUIElementSetAttributeValue(app_element, "AXManualAccessibility", kCFBooleanTrue)
    
    # 🌟 4. 窗口级双向唤醒 (遍历所有活跃窗口深度注入) 🌟
    err, windows = AX.AXUIElementCopyAttributeValue(app_element, "AXWindows", None)
    if err == 0 and windows:
        for window in windows:
            AX.AXUIElementSetAttributeValue(window, "AXEnhancedUserInterface", kCFBooleanTrue)
            AX.AXUIElementSetAttributeValue(window, "AXManualAccessibility", kCFBooleanTrue)
    
    # 🌟 5. 稍微增加等待延时至 400ms 🌟
    # Chromium 收到正确的 kCFBooleanTrue 后，会开始编译整个网页 DOM 树，给它 0.4 秒时间确保稳定生成
    time.sleep(0.4)
    
    elements = []
    max_elements = 80
    interactive_roles = {
        "AXButton", "AXTextField", "AXLink", "AXCheckBox", 
        "AXRadioButton", "AXPopUpButton", "AXTextArea", "AXStaticText"
    }
    
    pos_re = re.compile(r"x:\s*([\d.-]+)\s+y:\s*([\d.-]+)")
    size_re = re.compile(r"w:\s*([\d.-]+)\s+h:\s*([\d.-]+)")

    def get_element_name(element) -> str:
        """
        多属性降级提取算法：确保网页中 StaticText 的文本、输入框的 Value 和占位符都能被抓出来
        """
        err, title = AX.AXUIElementCopyAttributeValue(element, "AXTitle", None)
        if err == 0 and title and str(title).strip():
            return str(title).strip()
            
        err, val = AX.AXUIElementCopyAttributeValue(element, "AXValue", None)
        if err == 0 and val and str(val).strip():
            val_str = str(val).strip()
            if len(val_str) > 60:
                val_str = val_str[:60] + "..."
            return val_str
            
        err, desc = AX.AXUIElementCopyAttributeValue(element, "AXDescription", None)
        if err == 0 and desc and str(desc).strip():
            return str(desc).strip()
            
        return ""

    def extract_bounds_safely(pos_obj, size_obj):
        x, y, w, h = None, None, None, None
        try:
            x, y = pos_obj.x, pos_obj.y
            w, h = size_obj.width, size_obj.height
        except Exception:
            pass
        if x is None:
            try:
                x, y = pos_obj[0], pos_obj[1]
                w, h = size_obj[0], size_obj[1]
            except Exception:
                pass
        if x is None:
            try:
                pos_str, size_str = str(pos_obj), str(size_obj)
                pos_match = pos_re.search(pos_str)
                size_match = size_re.search(size_str)
                if pos_match and size_match:
                    x, y = float(pos_match.group(1)), float(pos_match.group(2))
                    w, h = float(size_match.group(1)), float(size_match.group(2))
            except Exception:
                pass
        return x, y, w, h

    def walk(element, depth=0):
        if len(elements) >= max_elements:
            return
            
        err, role = AX.AXUIElementCopyAttributeValue(element, "AXRole", None)
        role_str = role if err == 0 else ""
        
        title_str = get_element_name(element)
        is_interactive = role_str in interactive_roles
        
        if not is_interactive and not title_str:
            err, children = AX.AXUIElementCopyAttributeValue(element, "AXChildren", None)
            if err == 0 and children:
                for child in children:
                    walk(child, depth + 1)
            return
            
        err, pos = AX.AXUIElementCopyAttributeValue(element, "AXPosition", None)
        err2, size = AX.AXUIElementCopyAttributeValue(element, "AXSize", None)
        
        if err == 0 and err2 == 0 and pos and size:
            x, y, w, h = extract_bounds_safely(pos, size)
            
            if x is not None and w > 5 and h > 5:
                abs_x = x + w / 2
                abs_y = y + h / 2
                grid_x, grid_y = _normalize_coords(abs_x, abs_y, logical_width, logical_height, offset_x, offset_y)
                
                node_id = cache_element("Darwin", element)
                
                elements.append({
                    "id": node_id,
                    "type": role_str.replace("AX", ""),
                    "name": title_str if title_str else "[Actionable]",
                    "center": [grid_x, grid_y]
                })
                
        err, children = AX.AXUIElementCopyAttributeValue(element, "AXChildren", None)
        if err == 0 and children:
            for child in children:
                walk(child, depth + 1)
                
    walk(app_element)
    
    app_name = active_app.localizedName() or ""
    if ("java" in app_name.lower() or "idea" in app_name.lower() or "clion" in app_name.lower()) and len(elements) == 0:
        elements.append({
            "type": "Warning",
            "name": "[Java Accessibility might be disabled. Please check your JDK's accessibility.properties.]",
            "center": [500, 500]
        })
        
    return json.dumps(elements, ensure_ascii=False, indent=2)


# ==========================================
# Linux 平台实现
# ==========================================
def _get_linux_ui_tree(logical_width: int, logical_height: int, offset_x: int, offset_y: int) -> str:
    try:
        import pyatspi
    except ImportError:
        return "[]"
        
    try:
        registry = pyatspi.Registry
        desktop = registry.getDesktop(0)
        elements = []
        max_elements = 50
        interactive_roles = {"push button", "entry", "link", "check box", "radio button", "combo box"}
        
        def walk(obj, depth=0):
            if len(elements) >= max_elements or not obj:
                return
                
            role = obj.getRoleName()
            name = obj.getName()
            is_interactive = role in interactive_roles
            
            if is_interactive or name:
                try:
                    comp = obj.getComponent()
                    if comp:
                        rect = comp.getExtents(pyatspi.XY_SCREEN)
                        x, y, w, h = rect.x, rect.y, rect.width, rect.height
                        if w > 5 and h > 5:
                            abs_x = x + w / 2
                            abs_y = y + h / 2
                            grid_x, grid_y = _normalize_coords(abs_x, abs_y, logical_width, logical_height, offset_x, offset_y)
                            
                            # 缓存当前元素并获得本地唯一 ID
                            node_id = cache_element("Linux", obj)
                            
                            elements.append({
                                "id": node_id,
                                "type": role,
                                "name": name if name else "[Actionable]",
                                "center": [grid_x, grid_y]
                            })
                except Exception:
                    pass
            
            try:
                child_count = obj.getChildCount()
                for i in range(child_count):
                    walk(obj.getChildAtIndex(i), depth + 1)
            except Exception:
                pass
                
        for app in desktop:
            walk(app)
            if len(elements) > 0:
                break
                
        return json.dumps(elements, ensure_ascii=False, indent=2)
    except Exception:
        return "[]"