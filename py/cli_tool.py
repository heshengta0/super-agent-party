#!/usr/bin/env python3
import asyncio
import os
import re
import shutil
import signal
import subprocess
import json
import platform
import time
import uuid
import tempfile
import socket
import glob as std_glob
import fnmatch
from pathlib import Path
from typing import AsyncIterator
from datetime import datetime
from collections import deque
import aiofiles
import aiofiles.os
import hashlib
import anyio
from py.get_setting import load_settings
from py.get_setting import SKILLS_DIR

COMMAND_TIMEOUT = 300  # 5分钟超时

# ==================== 环境初始化 ====================

try:
    from zerobox import Sandbox, SandboxCommandError
    HAS_ZEROBOX = True
except ImportError:
    HAS_ZEROBOX = False

def get_shell_environment():
    """通过子进程获取完整的 shell 环境"""
    shell = os.environ.get('SHELL', '/bin/zsh')
    home = Path.home()
    
    config_commands = [
        f'source {home}/.zshrc && env',
        f'source {home}/.bash_profile && env', 
        f'source {home}/.bashrc && env',
        'env'
    ]
    
    # Windows 环境简单跳过
    if platform.system() == "Windows":
        return

    for cmd in config_commands:
        try:
            result = subprocess.run(
                [shell, '-i', '-c', cmd],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if '=' in line:
                        var_name, var_value = line.split('=', 1)
                        os.environ[var_name] = var_value
                print("Successfully loaded environment from shell")
                return
        except Exception as e:
            continue
    
    print("Warning: Could not load shell environment, using current environment")

get_shell_environment()

# ==================== 核心基础设施：流处理 ====================


async def read_stream(stream, *, is_error: bool = False):
    """
    改进的流读取器：支持多编码回退，确保能抓取到系统原始报错。
    """
    if stream is None:
        return
    
    prefix = "[ERROR] " if is_error else ""
    
    while True:
        line_bytes = await stream.readline()
        if not line_bytes:
            break
            
        decoded = ""
        # 依次尝试：UTF-8 -> GBK (Windows) -> CP437 -> 替换模式
        for enc in ['utf-8', 'gbk', 'cp437']:
            try:
                decoded = line_bytes.decode(enc).rstrip()
                break
            except UnicodeDecodeError:
                continue
        
        if not decoded:
            decoded = line_bytes.decode('utf-8', errors='replace').rstrip()
            
        yield f"{prefix}{decoded}"

# 修改 read_stream 为分块读取，防止进度条挂起
async def read_stream_chunks(stream, prefix=""):
    """
    分块读取流，不等待换行符，解决进度条显示问题。
    """
    if stream is None:
        return
    
    try:
        while True:
            # 读取 4KB 数据块
            chunk = await stream.read(4096)
            if not chunk:
                break
            
            # 尝试多种编码解码
            decoded = ""
            for enc in ['utf-8', 'gbk', 'cp437']:
                try:
                    decoded = chunk.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            
            if not decoded:
                decoded = chunk.decode('utf-8', errors='replace')
            
            if decoded:
                yield f"{prefix}{decoded}"
    except Exception as e:
        yield f"[System Stream Error] {e}"


async def _merge_streams(*streams):
    """合并多个异步流"""
    streams = [s.__aiter__() for s in streams]
    while streams:
        for stream in list(streams):
            try:
                item = await stream.__anext__()
                yield item
            except StopAsyncIteration:
                streams.remove(stream)

async def _get_current_cwd() -> str:
    """获取当前配置的工作目录"""
    settings = await load_settings()
    cwd = settings.get("CLISettings", {}).get("cc_path")
    if not cwd:
        raise ValueError("No workspace directory specified in settings (CLISettings.cc_path).")
    return cwd

def get_detailed_exit_info(code: int, command: str) -> str:
    """
    根据退出码和操作系统，生成详细的诊断信息和建议。
    """
    cmd_name = command.strip().split()[0] if command.strip() else "unknown"
    system = platform.system()
    
    # 基础映射
    explanations = {
        1: "提示: 退出码 1 表示命令执行失败，请仔细阅读上方输出中的错误信息（如 Error、Fatal、error 关键字）以确定具体原因。",
        2: "Shell 内置命令使用不当。",
        126: "命令不可执行 (权限不足或不是可执行文件)。",
        127: "找不到命令 (Linux/Unix)。",
        130: "由 Control-C 终止。",
        137: "进程被强制杀死 (可能触发了 OOM 内存溢出)。",
        # Windows 特有
        9009: f"Windows: 找不到命令 '{cmd_name}'。请检查程序是否已安装，或是否已加入 PATH 环境变量。",
        5: "Windows: 拒绝访问 (权限不足)。",
    }
    
    info = f"\n[诊断信息] 进程退出码: {code}\n"
    info += f"[解释] {explanations.get(code, '未知错误类型')}\n"
    
    if code in [127, 9009]:
        info += f"💡 建议:\n"
        if system == "Windows":
            info += f"  1. 运行 'where {cmd_name}' 检查程序位置。\n"
            info += f"  2. 如果是刚安装的软件，可能需要重启 Agent 或使用绝对路径。\n"
        else:
            info += f"  1. 运行 'which {cmd_name}' 检查程序位置。\n"
            info += f"  2. 检查环境变量: 'echo $PATH'\n"
            
    return info


def _apply_patch(content: str, old_string: str, new_string: str) -> tuple[bool, str, str]:
    """
    高度鲁棒的补丁应用算法，完美解决 AI 换行符幻觉和“空行不断膨胀”的问题。
    """
    # 统一换行符为 \n
    content_lf = content.replace('\r\n', '\n')
    old_lf = old_string.replace('\r\n', '\n')
    new_lf = new_string.replace('\r\n', '\n')

    # 1. 绝对精确匹配 (速度最快，且绝不产生多余空行)
    if old_lf in content_lf:
        return True, content_lf.replace(old_lf, new_lf, 1), "Exact match successful."

    # 2. 智能模糊匹配阶段
    content_lines = content_lf.split('\n')
    old_lines = old_lf.split('\n')
    new_lines = new_lf.split('\n')

    # 辅助函数：统计首尾的空行数量
    def count_empty_padding(lines):
        start = 0
        while start < len(lines) and not lines[start].strip():
            start += 1
        end = 0
        while end < len(lines) and not lines[len(lines)-1-end].strip():
            end += 1
        return start, end

    # 统计并剥离 old_lines 首尾用来凑格式的空行
    old_pad_start, old_pad_end = count_empty_padding(old_lines)
    old_actual_end = len(old_lines) - old_pad_end
    old_stripped = old_lines[old_pad_start : old_actual_end]
    
    if not old_stripped:
        return False, content, "old_string is empty or only contains whitespaces."

    # 滑动窗口匹配
    def find_match(ignore_leading=False):
        for i in range(len(content_lines) - len(old_stripped) + 1):
            match = True
            for j in range(len(old_stripped)):
                c_line = content_lines[i+j].rstrip()
                o_line = old_stripped[j].rstrip()
                if ignore_leading:
                    c_line = c_line.lstrip()
                    o_line = o_line.lstrip()
                if c_line != o_line:
                    match = False
                    break
            if match:
                return i
        return -1

    match_idx = find_match(ignore_leading=False)
    msg = "Fuzzy match successful (ignored trailing whitespaces)."
    if match_idx == -1:
        match_idx = find_match(ignore_leading=True)
        msg = "Fuzzy match successful (ignored leading/trailing whitespaces)."

    if match_idx != -1:
        # 切片截取被保留的原文
        pre = content_lines[:match_idx]
        post = content_lines[match_idx + len(old_stripped):]
        
        # --- 杜绝空行膨胀的核心逻辑 ---
        new_pad_start, new_pad_end = count_empty_padding(new_lines)
        
        # 抵消 AI 在 old 和 new 中同时附带的无用上下文空行
        # 只有当 AI 故意在 new_string 中多加了空行时（即差值），才将其写入文件
        strip_front = min(old_pad_start, new_pad_start)
        strip_back = min(old_pad_end, new_pad_end)
        
        new_actual_end = len(new_lines) - strip_back
        new_final = new_lines[strip_front : new_actual_end]
        
        # 核心修复：使用纯 List 合并，绝对不硬编码拼接额外的 "\n"
        new_content = '\n'.join(pre + new_final + post)
        return True, new_content, msg

    # 匹配失败，生成带行号的纠错建议给 AI
    first_line_clean = old_stripped[0].strip()
    candidates =[]
    for i, line in enumerate(content_lines):
        if first_line_clean and first_line_clean in line:
            candidates.append(f"Line {i+1}: {line.strip()[:80]}")
            
    err_msg = "[Error] old_string not found in file. Check line endings or indentation.\n"
    if candidates:
        err_msg += "Did you mean one of these locations?\n" + "\n".join(candidates[:5])
    return False, content, err_msg

# ==================== [新增] 核心基础设施：进程管理 ====================

class ProcessManager:
    """全局后台进程管理器 (Docker & Local) - 增强版 (支持 Windows 进程树查杀)"""
    def __init__(self):
        # 结构: {pid: {"proc": proc, "logs": deque, "cmd": str, "type": str, "task": task, "status": str, "start_time": str}}
        self._processes = {}
        self._counter = 0

    def generate_id(self):
        self._counter += 1
        return str(self._counter)

    async def register_process(self, proc, cmd: str, p_type: str):
        """注册并开始监控一个后台进程"""
        pid = self.generate_id()
        logs = deque(maxlen=2000)
        
        task = asyncio.create_task(self._monitor_output(pid, proc, logs))
        
        self._processes[pid] = {
            "proc": proc,
            "logs": logs,
            "cmd": cmd,
            "type": p_type,
            "task": task,
            "status": "running",
            "start_time": datetime.now().isoformat()
        }
        return pid

    async def _monitor_output(self, pid: str, proc, logs: deque):
        async def read_stream_to_log(stream, prefix=""):
            if not stream: return
            try:
                while True:
                    # 改为读取分块，不再使用 readline()
                    chunk = await stream.read(1024) 
                    if not chunk:
                        break
                    
                    decoded = ""
                    for enc in ['utf-8', 'gbk', 'cp437']:
                        try:
                            decoded = chunk.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    if not decoded:
                        decoded = chunk.decode('utf-8', errors='replace')

                    timestamp = datetime.now().strftime("%H:%M:%S")
                    
                    # 处理回车符 \r，将其替换为换行，这样 log 中能看到进度条的每一步更新
                    # 如果不需要保留进度条每一行，可以直接 decoded.strip()
                    lines = decoded.replace('\r', '\n').splitlines()
                    for line in lines:
                        if line.strip():
                            logs.append(f"[{timestamp}] {prefix}{line}")
            except Exception as e:
                logs.append(f"[SYSTEM ERROR] {prefix}Monitoring failed: {str(e)}")

        try:
            await asyncio.gather(
                read_stream_to_log(proc.stdout, ""),
                read_stream_to_log(proc.stderr, "[ERR] ")
            )
            await proc.wait()
            if pid in self._processes:
                if "terminated" not in self._processes[pid]["status"]:
                    self._processes[pid]["status"] = f"exited (code {proc.returncode})"
        except Exception:
            pass

    def get_logs(self, pid: str, lines: int = 50) -> str:
        if pid not in self._processes:
            return f"Error: Process ID {pid} not found."
        
        entry = self._processes[pid]
        stored_logs = list(entry["logs"])
        subset = stored_logs[-lines:] if lines > 0 else stored_logs
        
        header = f"--- Logs for Process {pid} ({entry['status']}) ---\nCommand: {entry['cmd']}\n"
        return header + "\n".join(subset)

    def list_processes(self):
        if not self._processes:
            return "No background processes running."
        
        result = ["PID | Type   | Status       | Start Time          | Command"]
        result.append("-" * 90)
        
        active_found = False
        for pid, info in list(self._processes.items()):
            cmd_display = (info['cmd'][:45] + '...') if len(info['cmd']) > 45 else info['cmd']
            start_time = info['start_time'].split('T')[-1][:8]
            result.append(f"{pid:<4}| {info['type']:<7}| {info['status']:<13}| {start_time:<20}| {cmd_display}")
            active_found = True
        
        if not active_found:
            return "No background processes running."
        return "\n".join(result)

    async def kill_process(self, pid: str):
        """
        强制结束进程。
        针对 Windows 使用 taskkill /T 结束进程树，防止子进程残留。
        """
        if pid not in self._processes:
            return f"Error: Process ID {pid} not found."
        
        info = self._processes[pid]
        proc = info["proc"]
        
        # 即使 proc.returncode 已经有值，也要尝试清理可能的孤儿进程
        os_pid = proc.pid
        
        try:
            info["status"] = "terminating..."
            
            if platform.system() == "Windows":
                # Windows: 使用 taskkill /F (强制) /T (进程树) /PID <pid>
                # 这是清理 PowerShell/CMD 启动的子进程的关键
                kill_cmd = f"taskkill /F /T /PID {os_pid}"
                subprocess.run(kill_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # Linux/Mac: 尝试杀进程组 (如果适用) 或标准 terminate
                try:
                    proc.terminate()
                    # 给一点时间优雅退出
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except:
                        pass
            
            info["status"] = "terminated"
            return f"Process {pid} (OS PID {os_pid}) terminated successfully."
            
        except Exception as e:
            return f"Error terminating process {pid}: {str(e)}"
        
process_manager = ProcessManager()

# ==================== [新增] 核心基础设施：Docker 网络代理 ====================

class DockerPortProxy:
    """纯 Python 实现的 Docker 端口转发器 (Container -> Host)"""
    def __init__(self, container_name: str):
        self.container_name = container_name
        self.proxies = {} # {local_port: server_obj}

    async def start_forward(self, local_port: int, container_port: int):
        """开启转发：本地 TCP Server -> docker exec 桥接 -> 容器内部端口"""
        if local_port in self.proxies:
            return f"Port {local_port} is already being forwarded."

        if not self._is_port_available(local_port):
            return f"Error: Local port {local_port} is already in use."

        try:
            server = await asyncio.start_server(
                lambda r, w: self._handle_client(r, w, container_port),
                '127.0.0.1', local_port
            )
            
            self.proxies[local_port] = server
            asyncio.create_task(server.serve_forever())
            return f"Success: Forwarding localhost:{local_port} -> Docker:{container_port}"
        except Exception as e:
            return f"Error starting proxy: {str(e)}"

    def _is_port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) != 0

    async def _handle_client(self, client_reader, client_writer, container_port):
        """处理每个连接：启动一个 docker exec 进程作为管道"""
        try:
            # 微型 Python 转发脚本，在容器内运行
            proxy_script = (
                "import socket,sys,threading;"
                "s=socket.socket();"
                f"s.connect(('127.0.0.1',{container_port}));"
                "def r():"
                " while True:"
                "  d=s.recv(4096);"
                "  if not d: break;"
                "  sys.stdout.buffer.write(d);sys.stdout.flush();\n"
                "threading.Thread(target=r,daemon=True).start();"
                "while True:"
                " d=sys.stdin.buffer.read(4096);"
                " if not d: break;"
                " s.sendall(d)"
            )

            cmd = [
                "docker", "exec", "-i", 
                self.container_name, 
                "python3", "-u", "-c", proxy_script
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL 
            )

            async def pipe_reader_to_writer(reader, writer):
                try:
                    while True:
                        data = await reader.read(4096)
                        if not data: break
                        writer.write(data)
                        await writer.drain()
                except Exception:
                    pass
                finally:
                    try: writer.close()
                    except: pass

            await asyncio.gather(
                pipe_reader_to_writer(client_reader, proc.stdin),  # Local -> Docker
                pipe_reader_to_writer(proc.stdout, client_writer)  # Docker -> Local
            )
            try: proc.terminate()
            except: pass

        except Exception as e:
            try: client_writer.close()
            except: pass

    async def stop_forward(self, local_port: int):
        if local_port in self.proxies:
            server = self.proxies[local_port]
            server.close()
            await server.wait_closed()
            del self.proxies[local_port]
            return f"Stopped forwarding on port {local_port}"
        return f"Port {local_port} was not being forwarded."
    
    def list_proxies(self):
        if not self.proxies:
            return "No active port forwardings."
        return "\n".join([f"localhost:{p} -> container:{p} (active)" for p in self.proxies.keys()])

DOCKER_PROXIES = {} # {container_name: ProxyInstance}

# ==================== Docker Sandbox 基础设施 ====================

def get_safe_container_name(cwd: str) -> str:
    """根据路径生成合法容器名"""
    abs_path = str(Path(cwd).resolve())
    path_hash = hashlib.md5(abs_path.encode()).hexdigest()[:12]
    return f"sandbox-{path_hash}"

async def get_or_create_docker_sandbox(cwd: str, image_name: str = "docker/sandbox-templates:claude-code") -> str:
    """获取或创建基于路径的持久化沙盒，并映射全局skills目录"""
    container_name = get_safe_container_name(cwd)
    
    # 获取主机的全局skills目录
    host_skills_dir = SKILLS_DIR
    
    check_proc = await asyncio.create_subprocess_exec(
        "docker", "ps", "-a", "--filter", f"name=^/{container_name}$", "--format", "{{.Names}}|{{.Status}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await check_proc.communicate()
    output = stdout.decode().strip()
    
    if container_name in output:
        status = output.split("|")[-1] if "|" in output else ""
        if "Up" in status:
            return container_name
        else:
            # 启动已存在的容器
            await asyncio.create_subprocess_exec("docker", "start", container_name, stdout=asyncio.subprocess.PIPE)
            return container_name
    
    # 创建新容器，映射主机的全局skills目录
    # 注意：我们将主机skills目录映射到容器内的 /root/.agents/skills
    # 这是标准Agent Skills CLI使用的路径
    create_cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-v", f"{cwd}:/workspace",  # 映射工作目录
        "-v", f"{host_skills_dir}:/home/agent/.agents/skills",   # 映射全局skills目录到容器内
        "-w", "/workspace",
        "--restart", "unless-stopped",
        image_name,
        "tail", "-f", "/dev/null"
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *create_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode == 0:
        # 容器创建成功，确保容器内的skills目录权限正确
        try:
            # 设置容器内skills目录的权限
            chown_cmd = [
                "docker", "exec", container_name,
                "chown", "-R", "root:root", "/root/.agents/skills"
            ]
            chown_proc = await asyncio.create_subprocess_exec(
                *chown_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await chown_proc.communicate()
        except Exception:
            # 权限设置失败不影响主要功能
            pass
        
        return container_name
    else:
        # 简单重试逻辑
        if "is already in use" in stderr.decode():
            await asyncio.sleep(0.5)
            return await get_or_create_docker_sandbox(cwd, image_name)
        raise Exception(f"Failed to create sandbox: {stderr.decode()}")


async def _exec_docker_cmd_simple(cwd: str, cmd_list: list) -> str:
    """内部辅助函数：在容器内执行简单命令并获取输出"""
    container_name = await get_or_create_docker_sandbox(cwd)
    full_cmd = ["docker", "exec", "-w", "/workspace", container_name] + cmd_list
    
    proc = await asyncio.create_subprocess_exec(
        *full_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        raise Exception(f"Command failed: {stderr.decode().strip()}")
    return stdout.decode()

# ==================== 敏感信息脱敏工具 ====================

def _is_env_file(file_path: str) -> bool:
    """判断文件是否为环境变量文件（基于文件名）"""
    if not file_path:
        return False
    name = os.path.basename(file_path)
    return (
        name.startswith('.env') or 
        name.startswith('env.') or 
        name in ['.env', 'env', 'environment']
    )

def _mask_sensitive_value(value: str) -> str:
    """脱敏值：将值替换为掩码，只显示首尾部分"""
    v = value.strip()
    if len(v) <= 4:
        return '*' * len(v)
    elif len(v) <= 8:
        return v[0] + '*' * (len(v) - 2) + v[-1]
    else:
        return v[:3] + '*' * (len(v) - 6) + v[-3:]

def _mask_env_content(text: str) -> str:
    """对包含 KEY=VALUE 的行进行脱敏，VALUE 部分替换为掩码"""
    pattern = re.compile(
        r'^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*)(\S+)(.*)$',
        re.IGNORECASE
    )
    masked_lines = []
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            prefix = match.group(1)
            value = match.group(2)
            suffix = match.group(3) or ''
            masked_value = _mask_sensitive_value(value)
            new_line = f"{prefix}{masked_value}{suffix}"
            masked_lines.append(new_line)
        else:
            masked_lines.append(line)
    return "\n".join(masked_lines)

def _maybe_mask_output(file_path: str, output: str) -> str:
    """
    根据文件路径决定是否脱敏输出。
    用于 read_file、read_file_range、tail_file 等工具。
    输出可能包含行号前缀，例如 "   42 | CONTENT"。
    """
    if not _is_env_file(file_path):
        return output

    masked = []
    for line in output.splitlines():
        # 如果包含管道符，可能是带行号的输出
        if '|' in line:
            parts = line.split('|', 1)
            if len(parts) == 2:
                line_no = parts[0].strip()
                content = parts[1]
                masked_line = f"{line_no} | {_mask_env_content(content)}"
            else:
                masked_line = _mask_env_content(line)
        else:
            masked_line = _mask_env_content(line)
        masked.append(masked_line)
    return "\n".join(masked)

# ==================== Docker 环境工具实现 (含新功能) ====================

async def docker_sandbox(command: str, background: bool = False, timeout: int = 600) -> AsyncIterator[str]:
    """
    [Docker] 沙盒执行（打平版，直接返回异步生成器）
    """
    effective_timeout = max(1, min(timeout, 3600))
    settings = await load_settings()
    cwd = settings.get("CLISettings", {}).get("cc_path")
    if not cwd:
        yield "Error: No workspace directory specified."
        return
    
    try:
        container_name = await get_or_create_docker_sandbox(cwd)
    except Exception as e:
        yield f"Docker Sandbox Error: {str(e)}"
        return

    exec_cmd = [
        "docker", "exec", 
        "-i", 
        "-e", "PYTHONUNBUFFERED=1",
        "-e", "TERM=xterm",
        container_name, 
        "sh", "-c", f"cd /workspace && {command}"
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if background:
            pid = await process_manager.register_process(process, f"[Docker] {command}", "docker")
            yield f"[SUCCESS] Docker PID: {pid}"
            return

        queue = asyncio.Queue()
        async def wrap_stdout():
            async for chunk in read_stream_chunks(process.stdout, ""):
                await queue.put(chunk)
        async def wrap_stderr():
            async for chunk in read_stream_chunks(process.stderr, "[Docker stderr] "):
                await queue.put(chunk)

        stdout_task = asyncio.create_task(wrap_stdout())
        stderr_task = asyncio.create_task(wrap_stderr())

        start_time = time.time()
        try:
            while not (stdout_task.done() and stderr_task.done() and queue.empty()):
                remaining = effective_timeout - (time.time() - start_time)
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                try:
                    content = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield content
                except asyncio.TimeoutError:
                    continue
            
            await process.wait()

        except asyncio.TimeoutError:
            process.kill()
            yield f"\n\n[TIMEOUT ERROR] Docker 命令执行超过 {effective_timeout} 秒已强制终止。注意！命令并未完全执行完毕。"
            yield "\n💡 提示：对于启动应用或大文件下载，请使用 'background': true。"
    except Exception as e:
        yield f"[ERROR] Docker 进程启动失败: {str(e)}"

async def edit_file_patch_tool(path: str, old_string: str, new_string: str) -> str:
    """[Docker] 精确字符串替换（防膨胀版）"""
    try:
        real_cwd = await _get_current_cwd()
        container_name = await get_or_create_docker_sandbox(real_cwd)
        
        content = await _exec_docker_cmd_simple(real_cwd, ["cat", path])
        
        # 调用引擎处理
        success, new_content, msg = _apply_patch(content, old_string, new_string)
        if not success:
            return msg
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tmp:
            tmp.write(new_content)
            tmp_path = tmp.name
        
        dest_path = f"{container_name}:/workspace/{path}"
        cp_proc = await asyncio.create_subprocess_exec("docker", "cp", tmp_path, dest_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await cp_proc.communicate()
        os.unlink(tmp_path)
        
        if cp_proc.returncode != 0: return "[Error] Patch copy failed."
        return f"[Success] Patched '{path}'. ({msg})"
    except Exception as e:
        return f"[Error] Patch failed: {str(e)}"


async def glob_files_tool(pattern: str, exclude: str = "**/node_modules/**,**/.git/**,**/__pycache__/**") -> str:
    """[Docker] Glob 递归查找"""
    try:
        real_cwd = await _get_current_cwd()
        exclude_list = [e.strip() for e in exclude.split(",") if e.strip()]
        
        python_script = f'''
import glob, os, json, fnmatch
files = glob.glob("/workspace/{pattern}", recursive=True)
exclude_patterns = {exclude_list}
filtered = []
for f in files:
    if not os.path.isfile(f): continue
    rel_path = f.replace("/workspace/", "")
    should_exclude = False
    for ex in exclude_patterns:
        if fnmatch.fnmatch(rel_path, ex) or fnmatch.fnmatch(f, ex):
            should_exclude = True; break
    if not should_exclude: filtered.append(rel_path)
print(json.dumps(filtered))
'''
        output = await _exec_docker_cmd_simple(real_cwd, ["python3", "-c", python_script])
        files = json.loads(output)
        if not files: return "[Result] No files found."
        
        lines = [f"[{len(files)} files matched]"]
        for f in files[:50]:
            icon = "🐍" if f.endswith(".py") else "📄"
            lines.append(f"{icon} {f}")
        if len(files) > 50: lines.append(f"... {len(files)-50} more")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error] Glob failed: {str(e)}"

async def todo_write_tool(action: str, id: str = None, content: str = None, 
                          priority: str = "medium", status: str = None) -> str:
    """[Docker] 待办任务管理工具 - 使用3位数字有序ID"""
    try:
        real_cwd = await _get_current_cwd()
        container_name = await get_or_create_docker_sandbox(real_cwd)
        todo_file = "/workspace/.agent/ai_todos.json"
        
        # 从 Docker 容器读取任务列表
        try:
            data = await _exec_docker_cmd_simple(real_cwd, ["cat", todo_file])
            todos = json.loads(data)
        except:
            todos = []
            
        msg = ""

        # 生成下一个有序ID的辅助函数
        def _generate_ordered_id(existing_todos):
            if not existing_todos:
                return "1"
            # 找出最大数字 ID（兼容旧数据）
            numeric_ids = [int(t['id']) for t in existing_todos if t['id'].isdigit()]
            if not numeric_ids:
                return "1"
            return str(max(numeric_ids) + 1)  # 1, 2, 3... 不补零，不限制位数

        if action == "create":
            """创建新任务 - 自动生成3位数字有序ID"""
            if not content: 
                return "[Error] 创建任务必须提供 content 参数"
            
            new_id = _generate_ordered_id(todos)
            new_todo = {
                "id": new_id,
                "content": content,
                "priority": priority,
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "completed_at": None
            }
            todos.append(new_todo)
            msg = f"[Success] 已创建任务 #{new_id}: {content[:30]}"
            
        elif action == "list":
            """列出所有任务 - 按ID数字排序"""
            if not todos: 
                return "当前暂无任务"
            
            lines = ["📋 **任务列表** (ID越大创建越晚):"]
            sorted_todos = sorted(todos, key=lambda x: int(x['id']) if x['id'].isdigit() else 0)
            
            for t in sorted_todos:
                icon = "✅" if t.get('status') == 'done' else "⏳"
                priority_map = {"high": "🔴", "medium": "🟡", "low": "🟢"}
                p_icon = priority_map.get(t.get('priority', 'medium'), "⚪")
                lines.append(f"{icon} [{t['id']}] {p_icon} {t['content'][:40]}")
            return "\n".join(lines)

        elif action == "complete":
            """【高频】标记任务为已完成 - 幂等操作"""
            if not id: 
                return "[Error] 完成任务必须提供 id (如: 001)"
            
            target = next((t for t in todos if t['id'] == id), None)
            if not target: 
                return f"[Error] 未找到任务 #{id}"
            
            if target.get('status') == 'done':
                msg = f"[Info] 任务 #{id} 已经是完成状态"
            else:
                target['status'] = 'done'
                target['completed_at'] = datetime.now().isoformat()
                msg = f"[Success] 已完成任务 #{id}"

        elif action == "toggle":
            """切换完成状态"""
            if not id: 
                return "[Error] 切换状态必须提供 id"
            
            target = next((t for t in todos if t['id'] == id), None)
            if not target: 
                return f"[Error] 未找到任务 #{id}"
            
            if target.get('status') != 'done':
                target['status'] = 'done'
                target['completed_at'] = datetime.now().isoformat()
                msg = f"[Success] 已完成任务 #{id}"
            else:
                target['status'] = 'pending'
                target['completed_at'] = None
                msg = f"[Success] 已重新打开任务 #{id}"

        elif action == "update":
            """编辑任务详情"""
            if not id: 
                return "[Error] 更新任务必须提供 id"
            
            target = next((t for t in todos if t['id'] == id), None)
            if not target: 
                return f"[Error] 未找到任务 #{id}"
            
            if content: 
                target['content'] = content
            if priority: 
                target['priority'] = priority
            
            if status:
                if status == "done" and target.get('status') != "done":
                    target['completed_at'] = datetime.now().isoformat()
                elif status != "done" and target.get('status') == "done":
                    target['completed_at'] = None
                target['status'] = status
            
            target['updated_at'] = datetime.now().isoformat()
            msg = f"[Success] 已更新任务 #{id}"

        elif action == "delete":
            """删除任务"""
            if not id: 
                return "[Error] 删除任务必须提供 id"
            
            target = next((t for t in todos if t['id'] == id), None)
            if not target: 
                return f"[Error] 未找到任务 #{id}"
            
            todos.remove(target)
            msg = f"[Success] 已删除任务 #{id}"

        else:
            return f"[Error] 未知操作: {action}"

        # 写回 Docker 容器
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tmp:
            tmp.write(json.dumps(todos, indent=2, ensure_ascii=False))
            tmp_path = tmp.name
        
        await _exec_docker_cmd_simple(real_cwd, ["mkdir", "-p", "/workspace/.agent"])
        dest = f"{container_name}:{todo_file}"
        proc = await asyncio.create_subprocess_exec("docker", "cp", tmp_path, dest, 
                                                    stdout=asyncio.subprocess.PIPE)
        await proc.wait()
        
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
            
        return msg
        
    except Exception as e:
        return f"[Error] 任务操作失败: {str(e)}"
    
# 恢复原有的 Docker 基础文件工具
async def list_files_tool(path: str = ".", show_all: bool = True) -> str:
    try:
        real_cwd = await _get_current_cwd()
        flag = "-laF" if show_all else "-F"
        return await _exec_docker_cmd_simple(real_cwd, ["ls", flag, path])
    except Exception as e: return str(e)

async def read_file_tool(path: str, start_line: int = None, end_line: int = None) -> str:
    """[Docker] 读取文件：如果传入 start_line/end_line，自动委托给 read_file_range_tool"""
    if start_line is not None or end_line is not None:
        # 若模型误传范围参数，直接转入范围读取逻辑
        return await read_file_range_tool(path, start_line or 1, end_line or 1)
    try:
        real_cwd = await _get_current_cwd()
        
        MAX_LINES = 1000      # 最多读取行数
        MAX_LINE_WIDTH = 1000 # 单行最大字符数
        
        # 优化后的 Shell 脚本：
        # 1. 检查是否为二进制文件
        # 2. 获取总行数
        # 3. 使用 awk 处理每一行：编号、截断长行、限制总行数
        script = f"""
        FILE="{path}"
        if [ ! -f "$FILE" ]; then echo "[Error] File not found: $FILE"; exit 0; fi
        
        # 检查是否为二进制 (grep -I 返回非0表示二进制)
        if ! grep -qI . "$FILE"; then
            echo "[Error] Cannot read binary file: $FILE"
            exit 0
        fi

        total=$(wc -l < "$FILE" 2>/dev/null || echo 0)
        
        # 使用 awk 进行高效处理
        awk -v max_w={MAX_LINE_WIDTH} -v max_l={MAX_LINES} '
        NR <= max_l {{
            line = $0;
            if (length(line) > max_w) {{
                line = substr(line, 1, max_w) " ... [Line Truncated]";
            }}
            printf "%6d | %s\\n", NR, line;
        }}
        NR > max_l {{ exit }}
        ' "$FILE"

        if [ "$total" -gt {MAX_LINES} ]; then
            echo ""
            echo "... [Warning] File truncated. Showing 1 to {MAX_LINES} of $total lines."
            echo "💡 [Next Step Hint] The file is large. Use 'read_file_range' to read specific lines (e.g., 1001-2000) or 'tail_file' for the end."
        fi
        """
        raw_output = await _exec_docker_cmd_simple(real_cwd, ["sh", "-c", script])
        # 脱敏处理
        return _maybe_mask_output(path, raw_output)
    except Exception as e: 
        return f"[Error] Read failed: {str(e)}"

async def read_file_range_tool(path: str, start_line: int, end_line: int) -> str:
    """[Docker] 精准读取文件指定行范围，利用 awk 进行服务端截断"""
    try:
        if start_line < 1 or end_line < start_line:
            return "[Error] Invalid line range."
        
        real_cwd = await _get_current_cwd()
        # awk 逻辑说明：
        # 1. 指定行范围 NR>=start && NR<=end
        # 2. 如果行长度 > 1000，使用 substr 截断
        # 3. 格式化输出 行号 | 内容
        max_line_len = 1000
        script = (
            f"awk 'NR>={start_line} && NR<={end_line} {{"
            f"  line=$0; "
            f"  if (length(line) > {max_line_len}) "
            f"    line = substr(line, 1, {max_line_len}) \"... [Truncated]\"; "
            f"  printf \"%5d | %s\\n\", NR, line"
            f"}}' \"{path}\""
        )
        
        result = await _exec_docker_cmd_simple(real_cwd, ["sh", "-c", script])
        
        # 兜底：防止 Docker 返回的结果依然由于行数过多导致爆炸
        if len(result) > 50000:
            result = result[:50000] + "\n... [Warning] Output truncated by tool safety limit."
        # 脱敏处理
        return _maybe_mask_output(path, result)
    except Exception as e: 
        return str(e)

async def tail_file_tool(path: str, lines: int = 100) -> str:
    """[Docker] 读取文件末尾（常用于日志）"""
    try:
        real_cwd = await _get_current_cwd()
        # 先打行号，再 tail
        script = f"""cat -n "{path}" | tail -n {lines}"""
        raw_output = await _exec_docker_cmd_simple(real_cwd, ["sh", "-c", script])
        return _maybe_mask_output(path, raw_output)
    except Exception as e: return str(e)

async def edit_file_tool(path: str, content: str) -> str:
    try:
        real_cwd = await _get_current_cwd()
        container_name = await get_or_create_docker_sandbox(real_cwd)
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        await _exec_docker_cmd_simple(real_cwd, ["mkdir", "-p", os.path.dirname(path) or "."])
        dest = f"{container_name}:/workspace/{path}"
        proc = await asyncio.create_subprocess_exec("docker", "cp", tmp_path, dest, stdout=asyncio.subprocess.PIPE)
        await proc.wait()
        os.unlink(tmp_path)
        return f"[Success] Saved {path}"
    except Exception as e: return str(e)

def _mask_grep_output(grep_output: str) -> str:
    """对 grep 输出进行脱敏，对于 .env 文件中的匹配行，遮蔽 VALUE 部分"""
    if not grep_output:
        return grep_output
    lines = grep_output.splitlines()
    masked_lines = []
    for line in lines:
        # grep 输出格式通常为：filename:line_number:matched_text
        parts = line.split(':', 2)
        if len(parts) >= 3:
            fname = parts[0]
            line_no = parts[1]
            content = parts[2]
            # 如果文件名匹配 .env 模式，脱敏内容
            if _is_env_file(fname) or '.env' in fname.lower():
                content = _mask_env_content(content)
            masked_lines.append(f"{fname}:{line_no}:{content}")
        else:
            masked_lines.append(line)
    return "\n".join(masked_lines)


async def search_files_tool(pattern: str, path: str = ".") -> str:
    try:
        real_cwd = await _get_current_cwd()
        raw_output = await _exec_docker_cmd_simple(real_cwd, ["grep", "-rn", pattern, path])
        # 脱敏
        if _is_env_file(path) or '.env' in path.lower():
            return _mask_grep_output(raw_output)
        # 如果 path 本身不是 .env，但 grep 结果中可能包含 .env 文件，仍需脱敏
        # 简单方案：始终检查结果中是否包含 .env，调用 _mask_grep_output
        # 为避免性能影响，只在 raw_output 中包含 '.env' 时脱敏
        if '.env' in raw_output.lower():
            return _mask_grep_output(raw_output)
        return raw_output
    except Exception as e: return str(e)


# ==================== [新增] 管理工具：进程与网络 ====================

async def list_processes_tool() -> str:
    """[Common] 列出所有后台进程 (Docker & 本地)"""
    return process_manager.list_processes()

async def get_process_logs_tool(pid: str) -> str:
    """[Common] 获取指定进程的日志"""
    if not pid:
        return "Error: 'pid' is required to fetch logs."
    return process_manager.get_logs(pid)

async def kill_process_tool(pid: str) -> str:
    """[Common] 终止指定的后台进程"""
    if not pid:
        return "Error: 'pid' is required to kill a process."
    return await process_manager.kill_process(pid)

async def docker_manage_ports_tool(action: str, container_port: int = 8000, host_port: int = None) -> str:
    """[Docker] 端口转发管理"""
    try:
        real_cwd = await _get_current_cwd()
        container_name = await get_or_create_docker_sandbox(real_cwd)
        
        if container_name not in DOCKER_PROXIES:
            DOCKER_PROXIES[container_name] = DockerPortProxy(container_name)
        proxy = DOCKER_PROXIES[container_name]
        
        if action == "list":
            return proxy.list_proxies()
        if action == "forward":
            if not host_port: host_port = container_port
            return await proxy.start_forward(host_port, container_port)
        if action == "stop":
            if not host_port: return "Error: host_port required to stop."
            return await proxy.stop_forward(host_port)
        return "Unknown action."
    except Exception as e:
        return f"[Error] Port tool failed: {str(e)}"

async def local_net_tool(action: str, port: int = None) -> str:
    """[Local] 本地网络工具：检查端口占用"""
    if action == "check":
        if not port: return "Error: Port required."
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            result = s.connect_ex(('127.0.0.1', port))
            status = "OPEN/BUSY" if result == 0 else "CLOSED/FREE"
            return f"Port {port} on localhost is {status}."
    
    if action == "scan":
        # 简单扫描常用开发端口
        common_ports = [3000, 5000, 8000, 8080, 80, 443, 3306, 5432]
        results = []
        for p in common_ports:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                res = s.connect_ex(('127.0.0.1', p))
                status = "BUSY" if res == 0 else "FREE"
                results.append(f"{p}: {status}")
        return "Common Ports:\n" + "\n".join(results)
        
    return "Unknown action. Use check or scan."

# ==================== 本地环境 (Local) 工具实现 ====================

def resolve_strict_path(cwd: str, sub_path: str, check_symlink: bool = True) -> Path:
    """
    严格工作区路径解析
    - 禁止绝对路径
    - 禁止 ../ 遍历  
    - 禁止通过符号链接指向工作区外
    """
    base = Path(cwd).resolve()
    
    if not sub_path:
        return base
        
    # 清理输入（阻止空字节、换行等）
    sub_path = sub_path.strip().replace('\x00', '').replace('\n', '')
    
    # 显式禁止路径遍历模式（快速失败）
    if '..' in sub_path.split(os.sep):
        raise PermissionError(f"Path traversal detected: {sub_path}")
    
    # 禁止绝对路径（Windows C:\ 和 Unix /）
    if os.path.isabs(sub_path) or (len(sub_path) > 1 and sub_path[1] == ':'):
        raise PermissionError(f"Absolute paths not allowed: {sub_path}")
    
    # 解析完整路径
    target = (base / sub_path).resolve()
    
    # 关键检查：确保 resolve 后的路径仍在 base 内
    try:
        target.relative_to(base)
    except ValueError:
        raise PermissionError(f"Access denied: {sub_path} resolves outside workspace")
    
    # 符号链接检查（防止 /workspace/link -> /etc）
    if check_symlink and target.exists():
        real_path = target.resolve(strict=True)
        try:
            real_path.relative_to(base)
        except ValueError:
            raise PermissionError(f"Symlink escape detected: {sub_path} -> {real_path}")
            
    return target

from typing import Tuple

def validate_bash_command(command: str, cwd: str, mode: str = "default") -> Tuple[bool, str]:
    """
    增强版安全校验策略（支持 Win/Mac/Linux 跨平台）
    """
    cmd_lower = command.lower()
    
    # ===== 1. 路径穿越与敏感目录防御 =====
    # 防止多级向上跳转逃逸工作区 (例如 ../../../etc/passwd)
    if re.search(r'(\.\.[/\\]){2,}', command):
        return False, "Multiple path traversal (../../) is blocked"

    # 跨平台敏感目录 (兼容 / 和 \ 写法)
    sensitive_roots = [
        # Linux / macOS
        r'(?:\s|^)/etc', r'(?:\s|^)/var', r'(?:\s|^)/root', 
        r'(?:\s|^)/bin', r'(?:\s|^)/sbin', r'(?:\s|^)/usr/local/bin',
        r'(?:\s|^)/sys', r'(?:\s|^)/proc', 
        # macOS 专属
        r'(?:\s|^)/Library', r'(?:\s|^)/System',
        # Windows (兼容 C:\Windows 和 C:/Windows)
        r'(?:\s|^)[a-z]:[/\\]Windows', r'(?:\s|^)[a-z]:[/\\]Program Files', 
        r'(?:\s|^)[a-z]:[/\\]Users[/\\](?:Default|Public|Administrator)' 
    ]
    
    for pattern in sensitive_roots:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Access to sensitive system directory blocked by pattern: {pattern}"

    # 禁止直接 cd 到根目录或其他盘符
    if re.search(r'\bcd\s+/[a-z0-9_]*$', command, re.IGNORECASE):
        return False, "Changing directory to root is blocked"
    if re.search(r'\bcd\s+[a-z]:[/\\]', command, re.IGNORECASE):
        return False, "Changing Windows drive directly is blocked"

    # ===== 2. 跨平台毁灭性操作 =====
    destructive_patterns = [
        # Linux/Mac 文件删除 (覆盖 rm -rf /, rm -rf /*, rm -r -f /)
        (r'rm\s+-[rRfF\s]+\s*(/|[a-z]:[/\\])\*?', "Recursive delete root"),
        # Linux 危险操作
        (r'mkfs\.[a-z]+', "Filesystem format"),                    
        (r'dd\s+if=.*of=/dev/[a-z]', "Direct device write"),       
        (r'>?\s*/dev/(sda|hd|nvme|mmcblk)', "Block device access"),
        (r'chmod\s+-[R\s]*777\s+/', "Change root permissions"),
        (r'chown\s+-[R\s]*root\s+/', "Change root ownership"),
        (r':\(\)\{\s*:\|:&?\s*\};\s*:', "Fork bomb"), 
        # Windows 危险操作 (注册表破坏、危险格式化)
        (r'(?:\s|^)format\s+[a-z]:', "Windows disk format"),
        (r'(?:\s|^)reg\s+(delete|add)\s+(HKLM|HKEY_LOCAL_MACHINE)', "Modify system registry"),
        (r'Remove-Item\s+-Recurse\s+-Force\s+[a-z]:[/\\]', "Powershell recursive delete root"),
        # macOS 危险操作
        (r'nvram\s+-c', "Clear Mac NVRAM"),
    ]
    
    for pattern, reason in destructive_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Destructive operation blocked: {reason}"
    
    # ===== 3. 风险操作 (提权、钓鱼、远程执行) =====
    if mode != "yolo":
        risk_patterns = [
            # Linux/Mac 提权与远程加载
            (r'(?:\s|^)sudo\s+', "sudo usage blocked (prevents password wait/escalation)"),
            (r'(curl|wget).*\|\s*(sh|bash|zsh|python|perl|php)', "Remote execution via pipe"),
            (r'$\{?HOME\}?', "HOME env variable usage"),
            (r'~\s*/', "Home directory access via ~"),
            # macOS 钓鱼警告 (防范 AI 弹窗骗取用户密码)
            (r'(?:\s|^)osascript\s+-e\s+.*password', "AppleScript password prompt blocked"),
            # Windows 远程加载 (Powershell IEX)
            (r'(Invoke-WebRequest|iwr|Invoke-RestMethod|irm).*\|\s*(Invoke-Expression|iex)', "PowerShell remote script execution"),
        ]
        for pattern, reason in risk_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"{reason} blocked in {mode} mode"
    
    return True, command


async def shell_tool_local(command: str, background: bool = False, timeout: int = 600) -> AsyncIterator[str]:
    """
    [Local] 执行本地命令
    - Windows: 维持原有的 powershell/cmd 逻辑。
    - 非 Windows: 优先使用 zerobox.Sandbox 提供 OS 级沙盒隔离。
    """
    # 限制超时范围：1秒到1小时
    effective_timeout = max(1, min(timeout, 3600))
    
    settings = await load_settings()
    cwd = settings.get("CLISettings", {}).get("cc_path")
    perm = settings.get("localEnvSettings", {}).get("permissionMode", "default")
    
    if not cwd: 
        yield "Error: No workspace directory specified (cc_path)."
        return
    
    # 安全校验策略（双重防护）
    allowed, validate_result = validate_bash_command(command, cwd, mode=perm)
    if not allowed:
        yield f"[Security] Command blocked: {validate_result}"
        return
    
    system = platform.system()

    # ==================== 非 Windows 且安装了 ZeroBox 的情况 ====================
    # 注意：SDK 目前是同步阻塞设计。为了保持 CLI 响应，我们在线程中运行它。
    # 且为了 PID 管理器的兼容性，仅在前台任务（background=False）时使用 SDK。
    if system != "Windows" and HAS_ZEROBOX and not background:
        try:
            yield f"--- [Sandbox Mode] Executing via zerobox.Sandbox ---\n"
            
            def run_sandbox():
                # 创建沙盒实例：允许读写当前工作目录，开启所有权限以兼容复杂命令
                # 如果需要更严格，可以将 allow_all=True 改为 allow_write=[cwd]
                sb = Sandbox.create({
                    "cwd": cwd,
                    "allow_write": [cwd],  
                    "allow_read": [cwd],  
                    "allow_net": True,
                })
                # 使用 .output() 获取 code, stdout, stderr 且不抛出执行异常
                return sb.sh(command).output(timeout=float(effective_timeout))

            # 在线程池中执行同步的 SDK 调用
            result = await asyncio.to_thread(run_sandbox)
            
            if result.stdout:
                yield result.stdout
            if result.stderr:
                yield f"[stderr] {result.stderr}"
            
            if result.code != 0:
                yield f"\n--- 运行结束 (Exit Code: {result.code}) ---"
                yield get_detailed_exit_info(result.code, command)
            
            return # 任务结束

        except subprocess.TimeoutExpired:
            yield f"\n\n[TIMEOUT ERROR] 命令执行超过 {effective_timeout} 秒已强制终止。"
            return
        except Exception as e:
            yield f"[Sandbox Error] ZeroBox SDK 运行异常: {str(e)}\n尝试回退到标准 Shell...\n"
            # 报错后不返回，继续尝试下方的标准执行逻辑

    # ==================== Windows 或 回退到标准 Shell 的情况 ====================
    
    if system == "Windows":
        def is_strictly_cmd(cmd_str: str) -> bool:
            c = cmd_str.lower().strip()
            if re.search(r'%[a-z0-9_]+%', c): return True
            if '&&' in c and '$' not in c: return True
            return False
            
        if is_strictly_cmd(command):
            exe, args = "cmd.exe", ["/c", command]
        else:
            exe, args = "powershell.exe", ["-NonInteractive", "-NoProfile", "-Command", command]
    else:
        # 非 Windows 环境回退或后台任务
        exe, args = os.environ.get('SHELL', '/bin/bash'), ["-c", command]

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["TERM"] = "xterm"

    try:
        process = await asyncio.create_subprocess_exec(
            exe, *args,
            stdout=asyncio.subprocess.PIPE, 
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=(system != "Windows")
        )

        if background:
            pid = await process_manager.register_process(process, command, "local")
            yield f"[SUCCESS] Background process started.\nPID: {pid}"
            return

        queue = asyncio.Queue()
        async def wrap_stdout():
            async for chunk in read_stream_chunks(process.stdout, ""):
                await queue.put(chunk)
        async def wrap_stderr():
            async for chunk in read_stream_chunks(process.stderr, "[stderr] "):
                await queue.put(chunk)

        stdout_task = asyncio.create_task(wrap_stdout())
        stderr_task = asyncio.create_task(wrap_stderr())

        start_time = time.time()
        try:
            while not (stdout_task.done() and stderr_task.done() and queue.empty()):
                remaining = effective_timeout - (time.time() - start_time)
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                try:
                    content = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield content
                except asyncio.TimeoutError:
                    continue

            await process.wait()
            if process.returncode != 0:
                yield f"\n--- 运行结束 (Exit Code: {process.returncode}) ---"
                yield get_detailed_exit_info(process.returncode, command)
                
        except asyncio.TimeoutError:
            # 杀进程树逻辑
            if system == "Windows":
                subprocess.run(f"taskkill /F /T /PID {process.pid}", shell=True, capture_output=True)
            else:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except:
                    process.kill()
            yield f"\n\n[TIMEOUT ERROR] 命令执行超过 {effective_timeout} 秒已强制终止。"
            yield "\n💡 提示：对于长期运行的任务，请使用 'background': true。"
            
    except Exception as e:
        yield f"[系统错误] 无法启动进程: {str(e)}"

# 恢复原有的 Local 文件工具
async def list_files_tool_local(path: str = ".", show_all: bool = True) -> str:
    """[Local] 列出文件：优先显示目录，支持数量截断，过滤隐藏文件"""
    try:
        cwd = await _get_current_cwd()
        target = resolve_strict_path(cwd, path, check_symlink=True)
        
        if not target.is_dir():
            return f"[Error] Not a directory: {path}"

        # 使用 scandir 获取更详细的信息且速度更快
        entries = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    if not show_all and entry.name.startswith('.'):
                        continue
                    
                    is_dir = entry.is_dir()
                    # 格式：(是否目录, 排序名, 显示字符串)
                    # 目录排在前面 (0)，文件排在后面 (1)
                    display_name = f"{entry.name}/" if is_dir else entry.name
                    entries.append((0 if is_dir else 1, entry.name.lower(), display_name))
        except PermissionError:
            return f"[Error] Permission denied accessing: {path}"

        # 排序：先按目录/文件分，再按名称字母序
        entries.sort()

        # 数量截断防止 Token 爆炸
        MAX_ITEMS = 200
        result_lines = [e[2] for e in entries[:MAX_ITEMS]]
        
        summary = f"Total: {len(entries)} items"
        if len(entries) > MAX_ITEMS:
            summary += f" (Showing first {MAX_ITEMS})"
            result_lines.append(f"... {len(entries) - MAX_ITEMS} more items")
        
        return f"{summary} in {path}:\n" + "\n".join(result_lines) if result_lines else "Empty directory."

    except Exception as e:
        return f"[Error] List failed: {str(e)}"

def _format_line(line_number: int, content: str, max_line_chars: int = 1000) -> str:
    """格式化单行，如果太长则截断"""
    content = content.rstrip('\r\n')
    if len(content) > max_line_chars:
        # 截断并保留前后部分，中间提示
        half = max_line_chars // 2
        content = f"{content[:half]} ... [Truncated {len(content)-max_line_chars} chars] ... {content[-50:]}"
    return f"{line_number:5} | {content}"

async def read_file_tool_local(path: str, start_line: int = None, end_line: int = None) -> str:
    if start_line is not None or end_line is not None:
        return await read_file_range_tool_local(path, start_line or 1, end_line or 1)
    try:
        MAX_LINES = 1000
        MAX_LINE_CHARS = 1000
        MAX_TOTAL_CHARS = 50000
        
        cwd = await _get_current_cwd()
        target = resolve_strict_path(cwd, path, check_symlink=True)
        if not target.exists() or not target.is_file():
            return f"[Error] File not found: {path}"

        # 二进制检查保持不变...
        with open(target, 'rb') as f_bin:
            if b'\0' in f_bin.read(1024):
                return f"[Error] Cannot read binary file: {path}"

        output = []
        current_total_len = 0
        truncated = False
        
        async with aiofiles.open(target, 'r', encoding='utf-8', errors='replace') as f:
            line_idx = 1
            async for line in f:
                formatted = _format_line(line_idx, line, MAX_LINE_CHARS)
                output.append(formatted)
                current_total_len += len(formatted)
                
                if line_idx >= MAX_LINES or current_total_len > MAX_TOTAL_CHARS:
                    truncated = True
                    break
                line_idx += 1

        res = "\n".join(output)
        if truncated:
            res += f"\n\n... [Warning] Content truncated (Safety Limit). Last line read: {line_idx}."
            res += f"\n💡 [Hint] The file is large or has very long lines. Use 'read_file_range_local' to explore specific sections."
        
        # 脱敏处理
        return _maybe_mask_output(path, res)
    except Exception as e: 
        return f"[Error] Read failed: {str(e)}"  

async def read_file_range_tool_local(path: str, start_line: int, end_line: int) -> str:
    """[Local] 精准读取文件指定行范围，增加溢出保护"""
    try:
        MAX_TOTAL_CHARS = 30000  # 单次返回最大字符数，防止上下文爆炸
        MAX_LINE_CHARS = 1000    # 单行最大显示长度
        
        if start_line < 1 or end_line < start_line:
            return "[Error] Invalid line range."
            
        cwd = await _get_current_cwd()
        target = resolve_strict_path(cwd, path, check_symlink=True)
        if not target.exists() or not target.is_file(): 
            return f"[Error] File not found: {path}"

        output = []
        current_total_len = 0
        
        async with aiofiles.open(target, 'r', encoding='utf-8', errors='replace') as f:
            line_idx = 1
            async for line in f:
                if line_idx >= start_line:
                    formatted = _format_line(line_idx, line, MAX_LINE_CHARS)
                    output.append(formatted)
                    current_total_len += len(formatted)
                    
                    if current_total_len > MAX_TOTAL_CHARS:
                        output.append(f"--- [Warning] Output stopped: Reached limit of {MAX_TOTAL_CHARS} chars ---")
                        break
                
                if line_idx >= end_line:
                    break
                line_idx += 1
            
        if not output and line_idx < start_line:
            return f"[Error] start_line ({start_line}) is beyond file length ({line_idx})."
        
        res = "\n".join(output)
        # 脱敏处理
        return _maybe_mask_output(path, res)
    except Exception as e: 
        return f"[Error] Range read failed: {str(e)}"

async def tail_file_tool_local(path: str, lines: int = 100) -> str:
    """[Local] 读取文件末尾（常用于日志）"""
    try:
        cwd = await _get_current_cwd()
        target = resolve_strict_path(cwd, path, check_symlink=True)
        if not target.exists() or not target.is_file(): return f"[Error] File not found: {path}"

        # 本地简单实现：读入后切片（如果文件极大建议改用 seek 倒序读，但此处通常够用）
        async with aiofiles.open(target, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = await f.readlines()
            
        subset = all_lines[-lines:] if lines < len(all_lines) else all_lines
        start_idx = max(1, len(all_lines) - lines + 1)
        
        res = "\n".join(f"{i + start_idx:4} | {line.rstrip('\n')}" for i, line in enumerate(subset))
        # 脱敏处理
        return _maybe_mask_output(path, res)
    except Exception as e: return f"[Error] Tail failed: {str(e)}"

async def edit_file_tool_local(path: str, content: str) -> str:
    """[Local] 写入文件：修复了绝对路径误判问题"""
    try:
        cwd = await _get_current_cwd()
        # 这一步已经确保了 path 不会逃逸出 cwd
        target = resolve_strict_path(cwd, path, check_symlink=True)
        
        # 1. 确保父目录存在
        parent_dir = target.parent
        # --- 删除了导致报错的 resolve_strict_path(cwd, str(parent_dir)...) ---
        
        await aiofiles.os.makedirs(parent_dir, exist_ok=True)

        # 2. 创建备份 (如果文件存在)
        backup_msg = ""
        if target.exists():
            try:
                backup_path = target.with_suffix(target.suffix + ".bak")
                shutil.copy2(target, backup_path)
                backup_msg = f" (Backup created: {backup_path.name})"
            except Exception as e:
                print(f"[Warn] Backup failed: {e}")

        # 3. 原子写入
        temp_path = target.with_suffix(target.suffix + f".tmp.{uuid.uuid4().hex[:6]}")
        try:
            async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                await f.write(content)
            
            if os.path.exists(target):
                os.replace(temp_path, target)
            else:
                os.rename(temp_path, target)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

        return f"Saved successfully{backup_msg}."

    except Exception as e:
        return f"[Error] Edit failed: {str(e)}"

async def search_files_tool_local(pattern: str, path: str = ".") -> str:
    """[Local] 智能搜索：优先尝试 git grep/grep，回退到优化的 Python 实现"""
    try:
        cwd = await _get_current_cwd()
        target_dir = resolve_strict_path(cwd, path, check_symlink=True)
        target_str = str(target_dir)
        
        # 1. 尝试使用 git grep (速度最快，且自动尊重 .gitignore)
        # 只有当在 git 仓库内且安装了 git 时有效
        if os.path.isdir(os.path.join(cwd, ".git")) and shutil.which("git"):
            try:
                # -I: 不搜索二进制, -n: 行号, --full-name: 相对路径
                cmd = ["git", "grep", "-I", "-n", "--full-name", pattern]
                # 如果指定了子目录，限制搜索范围
                rel_path = os.path.relpath(target_str, cwd)
                if rel_path != ".":
                    cmd.append(rel_path)
                
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    result = stdout.decode('utf-8', errors='replace').strip()
                    # 如果搜索路径或结果包含 .env，进行脱敏
                    if _is_env_file(path) or '.env' in path.lower():
                        return _mask_grep_output(result)
                    return result
            except Exception:
                pass # git grep 失败则回退

        # 2. 优化的 Python 实现 (Ripgrep-lite)
        matches = []
        regex = re.compile(pattern)
        MAX_RESULTS = 1000  # 防止结果爆炸
        
        # 定义需要跳过的目录和扩展名
        SKIP_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', '.env', 'dist', 'build', 'coverage'}
        SKIP_EXTS = {'.pyc', '.pyo', '.so', '.dll', '.exe', '.bin', '.png', '.jpg', '.jpeg', '.gif', '.zip', '.tar', '.gz'}

        # 判断文件是否为二进制 (读取前 1024 字节检查 NULL)
        def is_binary(file_path):
            try:
                with open(file_path, 'rb') as f:
                    chunk = f.read(1024)
                    return b'\0' in chunk
            except:
                return True

        for root, dirs, files in os.walk(target_str, topdown=True):
            # 剪枝：直接修改 dirs 列表，阻止 os.walk 进入这些目录
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
            
            for file in files:
                if any(file.endswith(ext) for ext in SKIP_EXTS): continue
                
                full_path = os.path.join(root, file)
                # 相对路径用于显示
                display_path = os.path.relpath(full_path, cwd)
                
                if is_binary(full_path): continue

                try:
                    # 使用 aiofiles 异步读取文本
                    async with aiofiles.open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                        content = await f.read()
                        lines = content.splitlines()
                        for i, line in enumerate(lines, 1):
                            if regex.search(line):
                                # 截断过长的行
                                clean_line = line.strip()[:200]
                                # 如果文件是 .env，脱敏
                                if _is_env_file(file):
                                    clean_line = _mask_env_content(clean_line)
                                matches.append(f"{display_path}:{i}:{clean_line}")
                                if len(matches) >= MAX_RESULTS:
                                    return "\n".join(matches) + f"\n... (Truncated at {MAX_RESULTS} matches)"
                except Exception:
                    continue

        return "\n".join(matches) if matches else "No matches found."
    except Exception as e:
        return f"[Error] Search failed: {str(e)}"


async def glob_files_tool_local(pattern: str, exclude: str = "") -> str:
    """[Local] 智能查找：使用 pathlib.glob 支持 ** 递归，兼容 Windows"""
    try:
        cwd = await _get_current_cwd()
        base = Path(cwd).resolve()

        excludes = [e.strip() for e in exclude.split(",") if e.strip()]
        DEFAULT_EXCLUDES = {'.git', 'node_modules', '__pycache__', 'venv', 'dist', 'build'}

        # 使用 pathlib 的 glob，天然支持 ** 递归，并且与系统分隔符无关
        matched_paths = list(base.glob(pattern))

        results = []
        for p in matched_paths:
            if not p.is_file():
                continue

            # 计算相对路径，用于后续匹配和输出
            try:
                rel = p.relative_to(base)
            except ValueError:
                # 如果路径不在 base 内（理论上不会发生），跳过
                continue

            rel_str = rel.as_posix()  # 统一使用正斜杠，跨平台一致

            # 排除命中 exclude 参数中的模式
            if any(fnmatch.fnmatch(rel_str, ex) for ex in excludes):
                continue

            # 排除默认隐藏/构建目录（检查路径中的每一级目录）
            parts = rel.parts
            if any(part in DEFAULT_EXCLUDES or part.startswith('.') for part in parts):
                continue

            results.append(rel_str)

        output = sorted(results)
        limit = 200
        if not output:
            return "No files matched."
        if len(output) > limit:
            return "\n".join(output[:limit]) + f"\n... ({len(output)-limit} more files)"
        return "\n".join(output)

    except Exception as e:
        return f"[Error] Glob failed: {str(e)}"

async def edit_file_patch_tool_local(path: str, old_string: str, new_string: str) -> str:
    """[Local] 精确替换（防膨胀版）"""
    try:
        cwd = await _get_current_cwd()
        target = resolve_strict_path(cwd, path, check_symlink=True)
        if not target.exists():
            return f"[Error] File not found: {path}"

        async with aiofiles.open(target, 'r', encoding='utf-8') as f:
            content = await f.read()

        # 调用引擎处理
        success, new_content, msg = _apply_patch(content, old_string, new_string)
        if not success:
            return msg

        # 写回
        async with aiofiles.open(target, 'w', encoding='utf-8') as f:
            await f.write(new_content)
            
        return f"[Success] Patched '{path}'. ({msg})"
    except Exception as e:
        return f"[Error] Patch failed: {str(e)}"

async def todo_write_tool_local(action: str, id: str = None, content: str = None, 
                                priority: str = "medium", status: str = None) -> str:
    """本地待办任务管理工具 - 使用3位数字有序ID"""
    try:
        cwd = await _get_current_cwd()
        party_dir = Path(cwd) / ".agent"
        if not party_dir.exists():
            await aiofiles.os.makedirs(party_dir, exist_ok=True)
        
        todo_file = party_dir / "ai_todos.json"
        
        # 读取现有任务
        todos = []
        if todo_file.exists():
            try:
                async with aiofiles.open(todo_file, 'r', encoding='utf-8') as f:
                    file_content = await f.read()
                    if file_content.strip():
                        todos = json.loads(file_content)
            except (json.JSONDecodeError, Exception):
                todos = []
            
        msg = ""

        # 生成下一个有序ID的辅助函数
        def _generate_ordered_id(existing_todos):
            if not existing_todos:
                return "1"
            # 找出最大数字 ID（兼容旧数据）
            numeric_ids = [int(t['id']) for t in existing_todos if t['id'].isdigit()]
            if not numeric_ids:
                return "1"
            return str(max(numeric_ids) + 1)  # 1, 2, 3... 不补零，不限制位数

        if action == "create":
            """创建新任务 - 自动生成3位数字有序ID"""
            if not content: 
                return "[Error] 创建任务必须提供 content 参数"
            
            new_id = _generate_ordered_id(todos)
            new_todo = {
                "id": new_id,
                "content": content,
                "priority": priority,
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "completed_at": None
            }
            todos.append(new_todo)
            msg = f"[Success] 已创建任务 #{new_id}: {content[:30]}"
            
        elif action == "list":
            """列出所有任务 - 按ID数字大小排序"""
            if not todos: 
                return "当前项目暂无任务"
            
            lines = ["📋 **项目任务列表** (ID越大创建越晚):"]
            # 按ID数字排序，确保展示有序性
            sorted_todos = sorted(todos, key=lambda x: int(x['id']) if x['id'].isdigit() else 0)
            
            for t in sorted_todos:
                status_icon = "✅" if t.get('status') == 'done' else "⏳"
                priority_map = {"high": "🔴", "medium": "🟡", "low": "🟢"}
                p_icon = priority_map.get(t.get('priority', 'medium'), "⚪")
                lines.append(f"{status_icon} [{t['id']}] {p_icon} {t['content'][:40]}")
            return "\n".join(lines)

        elif action == "complete":
            """【高频】标记任务为已完成 - 幂等操作"""
            if not id: 
                return "[Error] 完成任务必须提供 id (如: 001)"
            
            target = next((t for t in todos if t['id'] == id), None)
            if not target: 
                return f"[Error] 未找到任务 #{id}"
            
            if target.get('status') == 'done':
                msg = f"[Info] 任务 #{id} 已经是完成状态"
            else:
                target['status'] = 'done'
                target['completed_at'] = datetime.now().isoformat()
                msg = f"[Success] 已完成任务 #{id}"

        elif action == "toggle":
            """切换完成状态 - pending↔done"""
            if not id: 
                return "[Error] 切换状态必须提供 id"
            
            target = next((t for t in todos if t['id'] == id), None)
            if not target: 
                return f"[Error] 未找到任务 #{id}"
            
            if target.get('status') != 'done':
                target['status'] = 'done'
                target['completed_at'] = datetime.now().isoformat()
                msg = f"[Success] 已完成任务 #{id}"
            else:
                target['status'] = 'pending'
                target['completed_at'] = None
                msg = f"[Success] 已重新打开任务 #{id}"

        elif action == "update":
            """编辑任务详情"""
            if not id: 
                return "[Error] 更新任务必须提供 id"
            
            target = next((t for t in todos if t['id'] == id), None)
            if not target: 
                return f"[Error] 未找到任务 #{id}"
            
            if content: 
                target['content'] = content
            if priority: 
                target['priority'] = priority
            
            if status:
                if status == "done" and target.get('status') != "done":
                    target['completed_at'] = datetime.now().isoformat()
                elif status != "done" and target.get('status') == "done":
                    target['completed_at'] = None
                target['status'] = status
            
            target['updated_at'] = datetime.now().isoformat()
            msg = f"[Success] 已更新任务 #{id}"

        elif action == "delete":
            """删除任务"""
            if not id: 
                return "[Error] 删除任务必须提供 id"
            
            target = next((t for t in todos if t['id'] == id), None)
            if not target: 
                return f"[Error] 未找到任务 #{id}"
            
            todos.remove(target)
            msg = f"[Success] 已删除任务 #{id}"

        else:
            return f"[Error] 未知操作: {action}"

        # 保存到本地文件
        async with aiofiles.open(todo_file, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(todos, indent=2, ensure_ascii=False))
            
        return msg

    except Exception as e:
        return f"[Error] 操作失败: {str(e)}"
    
# ==================== [新增] Skill 专用读取工具 ====================

async def read_skill_tool_logic(cwd: str, skill_id: str, is_docker: bool = True) -> str:
    """
    内部通用逻辑：读取 Skill 文件夹结构和说明文档。
    若工作区不存在该技能，且全局技能目录可用，则自动复制到工作区（Docker/Local 均支持）。
    """
    skill_rel_path = f".agent/skills/{skill_id}"
    workspace_skill_path = f"/workspace/.agent/skills/{skill_id}" if is_docker else str(Path(cwd) / ".agent" / "skills" / skill_id)

    # ----- 复制逻辑：工作区缺失时，从全局复制 -----
    if is_docker:
        # Docker 环境：利用已映射的全局技能目录
        container_name = await get_or_create_docker_sandbox(cwd)          # 获取/创建容器
        global_skill_path = f"/home/agent/.agents/skills/{skill_id}"      # 容器内全局技能路径
        try:
            # 1. 检查工作区技能是否存在
            test_cmd = ["test", "-d", workspace_skill_path]
            await _exec_docker_cmd_simple(cwd, test_cmd)                  # 不存在会抛出异常
        except Exception:
            # 2. 工作区不存在，尝试从全局复制
            try:
                # 检查全局技能是否存在
                test_global = ["test", "-d", global_skill_path]
                await _exec_docker_cmd_simple(cwd, test_global)

                # 确保目标父目录存在
                mkdir_cmd = ["mkdir", "-p", f"/workspace/.agent/skills"]
                await _exec_docker_cmd_simple(cwd, mkdir_cmd)

                # 执行复制
                cp_cmd = ["cp", "-r", global_skill_path, f"/workspace/.agent/skills/"]
                await _exec_docker_cmd_simple(cwd, cp_cmd)

                print(f"[Skill AutoCopy][Docker] Copied global skill '{skill_id}' to workspace.")
            except Exception as e:
                # 复制失败或全局技能不存在，继续尝试读取工作区（若不存在则后续报错）
                pass
    else:
        # Local 环境：使用 shutil 复制（已实现，但整合到 logic 中统一管理）
        workspace_path = Path(cwd) / ".agent" / "skills" / skill_id
        if not workspace_path.exists():
            global_path = Path(SKILLS_DIR) / skill_id
            if global_path.exists() and global_path.is_dir():
                try:
                    workspace_path.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(
                        shutil.copytree,
                        global_path,
                        workspace_path,
                        dirs_exist_ok=True
                    )
                    print(f"[Skill AutoCopy][Local] Copied global skill '{skill_id}' to workspace.")
                except Exception as e:
                    print(f"[Skill AutoCopy][Local] Copy failed: {e}. Will fallback to global read.")
                    # 降级读取已由主流程处理

    # ----- 原有读取逻辑保持不变（读取工作区技能）-----
    tree_str = ""
    doc_content = ""

    if is_docker:
        try:
            tree_str = await _exec_docker_cmd_simple(cwd, ["find", skill_rel_path, "-maxdepth", "2", "-not", "-path", '*/.*'])
            for name in ["SKILL.md", "skill.md", "SKILLS.md", "skills.md"]:
                try:
                    doc_path = f"{skill_rel_path}/{name}"
                    doc_content = await _exec_docker_cmd_simple(cwd, ["cat", doc_path])
                    break
                except:
                    continue
        except Exception as e:
            return f"[Error] Skill '{skill_id}' not found or inaccessible in Docker: {str(e)}"
    else:
        try:
            base_path = Path(cwd) / ".agent" / "skills" / skill_id
            if not base_path.exists():
                return f"[Error] Skill '{skill_id}' folder does not exist in workspace and auto-copy failed or global skill unavailable."

            # 生成本地文件树（深度 ≤2）
            tree_lines = [f"{skill_id}/"]
            for p in base_path.rglob("*"):
                if p.name.startswith("."): continue
                depth = len(p.relative_to(base_path).parts)
                if depth > 2: continue
                indent = "  " * depth
                tree_lines.append(f"{indent}{p.name}{'/' if p.is_dir() else ''}")
            tree_str = "\n".join(tree_lines)

            # 读取本地说明文档
            for name in ["SKILL.md", "skill.md", "SKILLS.md", "skills.md"]:
                doc_path = base_path / name
                if doc_path.exists():
                    async with aiofiles.open(doc_path, 'r', encoding='utf-8', errors='replace') as f:
                        doc_content = await f.read()
                    break
        except Exception as e:
            return f"[Error] Skill '{skill_id}' read failed: {str(e)}"

    if not doc_content and not tree_str:
        return f"[Error] Could not find skill details for '{skill_id}'."

    res = f"--- Skill Details: {skill_id} ---\n"
    res += f"\n📂 **Folder Structure:**\n```\n{tree_str}\n```\n"
    res += f"\n📖 **Documentation ({skill_rel_path}):**\n\n{doc_content or '(No SKILL.md found)'}"
    return res

async def read_skill_tool(skill_id: str) -> str:
    """[Docker] 读取特定技能的完整文档和文件树"""
    cwd = await _get_current_cwd()
    return await read_skill_tool_logic(cwd, skill_id, is_docker=True)

async def read_skill_tool_local(skill_id: str) -> str:
    """[Local] 读取特定技能的完整文档和文件树"""
    cwd = await _get_current_cwd()
    return await read_skill_tool_logic(cwd, skill_id, is_docker=False)

COMMON_BASH_DESC = (
    "Execute commands. Guidance: \n"
    "1. For long-running tasks (servers, watchers, large downloads), set 'background': true.\n"
    "2. For medium tasks, adjust 'timeout' (1-3600s, default 600s).\n"
    "3. If 'background' is true, wait a few seconds for initialization before checking logs/status; do not poll rapidly."
)

# ==================== 工具注册表 (完整) ====================

TOOLS_REGISTRY = {
    # --- 只读 ---
    "list_files": {
        "type": "function", "function": {
            "name": "list_files_tool", 
            "description": "List files in docker workspace.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to list files in (from workspace root)."
                    }, 
                    "show_all": {"type": "boolean", "default": True}
                }, 
                "required": ["path"]
            }
        }
    },
    "read_file": {
        "type": "function", "function": {
            "name": "read_file_tool", 
            "description": "Read file content.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file (from workspace root)."
                    }
                }, 
                "required": ["path"]
            }
        }
    },
    "read_file_range": {
        "type": "function", "function": {
            "name": "read_file_range_tool", 
            "description": "Read a specific range of lines from a file. Useful for large files after grepping.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"}
                }, 
                "required": ["path", "start_line", "end_line"]
            }
        }
    },
    "tail_file": {
        "type": "function", "function": {
            "name": "tail_file_tool", 
            "description": "Read the last N lines of a file. Useful for reading logs.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "lines": {"type": "integer", "default": 100, "description": "Number of lines to read from the end"}
                }, 
                "required": ["path"]
            }
        }
    },
    "search_files": {
        "type": "function", "function": {
            "name": "search_files_tool", 
            "description": "Grep search.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "pattern": {"type": "string"}, 
                    "path": {
                        "type": "string",
                        "description": "Relative path to directory to search in (from workspace root)."
                    }
                }, 
                "required": ["pattern"]
            }
        }
    },
    "glob_files": {
        "type": "function", "function": {
            "name": "glob_files_tool", 
            "description": "Recursive glob.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (relative to workspace root)."
                    }, 
                    "exclude": {"type": "string"}
                }, 
                "required": ["pattern"]
            }
        }
    },
    "read_skill": {
        "type": "function", "function": {
            "name": "read_skill_tool", 
            "description": "Read full documentation and file tree for a project-specific skill from .agent/skills/.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "skill_id": {"type": "string"}
                }, 
                "required": ["skill_id"]
            }
        }
    },
    # --- 编辑 ---
    "edit_file": {
        "type": "function", "function": {
            "name": "edit_file_tool", 
            "description": "Overwrite file.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file (from workspace root)."
                    }, 
                    "content": {"type": "string"}
                }, 
                "required": ["path", "content"]
            }
        }
    },
    "edit_file_patch": {
        "type": "function", "function": {
            "name": "edit_file_patch_tool", 
            "description": "Precise replacement.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file (from workspace root)."
                    }, 
                    "old_string": {"type": "string"}, 
                    "new_string": {"type": "string"}
                }, 
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    # --- 任务 ---
    "todo_write": {
        "type": "function",
        "function": {
            "name": "todo_write_tool",
            "description": "[Docker] 待办任务管理工具。用于在 Docker 沙箱环境中管理任务列表，支持创建、查看、完成、编辑、删除等操作。所有任务持久化存储在容器的 /workspace/.agent/ai_todos.json 文件中。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "complete", "toggle", "update", "delete"],
                        "description": "操作类型：create(创建), list(查看所有), complete(标记完成-幂等安全), toggle(切换状态-会反向), update(编辑详情), delete(删除)"
                    },
                    "id": {
                        "type": "string",
                        "description": "任务唯一标识。create时可选（自动生成），其他操作（complete/toggle/update/delete）时必需"
                    },
                    "content": {
                        "type": "string",
                        "description": "任务内容描述。create时必需，update时可选"
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "优先级：high(高), medium(中/默认), low(低)。create/update时可选"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "done"],
                        "description": "【仅用于update】强制设置任务状态：pending(未完成), done(已完成)。注意：标记完成建议使用complete动作而非status参数"
                    }
                },
                "required": ["action"]
            }
        }
    },
    # --- 基础设施 ---
    "bash": {
        "type": "function", "function": {
            "name": "docker_sandbox", 
            "description": f"[Docker] {COMMON_BASH_DESC}",
            "parameters": {
                "type": "object", 
                "properties": {
                    "command": {"type": "string"}, 
                    "background": {"type": "boolean", "description": "Run non-blocking. Returns PID."},
                    "timeout": {
                        "type": "integer", 
                        "default": 60, 
                        "description": "Max execution time in seconds (1-3600). Default 60."
                    }
                }, 
                "required": ["command"]
            }
        }
    },
    "list_processes": {
        "type": "function",
        "function": {
            "name": "list_processes_tool",
            "description": "List all running background processes (both Docker containers and local processes).",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    "get_process_logs": {
        "type": "function",
        "function": {
            "name": "get_process_logs_tool",
            "description": "Retrieve logs for a specific background process using its PID or Container ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "string",
                        "description": "The process ID or container ID to fetch logs for."
                    }
                },
                "required": ["pid"]
            }
        }
    },
    "kill_process": {
        "type": "function",
        "function": {
            "name": "kill_process_tool",
            "description": "Terminate a background process or stop a Docker container using its PID or ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "string",
                        "description": "The process ID or container ID to terminate."
                    }
                },
                "required": ["pid"]
            }
        }
    },
    "manage_ports": {
        "type": "function", "function": {
            "name": "docker_manage_ports_tool", 
            "description": "Forward Docker ports to localhost.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "action": {"type": "string", "enum": ["forward", "stop", "list"]},
                    "container_port": {"type": "integer"},
                    "host_port": {"type": "integer"}
                }, 
                "required": ["action"]
            }
        }
    }
}

