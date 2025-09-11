const isElectron = window.electronAPI ? true : false;
// 事件监听改造
if (isElectron) {
    document.addEventListener('contextmenu', (e) => {
      const imgTarget = e.target.closest('img');
      
      if (imgTarget) {
        e.preventDefault();
        window.electronAPI.showContextMenu('image', { 
          src: imgTarget.src,
          x: e.x,
          y: e.y
        });
      } else {
        window.electronAPI.showContextMenu('default');
      }
    });
  
    HOST = "127.0.0.1"
    PORT = window.location.port
    document.addEventListener('click', async (event) => {
      const link = event.target.closest('a[href]');
      if (!link) return;
      const href = link.getAttribute('href');
      
      try {
        const url = new URL(href);
        
        if (url.hostname === HOST && 
            url.port === PORT &&
            url.pathname.startsWith('/uploaded_files/')) {
          event.preventDefault();
          
          // 使用预加载接口处理路径
          const filename = url.pathname.split('/uploaded_files/')[1];
          const filePath = window.electronAPI.pathJoin(
            window.electronAPI.getAppPath(), 
            'uploaded_files', 
            filename
          );
          
          await window.electronAPI.openPath(filePath);
          return;
        }
        if (['http:', 'https:'].includes(url.protocol)) {
          event.preventDefault();
          await window.electronAPI.openExternal(href); // 确保调用electronAPI
          return;
        }
        
      } catch {
        event.preventDefault();
        window.location.href = href;
      }
    });
  }
  else {
    HOST = window.location.hostname
    PORT = window.location.port
  }
  // 判断协议
  const protocol = window.location.protocol;
  const backendURL = `${window.location.protocol}//${window.location.host}`;
