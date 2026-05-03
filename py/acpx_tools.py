# py/acpx_tools.py

import os
import sys
import signal
import shutil
import asyncio
import subprocess
from pathlib import Path
from typing import AsyncIterator, Optional, Dict, Any

# ==================== Environment Detection & acpx Path Resolution ====================

def _get_acpx_command() -> list[str]:
    """
    Smart acpx command resolution by environment priority:
    1. Electron packaged: use resources/acpx
    2. Global acpx command on PATH
    3. Docker: global npm-installed acpx
    4. Development: global acpx → local node_modules → nvm/fnm
    5. Fallback: npx
    """
    # 1. Electron packaged environment
    electron_node = os.environ.get("ELECTRON_NODE_EXEC")
    if electron_node and not os.environ.get("IS_DOCKER"):
        resources_path = os.environ.get("ELECTRON_RESOURCES_PATH", "")
        if not resources_path:
            if getattr(sys, 'frozen', False):
                resources_path = Path(sys.executable).parent / "resources"
            else:
                resources_path = Path(__file__).parent.parent / "node_modules"

        acpx_dir = Path(resources_path) / "acpx"
        acpx_bin = acpx_dir / "bin" / "acpx.js"
        if acpx_bin.exists():
            return ["node", str(acpx_bin)]

        acpx_bin = Path(__file__).parent.parent / "node_modules" / "acpx" / "bin" / "acpx.js"
        if acpx_bin.exists():
            return ["node", str(acpx_bin)]

    # 2. Global acpx command on PATH
    acpx_cmd = shutil.which("acpx")
    if acpx_cmd:
        return ["acpx"]

    # 3. Docker environment
    if os.environ.get("IS_DOCKER"):
        for npm_root in["/usr/local/lib/node_modules", "/usr/lib/node_modules"]:
            acpx_js = Path(npm_root) / "acpx" / "bin" / "acpx.js"
            if acpx_js.exists():
                return["node", str(acpx_js)]

    # 4. Global npm installation
    try:
        result = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            npm_global_root = result.stdout.strip()
            acpx_js = Path(npm_global_root) / "acpx" / "bin" / "acpx.js"
            if acpx_js.exists():
                return ["node", str(acpx_js)]
    except Exception:
        pass

    # Common paths
    home = Path.home()
    for p in[
        Path("/usr/local/lib/node_modules/acpx/bin/acpx.js"),
        Path("/opt/homebrew/lib/node_modules/acpx/bin/acpx.js"),
    ]:
        if p.exists():
            return ["node", str(p)]

    # nvm / fnm
    for base in [home / ".nvm", home / ".fnm"]:
        if base.exists():
            for acpx_path in base.rglob("acpx/bin/acpx.js"):
                if acpx_path.exists():
                    return ["node", str(acpx_path)]

    # 5. npx fallback
    if shutil.which("npx"):
        return["npx", "acpx@latest"]

    raise RuntimeError("acpx not found. Run: npm install -g acpx@latest")


# ==================== Agent Mapping ====================

ACPM_AGENT_MAP = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "cursor": "cursor",
    "copilot": "copilot",
    "qwen": "qwen",
    "opencode": "opencode",
    "openclaw": "openclaw",
    "pi": "pi",
    "droid": "droid",
    "iflow": "iflow",
    "kilocode": "kilocode",
    "kimi": "kimi",
    "kiro": "kiro",
    "qoder": "qoder",
    "trae": "trae",
    "goose": "goose",
}

# ==================== Permission Mode Configuration (Per-Agent) ====================
#
# Frontend sends a unified abstract mode:
#   plan         - Read-only, deny all operations
#   default      - Agent default (typically read-only / confirm)
#   auto-approve - Allow writes, deny destructive ops (delete, force push, etc.)
#   yolo         - Bypass all permissions, full autonomy
#   cowork       - Same as yolo, full autonomy for collaborative work

