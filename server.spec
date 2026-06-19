# -*- mode: python ; coding: utf-8 -*-
import platform
from PyInstaller.utils.hooks import collect_submodules, collect_data_files  
from imageio_ffmpeg import get_ffmpeg_exe                                

_ = get_ffmpeg_exe()
# ---------- 1. 收集 imageio-ffmpeg 的 binaries & datas ----------
ffmpeg_bin = [(get_ffmpeg_exe(), '.')]                                  
ffmpeg_data = collect_data_files('imageio_ffmpeg')     

# 全平台禁用签名配置
universal_disable_sign = {
    'codesign_identity': None,
    'entitlements_file': None,
    'signing_requirements': '',
    'exclude_binaries': True
}

my_hidden_imports = [
    'pydantic.deprecated.decorator',
    'tiktoken_ext',
    'tiktoken_ext.openai_public',
    'botpy',
    'imageio_ffmpeg', 
    *collect_submodules('mem0'),
]

my_extra_datas = []

if platform.system() != 'Windows':
    # 添加 zerobox 及其所有子模块
    my_hidden_imports.extend(collect_submodules('zerobox'))
    # 收集 zerobox 可能带有的数据文件（如配置文件等）
    my_extra_datas.extend(collect_data_files('zerobox'))


a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=ffmpeg_bin, 
    datas=[
        ('config/settings_template.json', 'config'),
        ('config/locales.json', 'config'),
        ('static', 'static'),
        ('vrm', 'vrm'),
        ('tha_models', 'tha_models'),
        ('tiktoken_cache', 'tiktoken_cache'),
        ('skills', 'skills'),
        *ffmpeg_data,
        *my_extra_datas,
    ],
    hiddenimports=my_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# 修改基础配置
base_exe_config = {
    'debug': False,
    'strip': False,
    'upx': True,
    'bootloader_ignore_signals': False,
    'disable_windowed_traceback': False,
    **universal_disable_sign
}

if platform.system() == 'Darwin':
    # macOS 配置：生成独立可执行文件
    exe = EXE(
        pyz,
        a.scripts,
        [],
        name='server',
        icon='static/source/icon.png',
        **base_exe_config
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        name='server',
        upx_exclude=[],
        **universal_disable_sign
    )
elif platform.system() == 'Windows':
    # Windows 特殊配置
    exe = EXE(
        pyz,
        a.scripts,
        [],
        name='server',
        icon='static/source/icon.ico',  # 使用 .ico 格式图标
        **base_exe_config
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        name='server',
        upx_exclude=[],
        **universal_disable_sign
    )
else:
    # Linux 配置
    exe = EXE(
        pyz,
        a.scripts,
        [],
        name='server',
        **base_exe_config
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        name='server',
        upx_exclude=[],
        **universal_disable_sign
    )