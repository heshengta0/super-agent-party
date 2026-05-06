import httpx
from typing import Optional, List, Dict, Any, AsyncIterator, Union
import functools

class AsyncClaudeAsOpenAI:
    """
    完全模拟 AsyncOpenAI 客户端，底层用 litellm.acompletion（懒加载）
    """
    
    def __init__(
        self, 
        api_key: str, 
        base_url: Optional[str] = None,
        default_model: Optional[str] = "claude-3-5-sonnet-20241022",
        http_client: Optional[httpx.AsyncClient] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **kwargs
    ):
        self.api_key = api_key
        self.base_url = base_url  
        self.default_model = default_model
        self.http_client = http_client
        self.timeout = timeout
        self.max_retries = max_retries
        self._extra_kwargs = kwargs
        self._litellm_module = None  # 缓存 litellm 模块

    @property
    def _litellm(self):
        """懒加载 litellm，第一次调用时才 import"""
        if self._litellm_module is None:
            import litellm
            self._litellm_module = litellm
            # 可选：配置 litellm（如关闭日志）
            # litellm.set_verbose = False
            # litellm.suppress_debug_info = True
        return self._litellm_module

    @property
    def models(self):
        return self._ModelsResource(self)

    class _ModelsResource:
        def __init__(self, parent: "AsyncClaudeAsOpenAI"):
            self._parent = parent

        async def list(self):
            # 构造兼容 OpenAI 返回形式的对象
            class ModelItem:
                def __init__(self, model_id: str):
                    self.id = model_id

            class ModelList:
                def __init__(self, data: list):
                    self.data = data

            # 处理请求 URL，Anthropic 的获取模型接口通常是 /v1/models
            base_url = self._parent.base_url or "https://api.anthropic.com"
            if base_url.endswith("/v1") or base_url.endswith("/v1/"):
                url = f"{base_url.rstrip('/')}/models"
            else:
                url = f"{base_url.rstrip('/')}/v1/models"

            headers = {
                "x-api-key": self._parent.api_key,
                "anthropic-version": "2023-06-01"
            }

            try:
                # 优先复用全局 http_client 以走系统代理配置
                client = self._parent.http_client
                need_close = False
                if not client:
                    client = httpx.AsyncClient()
                    need_close = True

                response = await client.get(url, headers=headers)
                
                if need_close:
                    await client.aclose()

                # 如果 API 成功响应
                if response.status_code == 200:
                    data = response.json()
                    # 解析官方格式: {"type": "list", "data":[{"id": "claude-3-opus-...", ...}]}
                    models = [ModelItem(m["id"]) for m in data.get("data",[])]
                    if models:
                        return ModelList(models)
            except Exception as e:
                print(f"动态获取 Anthropic 模型列表失败 (可能代理/代理商不支持): {e}")

            # [静态兜底方案]：如果请求报错或代理商 API 未实现 /models 端点，返回常见的 Claude 模型
            fallback_models =[]
            return ModelList([ModelItem(m) for m in fallback_models])

    def _convert_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        """OpenAI Tools -> Claude Tools"""
        if not tools:
            return None
            
        claude_tools = []
        for tool in tools:
            tool_type = tool.get("type")
            
            if tool_type == "custom":
                continue  # Claude 不支持
            elif tool_type == "function":
                func = tool.get("function", {})
                claude_tools.append({
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                })
            elif tool_type in ["web_search_20250305", "web_search_20260209"]:
                claude_tools.append(tool)
                
        return claude_tools if claude_tools else None
    
    def _convert_tool_choice(self, tool_choice: Any) -> Any:
        """OpenAI tool_choice -> Claude tool_choice"""
        if tool_choice is None:
            return None
            
        if isinstance(tool_choice, str):
            return tool_choice
            
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            func_name = tool_choice.get("function", {}).get("name")
            if func_name:
                return {"type": "tool", "name": func_name}
                    
        return tool_choice

    @property
    def chat(self):
        return self._ChatResource(self)

    class _ChatResource:
        def __init__(self, parent: "AsyncClaudeAsOpenAI"):
            self.completions = self._CompletionsResource(parent)

        class _CompletionsResource:
            def __init__(self, parent: "AsyncClaudeAsOpenAI"):
                self._parent = parent

            async def create(
                self,
                model: Optional[str] = None,
                messages: Optional[List[Dict[str, Any]]] = None,
                temperature: Optional[float] = None,
                max_tokens: Optional[int] = None,
                stream: bool = False,
                top_p: Optional[float] = None,
                stop: Optional[Union[str, List[str]]] = None,
                tools: Optional[List[Dict]] = None,
                tool_choice: Optional[Any] = None,
                **kwargs
            ):
                model = model or self._parent.default_model
                if not model:
                    raise ValueError("model is required")

                if not model.startswith("anthropic/"):
                    model = f"anthropic/{model}"

                # ===== 懒加载 litellm =====
                litellm = self._parent._litellm
                
                completion_kwargs = {
                    "model": model,
                    "messages": messages,
                    "api_key": self._parent.api_key,
                    "stream": stream,
                }
                
                # Tools 转换
                if tools:
                    converted_tools = self._parent._convert_tools(tools)
                    if converted_tools:
                        completion_kwargs["tools"] = converted_tools
                        
                if tool_choice:
                    completion_kwargs["tool_choice"] = self._parent._convert_tool_choice(tool_choice)
                
                # 其他参数
                if self._parent.base_url:
                    completion_kwargs["api_base"] = self._parent.base_url
                if temperature is not None:
                    completion_kwargs["temperature"] = temperature
                if max_tokens is not None:
                    completion_kwargs["max_tokens"] = max_tokens
                if top_p is not None:
                    completion_kwargs["top_p"] = top_p
                if stop is not None:
                    completion_kwargs["stop"] = stop
                if self._parent.timeout is not None:
                    completion_kwargs["timeout"] = self._parent.timeout
                if self._parent.http_client is not None:
                    completion_kwargs["client"] = self._parent.http_client

                # 过滤 OpenAI 特有参数
                safe_kwargs = {k: v for k, v in kwargs.items() 
                              if k not in ['logprobs', 'top_logprobs', 'response_format', 'n']}
                
                return await litellm.acompletion(**completion_kwargs, **safe_kwargs)