LOCAL_TOOLS_REGISTRY = {
    # --- 只读 ---
    "list_files_local": {
        "type": "function", "function": {
            "name": "list_files_tool_local", 
            "description": "List local files.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to list files in (from current working directory)."
                    }, 
                    "show_all": {"type": "boolean","default": True}
                }, 
                "required": ["path"]
            }
        }
    },
    "read_file_local": {
        "type": "function", "function": {
            "name": "read_file_tool_local", 
            "description": "Read local file.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file (from current working directory)."
                    }
                }, 
                "required": ["path"]
            }
        }
    },
    "read_file_range_local": {
        "type": "function", "function": {
            "name": "read_file_range_tool_local", 
            "description": "Read a specific range of lines from a local file. Useful for large files after grepping.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "start_line": {"type": "integer", "description": "The line number to start reading from."},
                    "end_line": {"type": "integer", "description": "The line number to stop reading at."}
                }, 
                "required": ["path", "start_line", "end_line"]
            }
        }
    },
    "tail_file_local": {
        "type": "function", "function": {
            "name": "tail_file_tool_local", 
            "description": "Read the last N lines of a local file. Useful for reading logs.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "lines": {"type": "integer", "default": 100}
                }, 
                "required": ["path"]
            }
        }
    },
    "search_files_local": {
         "type": "function", "function": {
            "name": "search_files_tool_local", 
            "description": "Search local files.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Relative directory to search in (default .)"}
                },
                "required": ["pattern"]
            }
        }
    },
    "glob_files_local": {
         "type": "function", "function": {
            "name": "glob_files_tool_local", 
            "description": "Glob local files.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (relative to current working directory)."
                    },
                    "exclude": {"type": "string", "description": "Comma-separated patterns to exclude"}
                }, 
                "required": ["pattern"]
            }
        }
    },
    "read_skill_local": {
        "type": "function", "function": {
            "name": "read_skill_tool_local", 
            "description": "Read full documentation and file tree for a project-specific skill from .agent/skills/ (Local).",
            "parameters": {
                "type": "object", 
                "properties": {
                    "skill_id": {"type": "string", "description": "The ID of the skill to read."}
                }, 
                "required": ["skill_id"]
            }
        }
    },
    # --- 编辑 ---
    "edit_file_local": {
        "type": "function", "function": {
            "name": "edit_file_tool_local", 
            "description": "Write local file.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file (from current working directory)."
                    }, 
                    "content": {"type": "string", "description": "Full file content"}
                }, 
                "required": ["path", "content"]
            }
        }
    },
    "edit_file_patch_local": {
        "type": "function", "function": {
            "name": "edit_file_patch_tool_local", 
            "description": "Patch local file.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file (from current working directory)."
                    }, 
                    "old_string": {"type": "string", "description": "The string to be replaced."}, 
                    "new_string": {"type": "string", "description": "The replacement string."}
                }, 
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    "todo_write_local": {
        "type": "function",
        "function": {
            "name": "todo_write_tool_local",
            "description": "本地待办任务管理工具。用于管理项目中的任务列表，包括创建、查看、完成、编辑、删除等操作。所有任务持久化存储在项目根目录的 .agent/ai_todos.json 文件中。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "complete", "toggle", "update", "delete"],
                        "description": "操作类型：create(创建), list(查看所有), complete(标记完成-幂等), toggle(切换状态-会反向), update(编辑详情), delete(删除)"
                    },
                    "id": {
                        "type": "string",
                        "description": "任务唯一标识。create时可选（自动生成），其他操作（complete/toggle/update/delete）时必需"
                    },
                    "content": {
                        "type": "string",
                        "description": "任务内容描述。create时必需，update时可选"
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "优先级：high(高), medium(中/默认), low(低)。create/update时可选"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "done"],
                        "description": "【仅用于update】强制设置任务状态：pending(未完成), done(已完成)。注意：标记完成建议使用complete动作而非status参数"
                    }
                },
                "required": ["action"]
            }
        }
    },
    # --- 基础设施 ---
    "bash_local": {
        "type": "function", "function": {
            "name": "shell_tool_local", 
            "description": f"[Local] {COMMON_BASH_DESC}",
            "parameters": {
                "type": "object", 
                "properties": {
                    "command": {"type": "string"},
                    "background": {"type": "boolean", "description": "Run in background."},
                    "timeout": {
                        "type": "integer", 
                        "default": 60, 
                        "description": "Max execution time in seconds (1-3600). Default 60."
                    }
                }, 
                "required": ["command"]
            }
        }
    },
    "list_processes": {
        "type": "function",
        "function": {
            "name": "list_processes_tool",
            "description": "List all running background processes (both Docker containers and local processes).",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    "get_process_logs": {
        "type": "function",
        "function": {
            "name": "get_process_logs_tool",
            "description": "Retrieve logs for a specific background process using its PID or Container ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "string",
                        "description": "The process ID or container ID to fetch logs for."
                    }
                },
                "required": ["pid"]
            }
        }
    },
    "kill_process": {
        "type": "function",
        "function": {
            "name": "kill_process_tool",
            "description": "Terminate a background process or stop a Docker container using its PID or ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "string",
                        "description": "The process ID or container ID to terminate."
                    }
                },
                "required": ["pid"]
            }
        }
    },
    "local_net_tool": {
        "type": "function", "function": {
            "name": "local_net_tool", 
            "description": "Check local ports.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "action": {"type": "string", "enum": ["check", "scan"]},
                    "port": {"type": "integer"}
                }, 
                "required": ["action"]
            }
        }
    }
}

