const remoteMain = require('@electron/remote/main')
const { app, BrowserWindow, ipcMain, screen, shell, dialog, Tray, Menu, session,globalShortcut} = require('electron')
const { clipboard, nativeImage,desktopCapturer  } = require('electron')
const { autoUpdater } = require('electron-updater')
const path = require('path')
const { spawn } = require('child_process')
const { exec } = require('child_process');
const { download } = require('electron-dl');
const fs = require('fs')
const os = require('os')
const net = require('net') // 添加 net 模块用于端口检测
const dgram = require('dgram');
const osc = require('osc');
const chokidar = require('chokidar');
let workspaceWatcher = null; // 声明全局的 watcher 变量
// ★ VMC：UDP 收发资源
let vmcUdpPort = null;          // osc.UDPPort 实例
let vmcReceiverActive = false;  // 接收是否运行
let vrmWindows = []; 
let shotOverlay = null
let isMac = process.platform === 'darwin';
const vmcSendSocket = dgram.createSocket('udp4'); // 发送复用同一 socket
const MAX_LOG_LINES = 2000; // 保留最近2000行日志
let logBuffer = []; // 内存日志缓冲区
let activeDownloads = new Map(); 
function appendLogToBuffer(source, data) {
  const timestamp = new Date().toLocaleTimeString();
  const lines = data.toString().split(/\r?\n/);
  
  lines.forEach(line => {
    if (line.trim()) {
      logBuffer.push(`[${timestamp}] [${source}] ${line}`);
    }
  });

  // 清理旧日志，防止内存无限增长
  if (logBuffer.length > MAX_LOG_LINES) {
    logBuffer = logBuffer.slice(logBuffer.length - MAX_LOG_LINES);
  }
}
async function cropDesktop(rect) {
  if (!rect || typeof rect.x !== 'number' || typeof rect.y !== 'number' ||
      typeof rect.width !== 'number' || typeof rect.height !== 'number') {
    throw new Error('cropDesktop 需要 {x,y,width,height} 且均为数字')
  }

  const { width, height } = screen.getPrimaryDisplay().bounds
  const sources = await desktopCapturer.getSources({
    types: ['screen'],
    thumbnailSize: { width, height }
  })
  if (!sources.length) throw new Error('无法获取屏幕源')

  // 1. 拿到全屏 PNG 缓冲区
  const pngBuffer = sources[0].thumbnail.toPNG()

  // 2. 用 Electron 自带的 nativeImage 裁
  const img  = nativeImage.createFromBuffer(pngBuffer)
  const cropped = img.crop({
    x: Math.floor(rect.x),
    y: Math.floor(rect.y),
    width: Math.floor(rect.width),
    height: Math.floor(rect.height)
  })

  // 3. 直接返回 Buffer，下游无需改
  return cropped.toPNG()
}

// ★ 替换原来的 startVMCReceiver
function startVMCReceiver(cfg) {
  if (vmcReceiverActive) return;
  vmcUdpPort = new osc.UDPPort({
    localAddress: '0.0.0.0',
    localPort: cfg.receive.port,
    metadata: true,
  });
  vmcUdpPort.open();
  vmcUdpPort.on('message', (oscMsg) => {

    /* -------- 1. 骨骼 -------- */
    if (oscMsg.address === '/VMC/Ext/Bone/Pos') {
      if (!Array.isArray(oscMsg.args) || oscMsg.args.length < 8) return;
      const [boneName, x, y, z, qx, qy, qz, qw] = oscMsg.args.map(v => v.value ?? v);
      if (typeof boneName !== 'string') return;

      vrmWindows.forEach(w => {
        if (!w.isDestroyed()) {
          w.webContents.send('vmc-bone', { boneName, position:{x,y,z}, rotation:{x:qx,y:qy,z:qz,w:qw} });
          w.webContents.send('vmc-osc-raw', oscMsg);
        }
      });
      return;
    }

    /* -------- 2. 表情 -------- */
    if (oscMsg.address === '/VMC/Ext/Blend/Val') {
      if (!Array.isArray(oscMsg.args) || oscMsg.args.length < 2) return;
      vrmWindows.forEach(w => {
        if (!w.isDestroyed()) w.webContents.send('vmc-osc-raw', oscMsg);
      });
      return;
    }

    /* -------- 3. 表情 Apply -------- */
    if (oscMsg.address === '/VMC/Ext/Blend/Apply') {
      // Apply 不带参数，长度 0 也合法
      vrmWindows.forEach(w => {
        if (!w.isDestroyed()) w.webContents.send('vmc-osc-raw', oscMsg);
      });
    }
  });


  vmcReceiverActive = true;
  console.log(`[VMC] 接收已启动 @ ${cfg.receive.port}`);
}
function stopVMCReceiver() {
  if (!vmcReceiverActive) return;
  vmcUdpPort.close();
  vmcUdpPort = null;
  vmcReceiverActive = false;
  console.log('[VMC] 接收已停止');
}

// 发送 VMC Bone -------------------------------------------------
function sendVMCBoneMain(data) {
  if (!data) return;
  const { boneName, position, rotation } = data;
  if (!boneName || !position || !rotation) return;

  const { host, port } = global.vmcCfg.send;          // ← 面板配置
  const oscMsg = osc.writePacket({
    address: `/VMC/Ext/Bone/Pos`,
    args: [
      { type: 's', value: boneName },
      { type: 'f', value: position.x || 0 },
      { type: 'f', value: position.y || 0 },
      { type: 'f', value: position.z || 0 },
      { type: 'f', value: rotation.x || 0 },
      { type: 'f', value: rotation.y || 0 },
      { type: 'f', value: rotation.z || 0 },
      { type: 'f', value: rotation.w || 1 },
    ],
  });
  vmcSendSocket.send(oscMsg, port, host, (err) => {
    if (err) console.error('VMC send error:', err);
  });
}

// 发送 VMC Blend ------------------------------------------------
function sendVMCBlendMain(data) {
  if (!data) return;
  const { blendName, weight } = data;
  if (typeof blendName !== 'string' || typeof weight !== 'number') return;

  const { host, port } = global.vmcCfg.send;          // ← 面板配置
  const oscMsg = osc.writePacket({
    address: '/VMC/Ext/Blend/Val',
    args: [
      { type: 's', value: blendName },
      { type: 'f', value: Math.max(0, Math.min(1, weight)) },
    ],
  });
  vmcSendSocket.send(oscMsg, port, host, (err) => {
    if (err) console.error('VMC blend send error:', err);
  });
}

// 发送 VMC Blend Apply ------------------------------------------
function sendVMCBlendApplyMain() {
  const { host, port } = global.vmcCfg.send;          // ← 面板配置
  const oscMsg = osc.writePacket({
    address: '/VMC/Ext/Blend/Apply',
    args: [],
  });
  vmcSendSocket.send(oscMsg, port, host);
}

let pythonExec;
let isQuitting = false;

// 判断操作系统
if (os.platform() === 'win32') {
  // Windows
  pythonExec = path.join('.venv', 'Scripts', 'python.exe');
} else {
  // macOS / Linux
  pythonExec = path.join('.venv', 'bin', 'python3');
}


function getCleanUserAgent() {
  const chromeVersion = '124.0.0.0'; // 必须与前端代码中的版本保持一致！
  const baseUA = `Mozilla/5.0 ({os_info}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromeVersion} Safari/537.36`;
  
  let osInfo = '';
  // Node.js 环境直接用 process.platform
  switch (process.platform) {
    case 'darwin':
      osInfo = 'Macintosh; Intel Mac OS X 10_15_7';
      break;
    case 'win32':
      osInfo = 'Windows NT 10.0; Win64; x64';
      break;
    case 'linux':
      osInfo = 'X11; Linux x86_64';
      break;
    default:
      osInfo = 'Windows NT 10.0; Win64; x64';
  }

  return baseUA.replace('{os_info}', osInfo);
}

// 提前计算好，供后面使用
const REAL_CHROME_UA = getCleanUserAgent();

