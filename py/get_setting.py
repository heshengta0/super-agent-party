import io
import json
import logging
import os
import shutil
import sys
import time
import asyncio
import aiosqlite
from pathlib import Path
from appdirs import user_data_dir

# ----------------- 1. 基础环境检测 (极速版) -----------------
APP_NAME = "Super-Agent-Party"
HOST = None
PORT = None

IS_DOCKER = os.environ.get("IS_DOCKER", "").lower() in ("1", "true")

def in_docker():
    return IS_DOCKER

def get_base_path():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    else:
        return os.path.abspath(".")

base_path = get_base_path()

# ----------------- 2. 路径定义 -----------------
if IS_DOCKER:
    USER_DATA_DIR = '/app/data'
else:
    USER_DATA_DIR = user_data_dir(APP_NAME, roaming=True)

# --- 核心目录 ---
LOG_DIR = os.path.join(USER_DATA_DIR, 'logs')
MEMORY_CACHE_DIR = os.path.join(USER_DATA_DIR, 'memory_cache')
UPLOAD_FILES_DIR = os.path.join(USER_DATA_DIR, 'uploaded_files')
TOOL_TEMP_DIR = os.path.join(USER_DATA_DIR, 'tool_temp')
AGENT_DIR = os.path.join(USER_DATA_DIR, 'agents')
KB_DIR = os.path.join(USER_DATA_DIR, 'kb')
EXT_DIR = os.path.join(USER_DATA_DIR, "ext")
DEFAULT_ASR_DIR = os.path.join(USER_DATA_DIR, 'asr')
DEFAULT_TTS_DIR = os.path.join(USER_DATA_DIR, 'tts')
DEFAULT_EBD_DIR = os.path.join(USER_DATA_DIR, 'ebd')

# --- 跨平台全局Skills路径 ---
def get_global_skills_dir():
    """
    获取标准的全局Agent Skills目录，支持跨平台
    标准路径: ~/.agents/skills (macOS/Linux) 或 %USERPROFILE%\.agents\skills (Windows)
    """
    home_dir = Path.home()
    
    # 检查是否在Docker环境中
    if IS_DOCKER:
        # Docker环境中使用/app/.agents/skills
        docker_skills_dir = Path('/app/.agents/skills')
        docker_skills_dir.mkdir(parents=True, exist_ok=True)
        return str(docker_skills_dir)
    
    # 标准全局路径
    global_skills_dir = home_dir / '.agents' / 'skills'
    
    # 确保目录存在
    global_skills_dir.mkdir(parents=True, exist_ok=True)
    
    return str(global_skills_dir)

# 使用标准的全局skills路径
SKILLS_DIR = get_global_skills_dir()


# --- 配置文件 ---
SETTINGS_FILE = os.path.join(USER_DATA_DIR, 'settings.json')
CONFIG_BASE_PATH = os.path.join(base_path, 'config')
SETTINGS_TEMPLATE_FILE = os.path.join(CONFIG_BASE_PATH, 'settings_template.json')
BLOCKLIST_FILE = os.path.join(CONFIG_BASE_PATH, 'blocklist.json')

# --- 静态资源 ---
DEFAULT_VRM_DIR = os.path.join(base_path, 'vrm')
STATIC_DIR = os.path.join(base_path, "static")

# --- 数据库 ---
DATABASE_PATH = os.path.join(USER_DATA_DIR, 'super_agent_party.db')
COVS_PATH = os.path.join(USER_DATA_DIR, "conversations.db")

# 批量创建目录
dirs_to_create = [
    USER_DATA_DIR, LOG_DIR, MEMORY_CACHE_DIR, UPLOAD_FILES_DIR, 
    TOOL_TEMP_DIR, AGENT_DIR, KB_DIR, EXT_DIR, 
    DEFAULT_ASR_DIR, DEFAULT_TTS_DIR, DEFAULT_EBD_DIR, CONFIG_BASE_PATH, SKILLS_DIR
]
for d in set(dirs_to_create):
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass

# ----------------- 3. 关键修复：恢复全局 BLOCKLIST 变量 -----------------
# 兼容 py/load_files.py 的导入需求
# 虽然有一点点 I/O，但为了保证不报错，这里必须直接执行
blocklist_data = []
if os.path.exists(BLOCKLIST_FILE):
    try:
        with open(BLOCKLIST_FILE, 'r', encoding='utf-8') as f:
            blocklist_data = json.load(f)
    except Exception:
        pass
BLOCKLIST = set(blocklist_data)

# ----------------- 4. 工具函数 -----------------

