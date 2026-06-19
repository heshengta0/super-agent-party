import shutil
import tempfile
import json
import os
import httpx
import yaml
import re
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from py.get_setting import SKILLS_DIR

# 配置日志
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

# ==================== 数据模型 ====================

class Skill(BaseModel):
    id: str
    name: str
    description: str = "暂无描述"
    version: str = "1.0.0"
    author: str = "未知"
    files: List[str] = []

class SkillsResponse(BaseModel):
    skills: List[Skill]

class GitHubSkillInstallRequest(BaseModel):
    url: str = Field(..., description="GitHub URL，支持仓库或具体路径")

class SkillSyncRequest(BaseModel):
    skill_id: str
    project_path: str
    action: str  # "install" 或 "remove"

class InstallResponse(BaseModel):
    status: str
    message: str
    installed_ids: Optional[List[str]] = None
    error: Optional[str] = None

# ==================== 工具函数 ====================

def robust_rmtree(path: Path):
    """强制删除目录，处理 Windows 权限或被占用问题"""
    if path.exists():
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            print(f"删除目录 {path} 失败: {e}")

def parse_github_url(url: str):
    """
    解析 GitHub URL，支持深度链接。
    例如: https://github.com/anthropics/skills/tree/main/skills/docx 
    返回: (zip_download_url, branch, subpath)
    """
    url = url.strip().rstrip('/').removesuffix('.git')
    # 正则匹配 owner, repo 和可能的 tree/branch/path
    pattern = r"github\.com/([^/]+)/([^/]+)(?:/(?:tree|blob)/([^/]+)/(.*))?"
    match = re.search(pattern, url)
    
    if not match:
        raise ValueError("无效的 GitHub URL")
        
    owner, repo, branch, subpath = match.groups()
    
    # 🎯 如果URL中没有指定分支，动态获取默认分支
    if not branch:
        import httpx
        try:
            api_url = f"https://api.github.com/repos/{owner}/{repo}"
            with httpx.Client(timeout=5.0) as client:
                response = client.get(api_url)
                if response.status_code == 200:
                    repo_info = response.json()
                    branch = repo_info.get("default_branch", "main")
                else:
                    branch = "main"  # 降级策略
        except Exception:
            branch = "main"  # API调用失败时的降级策略
    else:
        branch = branch
    
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    return zip_url, branch, subpath

async def download_zip(url: str, dest: Path):
    """异步下载文件"""
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise Exception(f"下载失败: Status {resp.status_code}")
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)

