"""统一快捷指令（Shortcut Commands）模块。

这是快捷指令的"单一事实来源（single source of truth）"。

- 后端注入管线（server.py: tools_change_messages）使用 is_shortcuts_enabled() 进行总开关判断。
- 各 IM 平台（飞书 / QQ / Telegram / Discord / Slack / 微信 / 企业微信 / 钉钉）
  使用 parse_im_action() 与 build_help_text() 实现统一的控制类指令。
- Web 前端的快捷指令目录（catalog）镜像于 static/js/vue_data.js 的 shortcutCommands 数组，
  描述文案位于 static/js/locales/*.js，需与本文件中的 COMMAND_SPECS 保持一致。

控制类指令（control）会在 IM 平台被拦截处理；注入类指令（/<skill>、#memory）以及
前端文件引用（@path）仍在 server.py 的消息管线 / 前端处理，且需要工作区路径。
"""

# ==================== 指令目录（单一事实来源） ====================
# scope: web | im | both | workspace
# kind:  control | inject | info
COMMAND_SPECS = [
    {
        "id": "help",
        "syntax": "/help",
        "aliases": ["/help", "/帮助", "/?"],
        "scope": "both",
        "kind": "control",
        "label_zh": "查看指令", "label_en": "Help",
        "desc_zh": "列出所有可用的快捷指令。",
        "desc_en": "List all available shortcut commands.",
    },
    {
        "id": "new",
        "syntax": "/new",
        "aliases": ["/new", "/reset", "/重启", "/新建", "/restart"],
        "scope": "both",
        "kind": "control",
        "label_zh": "新建对话", "label_en": "New conversation",
        "desc_zh": "清空当前对话并开始新的会话。",
        "desc_en": "Clear the current conversation and start fresh.",
    },
    {
        "id": "stop",
        "syntax": "/stop",
        "aliases": ["/stop", "/停止"],
        "scope": "both",
        "kind": "control",
        "label_zh": "停止输出", "label_en": "Stop",
        "desc_zh": "停止当前正在进行的回复。",
        "desc_en": "Stop the response that is currently being generated.",
    },
    {
        "id": "retry",
        "syntax": "/retry",
        "aliases": ["/retry", "/重试"],
        "scope": "web",
        "kind": "control",
        "label_zh": "重新生成", "label_en": "Retry",
        "desc_zh": "重新生成上一条回复（仅聊天界面）。",
        "desc_en": "Regenerate the last reply (chat UI only).",
    },
    {
        "id": "model",
        "syntax": "/model [provider:model]",
        "aliases": ["/model", "/模型"],
        "scope": "both",
        "kind": "control",
        "label_zh": "切换模型", "label_en": "Model",
        "desc_zh": "查看当前模型与可用列表；IM 端仅显示，切换请在聊天界面操作。",
        "desc_en": "Show the current model and available list; IM shows only, switch in the chat UI.",
    },
    {
        "id": "personality",
        "syntax": "/personality [name]",
        "aliases": ["/personality", "/角色", "/persona"],
        "scope": "both",
        "kind": "control",
        "label_zh": "切换人格", "label_en": "Personality",
        "desc_zh": "查看当前人格与可用角色卡；IM 端仅显示，切换请在聊天界面操作。",
        "desc_en": "Show the current personality and character cards; IM shows only, switch in the chat UI.",
    },
    {
        "id": "skills",
        "syntax": "/skills",
        "aliases": ["/skills", "/技能"],
        "scope": "both",
        "kind": "control",
        "label_zh": "浏览技能", "label_en": "Skills",
        "desc_zh": "查看可用技能列表。",
        "desc_en": "Browse the list of available skills.",
    },
    {
        "id": "id",
        "syntax": "/id",
        "aliases": ["/id"],
        "scope": "im",
        "kind": "info",
        "label_zh": "会话ID", "label_en": "Session ID",
        "desc_zh": "查看当前会话/频道的 ChatID（用于主动消息推送配置）。",
        "desc_en": "Show the current session/channel ChatID (for proactive push config).",
    },
    {
        "id": "sub",
        "syntax": "/sub",
        "aliases": ["/sub", "/订阅"],
        "scope": "im",
        "kind": "control",
        "label_zh": "订阅主动消息", "label_en": "Subscribe",
        "desc_zh": "将当前会话加入主动消息列表（接收主动推送）。",
        "desc_en": "Add the current session to the proactive message list.",
    },
    {
        "id": "unsub",
        "syntax": "/unsub",
        "aliases": ["/unsub", "/取消订阅"],
        "scope": "im",
        "kind": "control",
        "label_zh": "取消订阅", "label_en": "Unsubscribe",
        "desc_zh": "将当前会话移出主动消息列表（停止主动推送）。",
        "desc_en": "Remove the current session from the proactive message list.",
    },
    {
        "id": "mode_plan",
        "syntax": "/plan",
        "aliases": ["/plan", "/计划"],
        "scope": "both",
        "kind": "mode",
        "requiresCli": True,
        "label_zh": "计划模式", "label_en": "Plan mode",
        "desc_zh": "切换到计划模式（只读，先规划再执行）。",
        "desc_en": "Switch to plan mode (read-only, plan before acting).",
    },
    {
        "id": "mode_read",
        "syntax": "/read",
        "aliases": ["/read", "/只读"],
        "scope": "both",
        "kind": "mode",
        "requiresCli": True,
        "label_zh": "只读模式", "label_en": "Read mode",
        "desc_zh": "切换到默认只读模式（每步需确认）。",
        "desc_en": "Switch to default read-only mode (confirm each action).",
    },
    {
        "id": "mode_edit",
        "syntax": "/edit",
        "aliases": ["/edit", "/编辑"],
        "scope": "both",
        "kind": "mode",
        "requiresCli": True,
        "label_zh": "编辑模式", "label_en": "Edit mode",
        "desc_zh": "切换到接受编辑模式（允许写入，禁危险操作）。",
        "desc_en": "Switch to accept-edits mode (allow writes, deny destructive ops).",
    },
    {
        "id": "mode_yolo",
        "syntax": "/yolo",
        "aliases": ["/yolo"],
        "scope": "both",
        "kind": "mode",
        "requiresCli": True,
        "label_zh": "Yolo 模式", "label_en": "Yolo mode",
        "desc_zh": "切换到 Yolo 模式（最高权限，无需确认）。",
        "desc_en": "Switch to Yolo mode (full autonomy, no confirmation).",
    },
    {
        "id": "mode_cowork",
        "syntax": "/cowork",
        "aliases": ["/cowork", "/协作"],
        "scope": "both",
        "kind": "mode",
        "requiresCli": True,
        "label_zh": "协作模式", "label_en": "Cowork mode",
        "desc_zh": "切换到协作模式（子智能体协作）。",
        "desc_en": "Switch to cowork mode (sub-agent collaboration).",
    },
    {
        "id": "mode_goal",
        "syntax": "/goal",
        "aliases": ["/goal", "/目标"],
        "scope": "both",
        "kind": "mode",
        "requiresCli": True,
        "label_zh": "目标模式", "label_en": "Goal mode",
        "desc_zh": "切换到目标完成模式（自主迭代直至完成）。",
        "desc_en": "Switch to goal mode (autonomous loop until done).",
    },
    {
        "id": "skill",
        "syntax": "/<skill-name>",
        "aliases": [],
        "scope": "both",
        "kind": "inject",
        "requiresCli": True,
        "label_zh": "注入技能", "label_en": "Inject skill",
        "desc_zh": "以 / 加技能名注入对应技能说明（需开启命令行控制并配置工作区）。",
        "desc_en": "Type / + a skill name to inject that skill (requires CLI control + workspace).",
    },
    {
        "id": "memory",
        "syntax": "#<text>",
        "aliases": [],
        "scope": "both",
        "kind": "inject",
        "requiresCli": True,
        "label_zh": "保存记忆", "label_en": "Save memory",
        "desc_zh": "以 # 开头保存工作区记忆（需开启命令行控制并配置工作区）。",
        "desc_en": "Start with # to save workspace memory (requires CLI control + workspace).",
    },
    {
        "id": "file",
        "syntax": "@<path>",
        "aliases": [],
        "scope": "web",
        "kind": "inject",
        "requiresCli": True,
        "label_zh": "文件引用", "label_en": "File reference",
        "desc_zh": "以 @ 开头引用工作区文件（需开启命令行控制并配置工作区）。",
        "desc_en": "Start with @ to reference a workspace file (requires CLI control + workspace).",
    },
]