def get_tools_for_mode(mode: str) -> list:
    """获取 Docker 环境工具集"""
    # 基础只读
    read = [TOOLS_REGISTRY["list_files"], 
            TOOLS_REGISTRY["read_file"], 
            TOOLS_REGISTRY["read_file_range"],
            TOOLS_REGISTRY["tail_file"],     
            TOOLS_REGISTRY["search_files"], 
            TOOLS_REGISTRY["glob_files"],
            TOOLS_REGISTRY["read_skill"]
            ]
    # 编辑
    edit = [TOOLS_REGISTRY["edit_file"], TOOLS_REGISTRY["edit_file_patch"], TOOLS_REGISTRY["todo_write"]]
    # 基础设施 (执行/进程/端口)
    infra = [TOOLS_REGISTRY["bash"], TOOLS_REGISTRY["list_processes"], TOOLS_REGISTRY["get_process_logs"], TOOLS_REGISTRY["kill_process"], TOOLS_REGISTRY["manage_ports"]]
    
    if mode == "default": return read
    if mode == "auto-approve": return read + edit + [TOOLS_REGISTRY["list_processes"], TOOLS_REGISTRY["get_process_logs"], TOOLS_REGISTRY["kill_process"]]
    if mode == "yolo": return read + edit + infra
    return read