_cached_default_settings = None
_db_init_done = False
_covs_db_init_done = False

def get_blocklist():
    """保留这个函数供未来使用"""
    return BLOCKLIST

def configure_host_port(host, port):
    global HOST, PORT
    HOST = host
    PORT = port

def get_host():
    return HOST or "127.0.0.1"

def get_port():
    return PORT or 3456

def get_default_settings_sync():
    global _cached_default_settings
    if _cached_default_settings is not None:
        return _cached_default_settings
    
    if os.path.exists(SETTINGS_TEMPLATE_FILE):
        try:
            with open(SETTINGS_TEMPLATE_FILE, 'r', encoding='utf-8') as f:
                _cached_default_settings = json.load(f)
        except Exception:
            _cached_default_settings = {}
    else:
        _cached_default_settings = {}
    return _cached_default_settings

# ----------------- Agent Skills 初始化 -----------------

async def _copy_default_skills():
    """
    将项目根目录的 skills/ 复制到 USER_DATA_DIR/skills/。
    核心逻辑：若目标子目录已存在，则跳过该目录；不覆盖用户已有文件。
    """
    # 源目录：项目根目录下的 skills
    src_skills_root = os.path.join(base_path, 'skills')
    # 目标目录：用户数据目录下的 skills
    dst_skills_root = SKILLS_DIR  # 你在路径定义中已配置

    # 如果源目录根本不存在，说明这个版本没带默认技能，直接跳过
    if not os.path.isdir(src_skills_root):
        logging.info("[Skills] 项目根目录无 skills/ 文件夹，跳过初始化复制。")
        return

    # 确保目标根目录存在（你在 dirs_to_create 已包含，这里双重保障）
    os.makedirs(dst_skills_root, exist_ok=True)

    # 遍历源目录下的每一项（一级子目录/文件）
    try:
        for item_name in os.listdir(src_skills_root):
            src_path = os.path.join(src_skills_root, item_name)
            dst_path = os.path.join(dst_skills_root, item_name)

            # 仅处理目录 —— Skill 的根必须是文件夹
            if os.path.isdir(src_path):
                # 核心判断：如果目标目录已存在，完全跳过该 Skill 的复制
                if os.path.exists(dst_path):
                    logging.debug(f"[Skills] 目标技能已存在，跳过: {item_name}")
                    continue
                
                # 不存在则完整复制整个 Skill 文件夹
                # 使用 shutil.copytree，且不覆盖（因为已判断不存在）
                import shutil
                shutil.copytree(src_path, dst_path)
                logging.info(f"[Skills] 已安装默认技能: {item_name}")
            else:
                # 源根目录下的孤立文件（非标准 Skill 结构），根据你的策略可忽略或复制
                # 标准 Agent Skills 只认文件夹，这里建议忽略
                logging.debug(f"[Skills] 忽略非文件夹项: {item_name}")
    except Exception as e:
        logging.error(f"[Skills] 复制默认技能时发生错误: {e}", exc_info=True)

# ----------------- 5. 初始化逻辑 -----------------