# ==================== IM 控制指令别名表 ====================
_STOP_ALIASES = {"/stop", "/停止"}
_RESET_ALIASES = {"/new", "/reset", "/restart", "/重启", "/新建"}
_HELP_ALIASES = {"/help", "/帮助", "/?"}
_SKILLS_ALIASES = {"/skills", "/技能"}
_MODEL_ALIASES = {"/model", "/模型"}
_PERSONALITY_ALIASES = {"/personality", "/角色", "/persona"}
_RETRY_ALIASES = {"/retry", "/重试"}
_SUB_ALIASES = {"/sub", "/订阅"}
_UNSUB_ALIASES = {"/unsub", "/取消订阅"}

# 平台 -> 设置中的机器人配置键（用于主动消息列表 behaviorTargetChatIds 的读写）
# 注意：仅包含支持主动消息推送的平台；QQ 无主动消息能力，故不在其中。
PLATFORM_CONFIG_KEY = {
    "feishu": "feishuBotConfig",
    "wechat": "wechatBotConfig",
    "wecom": "weComBotConfig",
    "dingtalk": "dingtalkBotConfig",
    "telegram": "telegramBotConfig",
    "discord": "discordBotConfig",
    "slack": "slackBotConfig",
}

# 模式切换指令 -> permissionMode 值（需开启电脑命令行控制）
MODE_COMMAND_MAP = {
    "/plan": "plan", "/计划": "plan",
    "/read": "default", "/只读": "default",
    "/edit": "auto-approve", "/编辑": "auto-approve",
    "/yolo": "yolo",
    "/cowork": "cowork", "/协作": "cowork",
    "/goal": "goal", "/目标": "goal",
}
_MODE_ALIASES = set(MODE_COMMAND_MAP)