def get_skill_metadata(skill_dir: Path, skill_id: str) -> Skill:
    """
    解析技能元数据 (SKILL.md 的 YAML Frontmatter)
    
    Args:
        skill_dir: 技能目录路径
        skill_id: 技能唯一标识
    
    Returns:
        Skill: 技能元数据对象
    
    Raises:
        ValueError: 当 skill_dir 无效时
    """
    
    # 1. 防御性参数校验
    if not isinstance(skill_dir, Path):
        try:
            skill_dir = Path(skill_dir)
        except Exception as e:
            raise ValueError(f"无效的 skill_dir 路径: {skill_dir}, 错误: {e}")
    
    if not isinstance(skill_id, str) or not skill_id.strip():
        skill_id = skill_dir.name if isinstance(skill_dir, Path) else "unknown"
        logger.warning(f"提供了无效的 skill_id，使用目录名替代: {skill_id}")
    
    skill_id = skill_id.strip()
    
    # 2. 目录存在性检查
    if not skill_dir.exists():
        logger.error(f"技能目录不存在: {skill_dir}")
        return _create_default_skill(skill_id, skill_dir, [])
    
    if not skill_dir.is_dir():
        logger.error(f"skill_dir 不是目录: {skill_dir}")
        return _create_default_skill(skill_id, skill_dir, [])
    
    # 3. 查找元数据文件（不区分大小写，支持更多变体）
    target_files = [
        "SKILL.md", "skill.md", "SKILLS.md", "skills.md",
        "Skill.md", "Skill.MD", "skill.MD", "SKILL.MD"
    ]
    
    meta_file: Optional[Path] = None
    try:
        # 使用生成器避免提前实例化所有路径
        meta_file = next(
            (skill_dir / f for f in target_files if (skill_dir / f).exists() and (skill_dir / f).is_file()),
            None
        )
    except PermissionError as e:
        logger.error(f"无权限访问目录 {skill_dir}: {e}")
        return _create_default_skill(skill_id, skill_dir, [])
    except OSError as e:
        logger.error(f"访问目录 {skill_dir} 时发生系统错误: {e}")
        return _create_default_skill(skill_id, skill_dir, [])
    
    # 4. 解析 YAML Frontmatter
    meta: dict[str, Any] = {}
    
    if meta_file is not None:
        try:
            # 检查文件大小，防止读取超大文件导致内存问题
            file_size = meta_file.stat().st_size
            if file_size > 1024 * 1024:  # 1MB 限制
                logger.warning(f"元数据文件过大 ({file_size} bytes): {meta_file}")
            else:
                # 尝试多种编码
                content = _read_file_with_encoding(meta_file)
                
                if content is not None:
                    # 提取 --- 之间的 YAML（更宽松的匹配）
                    match = re.search(
                        r'^\s*---\s*[\r\n]+(.*?)[\r\n]+---\s*',
                        content,
                        re.DOTALL | re.MULTILINE
                    )
                    
                    if match:
                        yaml_text = match.group(1).strip()
                        if yaml_text:
                            try:
                                parsed_meta = yaml.safe_load(yaml_text)
                                # 严格类型检查
                                if isinstance(parsed_meta, dict):
                                    meta = parsed_meta
                                elif parsed_meta is None:
                                    logger.debug(f"{meta_file.name} 中的 YAML 解析为空")
                                    meta = {}
                                else:
                                    logger.warning(
                                        f"{meta_file.name} 中的 YAML 不是字典类型，"
                                        f"而是 {type(parsed_meta).__name__}，忽略"
                                    )
                                    meta = {}
                            except yaml.YAMLError as e:
                                logger.warning(f"YAML 解析错误 in {meta_file.name}: {e}")
                                meta = {}
                            except Exception as e:
                                logger.error(f"解析 YAML 时发生未知错误: {e}")
                                meta = {}
                    else:
                        logger.debug(f"{meta_file.name} 中没有找到 YAML Frontmatter")
                        
        except PermissionError as e:
            logger.error(f"无权限读取文件 {meta_file}: {e}")
        except OSError as e:
            logger.error(f"读取文件 {meta_file} 时发生系统错误: {e}")
        except Exception as e:
            logger.exception(f"解析元数据文件时发生未预期错误: {e}")
    
    # 5. 安全地获取文件列表
    file_list: List[str] = []
    try:
        file_list = [
            f.name for f in skill_dir.iterdir() 
            if f.is_file() and not f.name.startswith('.') and not f.name.startswith('~')
        ]
        file_list.sort()
    except PermissionError as e:
        logger.error(f"无权限列出目录 {skill_dir} 内容: {e}")
    except OSError as e:
        logger.error(f"列出目录 {skill_dir} 内容时发生错误: {e}")
    except Exception as e:
        logger.exception(f"获取文件列表时发生未预期错误: {e}")
    
    # 6. 安全地提取元数据字段
    return _build_skill_from_meta(skill_id, skill_dir, meta, file_list)


def _read_file_with_encoding(file_path: Path, max_size: int = 1024 * 1024) -> Optional[str]:
    """
    尝试使用多种编码读取文件
    
    Args:
        file_path: 文件路径
        max_size: 最大读取字节数
    
    Returns:
        文件内容或 None
    """
    encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1', 'cp1252']
    
    for encoding in encodings:
        try:
            content = file_path.read_text(encoding=encoding, errors='strict')
            if encoding in ['latin-1', 'cp1252'] and '\ufffd' in content:
                continue
            return content
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.debug(f"使用 {encoding} 读取失败: {e}")
            continue
    
    try:
        return file_path.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        logger.error(f"所有编码尝试均失败: {e}")
        return None


def _extract_nested_value(meta: dict, keys: List[str], default: Any) -> Any:
    """
    安全地从嵌套字典中提取值
    """
    for key in keys:
        if not isinstance(key, str):
            continue
        try:
            if key in meta:
                value = meta[key]
                if isinstance(value, str):
                    value = value.strip()
                if value is not None and value != "":
                    return value
        except Exception:
            continue
    
    for key in keys:
        if "." in key:
            parts = key.split(".")
            current = meta
            try:
                for part in parts:
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        break
                else:
                    if current is not None and current != "":
                        return current
            except Exception:
                continue
    
    return default


