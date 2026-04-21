import asyncio
import json
import websockets
import logging
import numpy as np
import os
from py.get_setting import USER_DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("VTSManager")

class VTSManager:
    def __init__(self):
        self.vts_ws = None
        self.authenticated = False
        self.token_path = os.path.join(USER_DATA_DIR, 'vts_token.txt')
        self.token = self.load_token()
        self.is_running = False
        
        self.enabled_expressions = True
        self.enabled_motions = True
        
        self.mouth_value = 0.0
        self.mouth_smile = 0.0  
        self.audio_queue = asyncio.Queue()
        self.worker_task = None
        
        self.available_hotkeys = []      
        self.model_expressions =[]      

        self.sample_rate = 24000     
        self.frame_ms = 0.035        
        
        # 匹配 TTS 的真实高音量
        self.rms_threshold = 15000.0  
        self.smooth_factor = 0.45     

    @property
    def current_active_expressions(self):
        return [e['name'] for e in self.model_expressions if e.get('active')]

    def load_token(self):
        if os.path.exists(self.token_path):
            try:
                with open(self.token_path, "r") as f: return f.read().strip()
            except: return None
        return None

    def save_token(self, token):
        try:
            with open(self.token_path, "w") as f: f.write(token)
            self.token = token
        except: pass

    async def connect(self, config):
        url = config.get("url", "ws://127.0.0.1:8001")
        self.enabled_expressions = config.get("enabledExpressions", True)
        self.enabled_motions = config.get("enabledMotions", True)
        try:
            if self.vts_ws: await self.stop()
            self.vts_ws = await websockets.connect(url)
            self.is_running = True
            self.audio_queue = asyncio.Queue()
            asyncio.create_task(self.listen_vts())
            await self.authenticate()
            if self.worker_task is None or self.worker_task.done():
                self.worker_task = asyncio.create_task(self.vts_worker())
            logger.info("VTS: 连接并初始化成功")
            return True
        except Exception as e:
            logger.error(f"VTS: 连接失败: {e}")
            return False

    async def send(self, msg_type, data):
        if self.vts_ws:
            payload = {
                "apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0",
                "requestID": "AgentParty", "messageType": msg_type, "data": data
            }
            await self.vts_ws.send(json.dumps(payload))

    async def refresh_vts_data(self):
        if self.authenticated:
            await self.send("HotkeysInCurrentModelRequest", {})
            await self.send("ExpressionStateRequest", {})

    async def trigger_hotkey(self, tag_name):
        if not self.authenticated: return
        clean_name = tag_name.replace('<', '').replace('>', '').strip().lower()
        target_exp = None
        for exp in self.model_expressions:
            if exp['name'].strip().lower() == clean_name:
                target_exp = exp
                break
        
        if target_exp and self.enabled_expressions:
            for exp in self.model_expressions:
                should_active = (exp['name'] == target_exp['name'])
                if exp['active'] != should_active:
                    await self.send("ExpressionActivationRequest", {
                        "expressionFile": exp['file'],
                        "active": should_active
                    })
                    exp['active'] = should_active 
            return

        if self.enabled_motions:
            for hk in self.available_hotkeys:
                if hk['name'].strip().lower() == clean_name:
                    await self.send("HotkeyTriggerRequest", {"hotkeyID": hk['hotkeyID']})
                    return

    async def drive_mouth(self, pcm_bytes):
        if self.is_running: await self.audio_queue.put(pcm_bytes)

    async def vts_worker(self):
        samples_per_frame = int(self.sample_rate * self.frame_ms)
        vowel_start, vowel_end = 10, 88 
        debug_counter = 0

        while self.is_running:
            try:
                pcm_data = await self.audio_queue.get()
                
                # ====== 核心修复区：处理单数个字节的情况 ======
                if len(pcm_data) % 2 != 0:
                    pcm_data = pcm_data[:-1] # 如果是单数，丢弃最后 1 个字节
                
                if not pcm_data: # 如果丢弃后为空，跳过
                    self.audio_queue.task_done()
                    continue
                # ===============================================

                samples = np.frombuffer(pcm_data, dtype=np.int16)
                
                for i in range(0, len(samples), samples_per_frame):
                    if not self.is_running: break
                    frame = samples[i : i + samples_per_frame]
                    if len(frame) < 100: continue
                    
                    frame_float = frame.astype(np.float32)
                    rms = float(np.sqrt(np.mean(frame_float**2)))
                    
                    target_open = 0.0
                    target_smile = 0.0
                    vowel_ratio = 0.0
                    
                    if rms >= 400: 
                        volume_ratio = min(1.0, rms / self.rms_threshold)
                        volume_factor = volume_ratio ** 1.3 
                        
                        fft_mag = np.abs(np.fft.rfft(frame_float))
                        
                        if len(fft_mag) > vowel_end:
                            vowel_energy = float(np.mean(fft_mag[vowel_start:vowel_end]))
                            cons_energy = float(np.mean(fft_mag[vowel_end:]))
                            
                            total_energy = vowel_energy + cons_energy + 1e-6
                            vowel_ratio = vowel_energy / total_energy
                            cons_ratio = cons_energy / total_energy
                            
                            target_open = min(1.0, volume_factor * (0.1 + vowel_ratio * 1.5))
                            target_smile = min(1.0, volume_factor * cons_ratio * 1.5)
                        else:
                            target_open = volume_factor
                    else:
                        target_open = 0.0
                        target_smile = 0.0
                    
                    self.mouth_value += (target_open - self.mouth_value) * self.smooth_factor
                    self.mouth_smile += (target_smile - self.mouth_smile) * self.smooth_factor
                    
                    debug_counter += 1
                    if debug_counter % 10 == 0 and rms >= 400:
                        pass
                        # logger.info(f"[LipSync Debug] RMS: {rms:.1f} | 缩放后音量: {volume_factor:.2f} | 元音比例: {vowel_ratio:.2f} | 最终计算-> 张嘴: {self.mouth_value:.2f}")

                    if self.vts_ws:
                        msg = {
                            "apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0",
                            "requestID": "LipSync", "messageType": "InjectParameterDataRequest",
                            "data": { 
                                "faceFound": False, "mode": "set",
                                "parameterValues":[
                                    {"id": "MouthOpen", "value": float(round(self.mouth_value, 3))}
                                ]
                            }
                        }
                        try: 
                            await self.vts_ws.send(json.dumps(msg))
                        except Exception as e: 
                            logger.error(f"[VTS] 发送 WS 数据失败: {e}")
                            
                    await asyncio.sleep(self.frame_ms)
                self.audio_queue.task_done()
            except Exception as e:
                logger.error(f"[VTS] Worker 全局崩溃，正在重试: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    async def listen_vts(self):
        try:
            async for message in self.vts_ws:
                resp = json.loads(message)
                m_type = resp.get("messageType")
                data = resp.get("data")
                if m_type == "AuthenticationTokenResponse":
                    self.save_token(data["authenticationToken"])
                    await self.authenticate()
                elif m_type == "AuthenticationResponse":
                    if data.get("authenticated"):
                        self.authenticated = True
                        await self.refresh_vts_data()
                elif m_type == "HotkeysInCurrentModelResponse":
                    self.available_hotkeys =[hk for hk in data.get("availableHotkeys", []) if hk['type'] != 'ToggleExpression']
                elif m_type == "ExpressionStateResponse":
                    self.model_expressions = data.get("expressions",[])
        except: 
            self.is_running = False

    async def authenticate(self):
        if not self.vts_ws: return
        msg_type = "AuthenticationTokenRequest" if not self.token else "AuthenticationRequest"
        data = {"pluginName": "AgentParty", "pluginDeveloper": "AgentParty"}
        if self.token: data["authenticationToken"] = self.token
        await self.send(msg_type, data)

    async def stop(self):
        self.is_running = False
        if self.vts_ws: 
            await self.vts_ws.close()
            self.vts_ws = None

vts_instance = VTSManager()