# 统一回复文案
STOP_MSG_ZH = "已停止当前输出。"
STOP_MSG_EN = "Stopped current output."
RESET_MSG_ZH = "对话记录已重置。"
RESET_MSG_EN = "Conversation history has been reset."


def is_shortcuts_enabled(settings) -> bool:
    """读取全局快捷指令总开关，默认开启。"""
    try:
        return bool((settings or {}).get("systemSettings", {}).get("enableShortcuts", True))
    except Exception:
        return True


async def im_shortcuts_enabled() -> bool:
    """IM 平台用：异步读取全局快捷指令总开关。"""
    try:
        from py.get_setting import load_settings
        settings = await load_settings()
        return is_shortcuts_enabled(settings)
    except Exception:
        return True


def parse_im_action(text):
    """解析 IM 平台的控制类快捷指令。

    返回 "stop" / "reset" / "help" / "skills" / "model" / "personality" / "retry" / None。
    匹配首个 token，因此 "/model gpt-4o" 仍可识别为 model。
    """
    if not text:
        return None
    cmd = text.strip().lower()
    if not cmd.startswith("/"):
        return None
    first = cmd.split()[0]
    if first in _STOP_ALIASES:
        return "stop"
    if first in _RESET_ALIASES:
        return "reset"
    if first in _HELP_ALIASES:
        return "help"
    if first in _SKILLS_ALIASES:
        return "skills"
    if first in _MODEL_ALIASES:
        return "model"
    if first in _PERSONALITY_ALIASES:
        return "personality"
    if first in _RETRY_ALIASES:
        return "retry"
    if first in _MODE_ALIASES:
        return "mode"
    return None


def parse_subscribe_action(text):
    """解析 IM 平台的主动消息订阅指令。

    返回 "sub" / "unsub" / None。匹配首个 token。
    """
    if not text:
        return None
    cmd = text.strip().lower()
    if not cmd.startswith("/"):
        return None
    first = cmd.split()[0]
    if first in _SUB_ALIASES:
        return "sub"
    if first in _UNSUB_ALIASES:
        return "unsub"
    return None