def _sanitize_version(version: Any) -> str:
    if version is None:
        return "1.0.0"
    if isinstance(version, (int, float)):
        return str(version)
    if isinstance(version, str):
        version = version.strip()
        if re.match(r'^[\d]+(\.[\d]+)*([\-\+.]?[a-zA-Z0-9]+)*$', version):
            return version
        cleaned = re.sub(r'[^\d.\-+a-zA-Z]', '', version)
        if cleaned:
            return cleaned
    return "1.0.0"


def _sanitize_author(author: Any) -> str:
    if author is None:
        return "Local"
    if isinstance(author, str):
        author = author.strip()
        if author:
            return author[:100]
    if isinstance(author, (list, tuple)):
        if author and isinstance(author[0], str):
            return author[0].strip()[:100]
    return "Local"


def _build_skill_from_meta(
    skill_id: str, 
    skill_dir: Path, 
    meta: dict, 
    file_list: List[str]
) -> Skill:
    name = _extract_nested_value(meta, ["name", "title", "id"], skill_id)
    if not isinstance(name, str) or not name.strip():
        name = skill_id
    
    description = _extract_nested_value(
        meta, 
        ["description", "desc", "summary", "about"], 
        "Agent 智能体技能"
    )
    if not isinstance(description, str):
        description = str(description) if description is not None else "Agent 智能体技能"
    description = description[:500]
    
    version_raw = _extract_nested_value(meta, ["version", "ver"], "1.0.0")
    version = _sanitize_version(version_raw)
    
    author_raw = (
        meta.get("author") 
        or meta.get("authors")
        or meta.get("metadata", {}).get("author") 
        if isinstance(meta.get("metadata"), dict) 
        else None
    )
    author = _sanitize_author(author_raw)
    
    max_files = 8
    files = file_list[:max_files]
    
    return Skill(
        id=skill_id,
        name=name,
        description=description,
        version=version,
        author=author,
        files=files
    )


def _create_default_skill(
    skill_id: str, 
    skill_dir: Path, 
    file_list: List[str]
) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        description="Agent 智能体技能（元数据解析失败）",
        version="1.0.0",
        author="Local",
        files=file_list[:8]
    )

# ==================== 核心安装逻辑 ====================

def _install_skills_from_directory(source_dir: Path) -> List[str]:
    """
    智能安装处理器（增强递归扫描）：
    1. 如果 source_dir 包含 SKILL.md，视为单技能安装。
    2. 若存在 skills/ 子目录，优先从其中递归查找技能。
    3. 递归扫描所有子目录，安装所有包含 SKILL.md 的目录。
    """
    installed_ids = []
    target_files = ["SKILL.md", "skill.md", "SKILLS.md", "skills.md"]

    def is_skill_dir(d: Path):
        return any((d / f).exists() for f in target_files)

    # 1. 检查 source_dir 本身是否就是技能
    if is_skill_dir(source_dir):
        skill_id = source_dir.name
        dest_path = Path(SKILLS_DIR) / skill_id
        robust_rmtree(dest_path)
        shutil.copytree(source_dir, dest_path)
        installed_ids.append(skill_id)
        return installed_ids

    # 2. 确定递归搜索的根目录
    search_dir = source_dir
    skills_subdir = source_dir / "skills"
    if skills_subdir.exists() and skills_subdir.is_dir():
        search_dir = skills_subdir

    # 3. 递归收集所有技能目录
    found_skill_dirs = []
    def find_skill_dirs(root: Path):
        if not root.is_dir():
            return
        if is_skill_dir(root):
            found_skill_dirs.append(root)
        else:
            # 扫描子目录（忽略隐藏文件夹）
            try:
                for item in sorted(root.iterdir()):
                    if item.is_dir() and not item.name.startswith('.'):
                        find_skill_dirs(item)
            except PermissionError:
                logger.warning(f"无权限访问目录: {root}")

    find_skill_dirs(search_dir)

    # 4. 安装找到的技能，处理 ID 冲突
    used_ids = set()
    for skill_dir in found_skill_dirs:
        # 优先使用目录名作为 ID
        skill_id = skill_dir.name
        if skill_id in used_ids:
            # 使用相对于 search_dir 的路径创建唯一 ID
            try:
                rel_path = skill_dir.relative_to(search_dir)
                unique_id = "_".join(rel_path.parts)
                if unique_id in used_ids:
                    logger.warning(f"无法为冲突的技能目录生成唯一ID: {skill_dir}")
                    continue
                skill_id = unique_id
            except ValueError:
                logger.warning(f"无法计算相对路径，跳过目录: {skill_dir}")
                continue
        
        dest_path = Path(SKILLS_DIR) / skill_id
        robust_rmtree(dest_path)
        shutil.copytree(skill_dir, dest_path)
        installed_ids.append(skill_id)
        used_ids.add(skill_id)
    
    return installed_ids