let vue_data = {
    isMac: false,
    isWindows: false,
    partyURL:`${window.location.protocol}//${window.location.host}`,
    downloadProgress: 0,
    updateDownloaded: false,
    updateAvailable: false,
    updateInfo: null,
    updateIcon: 'fa-solid fa-download',
    system_prompt: ' ',
    isdocker: false,
    isExpanded: true,
    isElectron: isElectron,
    isCollapse: true,
    isBtnCollapse: true,
    activeMenu: 'home',
    isMaximized: false,
    hasUpdate: false,
    updateSuccess: false,
    settings: {
      model: '',
      base_url: '',
      api_key: '',
      temperature: 0.7,  // 默认温度值
      max_tokens: 4096,    // 默认最大输出长度
      max_rounds: 0,    // 默认最大轮数
      selectedProvider: null,
      top_p: 1,
      reasoning_effort: null,
      extra_params: [], // 额外参数
    },
    reasonerSettings: {
      enabled: false, // 默认不启用
      model: '',
      base_url: '',
      api_key: '',
      selectedProvider: null,
      temperature: 0.7,  // 默认温度值
      reasoning_effort: null,
    },
    target_lang: 'zh-CN',
    reasoningEfforts:[
      { value: null, label: 'auto' },
      { value: 'low', label: 'low' },
      { value: 'medium', label: 'medium' },
      { value: 'high', label: 'high' },
    ],
    visionSettings: {
      enabled: false, // 默认不启用
      model: '',
      base_url: '',
      api_key: '',
      selectedProvider: null,
      temperature: 0.7,  // 默认温度值
    },
    paramTypes: [
      { value: 'string', label: 'string' },
      { value: 'integer', label: 'integer' },
      { value: 'float', label: 'float' },
      { value: 'boolean', label: 'boolean' }
    ],
    ws: null,
    messages: [],
    cur_audioDatas: [],
    userInput: '',
    isTyping: false,
    currentMessage: '',
    conversationId: null, // 当前对话ID
    conversations: [], // 对话历史记录
    showHistoryDialog: false,
    showLLMToolsDialog: false,
    showHttpToolDialog: false,
    showComfyUIDialog: false,
    showStickerPacksDialog: false,
    showGsvRefAudioPathDialog: false,
    showModelDialog: false,
    deletingConversationId: null, // 正在被删除的对话ID
    jsonFile: null,
    models: [],
    modelsLoading: false,
    modelsError: null,
    isThinkOpen: false,
    showEditDialog: false,
    editContent: '',
    editType: 'system', // 或 'message'
    editIndex: null,
    asyncToolsID : [],
    TTSrunning:false,
    toolsSettings: {
      asyncTools: {
        enabled: false,
      },
      time: {
        enabled: false,
        triggerMode: 'beforeThinking',
      },
      accuweather: {
        enabled: false,
        apiKey: '',
      },
      wikipedia: {
        enabled: false,
      },
      arxiv: {
        enabled: false,
      },
      toolMemorandum: {
        enabled: true,
      },
      getFile: {
        enabled: false,
      },
      language: {
        enabled: false, // 默认不启用
        language: 'zh-CN',
        tone: 'normal',
      },
      inference: {
        enabled: false, // 默认不启用
      },
      deepsearch: {
        enabled: false, // 默认不启用
      },
      formula: {
        enabled: true
      }
    },
    mcpServers: {},
    showAddMCPDialog: false,
    showMCPConfirm: false,
    deletingMCPName: null,
    newMCPJson: '',
    newMCPType: 'stdio', // 新增类型字段
    currentMCPExample: '',
    mcpExamples: {
      stdio: `{
  "mcpServers": {
    "echo-server": {
      "command": "node",
      "args": [
        "path/to/echo-mcp/build/index.js"
      ],
      "disabled": false
    }
  }
}`,
      sse: `{
  "mcpServers": {
    "sse-server": {
      "url": "http://127.0.0.1:8000/sse",
      "headers": {
        "Content-Type": "text/event-stream",
        "Authorization": "Bearer YOUR_API_KEY"
      },
      "disabled": false
    }
  }
}`,
      ws: `{
  "mcpServers": {
    "websocket-server": {
      "url": "ws://127.0.0.1:8000/ws",
      "disabled": false
    }
  }
}`,
    streamablehttp: `{
  "mcpServers": {
    "streamablehttp-server": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Content-Type": "application/json",
        "Authorization": "Bearer YOUR_API_KEY"
      },
      "disabled": false
    }
  }
}`
    },
    activeKbTab: 'settings', // 默认激活的标签页
    webSearchSettings: {
      enabled: false,
      engine: 'tavily',
      crawler: 'jina',
      when: 'before_thinking',
      duckduckgo_max_results: 10, // 默认值
      searxng_url: `http://127.0.0.1:8080`,
      searxng_max_results: 10, // 默认值
      tavily_max_results: 10, // 默认值
      tavily_api_key: '',
      jina_api_key: '',
      Crawl4Ai_url: 'http://127.0.0.1:11235',
      Crawl4Ai_api_key: 'test_api_code',
      bing_max_results: 10, // 默认值
      bing_api_key: '',
      bing_search_url: 'https://api.bing.microsoft.com/v7.0/search',
      google_max_results: 10, // 默认值
      google_api_key: '',
      google_cse_id: '',
      brave_max_results: 10, // 默认值
      brave_api_key: '',
      exa_max_results:10,
      exa_api_key: '',
      serper_max_results:10,
      serper_api_key: '',
      bochaai_max_results:10,
      bochaai_api_key: '',
    },
    codeSettings: {
      enabled: false,
      engine: 'e2b',
      e2b_api_key: '',
      sandbox_url: 'http://127.0.0.1:8080',
    },
    HASettings: {
      enabled: false,
      api_key: '',
      url: 'http://127.0.0.1:8123',
    },
    chromeMCPSettings: {
      enabled: false,
      url: 'http://127.0.0.1:12306/mcp',
    },
    knowledgeBases: [],
    KBSettings: {
      when: 'before_thinking',
      is_rerank: false,
      selectedProvider: null,
      model: '',
      base_url: '',
      api_key: '',
      top_n: 5,
    },
    showAddKbDialog: false,
    showKnowledgeDialog: false,
    showMCPServerDialog: false,
    a2aServers: {},
    showA2AServerDialog: false,
    showAddA2ADialog: false,
    newA2AUrl: '',
    activeCollapse: [],
    newKb: {
      name: '',
      introduction: '',
      providerId: null,
      model: '',
      base_url: '',
      api_key: '',
      chunk_size: 2048,
      chunk_overlap: 512,
      chunk_k: 5,
      weight: 0.5,
      processingStatus: 'processing',
    },
    newKbFiles: [],
    systemSettings: {
      language: 'auto',
      theme: 'light',
      network:"local",
      proxy: '',
      proxyEnabled: false,
    },
    networkOptions:[
      { value: 'local', label: 'local' }, 
      { value: 'global', label: 'global' },
    ],
    imgHostOptions:[
      { value: 'smms', label: 'smms' },
      { value: 'easyImage2', label: 'easyImage2' },
      { value: 'gitee', label: 'gitee' },
      { value: 'github', label: 'github' },
    ],
    showRestartDialog: false,
    agents: {},
    showAgentForm: false,
    editingAgent: null,
    showAgentDialog: false,
    mainAgent: 'super-model',
    newAgent: {
      id: '',
      name: '',
      system_prompt: ''
    },
    editingAgent: false,
    currentLanguage: 'zh-CN',
    translations: translations,
    themeValues: ['light', 'dark','midnight','desert','neon','marshmallow','ink'],
    isBrowserOpening: false,
    expandedSections: {
      settingsBase: true,
      reasonerConfig: true,
      language: true,
      superapi: true,
      webSearchConfig: true,
      duckduckgoConfig: true,
      searxngConfig: true,
      tavilyConfig: true,
      jinaConfig: true,
      Crawl4AiConfig: true,
      settingsAdvanced: true,
      reasonerAdvanced: true,
      knowledgeAdvanced: false,
    },
    abortController: null, // 用于中断请求的控制器
    isSending: false, // 是否正在发送
    showAddDialog: false,
    modelProviders: [],
    // 更新相关
    updateAvailable: false,
    updateInfo: null,
    updateDownloaded: false,
    downloadProgress: 0,
    fileLinks: [],
    audioContext: null,
    mediaStream: null,
    mediaRecorder: null,
    audioChunks: [],
    isRecording: false,
    vad: null,
    speechTimeout: null,
    currentAudioChunks: [],
    currentTranscriptionId: null,
    speechStartTime: null,
    recognition: null,
    asrSettings: {
      enabled: false,
      engine: 'webSpeech',
      selectedProvider: null,
      webSpeechLanguage: 'auto',
      vendor: "OpenAI",
      model: "",
      base_url: "",
      api_key: "",
      funasr_ws_url: "ws://127.0.0.1:10095",
      funasr_mode: "2pass",
      interactionMethod: "auto",
      hotkey : "Alt",
      wakeWord: "小派",
      hotwords: "小派 80\nagent party 60",
    },
    supportedLanguages: [
      { code: 'zh-CN', name: '中文' },
      { code: 'en-US', name: 'English' },
      { code: 'ja-JP', name: '日本語' },
      { code: 'ko-KR', name: '한국어' },
      { code: 'es-ES', name: 'Español' },
      { code: 'fr-FR', name: 'Français' },
      { code: 'de-DE', name: 'Deutsch' },
      { code: 'ru-RU', name: 'Русский' },
    ],
    userInputBuffer: '',
    ttsSettings: {
      enabled: false,
      engine: 'edgetts',
      separators:["。", "\n", "？", "！", "，","～","!","?",",","~"],
      maxConcurrency: 2,
      enabledInterruption: false,
      bufferWordList: [],
      edgettsLanguage: 'zh-CN',
      edgettsGender: 'Female',
      edgettsVoice: 'XiaoyiNeural',
      edgettsRate: 1.0,
      gsvServer: "http://127.0.0.1:9880",
      gsvTextLang: 'zh',
      gsvRate: 1.0,
      gsvPromptLang: 'zh',
      gsvPromptText: '',
      gsvRefAudioPath: '',
      gsvAudioOptions: [],
      selectedProvider: null,
      vendor: "OpenAI",
      model: "",
      base_url: "",
      api_key: "",
      openaiVoice:"alloy",
      openaiSpeed: 1.0,
      customTTSserver: "http://127.0.0.1:9880",
      customTTSspeaker: "",
      customTTSspeed: 1.0,
      newtts:{}
    },
    activeTTSTab: 'default', // 控制 TTS 标签页切换
    showAddTTSDialog: false, // 控制添加 TTS 的对话框显示
    newTTSConfig: {
      name: '',
      enabled: false,
      engine: 'edgetts',
      edgettsLanguage: 'zh-CN',
      edgettsGender: 'Female',
      edgettsVoice: 'XiaoyiNeural',
      edgettsRate: 1.0,
      gsvServer: "http://127.0.0.1:9880",
      gsvTextLang: 'zh',
      gsvRate: 1.0,
      gsvPromptLang: 'zh',
      gsvPromptText: '',
      gsvRefAudioPath: '',
      gsvAudioOptions: [],
      selectedProvider: null,
      vendor: "OpenAI",
      model: "",
      base_url: "",
      api_key: "",
      openaiVoice:"alloy",
      openaiSpeed: 1.0,
      customTTSserver: "http://127.0.0.1:9880",
      customTTSspeaker: "",
      customTTSspeed: 1.0,
    },
    cur_voice :'default',
    openaiVoices:['alloy', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse'],
    showVrmModelDialog: false,
    newVrmModel: {
      name: '',
      displayName: '',
      file: null
    },
    VRMConfig: {
      name: 'default',
      enabledExpressions: false,
      selectedModelId: 'alice', // 默认选择Alice模型
      windowWidth: 540,
      windowHeight: 960,
      defaultModels: [], // 存储默认模型
      userModels: [],     // 存储用户上传的模型
      defaultMotions: [], // 存储默认动作
      userMotions: [],     // 存储用户上传的动作
      selectedMotionIds: [],
      selectedNewModelId: 'alice',
      selectedNewMotionIds: [],
      newVRM:{}
    },
    newAppearanceConfig: {
      name: '',
      windowWidth: 540,
      windowHeight: 960,
      selectedModelId: 'alice', // 默认选择Alice模型
      selectedMotionIds: [],
    },
    showAddAppearanceDialog: false,
    showVrmaMotionDialog: false,
    showFileDialog: false,
    newVrmaMotion: {
      name: '',
      displayName: '',
      file: null
    },
    expressionMap : [
      '<happy>', 
      '<angry>', 
      '<sad>',
      '<neutral>',
      '<surprised>', 
      '<relaxed>'],
    newGsvAudio: {
      name: '',
      path: '',
      text: '',
    },
    startTime: null,
    elapsedTime: 0,
    gsvTextLangs:["zh", "en" , "yue","ja","ko","auto","auto_yue"],
    audioPlayQueue: [],
    currentAudio: null,
    edgettsLanguage: 'zh-CN',
    edgettsGender: 'Female',
    edgettsvoices: [
    { language: "af-ZA", gender: "Female", name: "AdriNeural" },
    { language: "af-ZA", gender: "Male", name: "WillemNeural" },
    { language: "am-ET", gender: "Male", name: "AmehaNeural" },
    { language: "am-ET", gender: "Female", name: "MekdesNeural" },
    { language: "ar-AE", gender: "Female", name: "FatimaNeural" },
    { language: "ar-AE", gender: "Male", name: "HamdanNeural" },
    { language: "ar-BH", gender: "Male", name: "AliNeural" },
    { language: "ar-BH", gender: "Female", name: "LailaNeural" },
    { language: "ar-DZ", gender: "Female", name: "AminaNeural" },
    { language: "ar-DZ", gender: "Male", name: "IsmaelNeural" },
    { language: "ar-EG", gender: "Female", name: "SalmaNeural" },
    { language: "ar-EG", gender: "Male", name: "ShakirNeural" },
    { language: "ar-IQ", gender: "Male", name: "BasselNeural" },
    { language: "ar-IQ", gender: "Female", name: "RanaNeural" },
    { language: "ar-JO", gender: "Female", name: "SanaNeural" },
    { language: "ar-JO", gender: "Male", name: "TaimNeural" },
    { language: "ar-KW", gender: "Male", name: "FahedNeural" },
    { language: "ar-KW", gender: "Female", name: "NouraNeural" },
    { language: "ar-LB", gender: "Female", name: "LaylaNeural" },
    { language: "ar-LB", gender: "Male", name: "RamiNeural" },
    { language: "ar-LY", gender: "Female", name: "ImanNeural" },
    { language: "ar-LY", gender: "Male", name: "OmarNeural" },
    { language: "ar-MA", gender: "Male", name: "JamalNeural" },
    { language: "ar-MA", gender: "Female", name: "MounaNeural" },
    { language: "ar-OM", gender: "Male", name: "AbdullahNeural" },
    { language: "ar-OM", gender: "Female", name: "AyshaNeural" },
    { language: "ar-QA", gender: "Female", name: "AmalNeural" },
    { language: "ar-QA", gender: "Male", name: "MoazNeural" },
    { language: "ar-SA", gender: "Male", name: "HamedNeural" },
    { language: "ar-SA", gender: "Female", name: "ZariyahNeural" },
    { language: "ar-SY", gender: "Female", name: "AmanyNeural" },
    { language: "ar-SY", gender: "Male", name: "LaithNeural" },
    { language: "ar-TN", gender: "Male", name: "HediNeural" },
    { language: "ar-TN", gender: "Female", name: "ReemNeural" },
    { language: "ar-YE", gender: "Female", name: "MaryamNeural" },
    { language: "ar-YE", gender: "Male", name: "SalehNeural" },
    { language: "az-AZ", gender: "Male", name: "BabekNeural" },
    { language: "az-AZ", gender: "Female", name: "BanuNeural" },
    { language: "bg-BG", gender: "Male", name: "BorislavNeural" },
    { language: "bg-BG", gender: "Female", name: "KalinaNeural" },
    { language: "bn-BD", gender: "Female", name: "NabanitaNeural" },
    { language: "bn-BD", gender: "Male", name: "PradeepNeural" },
    { language: "bn-IN", gender: "Male", name: "BashkarNeural" },
    { language: "bn-IN", gender: "Female", name: "TanishaaNeural" },
    { language: "bs-BA", gender: "Male", name: "GoranNeural" },
    { language: "bs-BA", gender: "Female", name: "VesnaNeural" },
    { language: "ca-ES", gender: "Male", name: "EnricNeural" },
    { language: "ca-ES", gender: "Female", name: "JoanaNeural" },
    { language: "cs-CZ", gender: "Male", name: "AntoninNeural" },
    { language: "cs-CZ", gender: "Female", name: "VlastaNeural" },
    { language: "cy-GB", gender: "Male", name: "AledNeural" },
    { language: "cy-GB", gender: "Female", name: "NiaNeural" },
    { language: "da-DK", gender: "Female", name: "ChristelNeural" },
    { language: "da-DK", gender: "Male", name: "JeppeNeural" },
    { language: "de-AT", gender: "Female", name: "IngridNeural" },
    { language: "de-AT", gender: "Male", name: "JonasNeural" },
    { language: "de-CH", gender: "Male", name: "JanNeural" },
    { language: "de-CH", gender: "Female", name: "LeniNeural" },
    { language: "de-DE", gender: "Female", name: "AmalaNeural" },
    { language: "de-DE", gender: "Male", name: "ConradNeural" },
    { language: "de-DE", gender: "Male", name: "FlorianMultilingualNeural" },
    { language: "de-DE", gender: "Female", name: "KatjaNeural" },
    { language: "de-DE", gender: "Male", name: "KillianNeural" },
    { language: "de-DE", gender: "Female", name: "SeraphinaMultilingualNeural" },
    { language: "el-GR", gender: "Female", name: "AthinaNeural" },
    { language: "el-GR", gender: "Male", name: "NestorasNeural" },
    { language: "en-AU", gender: "Female", name: "NatashaNeural" },
    { language: "en-AU", gender: "Male", name: "WilliamNeural" },
    { language: "en-CA", gender: "Female", name: "ClaraNeural" },
    { language: "en-CA", gender: "Male", name: "LiamNeural" },
    { language: "en-GB", gender: "Female", name: "LibbyNeural" },
    { language: "en-GB", gender: "Female", name: "MaisieNeural" },
    { language: "en-GB", gender: "Male", name: "RyanNeural" },
    { language: "en-GB", gender: "Female", name: "SoniaNeural" },
    { language: "en-GB", gender: "Male", name: "ThomasNeural" },
    { language: "en-HK", gender: "Male", name: "SamNeural" },
    { language: "en-HK", gender: "Female", name: "YanNeural" },
    { language: "en-IE", gender: "Male", name: "ConnorNeural" },
    { language: "en-IE", gender: "Female", name: "EmilyNeural" },
    { language: "en-IN", gender: "Female", name: "NeerjaExpressiveNeural" },
    { language: "en-IN", gender: "Female", name: "NeerjaNeural" },
    { language: "en-IN", gender: "Male", name: "PrabhatNeural" },
    { language: "en-KE", gender: "Female", name: "AsiliaNeural" },
    { language: "en-KE", gender: "Male", name: "ChilembaNeural" },
    { language: "en-NG", gender: "Male", name: "AbeoNeural" },
    { language: "en-NG", gender: "Female", name: "EzinneNeural" },
    { language: "en-NZ", gender: "Male", name: "MitchellNeural" },
    { language: "en-NZ", gender: "Female", name: "MollyNeural" },
    { language: "en-PH", gender: "Male", name: "JamesNeural" },
    { language: "en-PH", gender: "Female", name: "RosaNeural" },
    { language: "en-SG", gender: "Female", name: "LunaNeural" },
    { language: "en-SG", gender: "Male", name: "WayneNeural" },
    { language: "en-TZ", gender: "Male", name: "ElimuNeural" },
    { language: "en-TZ", gender: "Female", name: "ImaniNeural" },
    { language: "en-US", gender: "Female", name: "AnaNeural" },
    { language: "en-US", gender: "Male", name: "AndrewMultilingualNeural" },
    { language: "en-US", gender: "Male", name: "AndrewNeural" },
    { language: "en-US", gender: "Female", name: "AriaNeural" },
    { language: "en-US", gender: "Female", name: "AvaMultilingualNeural" },
    { language: "en-US", gender: "Female", name: "AvaNeural" },
    { language: "en-US", gender: "Male", name: "BrianMultilingualNeural" },
    { language: "en-US", gender: "Male", name: "BrianNeural" },
    { language: "en-US", gender: "Male", name: "ChristopherNeural" },
    { language: "en-US", gender: "Female", name: "EmmaMultilingualNeural" },
    { language: "en-US", gender: "Female", name: "EmmaNeural" },
    { language: "en-US", gender: "Male", name: "EricNeural" },
    { language: "en-US", gender: "Male", name: "GuyNeural" },
    { language: "en-US", gender: "Female", name: "JennyNeural" },
    { language: "en-US", gender: "Female", name: "MichelleNeural" },
    { language: "en-US", gender: "Male", name: "RogerNeural" },
    { language: "en-US", gender: "Male", name: "SteffanNeural" },
    { language: "en-ZA", gender: "Female", name: "LeahNeural" },
    { language: "en-ZA", gender: "Male", name: "LukeNeural" },
    { language: "es-AR", gender: "Female", name: "ElenaNeural" },
    { language: "es-AR", gender: "Male", name: "TomasNeural" },
    { language: "es-BO", gender: "Male", name: "MarceloNeural" },
    { language: "es-BO", gender: "Female", name: "SofiaNeural" },
    { language: "es-CL", gender: "Female", name: "CatalinaNeural" },
    { language: "es-CL", gender: "Male", name: "LorenzoNeural" },
    { language: "es-CO", gender: "Male", name: "GonzaloNeural" },
    { language: "es-CO", gender: "Female", name: "SalomeNeural" },
    { language: "es-CR", gender: "Male", name: "JuanNeural" },
    { language: "es-CR", gender: "Female", name: "MariaNeural" },
    { language: "es-CU", gender: "Female", name: "BelkysNeural" },
    { language: "es-CU", gender: "Male", name: "ManuelNeural" },
    { language: "es-DO", gender: "Male", name: "EmilioNeural" },
    { language: "es-DO", gender: "Female", name: "RamonaNeural" },
    { language: "es-EC", gender: "Female", name: "AndreaNeural" },
    { language: "es-EC", gender: "Male", name: "LuisNeural" },
    { language: "es-ES", gender: "Male", name: "AlvaroNeural" },
    { language: "es-ES", gender: "Female", name: "ElviraNeural" },
    { language: "es-ES", gender: "Female", name: "XimenaNeural" },
    { language: "es-GQ", gender: "Male", name: "JavierNeural" },
    { language: "es-GQ", gender: "Female", name: "TeresaNeural" },
    { language: "es-GT", gender: "Male", name: "AndresNeural" },
    { language: "es-GT", gender: "Female", name: "MartaNeural" },
    { language: "es-HN", gender: "Male", name: "CarlosNeural" },
    { language: "es-HN", gender: "Female", name: "KarlaNeural" },
    { language: "es-MX", gender: "Female", name: "DaliaNeural" },
    { language: "es-MX", gender: "Male", name: "JorgeNeural" },
    { language: "es-NI", gender: "Male", name: "FedericoNeural" },
    { language: "es-NI", gender: "Female", name: "YolandaNeural" },
    { language: "es-PA", gender: "Female", name: "MargaritaNeural" },
    { language: "es-PA", gender: "Male", name: "RobertoNeural" },
    { language: "es-PE", gender: "Male", name: "AlexNeural" },
    { language: "es-PE", gender: "Female", name: "CamilaNeural" },
    { language: "es-PR", gender: "Female", name: "KarinaNeural" },
    { language: "es-PR", gender: "Male", name: "VictorNeural" },
    { language: "es-PY", gender: "Male", name: "MarioNeural" },
    { language: "es-PY", gender: "Female", name: "TaniaNeural" },
    { language: "es-SV", gender: "Female", name: "LorenaNeural" },
    { language: "es-SV", gender: "Male", name: "RodrigoNeural" },
    { language: "es-US", gender: "Male", name: "AlonsoNeural" },
    { language: "es-US", gender: "Female", name: "PalomaNeural" },
    { language: "es-UY", gender: "Male", name: "MateoNeural" },
    { language: "es-UY", gender: "Female", name: "ValentinaNeural" },
    { language: "es-VE", gender: "Female", name: "PaolaNeural" },
    { language: "es-VE", gender: "Male", name: "SebastianNeural" },
    { language: "et-EE", gender: "Female", name: "AnuNeural" },
    { language: "et-EE", gender: "Male", name: "KertNeural" },
    { language: "fa-IR", gender: "Female", name: "DilaraNeural" },
    { language: "fa-IR", gender: "Male", name: "FaridNeural" },
    { language: "fi-FI", gender: "Male", name: "HarriNeural" },
    { language: "fi-FI", gender: "Female", name: "NooraNeural" },
    { language: "fil-PH", gender: "Male", name: "AngeloNeural" },
    { language: "fil-PH", gender: "Female", name: "BlessicaNeural" },
    { language: "fr-BE", gender: "Female", name: "CharlineNeural" },
    { language: "fr-BE", gender: "Male", name: "GerardNeural" },
    { language: "fr-CA", gender: "Male", name: "AntoineNeural" },
    { language: "fr-CA", gender: "Male", name: "JeanNeural" },
    { language: "fr-CA", gender: "Female", name: "SylvieNeural" },
    { language: "fr-CA", gender: "Male", name: "ThierryNeural" },
    { language: "fr-CH", gender: "Female", name: "ArianeNeural" },
    { language: "fr-CH", gender: "Male", name: "FabriceNeural" },
    { language: "fr-FR", gender: "Female", name: "DeniseNeural" },
    { language: "fr-FR", gender: "Female", name: "EloiseNeural" },
    { language: "fr-FR", gender: "Male", name: "HenriNeural" },
    { language: "fr-FR", gender: "Male", name: "RemyMultilingualNeural" },
    { language: "fr-FR", gender: "Female", name: "VivienneMultilingualNeural" },
    { language: "ga-IE", gender: "Male", name: "ColmNeural" },
    { language: "ga-IE", gender: "Female", name: "OrlaNeural" },
    { language: "gl-ES", gender: "Male", name: "RoiNeural" },
    { language: "gl-ES", gender: "Female", name: "SabelaNeural" },
    { language: "gu-IN", gender: "Female", name: "DhwaniNeural" },
    { language: "gu-IN", gender: "Male", name: "NiranjanNeural" },
    { language: "he-IL", gender: "Male", name: "AvriNeural" },
    { language: "he-IL", gender: "Female", name: "HilaNeural" },
    { language: "hi-IN", gender: "Male", name: "MadhurNeural" },
    { language: "hi-IN", gender: "Female", name: "SwaraNeural" },
    { language: "hr-HR", gender: "Female", name: "GabrijelaNeural" },
    { language: "hr-HR", gender: "Male", name: "SreckoNeural" },
    { language: "hu-HU", gender: "Female", name: "NoemiNeural" },
    { language: "hu-HU", gender: "Male", name: "TamasNeural" },
    { language: "id-ID", gender: "Male", name: "ArdiNeural" },
    { language: "id-ID", gender: "Female", name: "GadisNeural" },
    { language: "is-IS", gender: "Female", name: "GudrunNeural" },
    { language: "is-IS", gender: "Male", name: "GunnarNeural" },
    { language: "it-IT", gender: "Male", name: "DiegoNeural" },
    { language: "it-IT", gender: "Female", name: "ElsaNeural" },
    { language: "it-IT", gender: "Male", name: "GiuseppeMultilingualNeural" },
    { language: "it-IT", gender: "Female", name: "IsabellaNeural" },
    { language: "iu-Cans-CA", gender: "Female", name: "SiqiniqNeural" },
    { language: "iu-Cans-CA", gender: "Male", name: "TaqqiqNeural" },
    { language: "iu-Latn-CA", gender: "Female", name: "SiqiniqNeural" },
    { language: "iu-Latn-CA", gender: "Male", name: "TaqqiqNeural" },
    { language: "ja-JP", gender: "Male", name: "KeitaNeural" },
    { language: "ja-JP", gender: "Female", name: "NanamiNeural" },
    { language: "jv-ID", gender: "Male", name: "DimasNeural" },
    { language: "jv-ID", gender: "Female", name: "SitiNeural" },
    { language: "ka-GE", gender: "Female", name: "EkaNeural" },
    { language: "ka-GE", gender: "Male", name: "GiorgiNeural" },
    { language: "kk-KZ", gender: "Female", name: "AigulNeural" },
    { language: "kk-KZ", gender: "Male", name: "DauletNeural" },
    { language: "km-KH", gender: "Male", name: "PisethNeural" },
    { language: "km-KH", gender: "Female", name: "SreymomNeural" },
    { language: "kn-IN", gender: "Male", name: "GaganNeural" },
    { language: "kn-IN", gender: "Female", name: "SapnaNeural" },
    { language: "ko-KR", gender: "Male", name: "HyunsuMultilingualNeural" },
    { language: "ko-KR", gender: "Male", name: "InJoonNeural" },
    { language: "ko-KR", gender: "Female", name: "SunHiNeural" },
    { language: "lo-LA", gender: "Male", name: "ChanthavongNeural" },
    { language: "lo-LA", gender: "Female", name: "KeomanyNeural" },
    { language: "lt-LT", gender: "Male", name: "LeonasNeural" },
    { language: "lt-LT", gender: "Female", name: "OnaNeural" },
    { language: "lv-LV", gender: "Female", name: "EveritaNeural" },
    { language: "lv-LV", gender: "Male", name: "NilsNeural" },
    { language: "mk-MK", gender: "Male", name: "AleksandarNeural" },
    { language: "mk-MK", gender: "Female", name: "MarijaNeural" },
    { language: "ml-IN", gender: "Male", name: "MidhunNeural" },
    { language: "ml-IN", gender: "Female", name: "SobhanaNeural" },
    { language: "mn-MN", gender: "Male", name: "BataaNeural" },
    { language: "mn-MN", gender: "Female", name: "YesuiNeural" },
    { language: "mr-IN", gender: "Female", name: "AarohiNeural" },
    { language: "mr-IN", gender: "Male", name: "ManoharNeural" },
    { language: "ms-MY", gender: "Male", name: "OsmanNeural" },
    { language: "ms-MY", gender: "Female", name: "YasminNeural" },
    { language: "mt-MT", gender: "Female", name: "GraceNeural" },
    { language: "mt-MT", gender: "Male", name: "JosephNeural" },
    { language: "my-MM", gender: "Female", name: "NilarNeural" },
    { language: "my-MM", gender: "Male", name: "ThihaNeural" },
    { language: "nb-NO", gender: "Male", name: "FinnNeural" },
    { language: "nb-NO", gender: "Female", name: "PernilleNeural" },
    { language: "ne-NP", gender: "Female", name: "HemkalaNeural" },
    { language: "ne-NP", gender: "Male", name: "SagarNeural" },
    { language: "nl-BE", gender: "Male", name: "ArnaudNeural" },
    { language: "nl-BE", gender: "Female", name: "DenaNeural" },
    { language: "nl-NL", gender: "Female", name: "ColetteNeural" },
    { language: "nl-NL", gender: "Female", name: "FennaNeural" },
    { language: "nl-NL", gender: "Male", name: "MaartenNeural" },
    { language: "pl-PL", gender: "Male", name: "MarekNeural" },
    { language: "pl-PL", gender: "Female", name: "ZofiaNeural" },
    { language: "ps-AF", gender: "Male", name: "GulNawazNeural" },
    { language: "ps-AF", gender: "Female", name: "LatifaNeural" },
    { language: "pt-BR", gender: "Male", name: "AntonioNeural" },
    { language: "pt-BR", gender: "Female", name: "FranciscaNeural" },
    { language: "pt-BR", gender: "Female", name: "ThalitaMultilingualNeural" },
    { language: "pt-PT", gender: "Male", name: "DuarteNeural" },
    { language: "pt-PT", gender: "Female", name: "RaquelNeural" },
    { language: "ro-RO", gender: "Female", name: "AlinaNeural" },
    { language: "ro-RO", gender: "Male", name: "EmilNeural" },
    { language: "ru-RU", gender: "Male", name: "DmitryNeural" },
    { language: "ru-RU", gender: "Female", name: "SvetlanaNeural" },
    { language: "si-LK", gender: "Male", name: "SameeraNeural" },
    { language: "si-LK", gender: "Female", name: "ThiliniNeural" },
    { language: "sk-SK", gender: "Male", name: "LukasNeural" },
    { language: "sk-SK", gender: "Female", name: "ViktoriaNeural" },
    { language: "sl-SI", gender: "Female", name: "PetraNeural" },
    { language: "sl-SI", gender: "Male", name: "RokNeural" },
    { language: "so-SO", gender: "Male", name: "MuuseNeural" },
    { language: "so-SO", gender: "Female", name: "UbaxNeural" },
    { language: "sq-AL", gender: "Female", name: "AnilaNeural" },
    { language: "sq-AL", gender: "Male", name: "IlirNeural" },
    { language: "sr-RS", gender: "Male", name: "NicholasNeural" },
    { language: "sr-RS", gender: "Female", name: "SophieNeural" },
    { language: "su-ID", gender: "Male", name: "JajangNeural" },
    { language: "su-ID", gender: "Female", name: "TutiNeural" },
    { language: "sv-SE", gender: "Male", name: "MattiasNeural" },
    { language: "sv-SE", gender: "Female", name: "SofieNeural" },
    { language: "sw-KE", gender: "Male", name: "RafikiNeural" },
    { language: "sw-KE", gender: "Female", name: "ZuriNeural" },
    { language: "sw-TZ", gender: "Male", name: "DaudiNeural" },
    { language: "sw-TZ", gender: "Female", name: "RehemaNeural" },
    { language: "ta-IN", gender: "Female", name: "PallaviNeural" },
    { language: "ta-IN", gender: "Male", name: "ValluvarNeural" },
    { language: "ta-LK", gender: "Male", name: "KumarNeural" },
    { language: "ta-LK", gender: "Female", name: "SaranyaNeural" },
    { language: "ta-MY", gender: "Female", name: "KaniNeural" },
    { language: "ta-MY", gender: "Male", name: "SuryaNeural" },
    { language: "ta-SG", gender: "Male", name: "AnbuNeural" },
    { language: "ta-SG", gender: "Female", name: "VenbaNeural" },
    { language: "te-IN", gender: "Male", name: "MohanNeural" },
    { language: "te-IN", gender: "Female", name: "ShrutiNeural" },
    { language: "th-TH", gender: "Male", name: "NiwatNeural" },
    { language: "th-TH", gender: "Female", name: "PremwadeeNeural" },
    { language: "tr-TR", gender: "Male", name: "AhmetNeural" },
    { language: "tr-TR", gender: "Female", name: "EmelNeural" },
    { language: "uk-UA", gender: "Male", name: "OstapNeural" },
    { language: "uk-UA", gender: "Female", name: "PolinaNeural" },
    { language: "ur-IN", gender: "Female", name: "GulNeural" },
    { language: "ur-IN", gender: "Male", name: "SalmanNeural" },
    { language: "ur-PK", gender: "Male", name: "AsadNeural" },
    { language: "ur-PK", gender: "Female", name: "UzmaNeural" },
    { language: "uz-UZ", gender: "Female", name: "MadinaNeural" },
    { language: "uz-UZ", gender: "Male", name: "SardorNeural" },
    { language: "vi-VN", gender: "Female", name: "HoaiMyNeural" },
    { language: "vi-VN", gender: "Male", name: "NamMinhNeural" },
    { language: "zh-CN", gender: "Female", name: "XiaoxiaoNeural" },
    { language: "zh-CN", gender: "Female", name: "XiaoyiNeural" },
    { language: "zh-CN", gender: "Male", name: "YunjianNeural" },
    { language: "zh-CN", gender: "Male", name: "YunxiNeural" },
    { language: "zh-CN", gender: "Male", name: "YunxiaNeural" },
    { language: "zh-CN", gender: "Male", name: "YunyangNeural" },
    { language: "zh-CN-liaoning", gender: "Female", name: "XiaobeiNeural" },
    { language: "zh-CN-shaanxi", gender: "Female", name: "XiaoniNeural" },
    { language: "zh-HK", gender: "Female", name: "HiuGaaiNeural" },
    { language: "zh-HK", gender: "Female", name: "HiuMaanNeural" },
    { language: "zh-HK", gender: "Male", name: "WanLungNeural" },
    { language: "zh-TW", gender: "Female", name: "HsiaoChenNeural" },
    { language: "zh-TW", gender: "Female", name: "HsiaoYuNeural" },
    { language: "zh-TW", gender: "Male", name: "YunJheNeural" },
    { language: "zu-ZA", gender: "Female", name: "ThandoNeural" },
    { language: "zu-ZA", gender: "Male", name: "ThembaNeural" }
],
    roleTiles:[
        { id: 'memory', title: 'CharacterMemory', icon: 'fa-solid fa-brain' },
        { id: 'voice', title: 'CharacterVoice', icon: 'fa-solid fa-volume-high' },
        { id: 'appearance', title: 'CharacterAppearance', icon: 'fa-solid fa-person' },
    ],
    modelTiles: [
      { id: 'service', title: 'modelService', icon: 'fa-solid fa-cloud' },
      { id: 'main', title: 'mainModel', icon: 'fa-solid fa-microchip' },
      { id: 'reasoner', title: 'reasonerModel', icon: 'fa-solid fa-atom' },
      { id: 'vision', title: 'visionModel' , icon: 'fa-solid fa-camera'},
      { id: 'text2img', title: 'text2imgModel', icon: 'fa-solid fa-pencil' },
      { id: 'asr', title: 'asrModel', icon: 'fa-solid fa-microphone' },
      { id: 'tts', title: 'ttsModel', icon: 'fa-solid fa-volume-high' },
    ],
    toolkitTiles: [
      { id: 'tools', title: 'utilityTools', icon: 'fa-solid fa-screwdriver-wrench' },
      { id: 'websearch', title: 'webSearch', icon: 'fa-solid fa-globe' },
      { id: 'document', title: 'knowledgeBase', icon: 'fa-solid fa-book' },
      { id: 'interpreter', title: 'interpreter', icon: 'fa-solid fa-code'},
      { id: 'sticker', title: 'sticker/image', icon: 'fa-solid fa-face-smile'},
      { id: 'HA', title: 'homeAssistant', icon: 'fa-solid fa-house'},
      { id: 'chromeMCP', title: 'browserControl', icon: 'fa-brands fa-chrome' },
      { id: 'comfyui', title: 'ComfyUI', icon: 'fa-solid fa-palette'},
      { id: 'mcp', title: 'mcpServers', icon: 'fa-solid fa-server'},
      { id: 'a2a', title: 'a2aServers', icon: 'fa-solid fa-plug'},
      { id: 'llmTool', title: 'llmTools', icon: 'fa-solid fa-network-wired'},
      { id: 'customHttpTool', title: 'customHttpTool', icon: 'fa-solid fa-wifi'},
    ],
    apiTiles: [
      { id: 'openai', title: 'openaiStyleAPI', icon: 'fa-solid fa-link' },
      { id: 'mcp', title: 'MCPStyleAPI', icon: 'fa-solid fa-server' },
      { id: 'vrm', title: 'vrmAPI', icon: 'fa-solid fa-user-ninja' },
      { id: 'agents', title: 'agentSnapshot', icon: 'fa-solid fa-robot'},
      { id: 'docker', title: 'docker', icon: 'fa-brands fa-docker'},
      { id: 'browser', title: 'browserMode', icon: 'fa-solid fa-globe' },
      { id: 'fastapi', title: 'fastAPIDocs', icon: 'fa-solid fa-book' },
    ],
    storageTiles: [
      { id: 'text', icon: 'fa-solid fa-file-lines', title: 'storageText' },
      { id: 'image', icon: 'fa-solid fa-image', title: 'storageImage' },
      { id: 'video', icon: 'fa-solid fa-video', title: 'storageVideo' }
    ],
    defaultSeparators: [
      // 转义字符
      { label: '\\n', value: '\n' },
      { label: '\\n\\n', value: '\n\n' },
      { label: '\\t', value: '\t' },
      { label: ' ', value: ' ' },
      // 中文标点符号
      { label: '。', value: '。' },
      { label: '...', value: '...' },
      { label: '？', value: '？' },
      { label: '！', value: '！' },
      { label: '，', value: '，' },
      { label: '；', value: ';' },
      { label: '：', value: '：' },
      { label: '～', value: '～' },
      // 英文标点符号
      { label: '~', value: '~' },
      { label: '.', value: '.' },
      { label: '…', value: '…' },
      { label: '?', value: '?' },
      { label: '!', value: '!' },
      { label: ',', value: ',' },
      { label: ';', value: ';' },
      { label: ':', value: ':' },
      { label: '"', value: '"' },
      { label: '\'', value: '\'' },
      // 其他
      { label: '*', value: '*' },
      { label: '`', value: '`' },
      { label: '·', value: '·' },
      { label: '-', value: '-' },
      { label: '—', value: '—' },
      { label: '/', value: '/' },
    ],
    qqBotConfig: {
      QQAgent:'super-model',
      memoryLimit: 30,
      appid: '',
      secret: '',
      separators: ["。", "\n", "？", "！"],
      reasoningVisible: true,
      quickRestart: true,
      is_sandbox: false,
    },
    isAudioSynthesizing: false, // 音频合成状态
    audioChunksCount: 0,        // 已生成的音频片段数
    totalChunksCount: 0,        // 总音频片段数
    isConvertingAudio: false,    // 音频转换状态
    isConvertStopping: false, // 新增状态
    ttsWebSocket: null,
    wsConnected: false,
    isVRMRunning: false,
    isVRMStarting: false,
    isVRMStopping: false,
    isVRMReloading: false,
    BotConfig: {
      imgHost_enabled: false,
      imgHost: 'smms',
      SMMS_api_key: '',
      EI2_base_url: '',
      EI2_api_key: '',
      gitee_repo_owner: "",
      gitee_repo_name: "",
      gitee_token: "",
      gitee_branch: "master",
      github_repo_owner: "",
      github_repo_name: "",
      github_token: "",
      github_branch: "main"
    },
    deployTiles: [
      { id: 'table_pet', title: 'tablePet', icon: "fa-solid fa-user-ninja"},
      { id: 'live_stream', title: 'live_stream_bot', icon: "fa-solid fa-video"},
      { id: 'qq_bot', title: 'qqBot', icon: 'fa-brands fa-qq' },
      { id: 'read_bot', title: 'readBot', icon: "fa-solid fa-book-open-reader"}, 
      { id: 'bot_config', title: 'bot_config', icon: 'fa-solid fa-robot' }
    ],
    readConfig: {
      longText: "",
    },
    selectedFile: null,
    isReadStarting: false,
    isReadStopping: false,
    isReadRunning: false,
    readState: {
      ttsChunks: [],
      audioChunks: [],
      ttsQueue: new Set(),
      currentChunk: 0,
      isPlaying: false
    },
    liveConfig: {
      onlyDanmaku: true,
      danmakuQueueLimit: 5,
      bilibili_enabled: false,
      bilibili_type: 'web',
      bilibili_room_id: '',
      bilibili_sessdata: '',
      bilibili_ACCESS_KEY_ID: '',
      bilibili_ACCESS_KEY_SECRET: '',
      bilibili_APP_ID: '',
      bilibili_ROOM_OWNER_AUTH_CODE: '',
    },
    WXBotConfig: {
      WXAgent:'super-model',
      memoryLimit: 30,
      separators: ["。", "\n", "？", "！"],
      reasoningVisible: true,
      quickRestart: true,
      nickNameList: [],
      wakeWord: '小派',
    },
    danmu: [], // 弹幕列表
    bilibiliWs: null, // WebSocket连接
    danmuProcessTimer: null, // 弹幕处理定时器
    isProcessingDanmu: false, // 是否正在处理弹幕
    shouldReconnectWs :false,
    isLiveRunning: false,
    isLiveStarting: false,
    isLiveStopping: false,
    isLiveReloading: false,
    isWXStarting: false,
    isWXStopping: false,
    isWXReloading: false,
    isWXBotRunning: false,
    stickerPacks: [],
    showStickerDialog: false,
    newStickerPack: {
      name: '',
      stickers: [],
      tags: []
    },
    dialogVisible: false,
    imageUrl: '',
    uploadedStickers: [], // 格式: { uid: string, url: string, tags: string[] }
    isQQBotRunning: false, // QQ机器人状态
    isStarting: false,      // 启动中状态
    isStopping: false,      // 停止中状态
    isReloading: false,     // 重载中状态
    activeMemoryTab: 'config',
    activeMemoryTabName: 'autoUpdateSetting',
    memories: [],
    newMemory: {
      id: null,
      name: '',
      providerId: null,
      model: '',
      base_url: '',
      api_key: '',
      vendor: '',
      description: '',
      avatar: '',
      personality: '',
      mesExample: '',
      systemPrompt: '',
      firstMes: '',
      alternateGreetings: [],
      characterBook: [{ keysRaw: '', content: '' }]
    },
    firstMes: '',
    alternateGreetings: [],
    showAddMemoryDialog: false,
    showMemoryDialog: false,
    memorySettings: {
      selectedMemory: null,
      is_memory: false,
      memoryLimit: 10,
      userName:'user',
      genericSystemPrompt: '{{char}}必须使用{{user}}使用的语言与之交流，例如：当{{user}}使用中文时，你也必须尽可能地使用中文！当{{user}}使用英文时，你也必须尽可能地使用英文！包括交代旁白等文字也是同理！',
    },
    textFiles: [],
    imageFiles: [],
    videoFiles: [],
    selectedFiles: [],      // 存 unique_filename
    selectedImages: [],
    allImagesChecked: false,
    indeterminateImages: false,
    selectedVideos: [],
    allVideosChecked: false,
    indeterminateVideos: false,
    subMenu: '', // 新增子菜单状态
    isWorldviewSettingsExpanded: true,
    isRandomSettingsExpanded: true,
    isBasicCharacterExpanded: true,
    text2imgSettings: {
      enabled: false,
      engine: 'pollinations',
      pollinations_model: 'flux',
      pollinations_width: 512,
      pollinations_height: 512,
      selectedProvider: null,
      vendor: 'OpenAI',
      model: '',
      base_url: '',
      api_key: '',
      size: '1024x1024',
    },
    agentTiles: [
      { 
        id: 'agents',
        title: 'agentSnapshot',
        icon: 'fa-solid fa-robot'
      },
      {
        id: 'mcp',
        title: 'mcpServers', 
        icon: 'fa-solid fa-server'
      },
      {
        id: 'a2a',
        title: 'a2aServers',
        icon: 'fa-solid fa-plug'
      },
      {
        id: 'llmTool',
        title: 'llmTools',
        icon: 'fa-solid fa-network-wired'
      },
      {
        id: 'customHttpTool',
        title: 'customHttpTool',
        icon: 'fa-solid fa-wifi'
      },
      {
        id: 'comfyui',
        title: 'ComfyUI',
        icon: 'fa-solid fa-palette'
      },
    ],
    comfyuiServers: ['http://127.0.0.1:8188'], // 默认服务器
    comfyuiAPIkey: '',
    workflowDescription: "",
    activeComfyUIUrl: '',
    isConnecting: false,
    customHttpTools: [],  // 用于存储自定义HTTP工具的数组
    showCustomHttpToolForm: false,
    isInputExpanded: false,
    sidebarVisible: false,
    isMobile: false,
    searchKeyword: '',
    newCustomHttpTool: {
      enabled: true,
      name: '',
      description: '',
      url: '',
      method: 'GET',
      headers: '',
      body: ''
    },
    editingCustomHttpTool: false,
    vendorValues: [
      'custom', 'OpenAI', 'Ollama','Vllm','LMstudio','xinference','Dify', 'Deepseek', 'Volcano','302.AI',
      'siliconflow', 'aliyun', 'ZhipuAI', 'moonshot', 'minimax', 'Gemini','Anthropic', 'BurnCloud',
      'Grok', 'mistral', 'lingyi','baichuan', 'qianfan', 'hunyuan', 'stepfun', 'Github', 
      'openrouter','together', 'fireworks', '360', 'Nvidia',
      'jina', 'gitee', 'perplexity', 'infini',
      'modelscope', 'tencent'
    ],
    vendorLogoList: {
      'custom': 'source/providers/custom.png',
      'OpenAI': 'source/providers/openai.jpeg',
      'Ollama': 'source/providers/ollama.png',
      'Vllm': 'source/providers/vllm.png',
      'LMstudio': 'source/providers/lmstudio.png',
      'xinference': 'source/providers/xinference.png',
      'Dify': 'source/providers/dify.png',
      'Deepseek': 'source/providers/deepseek.png',
      'Volcano': 'source/providers/volcengine.png',
      'siliconflow': 'source/providers/silicon.png',
      '302.AI': 'source/providers/302.png',
      'aliyun': 'source/providers/bailian.png',
      'ZhipuAI': 'source/providers/zhipu.png',
      'moonshot': 'source/providers/moonshot.png',
      'minimax': 'source/providers/minimax.png',
      'Gemini': 'source/providers/gemini.png',
      'Anthropic': 'source/providers/anthropic.png',
      'Grok': 'source/providers/grok.png',
      'mistral': 'source/providers/mistral.png',
      'lingyi': 'source/providers/zero-one.png',
      'baichuan': 'source/providers/baichuan.png',
      'qianfan': 'source/providers/baidu-cloud.svg',
      'hunyuan': 'source/providers/hunyuan.png',
      'stepfun': 'source/providers/step.png',
      'Github': 'source/providers/github.png',
      'openrouter': 'source/providers/openrouter.png',
      'together': 'source/providers/together.png',
      'fireworks': 'source/providers/fireworks.png',
      '360': 'source/providers/360.png',
      'Nvidia': 'source/providers/nvidia.png',
      'jina': 'source/providers/jina.png',
      'gitee': 'source/providers/gitee-ai.png',
      'perplexity': 'source/providers/perplexity.png',
      'infini': 'source/providers/infini.png',
      'modelscope': 'source/providers/modelscope.png',
      'tencent': 'source/providers/tencent-cloud-ti.png',
      'BurnCloud': 'source/providers/burncloud.png'
    },
    vendorAPIpage: {
      'OpenAI': 'https://platform.openai.com/api-keys',
      'Ollama': 'https://ollama.com/',
      'Vllm': 'https://docs.vllm.ai/en/latest/',      
      'LMstudio': 'https://lmstudio.ai/docs/app',
      'xinference': 'https://inference.readthedocs.io/zh-cn/latest/index.html',
      'Dify': 'http://localhost/apps',
      'Deepseek': 'https://platform.deepseek.com/api_keys',
      'Volcano': 'https://www.volcengine.com/experience/ark',
      'siliconflow': 'https://cloud.siliconflow.cn/i/yGxrNlGb',
      '302.AI': 'https://302.ai/apis/list',
      'aliyun': 'https://bailian.console.aliyun.com/?tab=model#/api-key',
      'ZhipuAI': 'https://open.bigmodel.cn/usercenter/apikeys',
      'moonshot': 'https://platform.moonshot.cn/console/api-keys',
      'minimax': 'https://platform.minimaxi.com/user-center/basic-information/interface-key',
      'Gemini': 'https://aistudio.google.com/app/apikey',
      'Anthropic': 'https://console.anthropic.com/settings/keys',
      'Grok': 'https://console.x.ai/',
      'mistral': 'https://console.mistral.ai/api-keys/',
      'lingyi': 'https://platform.lingyiwanwu.com/apikeys',
      'baichuan': 'https://platform.baichuan-ai.com/console/apikey',
      'qianfan': 'https://console.bce.baidu.com/iam/#/iam/apikey/list',
      'hunyuan': 'https://console.cloud.tencent.com/hunyuan/api-key',
      'stepfun': 'https://platform.stepfun.com/interface-key',
      'Github': 'https://github.com/settings/tokens',
      'openrouter': 'https://openrouter.ai/settings/keys',
      'together': 'https://api.together.ai/settings/api-keys',
      'fireworks': 'https://fireworks.ai/account/api-keys',
      '360': 'https://ai.360.com/platform/keys',
      'Nvidia': 'https://build.nvidia.com/meta/llama-3_1-405b-instruct',
      'jina': 'https://jina.ai/api-dashboard',
      'gitee': 'https://ai.gitee.com/dashboard/settings/tokens',
      'perplexity': 'https://www.perplexity.ai/settings/api',
      'infini': 'https://cloud.infini-ai.com/iam/secret/key',
      'modelscope': 'https://modelscope.cn/my/myaccesstoken',
      'tencent': 'https://console.cloud.tencent.com/lkeap/api',
      'BurnCloud': 'https://ai.burncloud.com/'
    },
    newProviderTemp: {
      vendor: '',
      url: '',
      apiKey: '',
      modelId: ''
    },
    systemlanguageOptions:[
      { value: 'auto', label: 'auto' }, 
      { value: 'zh-CN', label: '中文' }, 
      { value: 'en-US', label: 'English' },
    ],
    toneValues: [
      'normal', 'formal', 'friendly', 'humorous', 'professional',
      'sarcastic', 'ironic', 'flirtatious', 'tsundere', 'coquettish',
      'angry', 'sad', 'excited', 'refutational'
    ],
    showUploadDialog: false,
    agentTabActive: 'knowledge',
    files: [],
    images: [],
    currentUploadType: 'file',
    selectedCodeLang: 'python',
    previewClickHandler: null,
    dockerExamples: `docker pull ailm32442/super-agent-party:latest
docker run -d \\
  -p 3456:3456 \\
  -v ./super-agent-data:/app/data \\
  ailm32442/super-agent-party:latest
`,
    codeExamples: {
      python: `from openai import OpenAI
client = OpenAI(
  api_key="super-secret-key",
  base_url="${backendURL}/v1"
)
response = client.chat.completions.create(
  model="super-model",
  messages=[
      {"role": "user", "content": "什么是super agent party？"}
  ]
)
print(response.choices[0].message.content)`,
    javascript: `import OpenAI from 'openai';
const client = new OpenAI({
  apiKey: "super-secret-key",
  baseURL: "${backendURL}/v1"
});
async function main() {
  const completion = await client.chat.completions.create({
      model: "super-model",
      messages: [
          { role: "user", content: "什么是super agent party？" }
      ]
  });
  console.log(completion.choices[0].message.content);
}
main();`,
    curl: `curl ${backendURL}/v1/chat/completions \\
-H "Content-Type: application/json" \\
-H "Authorization: Bearer super-secret-key" \\
-d '{
  "model": "super-model",
  "messages": [
    {"role": "user", "content": "什么是super agent party？"}
  ]
}'`
    },  
    llmTools: [],
    showLLMForm: false,
    editingLLM: null,
    newLLMTool: {
      name: '',
      type: 'openai',
      description: '',
      base_url: '',
      api_key: '',
      model: '',
      enabled: true
    },
    llmInterfaceTypes: [
      { value: 'openai', label: 'OpenAI' },
      { value: 'ollama', label: 'Ollama' }
    ],
    modelOptions: [],
    previewVisible: false,
    previewImageUrl: '',
    workflows: [], // 保存工作流文件列表
    showWorkflowUploadDialog: false, // 控制上传对话框的显示
    workflowFile: null, // 当前选中的工作流文件
    selectedTextInput: null,
    selectedImageInput: null,
    selectedTextInput2: null,
    selectedImageInput2: null,
    selectedSeedInput: null,
    selectedSeedInput2: null,
    textInputOptions: [], // 确保这里是一个空数组
    imageInputOptions: [], // 确保这里是一个空数组
    seedInputOptions: [], // 确保这里是一个空数组
};