import httpx
import os
from typing import Optional, List, Dict, Any, Union

class AsyncGeminiAsOpenAI:
    """
    完全模拟 AsyncOpenAI 客户端，底层用 litellm.acompletion
    自适应兼容：官方 Google AI Studio 直连、OpenAI 中转站、Gemini 专属网关
    """
    
    def __init__(
        self, 
        api_key: str, 
        base_url: Optional[str] = None,
        default_model: Optional[str] = "gemini-1.5-flash",
        http_client: Optional[httpx.AsyncClient] = None,
        **kwargs
    ):
        self.api_key = api_key
        if base_url == "https://generativelanguage.googleapis.com/v1beta/openai":
            # 兼容用户误传 Google OpenAI 兼容层地址的情况，自动修正为官方 AI Studio 地址
            base_url = "https://generativelanguage.googleapis.com"
        # 如果未传入，默认指向 Google AI Studio 官方地址
        self.base_url = base_url or "https://generativelanguage.googleapis.com"
        self.default_model = default_model
        self.http_client = http_client
        self._litellm_module = None

    @property
    def _litellm(self):
        if self._litellm_module is None:
            import litellm
            self._litellm_module = litellm
        return self._litellm_module

    # ================== 1. Chat 支持 ==================
    @property
    def chat(self):
        return self._ChatResource(self)

    class _ChatResource:
        def __init__(self, parent: "AsyncGeminiAsOpenAI"):
            self.completions = self._CompletionsResource(parent)

        class _CompletionsResource:
            def __init__(self, parent: "AsyncGeminiAsOpenAI"):
                self._parent = parent

            async def create(
                self,
                model: Optional[str] = None,
                messages: Optional[List[Dict[str, Any]]] = None,
                temperature: Optional[float] = None,
                max_tokens: Optional[int] = None,
                stream: bool = False,
                stop: Optional[Union[str, List[str]]] = None,
                tools: Optional[List[Dict]] = None,
                tool_choice: Optional[Any] = None,
                **kwargs
            ):
                litellm = self._parent._litellm
                raw_model = model or self._parent.default_model
                if not raw_model:
                    raise ValueError("model is required")

                # 【防御机制 1】强制注入环境变量，防止 LiteLLM 误判降级到 Vertex AI 验证流
                if self._parent.api_key:
                    os.environ["GEMINI_API_KEY"] = self._parent.api_key
                    os.environ["GOOGLE_API_KEY"] = self._parent.api_key

                base_url_str = self._parent.base_url or ""
                # 判断是否为官方直连
                is_official = not base_url_str or "generativelanguage.googleapis.com" in base_url_str

                # 判断是否为标准的 OpenAI 格式中转代理（如 OneAPI, NewAPI 等）
                is_openai_proxy = False
                if base_url_str:
                    cleaned_url = base_url_str.rstrip('/')
                    if cleaned_url.endswith('/v1') or "api." in cleaned_url or "proxy" in cleaned_url:
                        if "generativelanguage.googleapis.com" not in cleaned_url:
                            is_openai_proxy = True

                completion_kwargs = {
                    "messages": messages,
                    "stream": stream,
                }

                # 【核心自适应路由策略】
                if is_official:
                    # 场景 A: 官方 AI Studio 直连 
                    # 必须使用 gemini/ 前缀，且千万不要传递 api_base（防止 LiteLLM 拼装出双重 /v1beta/v1beta）
                    clean_model = raw_model.split('/')[-1]
                    completion_kwargs["model"] = f"gemini/{clean_model}"
                    completion_kwargs["api_key"] = self._parent.api_key
                elif is_openai_proxy:
                    # 场景 B: OpenAI 格式中转站
                    # 路由改走 openai 兼容通道，中转站会自动处理格式
                    clean_model = raw_model.split('/')[-1]
                    completion_kwargs["model"] = f"openai/{clean_model}"
                    completion_kwargs["api_key"] = self._parent.api_key
                    completion_kwargs["api_base"] = base_url_str
                else:
                    # 场景 C: 原生 Gemini 网关 (如 Cloudflare AI Gateway)
                    clean_model = raw_model.split('/')[-1]
                    completion_kwargs["model"] = f"gemini/{clean_model}"
                    completion_kwargs["api_key"] = self._parent.api_key
                    completion_kwargs["api_base"] = base_url_str
                    completion_kwargs["custom_llm_provider"] = "gemini"

                # 附加 OpenAI 标准参数
                if temperature is not None:
                    completion_kwargs["temperature"] = temperature
                if max_tokens is not None:
                    completion_kwargs["max_tokens"] = max_tokens
                if stop is not None:
                    completion_kwargs["stop"] = stop
                if tools:
                    completion_kwargs["tools"] = tools
                if tool_choice:
                    completion_kwargs["tool_choice"] = tool_choice
                if self._parent.http_client is not None:
                    completion_kwargs["client"] = self._parent.http_client

                # 过滤 OpenAI 特有但 Gemini 不支持的参数
                safe_kwargs = {k: v for k, v in kwargs.items() 
                              if k not in ['logprobs', 'top_logprobs', 'response_format', 'n']}

                return await litellm.acompletion(**completion_kwargs, **safe_kwargs)

    # ================== 2. Models 支持 ==================
    @property
    def models(self):
        return self._ModelsResource(self)

    class _ModelsResource:
        def __init__(self, parent: "AsyncGeminiAsOpenAI"):
            self._parent = parent

        async def list(self):
            class ModelItem:
                def __init__(self, model_id: str):
                    self.id = model_id

            class ModelList:
                def __init__(self, data: list):
                    self.data = data

            base_url_str = self._parent.base_url or ""
            is_official = not base_url_str or "generativelanguage.googleapis.com" in base_url_str

            is_openai_proxy = False
            if base_url_str:
                cleaned_url = base_url_str.rstrip('/')
                if cleaned_url.endswith('/v1') or "api." in cleaned_url or "proxy" in cleaned_url:
                    if "generativelanguage.googleapis.com" not in cleaned_url:
                        is_openai_proxy = True

            # 对应不同的获取模型列表 URL 和 Header
            if is_official:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self._parent.api_key}"
                headers = {}
            elif is_openai_proxy:
                url = f"{base_url_str.rstrip('/')}/models"
                if not url.endswith("/v1/models") and "/v1" not in url:
                    url = f"{base_url_str.rstrip('/')}/v1/models"
                headers = {"Authorization": f"Bearer {self._parent.api_key}"}
            else:
                url = f"{base_url_str.rstrip('/')}/v1beta/models?key={self._parent.api_key}"
                headers = {}

            try:
                client = self._parent.http_client
                need_close = False
                if not client:
                    client = httpx.AsyncClient()
                    need_close = True

                response = await client.get(url, headers=headers, timeout=10.0)
                
                if need_close:
                    await client.aclose()

                if response.status_code == 200:
                    res_data = response.json()
                    models = []
                    # 解析 Google 官方格式: {"models":[{"name": "models/gemini-1.5-flash", ...}]}
                    if "models" in res_data:
                        for m in res_data["models"]:
                            name = m.get("name", "").split("/")[-1]
                            if name:
                                models.append(ModelItem(name))
                    # 解析 OpenAI 格式: {"data":[{"id": "gemini-1.5-flash", ...}]}
                    elif "data" in res_data:
                        models = [ModelItem(m["id"]) for m in res_data["data"]]
                    
                    if models:
                        return ModelList(models)
            except Exception as e:
                print(f"动态获取 Gemini 模型列表失败 (已启动兜底机制): {e}")

            # 静态兜底方案
            fallback_models = []
            return ModelList([ModelItem(m) for m in fallback_models])