async def _process_github_install(url: str) -> Dict[str, Any]:
    """
    处理 GitHub 安装：解析 -> 下载 -> 智能安装
    返回包含状态、安装ID列表或错误信息的字典
    """
    temp_dir = Path(tempfile.mkdtemp())
    try:
        zip_url, branch, subpath = parse_github_url(url)
        zip_path = temp_dir / "repo.zip"
        
        # 1. 下载
        await download_zip(zip_url, zip_path)
        
        # 2. 解压
        extract_dir = temp_dir / "extracted"
        shutil.unpack_archive(zip_path, extract_dir)
        
        # 3. 定位内容根目录 (GitHub ZIP 第一层通常是 repo-main)
        repo_root = next(extract_dir.iterdir())
        
        # 4. 如果有 subpath，则进到 subpath 里
        target_source = repo_root
        if subpath:
            potential_path = repo_root.joinpath(*subpath.split('/'))
            if potential_path.exists():
                target_source = potential_path
        
        # 5. 调用统一安装器（现在支持深度递归）
        ids = _install_skills_from_directory(target_source)
        
        if not ids:
            return {
                "success": False,
                "error": "未检测到有效的 Agent Skill 结构（缺少 SKILL.md）",
                "installed_ids": []
            }
        
        return {
            "success": True,
            "installed_ids": ids,
            "message": f"成功安装 {len(ids)} 个技能: {', '.join(ids)}"
        }

    except ValueError as e:
        return {"success": False, "error": f"URL 解析失败: {str(e)}", "installed_ids": []}
    except Exception as e:
        return {"success": False, "error": f"安装过程出错: {str(e)}", "installed_ids": []}
    finally:
        robust_rmtree(temp_dir)

# ==================== API 路由 ====================

@router.get("/list", response_model=SkillsResponse)
async def list_skills():
    """列出所有已安装的全局技能"""
    if not os.path.exists(SKILLS_DIR):
        os.makedirs(SKILLS_DIR, exist_ok=True)
        return SkillsResponse(skills=[])
    
    skills_list = []
    base = Path(SKILLS_DIR)
    if base.exists():
        for item in sorted(base.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                skills_list.append(get_skill_metadata(item, item.name))
    return SkillsResponse(skills=skills_list)

@router.get("/{skill_id}/content")
async def get_skill_content(skill_id: str):
    """前端预览：读取 SKILL.md 的全文"""
    skill_dir = Path(SKILLS_DIR) / skill_id
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail="技能不存在")
    
    target_files = ["SKILL.md", "skill.md", "SKILLS.md", "skills.md"]
    for filename in target_files:
        p = skill_dir / filename
        if p.exists():
            return {"content": p.read_text(encoding="utf-8")}
                
    raise HTTPException(status_code=404, detail="未找到元数据文件 (SKILL.md)")