let mainWindow
let loadingWindow
let tray = null
let updateAvailable = false
let backendProcess = null
const HOST = '127.0.0.1'
let PORT = 3456 // 改为 let，允许修改
const DEFAULT_PORT = 3456 // 保存默认端口
const isDev = process.env.NODE_ENV === 'development'
const locales = {
  'zh-CN': {
    show: '显示窗口',
    exit: '退出',
    cut: '剪切',
    copy: '复制',
    paste: '粘贴',
    copyImage: '复制图片',
    copyImageLink: '复制图片链接',
    saveImageAs: '图片另存为...',
    supportedFiles: '支持的文件',
    allFiles: '所有文件',
    supportedimages: '支持的图片',
    // 新增项
    openNewTab: '在新标签页打开',
    copyLink: '复制链接地址',
    copyLinkText: '复制链接文本',
    selectAll: '全选',
    inspect: '检查元素'
  },
  'en-US': {
    show: 'Show Window',
    exit: 'Exit',
    cut: 'Cut',
    copy: 'Copy',
    paste: 'Paste',
    copyImage: 'Copy Image',
    copyImageLink: 'Copy Image Link',
    saveImageAs: 'Save Image As...',
    supportedFiles: 'Supported Files',
    allFiles: 'All Files',
    supportedimages: 'Supported Images',
    // 新增项
    openNewTab: 'Open in new tab',
    copyLink: 'Copy link address',
    copyLinkText: 'Copy link text',
    selectAll: 'Select All',
    inspect: 'Inspect'
  }
};
const ALLOWED_EXTENSIONS = [
  // 办公文档
    'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'pdf', 'pages', 
    'numbers', 'key', 'rtf', 'odt', 'epub',
  
  // 编程开发
  'js', 'ts', 'py', 'java', 'c', 'cpp', 'h', 'hpp', 'go', 'rs',
  'swift', 'kt', 'dart', 'rb', 'php', 'html', 'css', 'scss', 'less',
  'vue', 'svelte', 'jsx', 'tsx', 'json', 'xml', 'yml', 'yaml', 
  'sql', 'sh',
  
  // 数据配置
  'csv', 'tsv', 'txt', 'md', 'log', 'conf', 'ini', 'env', 'toml'
  ];
const ALLOWED_IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'];
const ALLOWED_VIDEO_EXTENSIONS =['mp4', 'webm', 'ogg', 'mov', 'avi'];
let currentLanguage = 'zh-CN';

// 构建菜单项
let menu;

// 配置日志文件路径
const logDir = path.join(app.getPath('userData'), 'logs')
if (!fs.existsSync(logDir)) {
  fs.mkdirSync(logDir, { recursive: true })
}

// 获取配置文件路径
function getConfigPath() {
  return path.join(app.getPath('userData'), 'config.json');
}

// 加载环境变量
function loadEnvVariables() {
  const configPath = getConfigPath();
  if (fs.existsSync(configPath)) {
    try {
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      
      // 遍历配置加载到环境变量
      for (const key in config) {
        const val = config[key];
        // ★ 同样只把基本类型加载到 env
        if (typeof val === 'string' || typeof val === 'number') {
          process.env[key] = val;
        }
      }
      return config; // ★ 返回完整配置对象给 CDP 逻辑使用
    } catch (e) {
      console.error('加载配置失败:', e);
    }
  }
  return {};
}

function saveEnvVariable(key, value) {
  const configPath = getConfigPath();
  let config = {};
  
  // 1. 读取现有文件
  try {
    if (fs.existsSync(configPath)) {
      config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    }
  } catch (e) { console.error('配置文件读取出错:', e); }

  // 2. 更新文件内容 (对象和字符串都能存)
  config[key] = value;
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
  
  // 3. ★ 关键改进：类型检查 ★
  // 只有字符串或数字才写入 process.env，防止对象变 "[object Object]"
  if (typeof value === 'string' || typeof value === 'number') {
    process.env[key] = value;
  }
}

const globalConfig = loadEnvVariables();

// 定义全局变量
let SESSION_CDP_PORT = 0; // 初始为0
let IS_INTERNAL_MODE_ACTIVE = false;

if (globalConfig?.chromeMCPSettings?.type === 'internal' && globalConfig?.chromeMCPSettings?.enabled) {
  
  // ★ 修改点 1：使用端口 '0'，让系统自动分配一个绝对安全的空闲端口
  app.commandLine.appendSwitch('remote-debugging-port', '0');
  
  // ★ 修改点 2：显式绑定到 127.0.0.1，防止防火墙报警
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
  
  app.commandLine.appendSwitch('remote-allow-origins', '*');
  
  IS_INTERNAL_MODE_ACTIVE = true;
  console.log('[CDP] 已请求系统自动分配内置浏览器调试端口...');
}
app.commandLine.appendSwitch('js-flags', '--max-old-space-size=4096'); // 允许使用 4GB 内存
// 新增：检测端口是否可用
function isPortAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.listen(port, HOST, () => {
      server.once('close', () => resolve(true))
      server.close()
    })
    server.on('error', () => resolve(false))
  })
}

// 新增：查找可用端口
async function findAvailablePort(startPort = DEFAULT_PORT, maxAttempts = 20000) {
  for (let i = 0; i < maxAttempts; i++) {
    const port = startPort + i
    if (await isPortAvailable(port)) {
      return port
    }
  }
  throw new Error(`无法找到可用端口，已尝试 ${startPort} 到 ${startPort + maxAttempts - 1}`)
}


// 创建骨架屏窗口
function createSkeletonWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize
  mainWindow = new BrowserWindow({
    width: width,
    height: height,
    frame: false,
    titleBarStyle: 'hiddenInset', // macOS 特有：隐藏标题栏但仍显示原生按钮
    trafficLightPosition: { x: 10, y: 12 }, // 自定义按钮位置（可选）
    show: true,
    icon: 'static/source/icon.png',
    webPreferences: {
      preload: path.join(__dirname, 'static/js/preload.js'),
      nodeIntegration: false,
      sandbox: false,
      contextIsolation: true,
      enableRemoteModule: false,
      webSecurity: false,
      devTools: isDev,
      partition: 'persist:main-session',
      webviewTag: true,
    }
  })

  remoteMain.enable(mainWindow.webContents)
  
  // 加载骨架屏页面
  mainWindow.loadFile(path.join(__dirname, 'static/skeleton.html'))
  
  // 设置自动更新
  setupAutoUpdater()
  
  // 窗口状态同步
  mainWindow.on('maximize', () => {
    mainWindow.webContents.send('window-state', 'maximized')
  })
  mainWindow.on('unmaximize', () => {
    mainWindow.webContents.send('window-state', 'normal')
  })
  
  // 窗口关闭事件处理 - 最小化到托盘而不是退出
  mainWindow.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault()
      mainWindow.hide()
      return false
    }
    return true
  })
}

function getAcpxPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'acpx');
  } else {
    return path.join(__dirname, 'node_modules', 'acpx');
  }
}

// 修改后的启动后端函数
/**
 * 启动后端服务
 * 逻辑：传 port 0 -> 捕获 REAL_PORT_FOUND -> 返回真实端口
 */
async function startBackend() {
  return new Promise((resolve, reject) => {
    try {
      console.log('🔍 准备启动后端进程...');
      const npmCliPath = isDev 
        ? path.join(__dirname, 'node_modules', 'npm', 'bin', 'npm-cli.js')
        : path.join(process.resourcesPath, 'npm', 'bin', 'npm-cli.js');
      const spawnOptions = {
        stdio: ['pipe', 'pipe', 'pipe'],
        shell: false,
        env: {
          ...process.env,
          NODE_ENV: isDev ? 'development' : 'production',
          PYTHONIOENCODING: 'utf-8',
          PYTHONUNBUFFERED: '1', // 强制 Python 实时刷新缓冲区
          ELECTRON_NODE_EXEC: process.execPath, 
          ELECTRON_NPM_CLI: npmCliPath,
          ELECTRON_RESOURCES_PATH: app.isPackaged ? process.resourcesPath : path.join(__dirname),
          ELECTRON_ACPM_PATH: getAcpxPath(),
        }
      };

      if (process.platform === 'win32') {
        spawnOptions.windowsHide = !isDev;
      }

      // 获取 Host 配置
      const BACKEND_HOST = (globalConfig?.networkVisible === 'global') ? '0.0.0.0' : '127.0.0.1';

      let execPath = "";
      let backendArgs = [];

      if (isDev) {
        execPath = pythonExec;
        // 使用 -u 确保输出不被缓存，即便在 import 很多库的情况下
        backendArgs = ['-u', 'server.py', '--host', BACKEND_HOST, '--port', '3456'];
      } else {
        const serverExecutable = process.platform === 'win32' ? 'server.exe' : 'server';
        const resourcesPath = process.resourcesPath || path.join(process.execPath, '..', 'resources');
        execPath = path.join(resourcesPath, 'server', serverExecutable);
        backendArgs = ['--host', BACKEND_HOST, '--port', '3456'];
        spawnOptions.cwd = path.dirname(execPath);
      }

      console.log(`🚀 执行路径: ${execPath}`);
      backendProcess = spawn(execPath, backendArgs, spawnOptions);

      let isHandshaked = false;

      // 核心监听逻辑
      const onData = (data) => {
        const output = data.toString();
        // 1. 依然保留日志缓冲，供前端查看
        appendLogToBuffer('BACKEND', output);

        if (isDev) {
            // 开发模式下在控制台打印原始输出，方便排查
            process.stdout.write(`[PY] ${output}`);
        }

        // 2. 尝试解析端口握手信号
        const match = output.match(/REAL_PORT_FOUND:(\d+)/);
        if (match && !isHandshaked) {
          const actualPort = parseInt(match[1], 10);
          if (actualPort > 0) {
            isHandshaked = true;
            PORT = actualPort; // 更新全局 PORT 变量
            console.log(`✅ 握手成功！后端运行端口: ${PORT}`);
            resolve(PORT);
          }
        }
      };

      backendProcess.stdout.on('data', onData);
      backendProcess.stderr.on('data', onData);

      // 进程错误处理
      backendProcess.on('error', (err) => {
        console.error('❌ 后端启动失败:', err);
        reject(err);
      });

      // 进程意外退出处理
      backendProcess.on('close', (code) => {
        console.log(`ℹ️ 后端进程已退出 (code ${code})`);
        if (!isHandshaked) {
          reject(new Error(`后端进程在分配端口前已关闭，退出码: ${code}`));
        }
      });

      // 5分钟超时保护
      setTimeout(() => {
        if (!isHandshaked) {
          if (backendProcess) backendProcess.kill();
          reject(new Error('后端启动超时：未能从 Python 日志捕获 REAL_PORT_FOUND 信号'));
        }
      }, 360000*5);

    } catch (err) {
      reject(err);
    }
  });
}