def build_help_text(lang="zh") -> str:
    """构建 IM 平台 /help 的回复文本。"""
    zh = str(lang).lower().startswith("zh")
    title = "可用快捷指令：" if zh else "Available shortcut commands:"
    lines = [title]
    for spec in COMMAND_SPECS:
        if spec["scope"] not in ("im", "both"):
            continue
        if spec["kind"] == "inject":
            continue
        syntax = spec["syntax"]
        desc = spec["desc_zh"] if zh else spec["desc_en"]
        aliases = [a for a in spec["aliases"] if a != syntax.split()[0]]
        alias_str = ("（" + " ".join(aliases) + "）") if (aliases and zh) else \
                    ((" (" + " ".join(aliases) + ")") if aliases else "")
        lines.append(f"{syntax}{alias_str} - {desc}")
    return "\n".join(lines)


# ==================== IM 信息类指令（只读，不改全局状态） ====================
async def _load_settings_safe():
    try:
        from py.get_setting import load_settings
        return (await load_settings()) or {}
    except Exception:
        return {}


def _extract_md_field(text, field):
    import re
    m = re.search(r'^\s*' + field + r'\s*:\s*(.+?)\s*$', text, re.M | re.I)
    if m:
        return m.group(1).strip().strip('"\'')
    return None


def _scan_skills_dir(base):
    from pathlib import Path
    out = []
    try:
        base = Path(base)
        if not base.exists():
            return out
        for item in sorted(base.iterdir()):
            if not item.is_dir() or item.name.startswith('.'):
                continue
            name = item.name
            desc = ""
            for fn in ["SKILL.md", "skill.md", "SKILLS.md", "skills.md"]:
                p = item / fn
                if p.exists():
                    try:
                        content = p.read_text(encoding="utf-8", errors="ignore")
                        nm = _extract_md_field(content, "name")
                        ds = _extract_md_field(content, "description")
                        if nm:
                            name = nm
                        if ds:
                            desc = ds
                    except Exception:
                        pass
                    break
            out.append((name, desc))
    except Exception:
        pass
    return out


async def build_skills_text(lang="zh"):
    """构建 IM /skills 的回复文本（全局技能 + 工作区项目技能）。"""
    zh = str(lang).lower().startswith("zh")
    from pathlib import Path
    skills = {}
    try:
        from py.get_setting import SKILLS_DIR
        for name, desc in _scan_skills_dir(SKILLS_DIR):
            skills[name] = desc
    except Exception:
        pass
    try:
        settings = await _load_settings_safe()
        cwd = (settings.get("CLISettings", {}) or {}).get("cc_path")
        if cwd:
            for name, desc in _scan_skills_dir(Path(cwd) / ".agents" / "skills"):
                skills.setdefault(name, desc)
    except Exception:
        pass
    if not skills:
        return "暂无可用技能。" if zh else "No skills available."
    lines = ["可用技能：" if zh else "Available skills:"]
    for name, desc in skills.items():
        lines.append(f"/{name}" + (f" - {desc}" if desc else ""))
    lines.append("（发送 /技能名 + 你的需求 即可调用，需开启电脑命令行控制并配置工作区）" if zh
                 else "(Send /skill-name + your request to use it; requires CLI control + a workspace.)")
    return "\n".join(lines)


def _provider_label(p):
    """无空格的模型标签：vendor/modelId（方便用户直接复制）。"""
    vendor = (p.get("vendor") or "").strip()
    mid = (p.get("modelId") or "").strip()
    return (vendor + "/" + mid) if vendor else mid