async def init_db():
    global _db_init_done
    if _db_init_done: return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            )
        ''')
        await db.commit()
    _db_init_done = True

async def init_covs_db():
    global _covs_db_init_done
    if _covs_db_init_done: return
    
    Path(USER_DATA_DIR).mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(COVS_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS group_memory (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                source_chat_id TEXT NOT NULL,
                source_message_id TEXT,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5,
                status TEXT NOT NULL DEFAULT 'active',
                version INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_used_at INTEGER,
                metadata_json TEXT
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_group_memory_group_status ON group_memory(group_id, status)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_group_memory_source_chat ON group_memory(source_chat_id)')
        await db.commit()
    _covs_db_init_done = True

# ----------------- 6. 业务功能函数 -----------------

async def clean_temp_files_task():
    try:
        await asyncio.to_thread(_clean_temp_files_sync)
    except Exception:
        pass

def _clean_temp_files_sync():
    if not os.path.exists(TOOL_TEMP_DIR): return
    threshold = time.time() - 7 * 24 * 60 * 60
    for filename in os.listdir(TOOL_TEMP_DIR):
        file_path = os.path.join(TOOL_TEMP_DIR, filename)
        try:
            if os.path.isfile(file_path):
                if os.path.getmtime(file_path) < threshold:
                    os.remove(file_path)
        except Exception:
            pass

def convert_to_opus_simple(audio_data):
    try:
        from pydub import AudioSegment
        import imageio_ffmpeg
        
        if not getattr(AudioSegment, 'converter_configured', False):
            try:
                ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
                AudioSegment.converter = ffmpeg_path
                AudioSegment.converter_configured = True
            except Exception:
                logging.warning("imageio-ffmpeg execution failed")

        audio = None
        # 1. Container format
        try:
            audio_io = io.BytesIO(audio_data)
            audio = AudioSegment.from_file(audio_io)
        except Exception:
            pass
            
        # 2. Raw PCM
        if audio is None:
            try:
                audio = AudioSegment(
                    data=audio_data,
                    sample_width=2,
                    frame_rate=24000,
                    channels=1
                )
            except Exception as e:
                logging.error(f"Raw PCM read failed: {e}")
                return audio_data, False

        # 3. Export Opus
        audio = audio.set_frame_rate(16000).set_channels(1)
        out_io = io.BytesIO()
        audio.export(
            out_io,
            format="opus",
            codec="libopus",
            parameters=["-b:a", "16k", "-application", "voip"]
        )
        return out_io.getvalue(), True
    except ImportError:
        logging.error("pydub/ffmpeg not installed")
        return _wrap_pcm_to_wav(audio_data), False
    except Exception as e:
        logging.error(f"Opus conversion failed: {e}")
        return _wrap_pcm_to_wav(audio_data), False

def convert_to_amr_simple(audio_data: bytes) -> bytes:
    """
    将音频转换为企业微信 AMR 格式
    """
    try:
        from pydub import AudioSegment
        import io, os, subprocess, tempfile, shutil

        # 1. 自动定位 ffmpeg
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            logging.error("未找到 ffmpeg，请确保已安装并加入环境变量")
            return None

        # 2. 读取音频并标准化 (8000Hz, Mono)
        audio = AudioSegment.from_file(io.BytesIO(audio_data))
        audio = audio.set_frame_rate(8000).set_channels(1)
        
        # 3. 创建临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            audio.export(tmp.name, format="wav")
            wav_name = tmp.name
        amr_name = wav_name.replace(".wav", ".amr")
        
        try:
            # 4. 执行转换并捕获错误输出
            cmd = [
                ffmpeg_path, "-y", "-i", wav_name, 
                "-ar", "8000", "-ab", "12.2k", "-ac", "1", 
                "-c:a", "libopencore_amrnb", amr_name
            ]
            process = subprocess.run(cmd, capture_output=True, text=True)
            
            if process.returncode != 0:
                # 【核心日志】这里会告诉你为什么 exit 8
                logging.error(f"FFmpeg 转换失败 (Code {process.returncode})")
                logging.error(f"FFmpeg 错误详情: {process.stderr}")
                return None
            
            with open(amr_name, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(wav_name): os.remove(wav_name)
            if os.path.exists(amr_name): os.remove(amr_name)

    except Exception as e:
        logging.error(f"AMR 转换流程异常: {e}")
        return None


def _wrap_pcm_to_wav(pcm_data):
    try:
        import wave
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(pcm_data)
        return wav_io.getvalue()
    except Exception:
        return pcm_data

# ----------------- 7. 配置读写 -----------------

async def load_settings():
    await init_db()
    defaults = get_default_settings_sync().copy()
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute('SELECT data FROM settings WHERE id = 1') as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    user_settings = json.loads(row[0])
                except Exception:
                    user_settings = {}
                
                # Merge logic
                has_changes = [False]
                def merge_defaults(default_dict, target_dict):
                    for key, value in default_dict.items():
                        if key not in target_dict:
                            target_dict[key] = value
                            has_changes[0] = True
                        elif isinstance(value, dict) and isinstance(target_dict.get(key), dict):
                            merge_defaults(value, target_dict[key])
                
                merge_defaults(defaults, user_settings)
                if has_changes[0]:
                    asyncio.create_task(save_settings(user_settings))
                return user_settings
            else:
                if IS_DOCKER:
                    defaults["isdocker"] = True
                await save_settings(defaults)
                return defaults

async def save_settings(settings):
    data = json.dumps(settings, ensure_ascii=False, indent=2)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO settings (id, data) VALUES (1, ?)', (data,))
        await db.commit()

async def load_covs():
    try:
        await init_covs_db()
        async with aiosqlite.connect(COVS_PATH) as db:
            async with db.execute('SELECT data FROM settings WHERE id = 1') as cursor:
                row = await cursor.fetchone()
                return json.loads(row[0]) if row else {"conversations": []}
    except Exception:
        return {"conversations": []}

async def save_covs(settings):
    data = json.dumps(settings, ensure_ascii=False, indent=2)
    async with aiosqlite.connect(COVS_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO settings (id, data) VALUES (1, ?)', (data,))
        await db.commit()