// 修改等待后端函数
async function waitForBackend() {
  const MAX_RETRIES = 60; // 最多等 30 秒
  const RETRY_INTERVAL = 500;
  let retries = 0;

  console.log(`⏳ 正在等待 http://127.0.0.1:${PORT}/health 响应...`);
  console.log(`⏳ 更新后的首次启动时会花费更久的时间，请耐心等待...`);
  console.log(`⏳ The first launch after an update may take longer, please be patient...`);
  while (retries < MAX_RETRIES) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/health`);
      if (response.ok) {
        console.log('✨ 后端健康检查通过！');
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('backend-ready', { port: PORT });
        }
        return;
      }
    } catch (err) {
      retries++;
      await new Promise(resolve => setTimeout(resolve, RETRY_INTERVAL));
    }
  }
  throw new Error('后端已启动但健康检查响应超时');
}
// 通用下载处理函数
function handleDownloadItem(event, item, webContents) {
  // ✅ 修复：直接使用全局定义的 mainWindow，而不是 getAllWindows()[0]
  if (!mainWindow || mainWindow.isDestroyed()) {
      console.log('主窗口不存在或已销毁，无法发送下载状态');
      return;
  }
  const win = mainWindow;

  const downloadId = Date.now().toString();
  
  // 放入 Map 管理 (你原来写好的逻辑)
  activeDownloads.set(downloadId, item);

  const fileName = item.getFilename();
  const filePath = item.getSavePath();

  // 1. 发送开始事件
  win.webContents.send('download-started', {
      id: downloadId,
      filename: fileName,
      totalBytes: item.getTotalBytes(),
      path: filePath
  });

  // 2. 监听状态更新
  item.on('updated', (event, state) => {
      if (state === 'interrupted') {
          win.webContents.send('download-updated', { id: downloadId, state: 'interrupted' });
      } else if (state === 'progressing') {
          if (item.isPaused()) {
              win.webContents.send('download-updated', { id: downloadId, state: 'paused' });
          } else {
              win.webContents.send('download-updated', {
                  id: downloadId,
                  state: 'progressing',
                  receivedBytes: item.getReceivedBytes(),
                  totalBytes: item.getTotalBytes(),
                  progress: item.getTotalBytes() > 0 ? item.getReceivedBytes() / item.getTotalBytes() : 0
              });
          }
      }
  });

  // 3. 监听完成
  item.once('done', (event, state) => {
      win.webContents.send('download-done', {
          id: downloadId,
          state: state,
          path: item.getSavePath()
      });
      // 下载完成，移除引用
      activeDownloads.delete(downloadId);
  });
}

// 2. 修改监听函数，同时监听两个会话
function setupDownloadListener(win) {
    
    // A. 监听主窗口默认会话 (用于应用自身的下载)
    win.webContents.session.on('will-download', (event, item, webContents) => {
        handleDownloadItem(win, event, item, webContents);
    });

    // B. ★★★ 关键修复：监听 Webview 的隔离会话 ★★★
    // 这里的字符串必须和你 HTML 里 <webview partition="..."> 的值一模一样！
    // 你之前的代码里写的是 'persist:party-browser-session'
    const webviewSession = session.fromPartition('persist:party-browser-session');
    
    webviewSession.on('will-download', (event, item, webContents) => {
        // 让主窗口 (win) 去通知渲染进程
        handleDownloadItem(win, event, item, webContents);
    });
}


// 处理前端发来的控制指令 (暂停/继续/取消)
ipcMain.handle('download-control', (event, { id, action }) => {
  // ★ 同样使用顶部的 activeDownloads
  const item = activeDownloads.get(id);
  
  if (!item) {
    console.log(`未找到下载任务 ID: ${id}`);
    return;
  }

  switch (action) {
    case 'pause':
      if (!item.isPaused()) item.pause();
      break;
    case 'resume':
      if (item.canResume()) item.resume();
      break;
    case 'cancel':
      item.cancel();
      break;
  }
});

// 打开文件所在文件夹
ipcMain.handle('show-item-in-folder', (event, filePath) => {
    if(filePath) shell.showItemInFolder(filePath);
});

// 配置自动更新
function setupAutoUpdater() {
  autoUpdater.autoDownload = false; // 先禁用自动下载
  if (isDev) {
    autoUpdater.on('error', (err) => {
      mainWindow.webContents.send('update-error', err.message);
    });
  }
  autoUpdater.on('update-available', (info) => {
    updateAvailable = true;
    // 显示更新按钮并开始下载
    mainWindow.webContents.send('update-available', info);
    autoUpdater.downloadUpdate(); // 自动开始下载
  });
  autoUpdater.on('download-progress', (progressObj) => {
    mainWindow.webContents.send('download-progress', {
      percent: progressObj.percent.toFixed(1),
      transferred: (progressObj.transferred / 1024 / 1024).toFixed(2),
      total: (progressObj.total / 1024 / 1024).toFixed(2)
    });
  });
  autoUpdater.on('update-downloaded', () => {
    mainWindow.webContents.send('update-downloaded');
  });
}

const PROTOCOL = 'sap';

// --- 1. 尽早获取单实例锁 ---
const gotTheLock = app.requestSingleInstanceLock();

// --- 2. 如果不是第一个实例，直接退出，不要执行任何其他代码 ---
if (!gotTheLock) {
  // 在 Windows 上，第二个实例启动是因为点击了协议链接
  // 我们需要解析参数传给第一个实例，然后立即退出
  const startUrl = process.argv.find(arg => arg.startsWith(`${PROTOCOL}://`));
  if (startUrl) {
    // 这里其实不需要做什么，因为 second-instance 事件会在第一个实例触发
    // 第二个实例直接退出即可
    console.log('Second instance detected with URL:', startUrl);
  }
  app.quit();
  return; // ← 关键：直接返回，阻止后续所有代码执行
}

// --- 3. 只有第一个实例才会执行到这里 ---
let pendingExtensionUrl = null;

// Windows 冷启动处理（第一个实例启动时就带有协议参数）
const startUrl = process.argv.find(arg => arg.startsWith(`${PROTOCOL}://`));
if (startUrl) {
  pendingExtensionUrl = startUrl;
}

app.on('second-instance', (event, commandLine) => {
  // 第二个实例启动时触发，在这里激活第一个实例的窗口并处理 URL
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  }
  
  // 解析命令行参数中的 URL
  const url = commandLine.find(arg => arg.startsWith(`${PROTOCOL}://`));
  handleProtocolUrl(url);
});

// 注册协议（只在第一个实例中执行）
if (process.defaultApp) {
  if (process.argv.length >= 2) {
    app.setAsDefaultProtocolClient(PROTOCOL, process.execPath, [path.resolve(process.argv[1])]);
  }
} else {
  app.setAsDefaultProtocolClient(PROTOCOL);
}