@router.post("/install-from-github", response_model=InstallResponse)
async def install_skill_github(req: GitHubSkillInstallRequest):
    """
    从 GitHub 安装技能（同步执行，立即返回结果）
    支持具体路径或整个仓库
    """
    try:
        result = await _process_github_install(req.url)
        
        if result["success"]:
            return InstallResponse(
                status="success",
                message=result["message"],
                installed_ids=result["installed_ids"]
            )
        else:
            raise HTTPException(
                status_code=400, 
                detail=result["error"]
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

@router.post("/upload-zip", response_model=InstallResponse)
async def upload_skill_zip(file: UploadFile = File(...)):
    """本地 ZIP 上传，支持单技能压缩包或多技能仓库压缩包"""
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 zip 文件")

    with tempfile.TemporaryDirectory() as td:
        temp_path = Path(td)
        zip_file = temp_path / "upload.zip"
        with open(zip_file, "wb") as f:
            shutil.copyfileobj(file.file, f)
            
        extract_dir = temp_path / "extracted"
        shutil.unpack_archive(zip_file, extract_dir)
        
        # 处理可能的"包一层"目录结构
        items = [i for i in extract_dir.iterdir() if not i.name.startswith('.')]
        source = items[0] if len(items) == 1 and items[0].is_dir() else extract_dir

        installed_ids = _install_skills_from_directory(source)
        
    if not installed_ids:
        raise HTTPException(status_code=400, detail="未检测到有效的 Agent Skill 结构（缺少 SKILL.md）")
        
    return InstallResponse(
        status="success",
        message=f"成功安装 {len(installed_ids)} 个技能",
        installed_ids=installed_ids
    )

@router.delete("/{skill_id}")
async def delete_skill(skill_id: str):
    """从全局存储中删除技能"""
    target = Path(SKILLS_DIR) / skill_id
    if not target.exists():
        raise HTTPException(status_code=404, detail="技能不存在")
    
    robust_rmtree(target)
    return {"status": "success", "message": f"技能 {skill_id} 已删除"}

@router.get("/project-status")
async def get_project_skills_status(path: str):
    """查询指定项目已开启了哪些技能，并返回具体元数据"""
    if not path or not os.path.exists(path):
        return {"installed_ids": [], "project_skills": []}
    
    project_skills_dir = Path(path) / ".agents" / "skills"
    if not project_skills_dir.exists():
        return {"installed_ids": [], "project_skills": []}
    
    installed_ids = []
    project_skills = []
    
    for item in project_skills_dir.iterdir():
        if item.is_dir() and not item.name.startswith('.'):
            installed_ids.append(item.name)
            skill_meta = get_skill_metadata(item, item.name)
            project_skills.append(skill_meta)
            
    return {"installed_ids": installed_ids, "project_skills": project_skills}

@router.post("/sync")
async def sync_skill_to_project(req: SkillSyncRequest):
    """在全局目录和项目目录之间同步技能"""
    if not req.project_path or not os.path.exists(req.project_path):
        raise HTTPException(status_code=400, detail="项目路径无效")

    global_skill_path = Path(SKILLS_DIR) / req.skill_id
    project_skills_dir = Path(req.project_path) / ".agents" / "skills"
    target_path = project_skills_dir / req.skill_id

    if req.action == "install":
        if not global_skill_path.exists():
            raise HTTPException(status_code=404, detail="全局技能不存在，请先安装到系统")
        project_skills_dir.mkdir(parents=True, exist_ok=True)
        robust_rmtree(target_path)
        shutil.copytree(global_skill_path, target_path)
        return {"status": "success", "message": f"技能 {req.skill_id} 已同步至项目"}

    elif req.action == "remove":
        if target_path.exists():
            robust_rmtree(target_path)
        return {"status": "success", "message": f"技能 {req.skill_id} 已从项目移除"}
    
    elif req.action == "sync_to_global":
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="项目技能不存在，无法同步到全局")
        Path(SKILLS_DIR).mkdir(parents=True, exist_ok=True)
        robust_rmtree(global_skill_path)
        shutil.copytree(target_path, global_skill_path)
        return {"status": "success", "message": f"技能 {req.skill_id} 已反向同步至全局"}
    
    raise HTTPException(status_code=400, detail="无效的操作类型，支持 'install', 'remove', 'sync_to_global'")

@router.get("/get_path")
async def get_skills_path():
    """获取技能存储目录的绝对路径"""
    try:
        abs_path = os.path.abspath(SKILLS_DIR)
        if not os.path.exists(abs_path):
            os.makedirs(abs_path, exist_ok=True)
        return {"path": abs_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== 健康检查 ====================

@router.get("/health")
async def health_check():
    """服务健康检查"""
    return {"status": "ok", "skills_dir": SKILLS_DIR, "exists": os.path.exists(SKILLS_DIR)}