def get_local_tools_for_mode(mode: str) -> list:
    """获取 Local 环境工具集"""
    read = [
        LOCAL_TOOLS_REGISTRY["list_files_local"], 
        LOCAL_TOOLS_REGISTRY["read_file_local"], 
        LOCAL_TOOLS_REGISTRY["read_file_range_local"],
        LOCAL_TOOLS_REGISTRY["tail_file_local"],    
        LOCAL_TOOLS_REGISTRY["search_files_local"], 
        LOCAL_TOOLS_REGISTRY["glob_files_local"],
        LOCAL_TOOLS_REGISTRY["read_skill_local"] 
    ]
    edit = [LOCAL_TOOLS_REGISTRY["edit_file_local"], LOCAL_TOOLS_REGISTRY["edit_file_patch_local"], LOCAL_TOOLS_REGISTRY["todo_write_local"]]
    infra = [
        LOCAL_TOOLS_REGISTRY["bash_local"], 
        LOCAL_TOOLS_REGISTRY["list_processes"], LOCAL_TOOLS_REGISTRY["get_process_logs"], LOCAL_TOOLS_REGISTRY["kill_process"],
        LOCAL_TOOLS_REGISTRY["local_net_tool"]
    ]
    
    if mode == "default": return read
    if mode == "auto-approve": return read + edit + [LOCAL_TOOLS_REGISTRY["list_processes"], LOCAL_TOOLS_REGISTRY["get_process_logs"], LOCAL_TOOLS_REGISTRY["kill_process"], LOCAL_TOOLS_REGISTRY["local_net_tool"]]
    if mode == "yolo": return read + edit + infra
    return read