ipcMain.handle('get-window-size', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  return win.getSize();
});
const CHROME_VERSION = '124.0.0.0';
const CHROME_MAJOR = '124';
app.commandLine.appendSwitch('disable-blink-features', 'AutomationControlled');
app.commandLine.appendSwitch('enable-features', 'NetworkService,NetworkServiceInProcess');
app.commandLine.appendSwitch('disable-features', 'CrossOriginOpenerPolicy,SameSiteByDefaultCookies,CookiesWithoutSameSiteMustBeSecure,LogAds');
app.commandLine.appendSwitch('ignore-gpu-blocklist');
// 只有在获得锁（第一个实例）时才执行初始化
app.whenReady().then(async () => {
  try {


    const partySession = session.fromPartition('persist:party-browser-session');

    // 拦截请求头，进行深度伪装
    partySession.webRequest.onBeforeSendHeaders({ urls: ['*://*/*'] }, (details, callback) => {
        const headers = details.requestHeaders;
        
        // 1. 强制 UA
        headers['User-Agent'] = REAL_CHROME_UA;

        // 2. 伪造 Sec-Ch-Ua (Client Hints)
        // 这是 Google 检查的重点
        const brand = `"Chromium";v="${CHROME_MAJOR}", "Google Chrome";v="${CHROME_MAJOR}", "Not-A.Brand";v="99"`;
        headers['Sec-Ch-Ua'] = brand;
        headers['Sec-Ch-Ua-Mobile'] = '?0';
        headers['Sec-Ch-Ua-Full-Version'] = `"${CHROME_VERSION}"`;
        headers['Sec-Ch-Ua-Full-Version-List'] = brand;
        
        // 3. 平台伪装 (根据 process.platform 动态设置)
        let platform = 'Windows';
        if (process.platform === 'darwin') platform = 'macOS';
        else if (process.platform === 'linux') platform = 'Linux';
        headers['Sec-Ch-Ua-Platform'] = `"${platform}"`;

        // 4. 删除 Electron 特征头
        delete headers['Sec-Ch-Ua-Model']; // 桌面端通常没有 Model
        delete headers['Electron-Major-Version'];
        delete headers['X-Electron-App-Name'];

        callback({ requestHeaders: headers });
    });
    app.on('session-created', (sess) => {
        // console.log('发现新 Session 创建:', sess.getUserAgent()); 
        
        // 给每一个新创建的会话（包括 webview 的）都挂上下载监听
        sess.on('will-download', (event, item, webContents) => {
            console.log('捕获到下载请求 (来自 Webview/Session):', item.getFilename());
            handleDownloadItem(event, item, webContents);
        });
    });
    session.defaultSession.on('will-download', (event, item, webContents) => {
        console.log('捕获到下载请求 (来自主窗口):', item.getFilename());
        handleDownloadItem(event, item, webContents);
    });    
      // 默认配置
    global.vmcCfg = {
      receive: { enable: false, port: 39539,syncExpression: false },
      send:    { enable: false, host: '127.0.0.1', port: 39540 }
    };
    ipcMain.handle('get-vmc-config', () => {
      // 保证字段存在，避免 undefined
      global.vmcCfg.receive.syncExpression ??= false;
      return global.vmcCfg;
    });
    // 创建骨架屏窗口
    createSkeletonWindow()
    if (global.vmcCfg.receive.enable) startVMCReceiver(global.vmcCfg);
    // 启动后端服务（现在会自动查找可用端口）
    await startBackend()
    ipcMain.handle('get-backend-logs', () => {
      return logBuffer.join('\n');
    });
    // 等待后端服务准备就绪
    await waitForBackend()
    
    // 后端服务准备就绪后，加载完整内容
    console.log(`Backend server is running at http://${HOST}:${PORT}`)

    if (IS_INTERNAL_MODE_ACTIVE) {
        try {
            // Electron 会将活动端口写入 userData 目录下的 DevToolsActivePort 文件
            const portFile = path.join(app.getPath('userData'), 'DevToolsActivePort');
            
            // 给一点点时间确保文件写入（通常 Ready 时已经有了，为了稳妥可以用个简单的轮询，这里直接读通常没问题）
            // 如果读取失败，尝试等待 500ms
            if (!fs.existsSync(portFile)) {
                await new Promise(r => setTimeout(r, 500));
            }
            
            if (fs.existsSync(portFile)) {
                const content = fs.readFileSync(portFile, 'utf8');
                // 文件格式第一行是端口号，第二行是路径
                const realPort = parseInt(content.split('\n')[0], 10);
                
                if (!isNaN(realPort)) {
                    SESSION_CDP_PORT = realPort;
                    console.log(`✅ [CDP] 成功获取系统分配内置浏览器调试端口: ${SESSION_CDP_PORT}`);
                }
            } else {
                console.error('❌ [CDP] 未找到 DevToolsActivePort 文件，无法获取端口');
            }
        } catch (e) {
            console.error('❌ [CDP] 读取端口文件失败:', e);
        }
    }

    ipcMain.handle('get-app-path', () => {
      return app.getAppPath();
    });

    // 1. 获取 CDP 状态 (前端初始化用)
    ipcMain.handle('get-internal-cdp-info', () => {
      return {
        active: IS_INTERNAL_MODE_ACTIVE,
        port: SESSION_CDP_PORT
      };
    });

    // 3. 处理 Chrome 配置保存 (也是调用 saveEnvVariable)
    // 前端传来的 settings 是一个对象，saveEnvVariable 现在能处理它了
    ipcMain.handle('save-chrome-config', async (event, settings) => {
      saveEnvVariable('chromeMCPSettings', settings);
      return true;
    });

    // 添加获取端口信息的 IPC 处理
    ipcMain.handle('get-server-info', () => {
      return {
        port: PORT,
        defaultPort: DEFAULT_PORT,
        isDefaultPort: PORT === DEFAULT_PORT
      }
    })

    ipcMain.handle('set-env', async (event, arg) => {
      saveEnvVariable(arg.key, arg.value);
    });
    //重启应用
    ipcMain.handle('restart-app', () => {
      app.relaunch();
      app.exit();
    })

    ipcMain.handle('save-screenshot-direct', async (event, { buffer }) => {
      // 1. 确定保存路径: userData/uploaded_files
      // 确保这个路径和 Python 后端挂载的静态目录一致
      const uploadDir = path.join(app.getPath('userData'),'Super-Agent-Party', 'uploaded_files');
      
      // 2. 确保目录存在
      if (!fs.existsSync(uploadDir)) {
        fs.mkdirSync(uploadDir, { recursive: true });
      }

      // 3. 生成文件名
      const filename = `screenshot-${Date.now()}-${Math.random().toString(36).substr(2, 6)}.jpg`;
      const filePath = path.join(uploadDir, filename);

      // 4. 写入文件
      fs.writeFileSync(filePath, Buffer.from(buffer));
      
      // 5. 只返回文件名，由前端拼接 URL
      return filename;
    });

    // 在 main.js 的 app.whenReady().then(async () => { 中添加以下代码

    ipcMain.handle('open-extension-window', async (_, { url, extension }) => {
      const { width, height } = screen.getPrimaryDisplay().workAreaSize;
      
      // 根据扩展配置决定窗口属性
      const windowConfig = {
        width: extension.width || 800,
        height: extension.height || 600,
        webPreferences: {
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: false,
          webSecurity: false,
          webviewTag: true,
          devTools: isDev,
          preload: path.join(__dirname, 'static/js/preload.js')
        }
      };

      // 如果扩展需要透明和无边框
      if (extension.transparent) {
        Object.assign(windowConfig, {
          frame: false,
          transparent: true,
          alwaysOnTop: true,
          skipTaskbar: false,
          hasShadow: false,
          backgroundColor: 'rgba(0, 0, 0, 0)',
        });
      } else {
        // 普通窗口配置
        Object.assign(windowConfig, {
          frame: true,
          transparent: false,
          titleBarStyle: isMac ? 'hiddenInset' : 'default',
          icon: 'static/source/icon.png'
        });
      }

      const extensionWindow = new BrowserWindow(windowConfig);
      
      // 启用远程模块
      remoteMain.enable(extensionWindow.webContents);
      
      // 加载URL
      await extensionWindow.loadURL(url);
      
      // 如果是透明窗口，设置一些特殊行为
      if (extension.transparent) {
        // 可以根据需要设置鼠标穿透等行为
        // extensionWindow.setIgnoreMouseEvents(false);
      }
      
      return extensionWindow.id;
    });


ipcMain.handle('upload-to-workspace', async (event, { targetDirPath, sourceFilePaths }) => {
  try {
    if (!fs.existsSync(targetDirPath)) {
      return { success: false, error: '目标路径不存在' };
    }

    for (const source of sourceFilePaths) {
      const fileName = path.basename(source);
      const destPath = path.join(targetDirPath, fileName);
      
      // 原生同步拷贝（不支持直接拷贝整个文件夹，仅支持文件）
      fs.copyFileSync(source, destPath);
    }
    return { success: true };
  } catch (error) {
    console.error('上传失败:', error);
    return { success: false, error: error.message };
  }
});

    ipcMain.handle('start-vrm-window', async (_, windowConfig = {}) => {
      const { width, height } = screen.getPrimaryDisplay().workAreaSize;

      // 使用传入的配置或默认值
      const windowWidth = windowConfig.width || 540;
      const windowHeight = windowConfig.height || 960;

      const x = windowConfig.x !== undefined ? windowConfig.x : width - windowWidth - 40;
      // 修复：当屏幕高度小于窗口高度时，避免y坐标为负数
      let defaultY;
      if (height >= windowHeight) {
        defaultY = height - windowHeight; // 屏幕够高时，放在底部
      } else {
        defaultY = 0; // 屏幕不够高时，放在顶部，避免窗口超出屏幕
      }
      const y = windowConfig.y !== undefined ? windowConfig.y : defaultY;

      const vrmWindow = new BrowserWindow({
        width: windowWidth,
        height: windowHeight,
        x,
        y,
        transparent: true,
        frame: false,
        alwaysOnTop: true,
        skipTaskbar: true,
        hasShadow: false,
        acceptFirstMouse: true,
        backgroundColor: 'rgba(0, 0, 0, 0)',
        webPreferences: {
          contextIsolation: true,
          nodeIntegration: true,
          enableRemoteModule: true,
          sandbox: false,
          webgl: true,
          devTools: isDev,
          webAudio: true,
          autoplayPolicy: 'no-user-gesture-required',
          preload: path.join(__dirname, 'static/js/preload.js')
        }
      });

      // 加载页面
      await vrmWindow.loadURL(`http://${HOST}:${PORT}/vrm.html`);
      // 默认设置（不穿透，可以交互）
      vrmWindow.setIgnoreMouseEvents(false);
      vrmWindow.setAlwaysOnTop(true);
      // 保存窗口引用
      vrmWindows.push(vrmWindow);

      // 窗口关闭处理
      vrmWindow.on('closed', () => {
        vrmWindows = vrmWindows.filter(w => w !== vrmWindow);
      });

      return vrmWindow.id;  // 可选：返回窗口 ID 用于后续操作
    });
    // 👈 桌面截图
    ipcMain.handle('capture-desktop', async () => {
      const sources = await desktopCapturer.getSources({
        types: ['screen'],
        thumbnailSize: { width: 1920, height: 1080 } // 可按需改
      })
      if (!sources.length) throw new Error('无法获取屏幕源')
      const pngBuffer = sources[0].thumbnail.toPNG() // 返回原生 Buffer
      return pngBuffer // 给渲染进程
    })

    ipcMain.handle('crop-desktop', async (e, { rect }) => {
      const png = await cropDesktop(rect)          // 不管是 sharp 还是 nativeImage
      return png.buffer.slice(png.byteOffset, png.byteOffset + png.byteLength)
    })

    ipcMain.handle('show-screenshot-overlay', async (_, { hideWindow = true } = {}) => {
      // 1. 根据 hideWindow 参数决定是否隐藏主窗口
      if (hideWindow) {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.hide()
      }

      // 2. 创建全屏无框透明窗口
      const { width, height } = screen.getPrimaryDisplay().bounds
      shotOverlay = new BrowserWindow({
        x: 0, y: 0, width, height,
        frame: false, 
        transparent: true, 
        alwaysOnTop: true,
        skipTaskbar: true, 
        resizable: false, 
        movable: false,
        enableLargerThanScreen: true,
        webPreferences: {
          contextIsolation: true,
          preload: path.join(__dirname, 'static/js/shotPreload.js')
        }
      })
      
      shotOverlay.setIgnoreMouseEvents(false)
      shotOverlay.loadFile(path.join(__dirname, 'static/shotOverlay.html'))
      shotOverlay.webContents.on('did-finish-load', () => {
        shotOverlay.webContents.send('set-shot-language', currentLanguage)
      })

      shotOverlay.setVisibleOnAllWorkspaces(true)

      return new Promise((resolve) => {
        ipcMain.once('screenshot-selected', (e, rect) => {
          shotOverlay.close()
          shotOverlay = null
          resolve(rect)
        })
      })
    })

    ipcMain.handle('cancel-screenshot-overlay', () => {
      if (shotOverlay && !shotOverlay.isDestroyed()) {
        shotOverlay.close()
        shotOverlay = null
      }
    })


    // 添加IPC处理器
    ipcMain.handle('set-ignore-mouse-events', (event, ignore, options) => {
        const win = BrowserWindow.fromWebContents(event.sender);
        win.setIgnoreMouseEvents(ignore, options);
    });
    ipcMain.handle('dialog:openDirectory', async () => {
      const { dialog } = require('electron');
      const result = await dialog.showOpenDialog({
        properties: ['openDirectory']
      });
      return result;
    });
    // 添加新的IPC处理器
    ipcMain.handle('get-ignore-mouse-status', (event) => {
        const win = BrowserWindow.fromWebContents(event.sender);
        return win.isIgnoreMouseEvents();
    });
    ipcMain.handle('stop-vrm-window', (_, windowId) => {
      if (windowId !== undefined) {
        const win = vrmWindows.find(w => w.id === windowId);
        if (win && !win.isDestroyed()) {
          win.close();
        }
        vrmWindows = vrmWindows.filter(w => w.id !== windowId);
      } else {
        // 关闭所有窗口
        vrmWindows.forEach(win => {
          if (!win.isDestroyed()) {
            win.close();
          }
        });
        vrmWindows = [];
      }
    });
    // 统一处理下载
    ipcMain.handle('download-file', async (event, payload) => {

      const { url, filename } = payload;   // 这里再解构即可
      const dlItem = await download(mainWindow, url, {
        filename,
        saveAs: true,
        openFolderWhenDone: true
      });
      return { success: true, savePath: dlItem.getSavePath() };
    });
    // 检查更新IPC
    ipcMain.handle('check-for-updates', async () => {
      if (isDev) {
        console.log('Auto updates are disabled in development mode.')
        return { updateAvailable: false }
      }
      try {
        const result = await autoUpdater.checkForUpdates()
        // 只返回必要的可序列化数据
        return {
          updateAvailable: updateAvailable,
          updateInfo: result ? {
            version: result.updateInfo.version,
            releaseDate: result.updateInfo.releaseDate
          } : null
        }
      } catch (error) {
        console.error('检查更新出错:', error)
        return { 
          updateAvailable: false, 
          error: error.message 
        }
      }
    })

    // 下载更新IPC
    ipcMain.handle('download-update', () => {
      if (updateAvailable) {
        return autoUpdater.downloadUpdate()
      }
    })

    // 安装更新IPC
    ipcMain.handle('quit-and-install', () => {
      setTimeout(() => autoUpdater.quitAndInstall(), 500);
    });
            
    // 加载主页面
    await mainWindow.loadURL(`http://${HOST}:${PORT}`)
    ipcMain.on('set-language', (_, lang) => {
      if (lang === 'auto') {
        // 获取系统设置，默认是'en-US'，如果系统语言是中文，则设置为'zh-CN'
        const systemLang = app.getLocale().split('-')[0];
        lang = systemLang === 'zh' ? 'zh-CN' : 'en-US';
      }
      currentLanguage = lang;
      updateTrayMenu();
      updatecontextMenu();
    });
    // 创建系统托盘
    createTray();
    updatecontextMenu();
    // ★ 下面这段就是你要放的「主进程 IPC + 默认配置」
    ipcMain.handle('set-vmc-config', async (_, cfg) => {
      if (cfg.receive.enable) {
        if (!vmcReceiverActive || cfg.receive.port !== global.vmcCfg?.receive.port) {
          if (vmcReceiverActive) stopVMCReceiver();
          startVMCReceiver(cfg);
        }
      } else {
        stopVMCReceiver();
      }
      global.vmcCfg = cfg;
      BrowserWindow.getAllWindows().forEach(w => {
        if (!w.isDestroyed()) w.webContents.send('vmc-config-changed', cfg);
      });
      return { success: true };
    });

    ipcMain.handle('send-vmc-frame', (event, frameData) => {
      if (!global.vmcCfg?.send.enable) return;

      const { host, port } = global.vmcCfg.send;
      const { bones, blends } = frameData;
      const packets = [];

      // 1. 发送 Root (保持之前修正后的归零逻辑)
      packets.push({
        address: '/VMC/Ext/Root/Pos',
        args: [
          { type: 's', value: 'root' },
          { type: 'f', value: 0 }, { type: 'f', value: 0 }, { type: 'f', value: 0 },
          { type: 'f', value: 0 }, { type: 'f', value: 0 }, { type: 'f', value: 0 }, { type: 'f', value: 1 }
        ]
      });

      // 2. 发送骨骼 (★ 核心修复在这里)
      bones.forEach(b => {
        if (b.name === 'root') return;

        // ★ Warudo 强制要求 PascalCase (大驼峰)
        // Three.js 是 "hips", Warudo 要 "Hips"
        // Three.js 是 "leftUpperArm", Warudo 要 "LeftUpperArm"
        const vmcName = b.name.charAt(0).toUpperCase() + b.name.slice(1);

        packets.push({
          address: '/VMC/Ext/Bone/Pos',
          args: [
            { type: 's', value: vmcName },  // <--- 这里用转换后的大写名字
            { type: 'f', value: b.pos.x },
            { type: 'f', value: b.pos.y },
            { type: 'f', value: b.pos.z },
            { type: 'f', value: b.rot.x },
            { type: 'f', value: b.rot.y },
            { type: 'f', value: b.rot.z },
            { type: 'f', value: b.rot.w }
          ]
        });
      });

      // 3. 发送表情 (BlendShape 名字通常也需要对应)
      blends.forEach(blend => {
        // 表情名字我们在 vrm.js 里已经通过映射表转过了(Joy, A, I...), 这里直接用
        packets.push({
          address: '/VMC/Ext/Blend/Val',
          args: [
            { type: 's', value: blend.name },
            { type: 'f', value: blend.weight }
          ]
        });
      });

      // 4. Apply
      if (blends.length > 0) {
        packets.push({ address: '/VMC/Ext/Blend/Apply', args: [] });
      }

      // 5. OK (Warudo 必须)
      packets.push({ 
        address: '/VMC/Ext/OK', 
        args: [{ type: 'i', value: 1 }] 
      });

      // ... 发送逻辑保持不变 ...
      try {
        const bundleBuffer = osc.writePacket({
          timeTag: osc.timeTag(0),
          packets: packets
        });
        vmcSendSocket.send(bundleBuffer, port, host, (err) => {
            if (err) console.error(err);
        });
      } catch (e) { console.error(e); }
    });

    // 窗口控制事件
    ipcMain.handle('window-action', (_, action) => {
      switch (action) {
        case 'show':
          mainWindow.show()
          break
        case 'hide':
          mainWindow.hide()
          break
        case 'minimize':
          mainWindow.minimize()
          break
        case 'maximize':
          mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize()
          break
        case 'close':
          mainWindow.close()
          break
      }
    })
    ipcMain.handle('toggle-window-size', async (event, { width, height }) => {
      const win = BrowserWindow.fromWebContents(event.sender);

      if (win.isMaximized()) {
        // 1. 开始还原
        win.unmaximize();

        if (isMac){
          // 2. 等到连续 50 ms 内尺寸不再变化，才算“真正还原完成”
          let last = win.getNormalBounds();
          for (let i = 0; i < 10; i++) {          // 最多 500 ms
            await new Promise(r => setTimeout(r, 50));
            const curr = win.getNormalBounds();
            if (curr.width === last.width && curr.height === last.height) break;
            last = curr;
          }
        }else {
          // 2. 等窗口“彻底”变成普通状态
          for (let i = 0; i < 20; i++) {          // 最多 1 s
            await new Promise(r => setTimeout(r, 50));
            if (!win.isMaximized()) break;        // 真正退出后即可跳出
          }
        }


        // 3. 现在再改助手尺寸，系统不会再覆盖
        win.setSize(width, height, true);
      } else {
        if (isMac) {
            win.maximize();
        }else{
            win.setSize(width, height, true);
        }
      }
    });

    ipcMain.handle('set-always-on-top', (e, flag) => {
      const win = BrowserWindow.fromWebContents(e.sender);
      win.setAlwaysOnTop(flag, 'screen-saver');
    });
    // 窗口状态同步
    mainWindow.on('maximize', () => {
      mainWindow.webContents.send('window-state', 'maximized')
    })
    mainWindow.on('unmaximize', () => {
      mainWindow.webContents.send('window-state', 'normal')
    })
    
    // 窗口关闭事件处理 - 最小化到托盘而不是退出
    mainWindow.on('close', (event) => {
      if (!app.isQuitting) {
        event.preventDefault()
        mainWindow.hide()
        return false
      }
      return true
    })
    mainWindow.on('resize', () => {
      const size = mainWindow.getSize();
      mainWindow.webContents.send('window-resized', size);
    });

    // ★ 新增：增强型复制函数（同时支持粘贴为图片和粘贴为文件）
    function copyImageToClipboardWithFile(image) {
      try {
        // 1. 保存图片到临时目录
        const tempDir = os.tmpdir();
        // 生成带时间戳的文件名，避免冲突
        const fileName = `image_${Date.now()}.png`;
        const filePath = path.join(tempDir, fileName);
        
        // 将 nativeImage 转换为 buffer 并写入磁盘
        const buffer = image.toPNG();
        fs.writeFileSync(filePath, buffer);

        // 2. 准备剪贴板数据对象
        const clipboardData = {
          image: image, // 写入位图数据 (用于粘贴到聊天框/PS)
        };

        // 3. 根据系统添加文件路径数据 (用于粘贴到文件夹)
        if (process.platform === 'win32') {
          // --- Windows (CF_HDROP) ---
          // 构造 DROPFILES 结构体
          // 结构: offset(4) + pt(8) + fNC(4) + fWide(4) + path(UTF16) + double-null
          const pathBuffer = Buffer.from(filePath, 'ucs2');
          const dropFiles = Buffer.alloc(20 + pathBuffer.length + 4);
          
          dropFiles.writeUInt32LE(20, 0); // pFiles (offset)
          dropFiles.writeUInt32LE(1, 16); // fWide (Unicode flag)
          pathBuffer.copy(dropFiles, 20); // 写入路径
          dropFiles.writeUInt32LE(0, 20 + pathBuffer.length); // 结尾的双 null

          clipboardData['CF_HDROP'] = dropFiles;
          
        } else if (process.platform === 'darwin') {
          // --- macOS (NSFilenamesPboardType) ---
          // 写入 Property List XML
          const plist = `
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
              <array>
                <string>${filePath}</string>
              </array>
            </plist>
          `;
          clipboardData['NSFilenamesPboardType'] = plist;
        }
        // Linux 通常支持 text/uri-list，这里暂从略，如有需要可补充

        // 4. 一次性写入所有格式
        clipboard.write(clipboardData);
        
        console.log(`已复制图片及文件路径: ${filePath}`);

      } catch (err) {
        console.error('增强复制失败，回退到普通复制:', err);
        // 如果出错，至少尝试写入纯图片
        clipboard.writeImage(image);
      }
    }

    // 修改 show-context-menu 的 IPC 处理

    ipcMain.handle('show-context-menu', async (event, { menuType, data }) => {
      let menuTemplate = [];
      const win = BrowserWindow.fromWebContents(event.sender);
      
      // 直接使用 locales[currentLanguage]
      const lang = locales[currentLanguage]; 

      // --- A. 图片菜单 ---
      if (menuType === 'image') {
        menuTemplate = [
          {
            label: lang.openNewTab,
            click: () => {
              win.webContents.send('create-tab', data.src);
            }
          },
          { type: 'separator' },
          {
            label: lang.copyImageLink,
            click: () => clipboard.writeText(data.src)
          },
          {
            label: lang.copyImage,
            click: async () => {
              try {
                if (data.src.startsWith('data:')) {
                  const image = nativeImage.createFromDataURL(data.src);
                  clipboard.writeImage(image);
                } else if (data.src.startsWith('http')) {
                  const response = await fetch(data.src);
                  const blob = await response.blob();
                  const buffer = await blob.arrayBuffer();
                  const image = nativeImage.createFromBuffer(Buffer.from(buffer));
                  clipboard.writeImage(image);
                } else {
                  const image = nativeImage.createFromPath(data.src);
                  clipboard.writeImage(image);
                }
              } catch (error) {
                console.error('复制图片失败:', error);
              }
            }
          },
          {
            label: lang.saveImageAs,
            click: async () => {
              try {
                let buffer = null;
                let defaultExtension = 'png';

                if (data.src.startsWith('data:')) {
                  const image = nativeImage.createFromDataURL(data.src);
                  buffer = image.toPNG();
                } else if (data.src.startsWith('http')) {
                  const response = await fetch(data.src);
                  const blob = await response.blob();
                  buffer = Buffer.from(await blob.arrayBuffer());
                  const lowerSrc = data.src.toLowerCase();
                  if (lowerSrc.endsWith('.jpg') || lowerSrc.endsWith('.jpeg')) defaultExtension = 'jpg';
                  else if (lowerSrc.endsWith('.gif')) defaultExtension = 'gif';
                  else if (lowerSrc.endsWith('.webp')) defaultExtension = 'webp';
                } else {
                  buffer = fs.readFileSync(data.src);
                  defaultExtension = path.extname(data.src).replace('.', '') || 'png';
                }

                const { filePath } = await dialog.showSaveDialog(win, {
                  title: lang.saveImageAs,
                  defaultPath: `image_${Date.now()}.${defaultExtension}`,
                  filters: [
                    { name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp'] },
                    { name: 'All Files', extensions: ['*'] }
                  ]
                });

                if (filePath) {
                  fs.writeFileSync(filePath, buffer);
                }
              } catch (error) {
                console.error('图片另存为失败:', error);
                dialog.showErrorBox('保存失败', '无法保存该图片: ' + error.message);
              }
            }
          }
        ];
      } 
      // --- B. 链接菜单 ---
      else if (menuType === 'link') {
        menuTemplate = [
          {
            label: lang.openNewTab,
            click: () => {
              win.webContents.send('create-tab', data.url);
            }
          },
          { type: 'separator' },
          {
            label: lang.copyLink,
            click: () => clipboard.writeText(data.url)
          },
          {
            label: lang.copyLinkText,
            click: () => clipboard.writeText(data.text || '')
          }
        ];
      }
      // --- C. 纯文本/选区菜单 ---
      else if (menuType === 'text') {
        menuTemplate = [
          { label: lang.copy, role: 'copy' },
          { 
            label: `Search "${data.text.length > 15 ? data.text.slice(0, 15) + '...' : data.text}"`,
            click: () => {
               win.webContents.send('trigger-search', `Search "${data.text}"`);
            } 
          },
          { type: 'separator' },
          { label: lang.selectAll, role: 'selectAll' }
        ];
      }
      // --- D. 默认/空白处菜单 ---
      else {
        menuTemplate = [
          { label: lang.cut, role: 'cut' },
          { label: lang.copy, role: 'copy' },
          { label: lang.paste, role: 'paste' },
          { type: 'separator' },
          { label: lang.selectAll, role: 'selectAll' }
        ];
      }

      // --- E. 开发模式下添加检查元素 ---
      if (isDev) {
        menuTemplate.push({ type: 'separator' });
        menuTemplate.push({
          label: lang.inspect,
          click: () => {
            win.webContents.openDevTools({ mode: 'detach' });
          }
        });
      }

      menu = Menu.buildFromTemplate(menuTemplate);
      menu.popup({ window: win });
    });

    // 监听关闭事件
    ipcMain.handle('request-stop-qqbot', async (event) => {
      const win = BrowserWindow.getAllWindows()[0]; // 获取主窗口
      if (win && !win.isDestroyed()) {
        // 通过webContents执行渲染进程方法
        await win.webContents.executeJavaScript(`
          window.stopQQBotHandler && window.stopQQBotHandler()
        `);
      }
    });
    ipcMain.handle('request-stop-feishubot', async (event) => {
      const win = BrowserWindow.getAllWindows()[0]; // 获取主窗口
      if (win && !win.isDestroyed()) {
        // 通过webContents执行渲染进程方法
        await win.webContents.executeJavaScript(`
          window.stopFeishuBotHandler && window.stopFeishuBotHandler()
        `);
      }
    });
    ipcMain.handle('request-stop-wechatbot', async (event) => {
      const win = BrowserWindow.getAllWindows()[0]; // 获取主窗口
      if (win && !win.isDestroyed()) {
        // 通过webContents执行渲染进程方法
        await win.webContents.executeJavaScript(`
          window.stopWechatBotHandler && window.stopWechatBotHandler()
        `);
      }
    });
    ipcMain.handle('request-stop-wecombot', async (event) => {
      const win = BrowserWindow.getAllWindows()[0]; // 获取主窗口
      if (win && !win.isDestroyed()) {
        // 通过webContents执行渲染进程方法
        await win.webContents.executeJavaScript(`
          window.stopWeComBotHandler && window.stopWeComBotHandler()
        `);
      }
    });
    ipcMain.handle('request-stop-dingtalk', async (event) => {
      const win = BrowserWindow.getAllWindows()[0];
      if (win && !win.isDestroyed()) {
        // 执行渲染进程(Vue)中挂载的清理方法
        await win.webContents.executeJavaScript(`
          window.stopDingtalkBotHandler && window.stopDingtalkBotHandler()
        `);
      }
    });
    ipcMain.handle('request-stop-telegrambot', async (event) => {
      const win = BrowserWindow.getAllWindows()[0]; // 获取主窗口
      if (win && !win.isDestroyed()) {
        // 通过webContents执行渲染进程方法
        await win.webContents.executeJavaScript(`
          window.stopTelegramBotHandler && window.stopTelegramBotHandler()
        `);
      }
    });
    ipcMain.handle('request-stop-discordbot', async (event) => {
      const win = BrowserWindow.getAllWindows()[0]; // 获取主窗口
      if (win && !win.isDestroyed()) {
        // 通过webContents执行渲染进程方法
        await win.webContents.executeJavaScript(`
          window.stopDiscordBotHandler && window.stopDiscordBotHandler()
        `);
      }
    });
    ipcMain.handle('request-stop-slackbot', async (event) => {
      const win = BrowserWindow.getAllWindows()[0];
      if (win && !win.isDestroyed()) {
        await win.webContents.executeJavaScript(`
          window.stopSlackBotHandler && window.stopSlackBotHandler()
        `);
      }
    });
    ipcMain.handle('exec-command', (event, command) => {
      return new Promise((resolve, reject) => {
        exec(command, (error, stdout, stderr) => {
          if (error) reject(error);
          else resolve(stdout);
        });
      });
    });
    // 其他IPC处理...
    ipcMain.on('open-external', (event, url) => {
      shell.openExternal(url)
        .then(() => console.log(`Opened ${url} in the default browser.`))
        .catch(err => console.error(`Error opening ${url}:`, err))
    })
    ipcMain.handle('readFile', async (_, path) => {
      return fs.promises.readFile(path);
    });
    // 文件对话框处理器
    ipcMain.handle('open-file-dialog', async (options) => {
      const allAllowed = [...ALLOWED_EXTENSIONS, ...ALLOWED_IMAGE_EXTENSIONS, ...ALLOWED_VIDEO_EXTENSIONS];
      const result = await dialog.showOpenDialog({
        properties: ['openFile', 'multiSelections'],
        filters: [
          { name: locales[currentLanguage].supportedFiles, extensions: allAllowed },
          { name: locales[currentLanguage].allFiles, extensions: ['*'] }
        ]
      })
      return result
    })
    ipcMain.handle('open-image-dialog', async () => {
      const result = await dialog.showOpenDialog({
        properties: ['openFile'],
        filters: [
          { name: locales[currentLanguage].supportedimages, extensions: ALLOWED_IMAGE_EXTENSIONS },
          { name: locales[currentLanguage].allFiles, extensions: ['*'] }
        ]
      })
      // 返回包含文件名和路径的对象数组
      return result
    });
    ipcMain.handle('check-path-exists', (_, path) => {
      return fs.existsSync(path)
    })

  } catch (err) {
    console.error('启动失败:', err)
    if (loadingWindow && !loadingWindow.isDestroyed()) {
      loadingWindow.close()
    }
    dialog.showErrorBox('启动失败', `服务启动失败: ${err.message}`)
    app.quit()
  }


  let currentGlobalKey = null;

  ipcMain.handle('unregister-global-shortcut', () => {
    if (currentGlobalKey) {
      globalShortcut.unregister(currentGlobalKey);
      currentGlobalKey = null;
    }
    return true;
  });

  ipcMain.handle('register-global-shortcut', (event, key) => {
    // 如果之前有注册过的，先注销
    if (currentGlobalKey) {
      globalShortcut.unregister(currentGlobalKey);
    }
    try {
      // 注册新的快捷键
      const success = globalShortcut.register(key, () => {
        // 当全局快捷键被按下时，通知主窗口前端
        BrowserWindow.getAllWindows().forEach(w => {
          if (!w.isDestroyed()) w.webContents.send('global-shortcut-triggered');
        });
      });
      
      if (success) {
        currentGlobalKey = key;
        console.log(`[ASR] 全局快捷键 ${key} 注册成功`);
        return true;
      } else {
        console.warn(`[ASR] 全局快捷键 ${key} 注册失败，可能被系统或其他软件占用`);
        return false;
      }
    } catch (e) {
      console.error('[ASR] 全局快捷键异常:', e);
      return false;
    }
  });
// ================= [新增：工作区文件树后台逻辑] =================
    // 1. 读取目录内容 (懒加载)
    ipcMain.handle('read-directory', async (event, dirPath) => {
      try {
        if (!fs.existsSync(dirPath)) {
          return { success: false, error: 'Directory does not exist' };
        }
        const items = await fs.promises.readdir(dirPath, { withFileTypes: true });
        
        const result = items.map(item => ({
          name: item.name,
          path: path.join(dirPath, item.name),
          isDirectory: item.isDirectory()
        }));

        // 排序规则：文件夹排在前面，按字母顺序排列
        result.sort((a, b) => {
          if (a.isDirectory === b.isDirectory) {
            return a.name.localeCompare(b.name);
          }
          return a.isDirectory ? -1 : 1;
        });

        return { success: true, data: result };
      } catch (error) {
        console.error('读取目录失败:', error);
        return { success: false, error: error.message };
      }
    });

    // 2. 删除文件或文件夹 (移动到回收站以保安全)
    ipcMain.handle('delete-workspace-file', async (event, filePath) => {
      try {
        await shell.trashItem(filePath); // 移动到系统回收站，比 fs.rm 更安全
        return { success: true };
      } catch (error) {
        console.error('删除文件失败:', error);
        return { success: false, error: error.message };
      }
    });
    // ==============================================================

// ================= [新增：实时监听工作区文件变化] =================
ipcMain.handle('start-workspace-watch', (event, dirPath) => {
  console.log(`[Chokidar] 请求开始监听工作区: ${dirPath}`);
  
  if (!fs.existsSync(dirPath)) {
    console.log('[Chokidar] 目录不存在，监听失败');
    return { success: false, error: 'Directory does not exist' };
  }

  // 如果已经有监听器，先关闭
  if (workspaceWatcher) {
    workspaceWatcher.close();
  }

  workspaceWatcher = chokidar.watch(dirPath, {
    ignored: /(^|[\/\\])\..|node_modules/, 
    persistent: true,
    ignoreInitial: true, 
    awaitWriteFinish: {  
      stabilityThreshold: 100,
      pollInterval: 50
    }
  });

  // ⚠️ 关键修复：直接获取应用的主窗口，而不是依赖 event.sender，确保消息绝对能发出去
  const notifyRenderer = (action, filePath) => {
    console.log(`[Chokidar] 检测到文件变化: ${action} -> ${filePath}`);
    const win = BrowserWindow.getAllWindows()[0]; // 获取主窗口
    if (win && !win.isDestroyed()) {
      win.webContents.send('workspace-changed', { action, path: filePath });
    }
  };

  workspaceWatcher
    .on('add', path => notifyRenderer('add', path))
    .on('unlink', path => notifyRenderer('unlink', path))
    .on('addDir', path => notifyRenderer('addDir', path))
    .on('unlinkDir', path => notifyRenderer('unlinkDir', path));

  console.log('[Chokidar] 监听已成功启动');
  return { success: true };
});

ipcMain.handle('stop-workspace-watch', () => {
  if (workspaceWatcher) {
    workspaceWatcher.close();
    workspaceWatcher = null;
    console.log('[Chokidar] 监听已停止');
  }
  return { success: true };
});
// ==============================================================

})

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

// 应用退出处理
app.on('before-quit', async (event) => {
  // 防止重复处理退出事件
  if (isQuitting) return;
  
  // 标记退出状态并阻止默认退出行为 (以便我们执行异步操作)
  isQuitting = true;
  event.preventDefault();
  
  console.log('正在准备退出应用...');

  try {
    const mainWindow = BrowserWindow.getAllWindows()[0];
    
    // 1. 停止前端的机器人 (保留你原有的逻辑)
    if (mainWindow && !mainWindow.isDestroyed()) {
      await mainWindow.webContents.executeJavaScript(`
        if (window.stopQQBotHandler) window.stopQQBotHandler();
        if (window.stopFeishuBotHandler) window.stopFeishuBotHandler();
        if (window.stopWechatBotHandler) window.stopWechatBotHandler();
        if (window.stopDingtalkBotHandler) window.stopDingtalkBotHandler();
        if (window.stopDiscordBotHandler) window.stopDiscordBotHandler();
        if (window.stopTelegramBotHandler) window.stopTelegramBotHandler();
        if (window.stopSlackBotHandler) window.stopSlackBotHandler();
        if (window.stopWeComBotHandler) window.stopWeComBotHandler();
      `);
      // 给前端一点时间清理
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    
    // 2. ★★★ 新增：通知 Python 后端优雅退出 ★★★
    // 只要 PORT 存在，就尝试发送 HTTP 请求
    if (PORT && backendProcess) {
      try {
        console.log('通知后端执行优雅关闭...');
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000); // 2秒超时

        await fetch(`http://${HOST}:${PORT}/sys/shutdown`, { 
          method: 'POST',
          signal: controller.signal
        });
        clearTimeout(timeoutId);

        // 给 Python 1.5 秒的时间去执行 lifespan 中的 node_mgr.stop()
        console.log('等待后端清理资源...');
        await new Promise(resolve => setTimeout(resolve, 1500));
      } catch (err) {
        console.log('后端优雅关闭请求失败或超时 (可能后端已关闭):', err.message);
      }
    }

    // 3. 最后的补刀 (保留你原有的逻辑，作为保险)
    // 如果 Python 还没死透，或者出错了，强制杀死它
    if (backendProcess) {
      console.log('执行强制进程清理...');
      if (process.platform === 'win32') {
        spawn('taskkill', ['/pid', backendProcess.pid, '/f', '/t']);
      } else {
        backendProcess.kill('SIGKILL');
      }
      backendProcess = null;
    }

  } catch (error) {
    console.error('退出时发生错误:', error);
  } finally {
    // 4. 最终退出 Electron
    app.exit(0);
  }
});


// 自动退出处理
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// 处理渲染进程崩溃
app.on('render-process-gone', (event, webContents, details) => {
  console.error('渲染进程崩溃:', details)
  dialog.showErrorBox('应用崩溃', `渲染进程异常: ${details.reason}`)
})

// 处理主进程未捕获异常
process.on('uncaughtException', (err) => {
  console.error('未捕获异常:', err)
  if (loadingWindow && !loadingWindow.isDestroyed()) {
    loadingWindow.close()
  }
  dialog.showErrorBox('致命错误', `未捕获异常: ${err.message}`)
  app.quit()
})

function createTray() {
  const iconPath = path.join(__dirname, 'static/source/icon_tray.png');
  if (!tray) {
    tray = new Tray(iconPath);
    tray.setToolTip('Super Agent Party');
    tray.on('click', () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) {
        if (mainWindow.isMinimized()) mainWindow.restore();
          mainWindow.focus();
        } else {
          mainWindow.show();
        }
      }
    });
  }
  updateTrayMenu();
}
function updateTrayMenu() {
  const contextMenu = Menu.buildFromTemplate([
    {
      label: locales[currentLanguage].show,
      click: () => {
        if (mainWindow) {
          mainWindow.show()
          mainWindow.focus()
        }
      }
    },
    { type: 'separator' },
    {
      label: locales[currentLanguage].exit,
      click: () => {
        app.isQuitting = true
        app.quit()
      }
    }
  ])
  
  tray.setContextMenu(contextMenu);
}