AGENT_PERMISSION_CONFIG = {
    "claude": {
        "set_mode": {
            "plan":         "Plan",
            "default":      "Default",
            "auto-approve": "AcceptEdits",
            "yolo":         "BypassPermissions",
            "cowork":       "BypassPermissions",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "codex": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "auto",
            "cowork":       "auto",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "gemini": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-accept",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "cursor": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "copilot": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "qwen": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-accept",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "opencode": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "openclaw": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "pi": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "droid": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "iflow": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "kilocode": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "kimi": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-accept",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "kiro": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "qoder": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "trae": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
    "goose": {
        "set_mode": {
            "plan":         "plan",
            "default":      "default",
            "auto-approve": "auto-edit",
            "yolo":         "yolo",
            "cowork":       "yolo",
        },
        "global_flag": {
            "plan":         "--deny-all",
            "default":      None,
            "auto-approve": "--approve-all",
            "yolo":         "--approve-all",
            "cowork":       "--approve-all",
        },
    },
}


# ==================== Settings Loader ====================

async def _load_acp_settings() -> Dict[str, Any]:
    """Load ACP settings from persistent storage"""
    try:
        from py.get_setting import load_settings
        settings = await load_settings()
        acp_settings = settings.get("acpSettings", {})
        return {
            "agent": acp_settings.get("agent", "claude"),
            "permissionMode": acp_settings.get("permissionMode", "default"),
            "model": acp_settings.get("model", ""),
            "extraEnv": acp_settings.get("extraEnv", ""),
            "cc_path": settings.get("CLISettings", {}).get("cc_path", ""),
        }
    except Exception:
        return {
            "agent": "claude",
            "permissionMode": "default",
            "model": "",
            "extraEnv": "",
            "cc_path": "",
        }


def _parse_extra_env(extra_env_str: str) -> Dict[str, str]:
    """Parse KEY=value formatted environment variable string"""
    result = {}
    if not extra_env_str:
        return result
    for line in extra_env_str.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = value
    return result


# ==================== Stream Readers ====================

async def read_stream_chunks(
    stream: asyncio.StreamReader, is_error: bool = False
) -> AsyncIterator[str]:
    """Read stream in chunks, won't hang on missing newlines"""
    prefix = "[ERR] " if is_error else ""
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            decoded = chunk.decode("utf-8", errors="replace")
            if decoded.strip():
                yield f"{prefix}{decoded}"
    except asyncio.CancelledError:
        pass
    except Exception as e:
        yield f"{prefix}Stream read error: {e}"


async def read_stream_to_end(stream: asyncio.StreamReader) -> str:
    """Read entire stream, return as string. Used for stderr buffering."""
    parts =[]
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            parts.append(chunk.decode("utf-8", errors="replace"))
    except Exception:
        pass
    return "".join(parts)


# ==================== Process Cleanup ====================

async def _kill_process_tree(pid: int):
    """Force kill entire process tree (SIGTERM first, then SIGKILL)"""
    if pid is None:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        await asyncio.sleep(0.5)
        try:
            os.killpg(os.getpgid(pid), 0)
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass
    except ProcessLookupError:
        pass
    except Exception:
        pass


# ==================== Core Agent Function ====================

async def acpx_agent(
    prompt: str,
    agent_name: Optional[str] = None,
    mode: Optional[str] = None,
    cwd: Optional[str] = None,
    extra_env: Optional[Dict[str, str]] = None,
    args: Optional[list[str]] = None,
):
    """
    Invoke a sub-agent via ACPX, streaming results back.

    Features:
    - Same workspace reuses persistent session (sessions ensure + prompt)
    - Per-agent permission mode mapping (plan/default/auto-approve/yolo/cowork)
    - Auto cleanup child processes on exit (start_new_session + killpg)
    - Chunk-based stream reading, won't hang on missing newlines

    Args:
        prompt:      Instruction to send to the sub-agent
        agent_name:  Agent name (claude/codex/gemini/cursor/copilot/qwen/opencode/openclaw/pi/droid/iflow/kilocode/kimi/kiro/qoder/trae/goose)
        mode:        Permission mode (plan/default/auto-approve/yolo/cowork)
        cwd:         Working directory
        extra_env:   Extra environment variables dict
        args:        Extra command-line args passed to acpx
    """
    settings = await _load_acp_settings()

    final_agent = agent_name or settings["agent"]
    final_mode = mode or settings["permissionMode"]
    final_cwd = cwd or settings["cc_path"] or os.getcwd()

    # Environment variables
    env = os.environ.copy()
    for k, v in _parse_extra_env(settings["extraEnv"]).items():
        env[k] = v
    if extra_env:
        env.update(extra_env)
    if settings.get("model"):
        env["ACPM_MODEL"] = settings["model"]

    # Resolve acpx command
    try:
        acpx_cmd = _get_acpx_command()
    except RuntimeError as e:
        yield f"[ERROR] ACPX init failed: {e}\n"
        return

    agent_id = ACPM_AGENT_MAP.get(final_agent.lower())
    if not agent_id:
        yield f"[ERROR] Unsupported agent: {final_agent}. "
        yield f"Available: {list(ACPM_AGENT_MAP.keys())}\n"
        return

    # Get per-agent permission config (fallback to claude)
    agent_perm = AGENT_PERMISSION_CONFIG.get(
        agent_id, AGENT_PERMISSION_CONFIG["claude"]
    )
    set_mode_value = agent_perm["set_mode"].get(final_mode, "default")
    global_flag = agent_perm["global_flag"].get(final_mode)

    # Environment adaptation (Electron)
    if not os.environ.get("IS_DOCKER") and os.environ.get("ELECTRON_NODE_EXEC"):
        env["ELECTRON_RUN_AS_NODE"] = "1"
        node_dir = str(Path(os.environ["ELECTRON_NODE_EXEC"]).parent)
        env["PATH"] = f"{node_dir}{os.pathsep}{env.get('PATH', '')}"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 1: Ensure persistent session exists
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ensure_cmd = acpx_cmd + [agent_id, "sessions", "ensure"]
    try:
        ensure_proc = await asyncio.create_subprocess_exec(
            *ensure_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=final_cwd,
            env=env,
        )
        await ensure_proc.wait()
    except Exception:
        pass  # Don't block if ensure fails; try prompt anyway

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 2: Set session permission mode via set-mode
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    set_mode_cmd = acpx_cmd +[agent_id, "set-mode", set_mode_value]
    try:
        set_mode_proc = await asyncio.create_subprocess_exec(
            *set_mode_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=final_cwd,
            env=env,
        )
        await set_mode_proc.wait()
    except Exception:
        pass  # Don't block if set-mode fails

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 3: Build and execute prompt command
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    cmd = acpx_cmd.copy()

    # Global permission flag
    if global_flag:
        cmd.append(global_flag)

    # Working directory
    cmd.extend(["--cwd", final_cwd])

    # Agent name
    cmd.append(agent_id)

    # Use prompt subcommand (persistent session)
    cmd.append("prompt")

    # Extra args
    if args:
        cmd.extend(args)

    # Prompt content
    cmd.append(prompt)

    print(f"[ACPM] Executing: {' '.join(cmd)}")

    yield f"[Agent] {final_agent.upper()} via ACPM\n"
    yield f"[Mode]  {final_mode} (set-mode: {set_mode_value})\n"
    yield f"[CWD]   {final_cwd}\n"
    yield f"---\n"

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=final_cwd,
            env=env,
            start_new_session=True,
        )

        # Stream stdout, collect stderr in background
        queue: asyncio.Queue = asyncio.Queue()

        async def stream_stdout():
            async for chunk in read_stream_chunks(process.stdout):
                await queue.put(chunk)

        async def collect_stderr():
            err = await read_stream_to_end(process.stderr)
            if err.strip():
                await queue.put(f"\n[stderr]:\n{err}")

        stdout_task = asyncio.ensure_future(stream_stdout())
        stderr_task = asyncio.ensure_future(collect_stderr())

        while not (
            stdout_task.done() and stderr_task.done() and queue.empty()
        ):
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield chunk
            except asyncio.TimeoutError:
                continue

        await asyncio.gather(stdout_task, stderr_task)

        # Drain remaining queue items
        while not queue.empty():
            yield queue.get_nowait()

        await process.wait()

        if process.returncode == 0:
            yield "\n---\n[Done]\n"
        else:
            yield f"\n---\n[Exit code: {process.returncode}]\n"

    except FileNotFoundError:
        yield "\n[ERROR] acpx command not found\n"
    except Exception as e:
        yield f"\n[ERROR] {str(e)}\n"
    finally:
        if process is not None:
            await _kill_process_tree(process.pid)


# ==================== Status Check ====================

def check_acpx_available() -> dict:
    """Check if acpx is available in the current environment"""
    try:
        cmd = _get_acpx_command()
        return {
            "available": True,
            "command": cmd,
            "environment": (
                "docker"
                if os.environ.get("IS_DOCKER")
                else (
                    "electron"
                    if os.environ.get("ELECTRON_NODE_EXEC")
                    else "local"
                )
            ),
        }
    except RuntimeError as e:
        return {
            "available": False,
            "error": str(e),
            "environment": (
                "docker"
                if os.environ.get("IS_DOCKER")
                else (
                    "electron"
                    if os.environ.get("ELECTRON_NODE_EXEC")
                    else "local"
                )
            ),
        }


# ==================== OpenAI Tool Definition ====================

acp_agent_tool = {
    "type": "function",
    "function": {
        "name": "acpx_agent",
        "description": (
            "Invoke an AI coding agent via ACP protocol as a sub-agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Full instruction to send to the sub-agent",
                },
            },
            "required": ["prompt"],
        },
    },
}