async def build_model_info_text(lang="zh"):
    """构建 IM /model 的回复文本（只读：当前模型 + 可用列表）。"""
    zh = str(lang).lower().startswith("zh")
    settings = await _load_settings_safe()
    providers = settings.get("modelProviders", []) or []
    cur_model = settings.get("model") or ""
    sel = settings.get("selectedProvider")
    cur_label = cur_model
    for p in providers:
        if p.get("id") == sel:
            cur_label = _provider_label(p) or cur_model
            break
    lines = [("当前模型：" if zh else "Current model: ") + (cur_label or ("未设置" if zh else "not set"))]
    listed = []
    for p in providers:
        label = _provider_label(p)
        if not (p.get("modelId") or ""):
            continue
        listed.append("- " + label)
    if listed:
        lines.append("可用模型：" if zh else "Available models:")
        lines.extend(listed)
    lines.append("（发送 /model 模型名 切换）" if zh else "(Send /model <name> to switch.)")
    return "\n".join(lines)


async def build_personality_info_text(lang="zh"):
    """构建 IM /personality 的回复文本（只读：当前人格 + 可用角色卡）。"""
    zh = str(lang).lower().startswith("zh")
    settings = await _load_settings_safe()
    ms = settings.get("memorySettings", {}) or {}
    memories = settings.get("memories", []) or []
    cur = None
    if ms.get("is_memory") and ms.get("selectedMemory"):
        for m in memories:
            if m.get("id") == ms.get("selectedMemory"):
                cur = m.get("name")
                break
    lines = [("当前人格：" if zh else "Current personality: ") + (cur or ("未启用" if zh else "none"))]
    names = [m.get("name") for m in memories if m.get("name")]
    if names:
        lines.append("可用角色卡：" if zh else "Available personalities:")
        lines.extend("- " + n for n in names)
    lines.append("（发送 /personality 名称 切换；/personality 未启用 可关闭）" if zh
                 else "(Send /personality <name> to switch; /personality none to disable.)")
    return "\n".join(lines)


def retry_hint(lang="zh"):
    zh = str(lang).lower().startswith("zh")
    return "重新生成仅在聊天界面可用。" if zh else "Retry is only available in the chat UI."


async def build_mode_info_text(lang="zh"):
    """构建 IM /plan /read /edit /yolo /cowork /goal 的回复文本（只读：当前模式 + 可用列表）。"""
    zh = str(lang).lower().startswith("zh")
    settings = await _load_settings_safe()
    cli = settings.get("CLISettings", {}) or {}
    engine = cli.get("engine", "local")
    key = {"local": "localEnvSettings", "ds": "dsSettings", "acp": "acpSettings"}.get(engine, "localEnvSettings")
    cur = (settings.get(key, {}) or {}).get("permissionMode", "default")
    lines = []
    if not cli.get("enabled"):
        lines.append("⚠ 电脑命令行控制未开启。" if zh else "⚠ Computer CLI control is not enabled.")
    lines.append(("当前模式：" if zh else "Current mode: ") + cur)
    lines.append("可用模式：" if zh else "Available modes:")
    lines.append("/plan /read /edit /yolo /cowork /goal")
    lines.append("（需开启电脑命令行控制；切换请在聊天界面操作）" if zh
                 else "(Requires CLI control; switch in the chat UI.)")
    return "\n".join(lines)


# ==================== IM 切换类指令（/model、/personality 可切换全局设置） ====================
_PERSONALITY_OFF = {"未启用", "不启用", "关闭", "无", "none", "off", "disable", "disabled"}


def _command_arg(text):
    parts = (text or "").strip().split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _save_and_broadcast(settings):
    from py.get_setting import save_settings
    await save_settings(settings)
    try:
        from py.ws_manager import ws_manager
        await ws_manager.broadcast_settings_update(settings)
    except Exception:
        pass