function updatecontextMenu() {
  menu = Menu.buildFromTemplate([
    {
      label: locales[currentLanguage].cut,
      role: 'cut'
    },
    {
      label: locales[currentLanguage].copy,
      role: 'copy'
    },
    {
      label: locales[currentLanguage].paste,
      role: 'paste'
    }
  ]);
}

// app.on('web-contents-created', (e, webContents) => {
//   webContents.on('new-window', (event, url) => {
//   event.preventDefault();
//   shell.openExternal(url);
//   });
// });

app.on('web-contents-created', (event, contents) => {
  // 拦截所有新窗口请求（包括 <webview> 内部的 window.open 和 target="_blank"）
  contents.setWindowOpenHandler((details) => {
    const { url } = details;
    
    // 如果主窗口还在，就通知主窗口里的 Vue 页面去创建新标签
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('create-tab', url);
    }
    
    // 坚决阻止 Electron 创建原生弹窗
    return { action: 'deny' };
  });

  // (保留你原有的代码：拦截侧键后退等)
  contents.on('input-event', (_ev, input) => {
    if (input.type === 'mouseDown' && (input.button === 3 || input.button === 4)) {
      contents.stopNavigation();
    }
  });
  contents.on('before-input-event', (_ev, input) => {
    const { alt, key } = input;
    if (alt && (key === 'Left' || key === 'Right')) {
      input.preventDefault = true;
    }
  });
});
app.commandLine.appendSwitch('disable-http-cache');

