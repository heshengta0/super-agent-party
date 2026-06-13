import os
import sys
import asyncio
from pathlib import Path
from io import BytesIO
from py.get_setting import DEFAULT_ASR_DIR

# ---------- 占位符与全局变量 ----------
_recognizer = None
_last_model_name = None

# ---------- 懒加载工具函数 ----------
def _detect_device() -> str:
    """
    检测适用设备：
    - macOS (darwin) 使用专用后端 'coreml'
    - Windows 及其他系统使用 'cpu' 保证绝对兼容性
    """
    if sys.platform == "darwin":
        return "coreml"
    return "cpu"

# 关键修复：将函数名改回 _get_recognizer，并添加默认参数
def _get_recognizer(model_name: str = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue"):
    """初始化/获取识别器（包含重型库的懒加载）"""
    global _recognizer, _last_model_name
    
    # 如果已经加载且模型没变，直接返回
    if _recognizer is not None and model_name == _last_model_name:
        return _recognizer

    # --- 延迟导入重型依赖 ---
    try:
        import sherpa_onnx
    except ImportError as e:
        print("未安装 sherpa_onnx 库:", e)
        return None
    
    model_dir = Path(DEFAULT_ASR_DIR) / model_name
    model_path = model_dir / "model.int8.onnx"
    tokens_path = model_dir / "tokens.txt"

    # 检查文件是否存在，不存在时不抛出异常（防止主程序崩溃），只返回 None
    if not model_path.is_file() or not tokens_path.is_file():
        # 这里用 logging 或 print，不要抛出 ValueError，否则 server.py lifespan 会崩溃
        print(f"提示: Sherpa 模型文件尚未下载，ASR 功能暂不可用。路径: {model_dir}")
        return None

    device = _detect_device()
    print(f"正在加载 Sherpa-ONNX 模型 [{model_name}] 使用设备 [{device}]...")

    try:
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_path),
            tokens=str(tokens_path),
            num_threads=4,
            provider=device,
            use_itn=True,
            debug=False,
        )
        _recognizer = recognizer
        _last_model_name = model_name
        return _recognizer
    except Exception as e:
        # 安全兜底：如果 macOS 上 CoreML 加速初始化失败，尝试回退到 cpu 运行
        if device == "coreml":
            print(f"警告: Mac 专属后端 [{device}] 初始化失败 ({e})，正在尝试退回到 [cpu] 运行...")
            try:
                recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model_path),
                    tokens=str(tokens_path),
                    num_threads=4,
                    provider="cpu",
                    use_itn=True,
                    debug=False,
                )
                _recognizer = recognizer
                _last_model_name = model_name
                return _recognizer
            except Exception as cpu_err:
                print(f"退回到 [cpu] 兜底方案时也发生错误: {cpu_err}")
                return None
        else:
            print(f"加载 Sherpa 模型时发生错误: {e}")
            return None

# ---------- 核心同步逻辑 (运行在线程池中) ----------
def _process_audio_sync(recognizer, audio_bytes: bytes) -> str:
    """
    同步执行的 CPU 密集型任务：解码音频 + 神经网络推理
    """
    import soundfile as sf
    import numpy as np

    with BytesIO(audio_bytes) as audio_file:
        audio, sample_rate = sf.read(audio_file, dtype="float32", always_2d=True)
        audio = audio[:, 0]  # 转单声道
        
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        recognizer.decode_stream(stream)
        return stream.result.text

# ---------- 公开的异步接口 ----------
async def sherpa_recognize(audio_bytes: bytes, model_name: str = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue"):
    """
    异步封装：将繁重的推理任务扔到线程池
    """
    try:
        recognizer = _get_recognizer(model_name)
        if recognizer is None:
            raise RuntimeError("ASR 模型未就绪（可能未下载或加载失败）")
        
        text = await asyncio.to_thread(_process_audio_sync, recognizer, audio_bytes)
        return text
    except Exception as e:
        raise RuntimeError(f"Sherpa ASR 处理失败: {e}")