async def handle_model_command(text, lang="zh"):
    """IM /model：无参显示信息，有参切换全局模型（匹配忽略空格，支持 vendor/modelId）。"""
    zh = str(lang).lower().startswith("zh")
    arg = _command_arg(text)
    if not arg:
        return await build_model_info_text(lang)
    settings = await _load_settings_safe()
    providers = settings.get("modelProviders", []) or []
    q = arg.lower().replace(" ", "")
    target = None
    for p in providers:
        vendor = (p.get("vendor") or "").lower().replace(" ", "")
        mid = (p.get("modelId") or "").lower().replace(" ", "")
        if q and q in {mid, vendor, vendor + "/" + mid, vendor + ":" + mid}:
            target = p
            break
    if not target:
        for p in providers:
            mid = (p.get("modelId") or "").lower().replace(" ", "")
            if q and mid and q in mid:
                target = p
                break
    if not target:
        return (("未找到匹配的模型：" if zh else "No matching model: ") + arg)
    settings["model"] = target.get("modelId", "")
    settings["base_url"] = target.get("url", "")
    settings["api_key"] = target.get("apiKey", "")
    settings["selectedProvider"] = target.get("id")
    await _save_and_broadcast(settings)
    return (("已切换模型：" if zh else "Model switched: ") + _provider_label(target))


async def handle_personality_command(text, lang="zh"):
    """IM /personality：无参显示信息，有参切换全局人格（可切到未启用）。"""
    zh = str(lang).lower().startswith("zh")
    arg = _command_arg(text)
    if not arg:
        return await build_personality_info_text(lang)
    settings = await _load_settings_safe()
    ms = settings.get("memorySettings", {}) or {}
    if arg.strip().lower() in _PERSONALITY_OFF or arg.strip() in _PERSONALITY_OFF:
        ms["is_memory"] = False
        settings["memorySettings"] = ms
        await _save_and_broadcast(settings)
        return ("已关闭人格（未启用）。" if zh else "Personality disabled (none).")
    memories = settings.get("memories", []) or []
    q = arg.lower()
    target = None
    for m in memories:
        if (m.get("name") or "").lower() == q:
            target = m
            break
    if not target:
        for m in memories:
            if q and q in (m.get("name") or "").lower():
                target = m
                break
    if not target:
        return (("未找到匹配的角色卡：" if zh else "No matching personality: ") + arg)
    ms["is_memory"] = True
    ms["selectedMemory"] = target.get("id")
    settings["memorySettings"] = ms
    await _save_and_broadcast(settings)
    return (("已切换人格：" if zh else "Personality switched: ") + (target.get("name") or ""))


# ==================== IM 主动消息订阅指令（/sub、/unsub） ====================
async def handle_subscribe_command(platform, chat_id, subscribe: bool, lang="zh"):
    """IM /sub、/unsub：把当前会话 chat_id 加入 / 移出该平台的主动消息列表。

    - 持久化到 settings[<平台配置键>]["behaviorTargetChatIds"] 并广播；
    - 热更新行为引擎的 platform_targets，使其无需重启即时生效。
    仅支持具备主动消息能力的平台（见 PLATFORM_CONFIG_KEY），其余平台返回 None。
    """
    zh = str(lang).lower().startswith("zh")
    chat_id = str(chat_id) if chat_id is not None else ""
    if not chat_id:
        return None
    config_key = PLATFORM_CONFIG_KEY.get(platform)
    if not config_key:
        return None

    settings = await _load_settings_safe()
    bot_cfg = settings.get(config_key, {}) or {}
    targets = list(bot_cfg.get("behaviorTargetChatIds", []) or [])

    if subscribe:
        if chat_id in targets:
            return ("ℹ️ 本会话已在主动消息列表中。" if zh
                    else "ℹ️ This session is already in the proactive message list.")
        targets.append(chat_id)
        reply = (f"✅ 已将本会话加入主动消息列表：\n`{chat_id}`" if zh
                 else f"✅ Added this session to the proactive message list:\n`{chat_id}`")
    else:
        if chat_id not in targets:
            return ("ℹ️ 本会话不在主动消息列表中。" if zh
                    else "ℹ️ This session is not in the proactive message list.")
        targets = [t for t in targets if t != chat_id]
        reply = (f"✅ 已将本会话移出主动消息列表：\n`{chat_id}`" if zh
                 else f"✅ Removed this session from the proactive message list:\n`{chat_id}`")

    bot_cfg["behaviorTargetChatIds"] = targets
    settings[config_key] = bot_cfg
    await _save_and_broadcast(settings)

    try:
        from py.behavior_engine import global_behavior_engine
        global_behavior_engine.platform_targets[platform] = targets
    except Exception:
        pass

    return reply