// --- [修改后的 3] 协议处理核心函数 & IPC ---

// 处理 URL 的逻辑
// main.js 里的 handleProtocolUrl 关键部分
function handleProtocolUrl(url) {
  if (!url) return;
  try {
    const urlObj = new URL(url);
    if (urlObj.hostname === 'install') {
      const type = urlObj.searchParams.get('type'); // 'mcp'
      const repo = urlObj.searchParams.get('repo');
      const mcpType = urlObj.searchParams.get('mcpType'); // 'stdio' / 'sse'
      const config = urlObj.searchParams.get('config'); // JSON 字符串

      if (repo || config) {
        const payload = { type, repo, mcpType, config };
        if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isLoading()) {
          mainWindow.webContents.send('remote-install-any', payload); 
        } else {
          pendingExtensionUrl = url; 
        }
      }
    }
  } catch (e) { console.error('协议解析失败:', e); }
}

// 对应的 check-pending-install 也要改
ipcMain.handle('check-pending-install', () => {
  if (pendingExtensionUrl) {
    try {
      const urlObj = new URL(pendingExtensionUrl);
      const res = {
        type: urlObj.searchParams.get('type'),
        repo: urlObj.searchParams.get('repo'),
        config: urlObj.searchParams.get('config'),
        mcpType: urlObj.searchParams.get('mcpType') // 新增
      };
      pendingExtensionUrl = null;
      return res;
    } catch (e) { return null; }
  }
  return null;
});

// macOS 监听 (Mac 下点击链接触发这里)
app.on('open-url', (event, url) => {
  event.preventDefault();
  handleProtocolUrl(url);
});