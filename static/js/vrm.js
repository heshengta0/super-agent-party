const urlParams = new URLSearchParams(window.location.search);
const isRenderMode = urlParams.get('mode') === 'render'; // 是否是渲染模式（OBS采集用）
// --- 全景渲染专用变量 ---
let cubeCamera, cubeRenderTarget, panoMesh, panoCamera, panoShaderMaterial;
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PointerLockControls } from 'three/addons/controls/PointerLockControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { createVRMAnimationClip, VRMAnimationLoaderPlugin } from '@pixiv/three-vrm-animation';
import { SplatMesh } from '@sparkjsdev/spark';
let isVRM1 = true;
let currentMixer = null;
let idleAction = null;
let breathAction = null;
let blinkAction = null;

// 鼠标悬停自动隐藏模型相关变量
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let isAutoHideEnabled = false;           // 自动隐藏功能开关
let isModelHiddenByHover = false;        // 模型当前是否因悬停而隐藏
let hoverCheckTimeout = null;            // 防抖定时器
let mixerTimeScaleBeforeHide = 1;        // 隐藏前的动画速度
let animationsPausedForHide = false;     // 标记是否因隐藏暂停动画
const HOVER_CHECK_INTERVAL = 33;         // 检测间隔（毫秒），约30fps
const FADE_DURATION = 120;               // 渐变动画时长（毫秒），缩短以降低残留
let hideTransitionTimer = null;          // 记录隐藏计时器，避免交叉覆盖

// 在隐藏期间暂停/恢复动画播放，避免隐藏时继续消耗资源
function pauseModelAnimationsForHide() {
    if (!animationsPausedForHide && currentMixer) {
        mixerTimeScaleBeforeHide = currentMixer.timeScale ?? 1;
        currentMixer.timeScale = 0;
        animationsPausedForHide = true;
    }
}

function resumeModelAnimationsAfterHide() {
    if (animationsPausedForHide && currentMixer) {
        currentMixer.timeScale = mixerTimeScaleBeforeHide || 1;
    }
    animationsPausedForHide = false;
}

// renderer
// 检测运行环境
const isElectron = typeof require !== 'undefined' || navigator.userAgent.includes('Electron');

// 根据环境添加 class
document.body.classList.add(isElectron ? 'electron' : 'web');

// 优化渲染器设置
const renderer = new THREE.WebGLRenderer();
// 添加性能优化设置
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.max(1, window.devicePixelRatio));
renderer.setClearColor(0x00000000, 0);
renderer.xr.enabled = true;
// 用fetch查询/cur_language的值
async function fetchLanguage() {
    try {
        const http_protocol = window.location.protocol;
        const HOST = window.location.host;
        let res = await fetch(`${http_protocol}//${HOST}/cur_language`);
        const data = await res.json();
        return data.language;
    } catch (error) {
        console.error('Error fetching language:', error);
        return 'zh-CN';
    }
}
async function t(key) {
    const currentLanguage = await fetchLanguage();
    return translations[currentLanguage][key] || key;
}
// 用fetch查询/cur_language的值
async function fetchVRMConfig() {
    try {
        const http_protocol = window.location.protocol;
        const HOST = window.location.host;
        let res = await fetch(`${http_protocol}//${HOST}/vrm_config`);
        const data = await res.json();
        if(data.VRMConfig.name != 'default'){
            data.VRMConfig.selectedModelId = data.VRMConfig.selectedNewModelId;
            data.VRMConfig.selectedMotionIds = data.VRMConfig.selectedNewMotionIds;
        }
        if (data.VRMConfig.selectedGaussSceneId == ''){
            data.VRMConfig.selectedGaussSceneId = 'transparent';
        }
        console.log(data.VRMConfig);
        return data.VRMConfig;
    } catch (error) {
        console.error('Error fetching VRMConfig:', error);
        return   {
            name: 'default',
            enabledExpressions: false,
            enabledMotions: false,
            selectedModelId: 'alice', // 默认选择Alice模型
            defaultModels: [], // 存储默认模型
            userModels: [],     // 存储用户上传的模型
            defaultMotions: [], // 存储默认动作
            userMotions: [],     // 存储用户上传的动作
            selectedMotionIds: [],
            gaussDefaultScenes: [],   // GAUSS
            gaussUserScenes: [],      // GAUSS
            selectedGaussSceneId: 'transparent',
        };
    }
}
const modelConfig = await fetchVRMConfig();

// ==========================================
// NEW: Initialize Motion Map for ID Lookup
// ==========================================
const motionUrlMap = new Map();

function initMotionMap(config) {
    motionUrlMap.clear();
    const allMotions = [...(config.defaultMotions || []), ...(config.userMotions || [])];
    
    allMotions.forEach(motion => {
        if (motion.path) {
            try {
                let motionUrl = new URL(motion.path, window.location.origin);
                motionUrl.protocol = window.location.protocol;
                motionUrl.host = window.location.host;
                const finalUrl = motionUrl.toString();

                // 1. 使用 ID 绑定（保证唯一性，用于系统内部逻辑）
                if (motion.id) {
                    motionUrlMap.set(motion.id, finalUrl);
                }

                // 2. 关键：使用显示名称（Name）绑定（用于 AI 语义化调用）
                // 这样 AI 如果说 "点点头"，只要 display_name 是 "点点头"，就能匹配上
                if (motion.name) {
                    // 如果名字里有空格或特殊字符，AI 可能会处理得不一致，可以考虑转小写或去空格
                    motionUrlMap.set(motion.name, finalUrl);
                    
                    // 额外兼容：去掉后缀的名字（防止 AI 带了 .vrma 后缀）
                    const nameWithoutExt = motion.name.replace(/\.[^/.]+$/, "");
                    if (nameWithoutExt !== motion.name) {
                        motionUrlMap.set(nameWithoutExt, finalUrl);
                    }
                }
            } catch (e) {
                console.warn(`[MotionMap] 解析路径失败: ${motion.name}`, e);
            }
        }
    });
    console.log("Motion ID & Name Map Initialized. ", motionUrlMap);
}
initMotionMap(modelConfig);
// ==========================================

const windowName = modelConfig.name;
async function getVRMpath() {
    const vrmConfig = await fetchVRMConfig();
    const modelId = vrmConfig.selectedModelId;
    const defaultModel = vrmConfig.defaultModels.find(model => model.id === modelId) || vrmConfig.userModels.find(model => model.id === modelId);
    if (defaultModel) {
        // 替换defaultModel.path中的protocol和host
        let defaultModelURL = new URL(defaultModel.path);
        defaultModelURL.protocol = window.location.protocol;
        defaultModelURL.host = window.location.host;
        return defaultModelURL.toString();
    } else {
        const userModel = vrmConfig.userModels.find(model => model.id === modelId);
        if (userModel) {
            // 替换userModel.path中的protocol和host
            let userModelURL = new URL(userModel.path);
            userModelURL.protocol = window.location.protocol;
            userModelURL.host = window.location.host;
            return userModelURL.toString();
        }
        else {
            return `${window.location.protocol}//${window.location.host}/vrm/Alice.vrm`;
        }
    }
}

async function getVRMname() {
    const vrmConfig = await fetchVRMConfig();
    const modelId = vrmConfig.selectedModelId;
    const defaultModel = vrmConfig.defaultModels.find(model => model.id === modelId) || vrmConfig.userModels.find(model => model.id === modelId);
    if (defaultModel) {
        return defaultModel.name;
    } else {
        const userModel = vrmConfig.userModels.find(model => model.id === modelId);
        if (userModel) {
            return userModel.name;
        }
        else {
            return 'Alice';
        }
    }
}

const vrmPath = await getVRMpath();
console.log(vrmPath);
// 启用阴影（如果需要）
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

document.body.appendChild( renderer.domElement );

// camera
let camera;
if (isRenderMode) {
    // 全景模式：依然需要一个基础相机来驱动 renderer，但核心是 CubeCamera
    camera = new THREE.PerspectiveCamera(30.0, window.innerWidth / window.innerHeight, 0.1, 20.0);
    
    // 初始化立方体渲染目标（分辨率建议 2048 以保证全景清晰度）
    cubeRenderTarget = new THREE.WebGLCubeRenderTarget(2048, {
        format: THREE.RGBAFormat,
        generateMipmaps: true,
        magFilter: THREE.LinearFilter
    });
    cubeCamera = new THREE.CubeCamera(0.1, 1000, cubeRenderTarget);
    // 相机高度设置在角色头部高度 (约1.5m)，位置稍微靠前 (1m) 获得最佳视角
    cubeCamera.position.set(0, 1.5, 1);

    // 全景转换着色器材质：将 6 面体映射为 2:1 平面
    panoShaderMaterial = new THREE.ShaderMaterial({
        uniforms: {
            tCube: { value: cubeRenderTarget.texture }
        },
        vertexShader: `
            varying vec2 vUv;
            void main() {
                vUv = uv;
                gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
            }
        `,

        fragmentShader: `
            varying vec2 vUv;
            uniform samplerCube tCube;
            #define PI 3.141592653589793

            void main() {
                // --- 关键修改：将 UV.x 映射从 [0, 1] 改变偏移量 ---
                // 原来是: vUv.x * 2.0 * PI - PI
                // 修改为直接乘 2PI，这样 0.5 (中心) 对应的就是 PI (正前方 -Z)
                float longitude = vUv.x * 2.0 * PI; 
                
                float latitude = vUv.y * PI - PI / 2.0;

                vec3 dir;
                dir.x = cos(latitude) * sin(longitude);
                dir.y = sin(latitude);
                dir.z = cos(latitude) * cos(longitude);

                gl_FragColor = textureCube(tCube, dir);
            }
        `,
        side: THREE.DoubleSide
    });

    // 创建全屏覆盖平面和正交相机
    panoMesh = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), panoShaderMaterial);
    panoCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
} else {
    // 普通模式：维持原代码
    camera = new THREE.PerspectiveCamera(30.0, window.innerWidth / window.innerHeight, 0.1, 20.0);
}

camera.position.set( 0.0, 1.0, 4.0 );
camera.far = 1000; 
camera.updateProjectionMatrix();

// camera controls
const controls = new OrbitControls( camera, renderer.domElement );
let controlsEnabledBeforeAutoHide = true;   
controls.screenSpacePanning = true;
controls.target.set( 0.0, 1.0, 0.0 );
controls.update();

// scene
const scene = new THREE.Scene();

// light
const light = new THREE.DirectionalLight( 0xffffff, Math.PI );
light.position.set( 1, 3, 2 ).normalize();
light.castShadow = true;                       // 关键
light.shadow.mapSize.set( 2048, 2048 );        // 精度

// 让阴影相机覆盖角色附近区域（根据你的场景大小调）
const camSize = 4;
light.shadow.camera.left   = -camSize;
light.shadow.camera.right  =  camSize;
light.shadow.camera.top    =  camSize;
light.shadow.camera.bottom = -camSize;
light.shadow.camera.near   = 0.1;
light.shadow.camera.far    = 20;
scene.add( light );

const transformControl = new TransformControls( camera, renderer.domElement );
transformControl.addEventListener('change', () => {
    const obj = transformControl.object;
    if (transformControl.getMode() === 'scale' && obj) {
        
        // 获取当前用户正在拖动的轴 (X, Y, Z)
        const axis = transformControl.axis; 
        
        // 如果用户点击的是中心点或平面，axis 可能是 'XYZ' 或 'XY' 等
        // 我们只处理单轴拖动的情况来实现强制等比例
        let s = obj.scale.x; // 默认取值

        if (axis === 'X') {
            s = obj.scale.x;
        } else if (axis === 'Y') {
            s = obj.scale.y;
        } else if (axis === 'Z') {
            s = obj.scale.z;
        } else {
            // 如果是中心缩放 (XYZ)，原本就是等比例的，不需要处理
            return;
        }

        // 检查是否已经相等，避免多余的赋值操作
        if (obj.scale.y !== s || obj.scale.z !== s || obj.scale.x !== s) {
            obj.scale.set(s, s, s);
        }
    }
});
// 当用户拖拽模型时，禁用轨道控制器（OrbitControls），防止相机乱转
transformControl.addEventListener( 'dragging-changed', function ( event ) {
    controls.enabled = ! event.value;
});

// 默认设为 'translate' (移动模式)，也可以是 'rotate' 或 'scale'
transformControl.setMode('translate'); 

scene.add( transformControl.getHelper() ); // 添加辅助线

let currentSceneGroup = null;          // 当前场景根节点，方便整体卸载

/* 拉一次配置即可，外面已经 await fetchVRMConfig() 了，直接复用 */
async function loadGaussScene() {
    /* ---------- 1. 读配置 ---------- */
    const cfg        = await fetchVRMConfig();
    const sceneId    = cfg.selectedGaussSceneId;
    const defaultArr = cfg.gaussDefaultScenes || [];
    const userArr    = cfg.gaussUserScenes    || [];

    /* ---------- 2. 拼 URL ---------- */
    let sceneURL = null;
    if (sceneId === 'transparent') {
        /* 透明场景 -> 不下载 spz */
        sceneURL = 'transparent';
    } else {
        const hit = [...defaultArr, ...userArr].find(s => s.id === sceneId);
        if (!hit) {
            console.warn(`[SceneLoader] 找不到 id=${sceneId} 的场景，回退到 transparent`);
            sceneURL = 'transparent';
        } else {
            // 把相对 path 拼成绝对地址
            const url = new URL(hit.path);
            url.protocol = window.location.protocol;
            url.host     = window.location.host;
            sceneURL     = url.toString();
        }
    }

    /* ---------- 3. 卸载旧场景 ---------- */
    if (currentSceneGroup) {
        scene.remove(currentSceneGroup);
        currentSceneGroup.traverse(o => {
            if (o.dispose) o.dispose();      // SplatMesh 自带 dispose
        });
        currentSceneGroup = null;
    }

    /* ---------- 4. 构建新场景 ---------- */
    const group = new THREE.Group();
    group.name = `gaussScene_${sceneId}`;

    if (sceneURL === 'transparent') {
        /* ------ 4.1 透明阴影地面 ------ */
        const groundGeo = new THREE.PlaneGeometry(20, 20);
        const shadowMat = new THREE.ShadowMaterial({ opacity: 0.4 });
        const ground    = new THREE.Mesh(groundGeo, shadowMat);
        ground.rotation.x = -Math.PI / 2;
        ground.receiveShadow = true;
        group.add(ground);
    } else {
        /* ------ 4.2 加载 .spz ------ */
        const splat = new SplatMesh({ url: sceneURL });
        let splat_height = 0;
        let splat_scale = 2;
        if (sceneId === 'space') {
            splat_height = 1.55;
        }else if (sceneId === 'home') {
            splat_height = 1.6;
        }else if (sceneId === 'sea') {
            splat_height = 2.4;
            splat_scale = 4;
        }
        // 统一先缩放/位移到脚底中心，具体数值可按模型微调
        splat.quaternion.set(1, 0, 0, 0);
        splat.position.set(0, splat_height, 2);
        splat.scale.set(splat_scale, splat_scale, splat_scale);
        splat.receiveShadow = true;
        group.add(splat);
    }

    /* ---------- 5. 挂到场景 ---------- */
    scene.add(group);
    currentSceneGroup = group;
    console.log(`[SceneLoader] 场景 ${sceneId} 加载完成`);
}

/* ------------------------------------------------------------------ */
/* 初始化时调用一次                                                    */
/* ------------------------------------------------------------------ */
await loadGaussScene();


// lookat target
const lookAtTarget = new THREE.Object3D();
camera.add( lookAtTarget );

// 添加环境光，让整体更柔和
const ambientLight = new THREE.AmbientLight( 0xffffff, 0.1 );
scene.add( ambientLight );

// gltf and vrm
let currentVrm = undefined;
let currentVrmWrapper = new THREE.Group(); // 新增：用于包裹 VRM 的组
scene.add(currentVrmWrapper);              // 新增：一开始就加入场景
const loader = new GLTFLoader();
loader.crossOrigin = 'anonymous';

// ---------------- 新增：解决部分模型 SpringBone 配置不规范导致的解析崩溃 ----------------
loader.register((parser) => {
    return {
        name: 'VRMSpringBoneBugFixPlugin',
        beforeRoot: () => {
            const json = parser.json;
            if (!json || !json.extensions) return;

            // 修复 VRM 1.0 SpringBone
            if (json.extensions.VRMC_springBone) {
                const sb = json.extensions.VRMC_springBone;
                if (!sb.springs) sb.springs = [];
                if (!sb.colliders) sb.colliders = [];
                if (!sb.colliderGroups) sb.colliderGroups = [];
                
                sb.springs.forEach(spring => {
                    if (spring) {
                        if (!spring.joints) spring.joints = [];
                        if (!spring.colliderGroups) spring.colliderGroups = [];
                    }
                });
                
                sb.colliderGroups.forEach(group => {
                    if (group && !group.colliders) group.colliders = [];
                });
            }

            // 修复 VRM 0.0 动骨 (SecondaryAnimation)
            if (json.extensions.VRM && json.extensions.VRM.secondaryAnimation) {
                const sa = json.extensions.VRM.secondaryAnimation;
                if (!sa.boneGroups) sa.boneGroups = [];
                if (!sa.colliderGroups) sa.colliderGroups = [];
                
                sa.boneGroups.forEach(group => {
                    if (group) {
                        if (!group.bones) group.bones = [];
                        if (!group.colliderGroups) group.colliderGroups = [];
                    }
                });
                
                sa.colliderGroups.forEach(group => {
                    if (group && !group.colliders) group.colliders = [];
                });
            }
        }
    };
});
// -----------------------------------------------------------------------------------------

loader.register( ( parser ) => {

    return new VRMLoaderPlugin(parser); 

} );

loader.register( ( parser ) => {
    return new VRMAnimationLoaderPlugin( parser );
} );

// 设置自然姿势的函数
function setNaturalPose(vrm) {
    if (!vrm.humanoid) return;
    let v = 1;
    if (!isVRM1){
        v = -1;
    }
    // 1. 调整双臂：将 0.4 改为 0.45 让手臂更贴近身体，并增加 x 轴让手臂微向前倾，更放松
    const leftArm = vrm.humanoid.getNormalizedBoneNode('leftUpperArm');
    if (leftArm) {
        leftArm.rotation.z = -0.45 * Math.PI * v; 
        leftArm.rotation.x = 0.05; // 手臂微微前倾
    }

    const rightArm = vrm.humanoid.getNormalizedBoneNode('rightUpperArm');
    if (rightArm) {
        rightArm.rotation.z = 0.45 * Math.PI * v;
        rightArm.rotation.x = 0.05; // 手臂微微前倾
    }
    
    // 手腕保持原来的逻辑
    const leftHand = vrm.humanoid.getNormalizedBoneNode('leftHand');
    if (leftHand) {
        leftHand.rotation.z = 0.1 * v; // 手腕自然弯曲
        leftHand.rotation.x = 0.05;
    }
    const rightHand = vrm.humanoid.getNormalizedBoneNode('rightHand');
    if (rightHand) {
        rightHand.rotation.z = -0.1 * v; // 手腕自然弯曲
        rightHand.rotation.x = 0.05;
    }

    // 添加手指的自然弯曲（如果模型支持）
    const fingerBones = [
        'leftThumbProximal', 'leftThumbIntermediate', 'leftThumbDistal',
        'leftIndexProximal', 'leftIndexIntermediate', 'leftIndexDistal',
        'leftMiddleProximal', 'leftMiddleIntermediate', 'leftMiddleDistal',
        'leftRingProximal', 'leftRingIntermediate', 'leftRingDistal',
        'leftLittleProximal', 'leftLittleIntermediate', 'leftLittleDistal',
        'rightThumbProximal', 'rightThumbIntermediate', 'rightThumbDistal',
        'rightIndexProximal', 'rightIndexIntermediate', 'rightIndexDistal',
        'rightMiddleProximal', 'rightMiddleIntermediate', 'rightMiddleDistal',
        'rightRingProximal', 'rightRingIntermediate', 'rightRingDistal',
        'rightLittleProximal', 'rightLittleIntermediate', 'rightLittleDistal'
    ];

    fingerBones.forEach(boneName => {
        const bone = vrm.humanoid.getNormalizedBoneNode(boneName);
        if (bone) {
            // 根据手指部位设置不同的弯曲度
            if (boneName.includes('Thumb')) {
                // 拇指稍微向内
                bone.rotation.y = boneName.includes('left') ? 0.35 : -0.35;
            } else if (boneName.includes('Proximal')) {
                // 近端指骨轻微弯曲
                bone.rotation.z = boneName.includes('left') ? -0.35 * v : 0.35 * v;
            } else if (boneName.includes('Intermediate')) {
                // 中端指骨稍微弯曲
                bone.rotation.z = boneName.includes('left') ? -0.45 * v : 0.45 * v;
            } else if (boneName.includes('Distal')) {
                // 远端指骨轻微弯曲
                bone.rotation.z = boneName.includes('left') ? -0.3 * v : 0.3 * v;
            }
        }
    });
}

// 闲置动作的时间偏移量，让各个动作不同步
const idleOffsets = {
    body: Math.random() * Math.PI * 2,
    leftArm: Math.random() * Math.PI * 2,
    rightArm: Math.random() * Math.PI * 2,
    head: Math.random() * Math.PI * 2,
    spine: Math.random() * Math.PI * 2
};

// 在全局变量区域添加 - 改进后的闲置动画管理
let idleAnimations = [];
let currentIdleAnimationIndex = 0;
let idleAnimationAction = null;
let isLoadingAnimations = false;
let idleAnimationManager = null; // 新的闲置动画管理器
let defaultPoseAction = null; // 默认姿势动作
let useVRMAIdleAnimations = true; // 是否使用VRM-A的闲置动画
let isIdleAnimationModeChanging = false; // 防止重复切换


// 完整的闲置动画管理器类 - 修复版本
class IdleAnimationManager {
    constructor(vrm, mixer) {
        this.vrm = vrm;
        this.mixer = mixer;
        
        // 核心动作引用
        this.currentIdleAction = null;      // VRMA 待机动作
        this.defaultPoseAction = null;      // 默认 T-Pose/A-Pose 复位动作
        this.proceduralIdleAction = null;   // 程序化呼吸/微动动作
        this.currentOneShotAction = null;   // 当前正在播放的一次性动作 (新增)

        // 状态标识
        this.isTransitioning = false;
        this.animationQueue = [];
        this.currentIndex = 0;
        
        // 参数配置
        this.transitionDuration = 0.5; // 标准过渡时间
        this.pauseBetweenAnimations = 1.5;
        this.idleWeight = 1.0; 
        this.isActive = false;
        this.currentMode = 'none'; // 'vrma', 'procedural', 'none'
        
        // 监听器引用，用于发生冲突时移除旧的监听
        this._onOneShotFinished = null; 

        // 初始化基础动作
        this.createDefaultPoseAction();
        this.createProceduralIdleAction();
        
        console.log('IdleAnimationManager initialized (Conflict Fix Version)');
    }

    createDefaultPoseAction() {
        try {
            const defaultPoseClip = this.createDefaultPoseClip();
            this.defaultPoseAction = this.mixer.clipAction(defaultPoseClip);
            this.defaultPoseAction.setLoop(THREE.LoopOnce);
            this.defaultPoseAction.clampWhenFinished = true;
            this.defaultPoseAction.setEffectiveWeight(0);
        } catch (error) {
            console.error('Error creating default pose action:', error);
        }
    }

    createProceduralIdleAction() {
        try {
            const idleClip = createIdleClip(this.vrm);
            if (!idleClip) return;
            this.proceduralIdleAction = this.mixer.clipAction(idleClip);
            this.proceduralIdleAction.setLoop(THREE.LoopRepeat);
            this.proceduralIdleAction.setEffectiveWeight(0); 
        } catch (error) {
            console.error('Error creating procedural idle action:', error);
        }
    }
    
    createDefaultPoseClip() { return super.createDefaultPoseClip ? super.createDefaultPoseClip() : this._createDefaultPoseClipImpl(); }
    _createDefaultPoseClipImpl() {
         const tracks = [];
        const duration = 1.0;
        const fps = 30;
        const frameCount = duration * fps;
        const times = [];
        for (let i = 0; i <= frameCount; i++) times.push(i / fps);

        const bonesToReset = ['hips', 'spine', 'chest', 'neck', 'head', 'leftUpperArm', 'rightUpperArm', 'leftLowerArm', 'rightLowerArm', 'leftHand', 'rightHand', 'leftUpperLeg', 'rightUpperLeg', 'leftLowerLeg', 'rightLowerLeg', 'leftFoot', 'rightFoot']; 
        
        bonesToReset.forEach(boneName => {
            const bone = this.vrm.humanoid.getNormalizedBoneNode(boneName);
            if (!bone) return;
            const naturalRotation = this.getNaturalRotation(boneName);
            const values = [];
            times.forEach((time, index) => {
                if (index === 0) values.push(...bone.quaternion.toArray());
                else {
                    const progress = time / duration;
                    const easedProgress = this.easeInOutCubic(progress);
                    const currentQuat = new THREE.Quaternion().fromArray(values.slice((index - 1) * 4, index * 4));
                    const interpolatedQuat = currentQuat.clone().slerp(naturalRotation, easedProgress);
                    values.push(...interpolatedQuat.toArray());
                }
            });
            tracks.push(new THREE.QuaternionKeyframeTrack(bone.name + '.quaternion', times, values));
        });
        return new THREE.AnimationClip('defaultPose', duration, tracks);
    }
    
    easeInOutCubic(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
    
    getNaturalRotation(boneName) { 
         const euler = new THREE.Euler(0, 0, 0);
         const v = isVRM1 ? 1 : -1;
         
         if(boneName === 'leftUpperArm') {
             euler.set(0.05, 0, -0.45 * Math.PI * v);
         }
         else if(boneName === 'rightUpperArm') {
             euler.set(0.05, 0, 0.45 * Math.PI * v);
         }
         else if(boneName === 'leftHand') {
             euler.set(0.05, 0, 0.1 * v);
         }
         else if(boneName === 'rightHand') {
             euler.set(0.05, 0, -0.1 * v);
         }
         else if(boneName === 'leftUpperLeg') {
             euler.set(0, 0.05 * v, 0.04 * v);
         }
         else if(boneName === 'rightUpperLeg') {
             euler.set(0, -0.05 * v, -0.04 * v);
         }

         const q = new THREE.Quaternion();
         q.setFromEuler(euler);
         return q;
    }
    
    setAnimationQueue(animations) {
        this.animationQueue = [...animations];
        this.currentIndex = 0;
    }

    startIdleLoop() {
        if (this.currentOneShotAction && this.currentOneShotAction.isRunning()) return;
        
        if (this.animationQueue.length === 0) {
            this.switchToProceduralMode();
            return;
        }
        
        this.currentMode = 'vrma';
        this.isActive = true;
        this.playNextVRMAAnimation();
    }
    
    playNextVRMAAnimation() {
        if (!this.isActive || this.currentMode !== 'vrma' || this.animationQueue.length === 0) return;
        if (this.currentOneShotAction && this.currentOneShotAction.isRunning()) return;
        if (this.isTransitioning) return;

        const animation = this.animationQueue[this.currentIndex];
        this.playVRMAAnimation(animation);
        
        const previousIndex = this.currentIndex;
        if (this.animationQueue.length > 1) {
            let newIndex;
            do { newIndex = Math.floor(Math.random() * this.animationQueue.length); } 
            while (newIndex === previousIndex);
            this.currentIndex = newIndex;
        }
    }
    
    playVRMAAnimation(animationData) {
        if (!animationData || !animationData.animation) {
            this.scheduleNextVRMAAnimation();
            return;
        }

        try {
            const clip = createVRMAnimationClip(animationData.animation, this.vrm);
            if (!clip) return;

            if (this.currentIdleAction) {
                this.currentIdleAction.stop();
            }

            this.currentIdleAction = this.mixer.clipAction(clip);
            this.currentIdleAction.setLoop(THREE.LoopOnce);
            this.currentIdleAction.clampWhenFinished = true;
            this.currentIdleAction.reset();
            this.currentIdleAction.setEffectiveWeight(1.0); 
            this.currentIdleAction.play();
            this.currentIdleAction.fadeIn(0.5);

            const onFinished = (event) => {
                if (event.action === this.currentIdleAction) {
                    this.mixer.removeEventListener('finished', onFinished);
                    if (this.currentMode === 'vrma' && !this.currentOneShotAction) {
                        this.onVRMAAnimationFinished();
                    }
                }
            };
            this.mixer.addEventListener('finished', onFinished);

        } catch (error) {
            console.error('Error playing VRMA:', error);
            this.scheduleNextVRMAAnimation();
        }
    }

    onVRMAAnimationFinished() {
        if (this.currentOneShotAction) return; 

        this.isTransitioning = true;
        
        if (this.currentIdleAction) this.currentIdleAction.fadeOut(1.0);
        
        if (this.defaultPoseAction) {
            this.defaultPoseAction.reset().setEffectiveWeight(1.0).play();
            this.defaultPoseAction.fadeIn(0.5);
        }

        setTimeout(() => {
            if (this.currentOneShotAction) { this.isTransitioning = false; return; }
            
            if (this.defaultPoseAction) this.defaultPoseAction.fadeOut(0.5);
            this.isTransitioning = false;
            
            setTimeout(() => {
                if (!this.currentOneShotAction && this.currentMode === 'vrma') {
                    this.playNextVRMAAnimation();
                }
            }, 300);
        }, 1500);
    }

    scheduleNextVRMAAnimation() {
        setTimeout(() => {
            if (!this.currentOneShotAction && this.currentMode === 'vrma') this.playNextVRMAAnimation();
        }, 1000);
    }

    async playOneShotAnimation(url) {
        if (!url) return;
        console.log(`[IdleManager] Requesting One-Shot: ${url}`);

        if (this.currentOneShotAction) {
            if (this._onOneShotFinished) {
                this.mixer.removeEventListener('finished', this._onOneShotFinished);
                this._onOneShotFinished = null;
            }
            this.currentOneShotAction.stop();
            this.currentOneShotAction = null;
        }

        const fadeDuration = 0.3; 
        
        if (this.currentMode === 'vrma' && this.currentIdleAction) {
            this.currentIdleAction.fadeOut(fadeDuration);
        }
        if (this.currentMode === 'procedural' && this.proceduralIdleAction) {
            this.proceduralIdleAction.fadeOut(fadeDuration);
        }
        if (this.defaultPoseAction) {
            this.defaultPoseAction.fadeOut(fadeDuration);
        }

        try {
            const gltf = await new Promise((resolve, reject) => {
                loader.load(url, resolve, undefined, reject);
            });
            const vrmAnimations = gltf.userData.vrmAnimations;
            if (!vrmAnimations || vrmAnimations.length === 0) throw new Error('No VRMA found');
            
            const clip = createVRMAnimationClip(vrmAnimations[0], this.vrm);
            if (!clip) throw new Error('Failed to create clip');

            const action = this.mixer.clipAction(clip);
            action.setLoop(THREE.LoopOnce);
            action.clampWhenFinished = true;
            action.reset();
            action.setEffectiveWeight(1.0);
            action.play();
            action.fadeIn(fadeDuration);

            this.currentOneShotAction = action;

            this._onOneShotFinished = (e) => {
                if (e.action === action) {
                    console.log(`[IdleManager] One-Shot finished: ${url}`);
                    this.mixer.removeEventListener('finished', this._onOneShotFinished);
                    this._onOneShotFinished = null;
                    
                    this.resetToIdle();
                }
            };
            this.mixer.addEventListener('finished', this._onOneShotFinished);

        } catch (err) {
            console.error('[IdleManager] Failed to play one-shot:', err);
            this.resetToIdle(); 
        }
    }

    resetToIdle() {
        console.log('[IdleManager] Resetting to Idle state...');
        const fadeDuration = 0.5;

        if (this.currentOneShotAction) {
            this.currentOneShotAction.fadeOut(fadeDuration);
            const oldAction = this.currentOneShotAction;
            setTimeout(() => {
                oldAction.stop(); 
                if (this.currentOneShotAction === oldAction) {
                    this.currentOneShotAction = null;
                }
            }, fadeDuration * 1000);
        }

        if (useVRMAIdleAnimations) {
            this.switchToVRMAMode(fadeDuration);
        } else {
            this.switchToProceduralMode(fadeDuration);
        }
    }

    switchToVRMAMode(fadeInTime = 0.5) {
        this.stopProceduralAnimations();
        this.currentMode = 'vrma';
        this.isActive = true;

        if (this.animationQueue.length > 0) {
            if (!this.currentIdleAction || !this.currentIdleAction.isRunning()) {
                this.playNextVRMAAnimation();
            } else {
                this.currentIdleAction.enabled = true;
                this.currentIdleAction.setEffectiveWeight(1.0);
                this.currentIdleAction.fadeIn(fadeInTime);
            }
        } else {
            this.switchToProceduralMode();
        }
    }

    switchToProceduralMode(fadeInTime = 0.5) {
        this.stopVRMAAnimations();
        this.currentMode = 'procedural';
        this.isActive = true;

        if (this.proceduralIdleAction) {
            this.proceduralIdleAction.enabled = true;
            this.proceduralIdleAction.reset();
            this.proceduralIdleAction.play();
            this.proceduralIdleAction.setEffectiveWeight(1.0); 
            this.proceduralIdleAction.fadeIn(fadeInTime);
        } else {
            this.createProceduralIdleAction();
            if (this.proceduralIdleAction) this.proceduralIdleAction.play();
        }
    }

    stopVRMAAnimations() {
        if (this.currentIdleAction) this.currentIdleAction.fadeOut(0.5);
        if (this.defaultPoseAction) this.defaultPoseAction.fadeOut(0.5);
    }

    stopProceduralAnimations() {
        if (this.proceduralIdleAction) this.proceduralIdleAction.fadeOut(0.5);
    }

    stopAllAnimations() {
        console.log('Stopping all animations...');
        this.isActive = false;
        if (this.currentIdleAction) this.currentIdleAction.stop();
        if (this.proceduralIdleAction) this.proceduralIdleAction.stop();
        if (this.defaultPoseAction) this.defaultPoseAction.stop();
        if (this.currentOneShotAction) this.currentOneShotAction.stop();
        this.currentMode = 'none';
    }
}

// 切换闲置动画模式
async function toggleIdleAnimationMode() {
    if (isIdleAnimationModeChanging || !idleAnimationManager) {
        return;
    }
    
    isIdleAnimationModeChanging = true;
    useVRMAIdleAnimations = !useVRMAIdleAnimations;
    
    console.log(`Switching idle animation mode to: ${useVRMAIdleAnimations ? 'VRMA' : 'Procedural'}`);
    
    try {
        if (useVRMAIdleAnimations) {
            // 切换到VRMA动画
            if (idleAnimations.length === 0) {
                console.log('Loading VRMA animations...');
                await loadIdleAnimations();
            }
            
            if (idleAnimationManager) {
                idleAnimationManager.setAnimationQueue(idleAnimations);
                idleAnimationManager.switchToVRMAMode();
            }
        } else {
            // 切换到程序化动画
            if (idleAnimationManager) {
                idleAnimationManager.switchToProceduralMode();
            }
        }
        
        // 更新按钮状态
        updateIdleAnimationButton();
        
    } catch (error) {
        console.error('Error switching idle animation mode:', error);
        // 发生错误时回滚状态
        useVRMAIdleAnimations = !useVRMAIdleAnimations;
    } finally {
        isIdleAnimationModeChanging = false;
    }
}

// 更新闲置动画按钮状态
async function updateIdleAnimationButton() {
    const button = document.getElementById('idle-animation-handle');
    if (button) {
        button.style.color = useVRMAIdleAnimations ?  '#ff6b35': '#28a745';
        button.innerHTML = useVRMAIdleAnimations ? 
            '<i class="fas fa-stop"></i>' : 
            '<i class="fas fa-play"></i>';
        button.title = useVRMAIdleAnimations ? 
            await t('UsingVRMAAnimations') || 'Using VRMA Animations' : 
            await t('UsingProceduralAnimations') || 'Using Procedural Animations';
    }
}

// 获取动画目录下的所有VRMA文件
async function getAnimationFiles() {
  try {
    const cfg = await fetchVRMConfig();
    const motionPool = [...(cfg.defaultMotions || []), ...(cfg.userMotions || [])];

    // 取出被选中的动作
    const urls = (cfg.selectedMotionIds || [])
      .map(id => motionPool.find(m => m.id === id))
      .filter(Boolean)
      .map(item => {
        try {
          // 核心修复点：传入 window.location.origin 处理相对路径
          const urlObj = new URL(item.path, window.location.origin);
          urlObj.protocol = window.location.protocol;
          urlObj.host     = window.location.host;
          return urlObj.toString();
        } catch (e) {
          console.error(`[AnimationFiles] 无法构造有效URL: ${item.path}`, e);
          return null;
        }
      })
      .filter(u => u !== null); // 移除无效 URL

    // 如果没有任何选中，返回默认的兜底动画
    if (urls.length === 0) {
      const base = `${window.location.protocol}//${window.location.host}/vrm/animations/`;
      const fallback = [
        "greeting.vrma", "akimbo.vrma", "play_fingers.vrma", "scratch_head.vrma",
        "stretch.vrma", "shoot.vrma", "peace_sign.vrma", "show_full_body.vrma",
        "squat.vrma", "model_pose.vrma", "spin.vrma"
      ].map(file => base + file);
      
      console.warn('没有选中任何有效动作，使用默认目录下的兜底动画');
      return fallback;
    }

    return urls;

  } catch (err) {
    console.error('获取动画列表失败：', err);
    return [`${window.location.protocol}//${window.location.host}/vrm/animations/greeting.vrma`];
  }
}

// 加载VRMA动画文件
async function loadVRMAAnimation(url) {
    return new Promise((resolve, reject) => {
        loader.load(
            url,
            (gltf) => {
                const vrmAnimations = gltf.userData.vrmAnimations;
                if (vrmAnimations && vrmAnimations.length > 0) {
                    resolve(vrmAnimations[0]);
                } else {
                    reject(new Error('No VRM animation found in file'));
                }
            },
            (progress) => {
                console.log(`Loading animation ${url}...`, 100.0 * (progress.loaded / progress.total), '%');
            },
            (error) => {
                console.error(`Error loading animation ${url}:`, error);
                reject(error);
            }
        );
    });
}

// 加载所有闲置动画
async function loadIdleAnimations() {
    if (isLoadingAnimations) return;
    isLoadingAnimations = true;
    
    console.log('Loading idle animations...');
    
    try {
        const animationFiles = await getAnimationFiles();
        idleAnimations = [];
        
        for (const file of animationFiles) {
            try {
                const animation = await loadVRMAAnimation(file);
                idleAnimations.push({
                    animation: animation,
                    file: file,
                    name: file.split('/').pop().replace('.vrma', '')
                });
                console.log(`Loaded animation: ${file}`);
            } catch (error) {
                console.warn(`Failed to load animation: ${file}`, error);
            }
        }
        
        console.log(`Successfully loaded ${idleAnimations.length} idle animations`);
        
    } catch (error) {
        console.error('Error loading idle animations:', error);
    } finally {
        isLoadingAnimations = false;
    }
}

async function startIdleAnimationLoop() {
    if (!idleAnimationManager) {
        console.error('Idle animation manager not available');
        return;
    }
    
    console.log(`Starting idle animation with mode: ${useVRMAIdleAnimations ? 'VRMA' : 'Procedural'}`);
    
    if (useVRMAIdleAnimations) {
        // 使用VRMA动画
        if (idleAnimations.length === 0) {
            console.log('Loading VRMA animations...');
            await loadIdleAnimations();
        }
        
        if (idleAnimations.length > 0) {
            idleAnimationManager.setAnimationQueue(idleAnimations);
            idleAnimationManager.switchToVRMAMode();
        } else {
            console.warn('No VRMA animations available, falling back to procedural');
            idleAnimationManager.switchToProceduralMode();
        }
    } else {
        // 使用程序化动画
        idleAnimationManager.switchToProceduralMode();
    }
}

// 程序化闲置动画（作为备用）
function useProceduralIdleAnimation() {
    if (!currentVrm) return;
    
    const idleClip = createIdleClip(currentVrm);
    idleAction = currentMixer.clipAction(idleClip);
    idleAction.setLoop(THREE.LoopRepeat);
    idleAction.play();
}

// 生成闲置动画 clip - 修复版本
function createIdleClip(vrm) {
    const tracks = [];
    const fps = 30;
    const duration = 600;
    const frameCount = duration * fps;
    
    // 生成时间数组
    const times = [];
    for (let i = 0; i <= frameCount; i++) {
        times.push(i / fps);
    }
    
    // VRM版本检测
    const v = (vrm.meta.metaVersion === '1') ? 1 : -1;
    
    // 需要动画的骨骼列表
    const animatedBones = [
        'spine', 'chest', 'neck', 'head',
        'leftUpperArm', 'leftLowerArm', 'leftHand', 'leftShoulder',
        'rightUpperArm', 'rightLowerArm', 'rightHand', 'rightShoulder'
    ];
    
    animatedBones.forEach(boneName => {
        const bone = vrm.humanoid.getNormalizedBoneNode(boneName);
        if (!bone) return;
        
        const values = [];
        
        // 为每个时间点计算旋转值
        times.forEach(time => {
            let euler = new THREE.Euler(0, 0, 0);
            
            // 使用周期性函数，确保在 t=0 和 t=duration 时值相同
            const cycleTime = (time / duration) * 200 * Math.PI; // 0 到 2π
            
            switch (boneName) {
                case 'spine':
                    euler.set(
                        Math.sin(cycleTime * 0.6 + idleOffsets.body) * 0.02,     
                        0,                                                    
                        Math.cos(cycleTime * 0.5 + idleOffsets.body) * 0.015    
                    );
                    break;
                    
                case 'chest':
                    euler.set(
                        Math.sin(cycleTime * 0.6 + idleOffsets.body) * 0.01,     
                        0,                                                    
                        Math.cos(cycleTime * 0.5 + idleOffsets.body) * 0.0075   
                    );
                    break;
                    
                case 'neck':
                    euler.set(
                        Math.cos(cycleTime * 1.2 + idleOffsets.head) * 0.01,     
                        Math.sin(cycleTime * 1.5 + idleOffsets.head) * 0.02,     
                        0                                                     
                    );
                    break;
                    
                case 'head':
                    euler.set(
                        Math.sin(cycleTime * 1.0 + idleOffsets.head) * 0.02,     
                        Math.sin(cycleTime * 1.5 + idleOffsets.head) * 0.03,     
                        Math.cos(cycleTime * 0.8 + idleOffsets.head) * 0.01      
                    );
                    break;
                    
                case 'leftUpperArm':
                    euler.set(
                        Math.cos(cycleTime * 0.7 + idleOffsets.leftArm) * 0.03, 
                        Math.sin(cycleTime * 0.6 + idleOffsets.leftArm) * 0.02,  
                        -0.4 * Math.PI * v + Math.sin(cycleTime * 1.5 + idleOffsets.leftArm) * 0.03
                    );
                    break;
                    
                case 'leftLowerArm':
                    euler.set(
                        0,                                                   
                        0,                                                   
                        -Math.sin(cycleTime * 1.5 + idleOffsets.leftArm) * 0.02 
                    );
                    break;
                    
                case 'leftHand':
                    euler.set(
                        0.05,                                                
                        0,                                                   
                        0.1 * v + Math.sin(cycleTime * 1.2 + idleOffsets.leftArm) * 0.015 
                    );
                    break;
                    
                case 'leftShoulder':
                    euler.set(
                        0,                                                   
                        0,                                                   
                        Math.sin(cycleTime * 0.7 + idleOffsets.leftArm) * 0.02 
                    );
                    break;
                    
                case 'rightUpperArm':
                    euler.set(
                        Math.cos(cycleTime * 0.8 + idleOffsets.rightArm) * 0.03,  
                        Math.sin(cycleTime * 0.64 + idleOffsets.rightArm) * 0.02, 
                        0.4 * Math.PI * v + Math.sin(cycleTime * 1.5 + idleOffsets.rightArm) * 0.03 
                    );
                    break;
                    
                case 'rightLowerArm':
                    euler.set(
                        0,                                                    
                        0,                                                    
                        Math.sin(cycleTime * 1.5 + idleOffsets.rightArm) * 0.02 
                    );
                    break;
                    
                case 'rightHand':
                    euler.set(
                        0.05,                                                 
                        0,                                                    
                        -0.1 * v + Math.sin(cycleTime * 1.2 + idleOffsets.rightArm) * 0.015 
                    );
                    break;
                    
                case 'rightShoulder':
                    euler.set(
                        0,                                                    
                        0,                                                    
                        Math.sin(cycleTime * 0.8 + idleOffsets.rightArm) * 0.02  
                    );
                    break;
                    
                default:
                    euler.set(0, 0, 0);
                    break;
            }
            
            // 将欧拉角转换为四元数并添加到值数组
            const quaternion = new THREE.Quaternion();
            quaternion.setFromEuler(euler);
            values.push(...quaternion.toArray());
        });
        
        // 创建四元数关键帧轨道
        const track = new THREE.QuaternionKeyframeTrack(
            bone.name + '.quaternion',
            times,
            values
        );
        
        tracks.push(track);
    });
    
    // 创建并返回动画剪辑
    return new THREE.AnimationClip('idle', duration, tracks);
}


function createBreathClip(vrm) {
    const tracks = [];
    const duration = 4; // 4秒一个呼吸周期
    const fps = 30;
    const frameCount = duration * fps;
    
    const times = [];
    for (let i = 0; i <= frameCount; i++) {
        times.push(i / fps);
    }
    
    // 呼吸缩放动画
    const scaleValues = [];
    times.forEach(time => {
        const breathScale = 1 + Math.sin(time * Math.PI / 2) * 0.006; // 更自然的呼吸节奏
        scaleValues.push(breathScale, breathScale, breathScale);
    });
    
    const scaleTrack = new THREE.VectorKeyframeTrack(
        vrm.scene.name + '.scale',
        times,
        scaleValues
    );
    
    tracks.push(scaleTrack);
    return new THREE.AnimationClip('breath', duration, tracks);
}

function createBlinkClip(vrm) {
    if (!vrm.expressionManager) return null;
    
    const tracks = [];
    const duration = 6; // 6秒周期，包含随机间隔
    const fps = 30;
    const frameCount = duration * fps;
    
    const times = [];
    for (let i = 0; i <= frameCount; i++) {
        times.push(i / fps);
    }
    
    // 创建眨眼模式：在随机时间点眨眼
    const blinkValues = [];
    times.forEach(time => {
        let blinkValue = 0;
        
        // 在第1.5秒单次眨眼
        if (time >= 1.5 && time <= 1.6) {
            const progress = (time - 1.5) / 0.2;
            blinkValue = Math.sin(progress * Math.PI);
        }
        // 在第4秒双次眨眼
        else if (time >= 3.8 && time <= 4.4) {
            const localTime = time - 3.8;
            if (localTime < 0.15) {
                blinkValue = Math.sin((localTime / 0.15) * Math.PI);
            } else if (localTime > 0.25 && localTime < 0.4) {
                blinkValue = Math.sin(((localTime - 0.25) / 0.15) * Math.PI);
            }
        }
        
        blinkValues.push(blinkValue);
    });
    
    const blinkTrack = new THREE.NumberKeyframeTrack(
        vrm.expressionManager.getExpressionTrackName('blink'),
        times,
        blinkValues
    );
    
    tracks.push(blinkTrack);
    return new THREE.AnimationClip('blink', duration, tracks);
}

/**
 * 停止指定语音块的动画和音频
 * @param {string|number} chunkId 语音块的ID
 */
function stopChunkAnimation(chunkId) {
    const chunkState = chunkAnimations.get(chunkId);
    if (!chunkState) return;

    console.log(`正在停止 Chunk ${chunkId} 的动画和音频`);

    if (chunkState.animationId) {
        cancelAnimationFrame(chunkState.animationId);
    }
    if (chunkState.audio) {
        chunkState.audio.pause();
        chunkState.audio.removeAttribute('src'); // 彻底释放资源
        chunkState.audio.load();
    }
    if (chunkState.audioSource) {
        chunkState.audioSource.disconnect();
    }

    chunkAnimations.delete(chunkId);

    // 如果所有语音块都已结束，则重置表情
    if (chunkAnimations.size === 0 && currentVrm && currentVrm.expressionManager) {
        console.log('所有语音块播放完毕，重置表情。');
        currentVrm.expressionManager.resetValues();
        currentVrm.expressionManager.setValue('neutral', 1.0);
    }
}

/**
 * 停止所有正在播放的语音动画
 */
function stopAllChunkAnimations() {
    console.log('正在停止所有的口型同步动画。');
    for (const chunkId of chunkAnimations.keys()) {
        stopChunkAnimation(chunkId);
    }
    chunkAnimations.clear();
    if (currentVrm && currentVrm.expressionManager) {
        currentVrm.expressionManager.resetValues();
        currentVrm.expressionManager.setValue('neutral', 1.0);
    }
}

/**
 * 最终修复版：基于共振峰 (Formants F1/F2) 的科学口型同步
 * 通过查找频谱中的两个主要能量峰值 (F1, F2) 在元音三角形中的位置来确定口型
 */
function startChunkAnimation(chunkId, chunkState) {
    if (!chunkState || !chunkState.isPlaying || !chunkState.analyser) {
        return;
    }

    const analyser = chunkState.analyser;
    // 增加 FFT 精度，共振峰检测需要更高的频率分辨率
    analyser.fftSize = 1024; // 之前是 256，太小了，分不清 F1/F2
    
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    const sampleRate = currentAudioContext.sampleRate;
    
    // 平滑插值变量
    let currentBlends = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
    
    // 灵敏度设置 (根据麦克风/TTS音量调整)
    const SENSITIVITY = 1.0; // 如果嘴巴动静太小，调大这个数
    const NOISE_GATE = 15;   // 底噪门限

    function getFormant(minFreq, maxFreq) {
        // 将频率转换为数组索引
        const nyquist = sampleRate / 2;
        const startIndex = Math.floor((minFreq / nyquist) * bufferLength);
        const endIndex = Math.floor((maxFreq / nyquist) * bufferLength);
        
        let maxAmp = -Infinity;
        let maxIndex = -1;
        
        // 在指定频率范围内找最强峰值
        for (let i = startIndex; i <= endIndex; i++) {
            if (dataArray[i] > maxAmp) {
                maxAmp = dataArray[i];
                maxIndex = i;
            }
        }
        
        // 返回峰值对应的频率和强度
        return {
            freq: (maxIndex / bufferLength) * nyquist,
            amp: maxAmp
        };
    }

    function animateChunk() {
        const currentState = chunkAnimations.get(chunkId);
        if (!currentState || !currentState.isPlaying) {
            // 停止时归零
            if (currentVrm && currentVrm.expressionManager) {
                ['aa', 'ih', 'ou', 'ee', 'oh'].forEach(v => currentVrm.expressionManager.setValue(v, 0));
            }
            return;
        }

        currentState.animationId = requestAnimationFrame(animateChunk);

        // 1. 获取频域数据
        analyser.getByteFrequencyData(dataArray);

        // 2. 检测共振峰
        // F1 范围: 200Hz - 1000Hz (决定开口大小)
        // F2 范围: 1000Hz - 3000Hz (决定舌位前后)
        const f1 = getFormant(200, 1000);
        const f2 = getFormant(1000, 3000);

        // 3. 计算总音量 (用于控制开口幅度)
        // 只计算人声主要频段 (200-4000Hz) 的平均能量
        let vocalEnergy = 0;
        const startBin = Math.floor((200 / (sampleRate/2)) * bufferLength);
        const endBin = Math.floor((4000 / (sampleRate/2)) * bufferLength);
        for(let i=startBin; i<endBin; i++) vocalEnergy += dataArray[i];
        const avgVol = vocalEnergy / (endBin - startBin);

        // 4. 映射逻辑 (元音三角形)
        let target = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
        
        if (avgVol > NOISE_GATE) {
            // 归一化强度
            const intensity = Math.min(1.0, (avgVol / 255) * SENSITIVITY);
            
            // --- 核心算法：根据 F1/F2 坐标判断元音 ---
            // 数值基于一般人声统计学，可能需要微调
            
            if (f1.freq > 600) {
                // F1 很高 -> 大张嘴 -> A (aa)
                // 类似 "啊"
                target.aa = intensity;
            } 
            else if (f1.freq < 450 && f2.freq > 1800) {
                // F1 低 (闭嘴), F2 高 (舌前) -> I (ih)
                // 类似 "一"
                target.ih = intensity;
                // I 音通常也会带一点点 E
                target.ee = intensity * 0.3; 
            }
            else if (f1.freq < 450 && f2.freq < 1100) {
                // F1 低 (闭嘴), F2 低 (舌后) -> U (ou)
                // 类似 "呜"
                target.ou = intensity;
            }
            else if (f2.freq > 1600) {
                // 剩下的高 F2 -> E (ee)
                // 类似 "耶"
                target.ee = intensity;
                target.ih = intensity * 0.2;
            }
            else {
                // 剩下的 -> O (oh) 或 中性音
                // 类似 "哦"
                target.oh = intensity;
                target.ou = intensity * 0.3;
            }
        }

        // 5. 应用到 VRM
        if (currentVrm && currentVrm.expressionManager) {
            // 表情抑制逻辑
            const expression = chunkState.expression;
            let limit = 1.0;
            if (expression && ['happy', 'surprised'].includes(expression)) {
                limit = 0.5; 
            }

            if (expression) {
                // 常见的情绪列表（排除了 blink，以免干扰自动眨眼，除非明确需要）
                const EMOTIONS = ['surprised', 'happy', 'angry', 'sad', 'neutral', 'relaxed'];
                
                // 如果当前指令是情绪表情，则将其设为 1.0，并将其他情绪归 0.0（防止表情混合变成鬼脸）
                if (EMOTIONS.includes(expression)) {
                    EMOTIONS.forEach(exp => {
                        currentVrm.expressionManager.setValue(exp, exp === expression ? 1.0 : 0.0);
                    });
                } else {
                    // 如果是 blink 等特定表情，直接应用
                    currentVrm.expressionManager.setValue(expression, 1.0);
                }
            }

            ['aa', 'ih', 'ou', 'ee', 'oh'].forEach(v => {
                const t = target[v] * limit;
                const c = currentBlends[v];
                // 动态平滑: 张嘴快(0.5), 闭嘴慢(0.1)
                const smooth = t > c ? 0.5 : 0.1; 
                currentBlends[v] = c + (t - c) * smooth;
                
                currentVrm.expressionManager.setValue(v, currentBlends[v]);
            });
        }
    }

    console.log(`Chunk ${chunkId}: 启动共振峰口型同步`);
    chunkState.animationId = requestAnimationFrame(animateChunk);
}

/**
 * 完整版：基于共振峰 (Formants F1/F2) 的口型同步播放函数
 * 已适配 Promise 队列逻辑，确保播放顺序并保留所有核心算法
 */
async function startLipSyncForChunk(data) {
    return new Promise(async (resolve) => {
        const chunkId = data.chunkIndex;

        // 1. 初始化 Web Audio 环境
        if (!currentAudioContext) {
            currentAudioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (currentAudioContext.state === 'suspended') {
            await currentAudioContext.resume();
        }

        // 2. 检查动作 ID (Motion ID)
        const incomingExpressions = data.expressions || [];
        if (idleAnimationManager) {
            const foundMotionId = incomingExpressions.find(exp => motionUrlMap.has(exp));
            if (foundMotionId) {
                const motionUrl = motionUrlMap.get(foundMotionId);
                if (motionUrl) {
                    console.log(`[LipSync] 触发动作: ${foundMotionId}`);
                    idleAnimationManager.playOneShotAnimation(motionUrl);
                }
            }
        }

        // 3. 处理表情逻辑 (Blend Shapes)
        const ALLOW_EXPS = ['surprised','happy','angry','sad','neutral','relaxed','blink','blinkLeft','blinkRight'];
        const hitExpression = incomingExpressions.find(e => ALLOW_EXPS.includes(e));

        // 4. 创建播放状态
        const chunkState = {
            isPlaying: true,
            animationId: null,
            audio: new Audio(data.audioDataUrl),
            audioSource: null,
            analyser: currentAudioContext.createAnalyser(),
            expression: hitExpression,
        };
        chunkAnimations.set(chunkId, chunkState);

        const { audio, analyser } = chunkState;
        
        // 5. 设置分析器精度 (针对 F1/F2 优化)
        analyser.fftSize = 1024; 
        analyser.smoothingTimeConstant = 0.3;
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);
        const sampleRate = currentAudioContext.sampleRate;

        // 6. 音频连接
        audio.crossOrigin = 'anonymous';
        const audioSource = currentAudioContext.createMediaElementSource(audio);
        audioSource.connect(analyser);
        analyser.connect(currentAudioContext.destination);
        chunkState.audioSource = audioSource;

        // 7. 内部函数：寻找共振峰
        function getFormant(minFreq, maxFreq) {
            const nyquist = sampleRate / 2;
            const startIndex = Math.floor((minFreq / nyquist) * bufferLength);
            const endIndex = Math.floor((maxFreq / nyquist) * bufferLength);
            let maxAmp = -Infinity;
            let maxIndex = -1;
            for (let i = startIndex; i <= endIndex; i++) {
                if (dataArray[i] > maxAmp) {
                    maxAmp = dataArray[i];
                    maxIndex = i;
                }
            }
            return { freq: (maxIndex / bufferLength) * nyquist, amp: maxAmp };
        }

        // 8. 内部变量：平滑插值
        let currentBlends = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
        const SENSITIVITY = 1.0; 
        const NOISE_GATE = 15;

        // 9. 动画循环 (核心算法)
        function animateChunk() {
            const currentState = chunkAnimations.get(chunkId);
            if (!currentState || !currentState.isPlaying) {
                if (currentVrm && currentVrm.expressionManager) {
                    ['aa', 'ih', 'ou', 'ee', 'oh'].forEach(v => currentVrm.expressionManager.setValue(v, 0));
                }
                return;
            }

            currentState.animationId = requestAnimationFrame(animateChunk);
            analyser.getByteFrequencyData(dataArray);

            const f1 = getFormant(200, 1000);
            const f2 = getFormant(1000, 3000);

            let vocalEnergy = 0;
            const startBin = Math.floor((200 / (sampleRate/2)) * bufferLength);
            const endBin = Math.floor((4000 / (sampleRate/2)) * bufferLength);
            for(let i=startBin; i<endBin; i++) vocalEnergy += dataArray[i];
            const avgVol = vocalEnergy / (endBin - startBin);

            let target = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
            
            if (avgVol > NOISE_GATE) {
                const intensity = Math.min(1.0, (avgVol / 255) * SENSITIVITY);
                if (f1.freq > 600) {
                    target.aa = intensity;
                } else if (f1.freq < 450 && f2.freq > 1800) {
                    target.ih = intensity;
                    target.ee = intensity * 0.3; 
                } else if (f1.freq < 450 && f2.freq < 1100) {
                    target.ou = intensity;
                } else if (f2.freq > 1600) {
                    target.ee = intensity;
                    target.ih = intensity * 0.2;
                } else {
                    target.oh = intensity;
                    target.ou = intensity * 0.3;
                }
            }

            if (currentVrm && currentVrm.expressionManager) {
                // 表情限制
                const expression = currentState.expression;
                let limit = (expression && ['happy', 'surprised'].includes(expression)) ? 0.5 : 1.0;

                if (expression) {
                    const EMOTIONS = ['surprised', 'happy', 'angry', 'sad', 'neutral', 'relaxed'];
                    if (EMOTIONS.includes(expression)) {
                        EMOTIONS.forEach(exp => {
                            currentVrm.expressionManager.setValue(exp, exp === expression ? 1.0 : 0.0);
                        });
                    } else {
                        currentVrm.expressionManager.setValue(expression, 1.0);
                    }
                }

                ['aa', 'ih', 'ou', 'ee', 'oh'].forEach(v => {
                    const t = target[v] * limit;
                    const c = currentBlends[v];
                    const smooth = t > c ? 0.5 : 0.1; 
                    currentBlends[v] = c + (t - c) * smooth;
                    currentVrm.expressionManager.setValue(v, currentBlends[v]);
                });
            }
        }

        // 10. 绑定音频事件
        audio.onended = () => {
            stopChunkAnimation(chunkId);
            resolve(); // 播放结束，触发 Promise 解决，队列进入下一项
        };

        audio.onerror = (err) => {
            console.error(`Chunk ${chunkId} 播放错误:`, err);
            stopChunkAnimation(chunkId);
            resolve(); // 出错也必须 resolve，否则队列会卡死
        };

        // 11. 开始播放与动画
        try {
            await audio.play();
            chunkState.animationId = requestAnimationFrame(animateChunk);
        } catch (error) {
            console.error("Audio.play 失败:", error);
            stopChunkAnimation(chunkId);
            resolve();
        }
    });
}

let VRMname = await getVRMname();
showModelSwitchingIndicator(VRMname);
loader.load(

    // URL of the VRM you want to load
    vrmPath,

    // called when the resource is loaded
    ( gltf ) => {

        const vrm = gltf.userData.vrm;
        currentMixer = new THREE.AnimationMixer(vrm.scene); // 创建动画混合器
        isVRM1 = vrm.meta.metaVersion === '1';
        VRMUtils.rotateVRM0(vrm); // 旋转 VRM 使其面向正前方
        // calling these functions greatly improves the performance
        VRMUtils.removeUnnecessaryVertices( gltf.scene );

        // 添加材质修复
        gltf.scene.traverse((obj) => {
        if (obj.isMesh && obj.material) {
            // 解决透明材质黑边问题
            if (obj.material.transparent) {
                obj.material.alphaTest = 0.01;
                obj.material.depthWrite = true;
                obj.material.needsUpdate = true;
            }
            
            // 确保正确混合模式
            obj.material.blending = THREE.NormalBlending;
            obj.material.premultipliedAlpha = false;
            
            // 设置渲染顺序
            obj.renderOrder = obj.material.transparent ? 1 : 0;
        }
        });

        VRMUtils.combineSkeletons( gltf.scene );
        VRMUtils.combineMorphs( vrm );

        // 启用 Spring Bone 物理模拟
        if (vrm.springBoneManager) {
            console.log('Spring Bone Manager found:', vrm.springBoneManager);
            // Spring Bone 会在 vrm.update() 中自动更新
        }


        // Disable frustum culling
        vrm.scene.traverse( ( obj ) => {

            obj.frustumCulled = false;

        } );

        vrm.lookAt.target = camera;

        if (vrm.lookAt.applier) {
            vrm.lookAt.applier.yawLimit = 60.0;   // 左右转头最大 60 度
            vrm.lookAt.applier.pitchLimit = 30.0; // 上下抬头最大 30 度
        }

        currentVrm = vrm;
        console.log( vrm );
        currentVrmWrapper.add(vrm.scene); 
        
        // 让模型投射阴影
        vrm.scene.traverse((obj) => {
            if (obj.isMesh) {
                obj.castShadow = true;
                obj.receiveShadow = true;   // 如需让模型本身也接收阴影可保留
            }
        });
        // 设置自然姿势
        setNaturalPose(vrm);

        if (vrm.expressionManager) {
            vrm.expressionManager.setValue('neutral', 1.0);
        }

        const breathClip = createBreathClip(vrm);
        breathAction = currentMixer.clipAction(breathClip);
        breathAction.setLoop(THREE.LoopRepeat);
        breathAction.play();

        const blinkClip = createBlinkClip(vrm);
        blinkAction = currentMixer.clipAction(blinkClip);
        blinkAction.setLoop(THREE.LoopRepeat);
        blinkAction.play();

        // 创建闲置动画管理器
        idleAnimationManager = new IdleAnimationManager(vrm, currentMixer);

        // 开始闲置动画循环
        startIdleAnimationLoop();

        hideModelSwitchingIndicator();
    },

    (progress) => {
        console.log('Loading model...', 100.0 * (progress.loaded / progress.total), '%');
        // 可以在这里更新加载进度
        updateModelLoadingProgress(progress.loaded / progress.total);
    },

    (error) => {
        console.error('Error loading model:', error);
        hideModelSwitchingIndicator();
        
        // 如果加载失败，尝试回到之前的模型
        if (allModels.length > 1) {
            console.log('Attempting to load fallback model...');
            // 尝试加载第一个模型作为备用
            if (currentModelIndex !== 0) {
                switchToModel(0);
            }
        }
    }

);

// 在全局变量区域添加字幕相关变量
let subtitleElement = null;
let currentSubtitleChunkIndex = -1;
let subtitleTimeout = null;
let isSubtitleEnabled = true; // 字幕默认开启
let isDraggingSubtitle = false;
let subtitleOffsetX = 0;
let subtitleOffsetY = 0;

// 修改初始化字幕元素
function initSubtitleElement() {
    subtitleElement = document.createElement('div');
    subtitleElement.id = 'subtitle-container';
    subtitleElement.style.cssText = `
        position: fixed;
        top: 50%;  
        left: 50%;
        width: auto;
        max-width: 80%;
        transform: translateX(-50%);
        padding: 12px 24px;
        background: rgba(0, 0, 0, 0.8);
        color: white;
        border-radius: 8px;
        font-family: 'Arial', sans-serif;
        font-size: 1.2em;
        text-align: center;
        backdrop-filter: blur(10px);
        opacity: 0;
        transition: opacity 0.3s ease, transform 0.3s ease;
        z-index: 9998;
        white-space: pre-wrap;
        line-height: 1.5;
        cursor: move;
        user-select: none;
        min-width: 100px;
        max-width: 80%;
        width: max-content;
    `;

    // 添加拖拽事件监听
    subtitleElement.addEventListener('mousedown', startDragSubtitle);
    document.addEventListener('mousemove', dragSubtitle);
    document.addEventListener('mouseup', endDragSubtitle);

    document.body.appendChild(subtitleElement);
}

// 改进拖拽功能
function startDragSubtitle(e) {
    if (!isSubtitleEnabled) return;
    
    isDraggingSubtitle = true;
    
    // 获取字幕元素的初始位置
    const rect = subtitleElement.getBoundingClientRect();
    
    // 计算鼠标相对于字幕中心点的偏移量
    subtitleOffsetX = e.clientX - (rect.left + rect.width / 2);
    subtitleOffsetY = e.clientY - rect.top;
    
    // 禁用过渡效果
    subtitleElement.style.transition = 'none';
}

function dragSubtitle(e) {
    if (isDraggingSubtitle) {
        // 计算字幕中心点的目标位置
        const centerX = e.clientX - subtitleOffsetX;
        const centerY = e.clientY - subtitleOffsetY;
        
        // 限制在窗口范围内，保持水平居中
        const halfWidth = subtitleElement.offsetWidth / 2;
        const clampedX = Math.max(halfWidth, Math.min(centerX, window.innerWidth - halfWidth));
        
        // 设置位置时保持水平居中
        subtitleElement.style.left = `${clampedX}px`;
        subtitleElement.style.transform = 'translateX(-50%)'; // 水平居中
        
        // 垂直位置保持不变
        const maxY = window.innerHeight - subtitleElement.offsetHeight;
        const clampedY = Math.max(0, Math.min(centerY, maxY));
        
        subtitleElement.style.top = `${clampedY}px`;
        subtitleElement.style.bottom = 'auto'; // 取消底部定位
    }
}

function endDragSubtitle() {
    if (isDraggingSubtitle) {
        isDraggingSubtitle = false;
        subtitleElement.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
    }
}

// 修改字幕显示/隐藏功能
function toggleSubtitle(enable) {
    isSubtitleEnabled = enable;
    if (subtitleElement) {
        subtitleElement.style.display = enable ? 'block' : 'none';
    }
}

/**
 * 提取出的尺寸调整逻辑
 */
function adjustSubtitleSize() {
    if (!subtitleElement) return;
    const maxWidth = window.innerWidth * 0.8;
    subtitleElement.style.width = 'max-content';
    subtitleElement.style.minWidth = '100px';
    
    const rect = subtitleElement.getBoundingClientRect();
    if (rect.width > maxWidth) {
        subtitleElement.style.width = `${maxWidth}px`;
    }
}


let vmcLastSent = 0;
const VMC_SEND_INTERVAL = 1000 / 30;          // 30 fps
const VMC_BONES = [                           // VMC 标准骨骼列表
  'hips','spine','chest','upperChest','neck','head',
  'leftShoulder','leftUpperArm','leftLowerArm','leftHand',
  'rightShoulder','rightUpperArm','rightLowerArm','rightHand',
  'leftUpperLeg','leftLowerLeg','leftFoot','leftToes',
  'rightUpperLeg','rightLowerLeg','rightFoot','rightToes',
  // 手指（可选）
  'leftThumbProximal','leftThumbIntermediate','leftThumbDistal',
  'leftIndexProximal','leftIndexIntermediate','leftIndexDistal',
  'leftMiddleProximal','leftMiddleIntermediate','leftMiddleDistal',
  'leftRingProximal','leftRingIntermediate','leftRingDistal',
  'leftLittleProximal','leftLittleIntermediate','leftLittleDistal',
  'rightThumbProximal','rightThumbIntermediate','rightThumbDistal',
  'rightIndexProximal','rightIndexIntermediate','rightIndexDistal',
  'rightMiddleProximal','rightMiddleIntermediate','rightMiddleDistal',
  'rightRingProximal','rightRingIntermediate','rightRingDistal',
  'rightLittleProximal','rightLittleIntermediate','rightLittleDistal'
];

function getVMCBoneData() {
  if (!currentVrm?.humanoid) return [];

  const boneData = [];
  
  // VMC 接收端通常期望 Hips 的位置是相对于地面的绝对高度
  // 我们需要获取 Hips 的世界坐标
  const hipsNode = currentVrm.humanoid.getNormalizedBoneNode('hips');
  let rootY = 0;
  if (hipsNode) {
      const worldPos = new THREE.Vector3();
      hipsNode.getWorldPosition(worldPos);
      // 如果模型被缩放过，或者场景有位移，这里要用世界坐标
  }

  for (const name of VMC_BONES) {
    const node = currentVrm.humanoid.getNormalizedBoneNode(name);
    if (!node) continue;

    // 获取相对于父级的旋转（局部旋转），因为 VMC 传输的是 Local Rotation
    // 注意：Hips 需要特殊处理，它通常传输世界位置
    
    // 1. 位置处理 (Position)
    // 只有 Hips 需要传位置，其他骨骼位置通常由骨骼长度决定（VMC接收端会忽略非Hips的位置，或者用来缩放）
    // 为了兼容性，我们只对 Hips 传真实位置，其他传 0 (或者传 node.position 也行，但要注意转换)
    
    let x = node.position.x;
    let y = node.position.y;
    let z = node.position.z;

    // ★ 关键坐标系转换：ThreeJS(右手) -> Unity(左手)
    // Position: X 取反
    const vmcPos = { x: -x, y: y, z: z };

    // 2. 旋转处理 (Rotation)
    // ThreeJS: x, y, z, w
    // Unity:   x, -y, -z, w (通常转换公式)
    
    let qx = node.quaternion.x;
    let qy = node.quaternion.y;
    let qz = node.quaternion.z;
    let qw = node.quaternion.w;

    if (!isVRM1) {
        qx = -qx;
        qz = -qz;
    }

    const vmcRot = { 
        x: qx, 
        y: -qy, 
        z: -qz, 
        w: qw 
    };

    boneData.push({
        name: name,
        pos: vmcPos,
        rot: vmcRot
    });
  }
  return boneData;
}

// VRM1 → VRM0（VMC 事实标准）
const VRM1_TO_VMC0 = {
  happy:  'Joy',
  angry:  'Angry',
  sad:    'Sorrow',
  relaxed:'Fun',
  aa:     'A',
  ih:     'I',
  ou:     'U',
  ee:     'E',
  oh:     'O',
  blinkLeft:  'Blink_L',
  blinkRight: 'Blink_R',
  blink:      'Blink',
  surprised:  'Surprised',
  neutral:    'Neutral',
  lookDown:   'LookDown',
  lookUp:     'LookUp',
  lookLeft:   'LookLeft',
  lookRight:  'LookRight'
};

// 需要同步的表情（按需删减）
const VMC_BLEND_SHAPES = [
  // 五元音
  'aa','ee','ih','oh','ou',
  'blink', 'blinkLeft', 'blinkRight',
  'surprised','happy','angry', 'sad', 'neutral', 'relaxed',
  'lookDown','lookUp','lookLeft','lookRight'
];

let lastBlendWeights = {}; // 节流：变化了才发



function getVMCBlendData() {
  if (!currentVrm?.expressionManager) return [];
  
  const blendData = [];
  const mgr = currentVrm.expressionManager;

  for (const vrmName of VMC_BLEND_SHAPES) {
    const weight = mgr.getValue(vrmName);
    if (weight === undefined) continue;

    const vmcName = VRM1_TO_VMC0[vrmName];
    if (!vmcName) continue;
    
    // 这里为了保证数据完整，Warudo 建议每帧都发，或者至少变化时发
    // 如果为了带宽可以做节流，但最好打包发送
    blendData.push({
        name: vmcName,
        weight: weight
    });
  }
  return blendData;
}
const vmcToVrmBone = {
  LeftIndexIntermediate: 'leftIndexIntermediate',
  RightIndexIntermediate:'rightIndexIntermediate',
  LeftMiddleIntermediate:'leftMiddleIntermediate',
  RightMiddleIntermediate:'rightMiddleIntermediate',
  LeftRingIntermediate:  'leftRingIntermediate',
  RightRingIntermediate: 'rightRingIntermediate',
  LeftLittleIntermediate:'leftLittleIntermediate',
  RightLittleIntermediate:'rightLittleIntermediate',
  LeftThumbIntermediate: 'leftThumbIntermediate',
  RightThumbIntermediate:'rightThumbIntermediate',
  LeftUpperArm:  'leftUpperArm',
  LeftLowerArm:  'leftLowerArm',
  LeftHand:      'leftHand',
  RightUpperArm: 'rightUpperArm',
  RightLowerArm: 'rightLowerArm',
  RightHand:     'rightHand',
  UpperChest:    'upperChest',
  Chest:         'chest',
  Spine:         'spine',
  Hips:          'hips',
  Neck:          'neck',
  Head:          'head',
};

// animate
const clock = new THREE.Clock();
clock.start();
let currentLookYaw = 0;   // 左右偏航角 (Y轴)
let currentLookPitch = 0; // 上下俯仰角 (X轴)

let isPreviewing360 = false;
let debugSphere, debugCamera, debugControls;

function animate() {
    requestAnimationFrame(animate);
    
    const deltaTime = clock.getDelta();
    updatePointerLockMovement(deltaTime);
    const shouldSkipModelUpdate = isModelHiddenByHover && isAutoHideEnabled;

    if (currentVrm && !shouldSkipModelUpdate) {
        // 1. Mixer 更新
        if (currentMixer) {
            currentMixer.update(deltaTime);
        }

        // 2. VMC 接收更新
        if (vmcReceiveEnabled) {
            for (const [vmcName, data] of vmcBoneBuffer) {
                let boneName = vmcToVrmBone[vmcName] ??
                            vmcName.charAt(0).toLowerCase() + vmcName.slice(1);
                
                if (boneName === 'neck' || boneName === 'head') continue;

                const node = currentVrm.humanoid.getNormalizedBoneNode(boneName);
                if (!node) continue;
                if (isVRM1) {
                    node.position.copy(data.position);
                    node.quaternion.copy(data.rotation);
                } else {
                    node.position.copy(data.position);
                    node.quaternion.set(-data.rotation.x, data.rotation.y, -data.rotation.z, data.rotation.w);
                }
            }
        } 

        // 3. 仿生视线追踪 (彻底修复 VRM 0.x 坐标系朝向问题)
        const neck = currentVrm.humanoid.getNormalizedBoneNode('neck');
        const head = currentVrm.humanoid.getNormalizedBoneNode('head');

        if (neck && neck.parent) {
            const parent = neck.parent;
            const targetWorldPos = camera.position.clone();
            
            const localCameraPos = parent.worldToLocal(targetWorldPos.clone());
            const neckLocalPos = neck.position.clone();
            const viewVector = localCameraPos.sub(neckLocalPos);

            if (!isVRM1) {
                viewVector.z = -viewVector.z; 
                viewVector.x = -viewVector.x; 
            }

            const rawTargetYaw = Math.atan2(viewVector.x, viewVector.z);
            const horizontalDist = Math.sqrt(viewVector.x**2 + viewVector.z**2);
            const rawTargetPitch = Math.atan2(viewVector.y, horizontalDist);

            let targetYaw = rawTargetYaw * 0.6;
            let targetPitch = rawTargetPitch * 0.6;

            const yawLimit = THREE.MathUtils.degToRad(45);  
            const pitchUpLimit = THREE.MathUtils.degToRad(40);
            const pitchDownLimit = THREE.MathUtils.degToRad(20);
            const behindLimit = THREE.MathUtils.degToRad(110);

            if (Math.abs(rawTargetYaw) > behindLimit) {
                targetYaw = 0;
                targetPitch = 0;
            } else {
                targetYaw = THREE.MathUtils.clamp(targetYaw, -yawLimit, yawLimit);
                targetPitch = THREE.MathUtils.clamp(targetPitch, -pitchDownLimit, pitchUpLimit);
            }

            const lerpSpeed = 2.0 * deltaTime;
            currentLookYaw = THREE.MathUtils.lerp(currentLookYaw, targetYaw, lerpSpeed);
            currentLookPitch = THREE.MathUtils.lerp(currentLookPitch, targetPitch, lerpSpeed);

            let applyYaw = currentLookYaw;
            let applyPitch = -currentLookPitch; 

            if (!isVRM1) {
                applyYaw = currentLookYaw; 
                applyPitch = currentLookPitch; 
            }

            const qYaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), applyYaw);
            const qPitch = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), applyPitch);
            qYaw.multiply(qPitch);
            neck.quaternion.copy(qYaw);

            if (head) {
                const qHeadYaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), applyYaw * 0.5);
                const qHeadPitch = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), applyPitch * 0.5);
                qHeadYaw.multiply(qHeadPitch);
                head.quaternion.copy(qHeadYaw);
            }
        }

        // 4. VRM 最终更新
        currentVrm.update(deltaTime);
    }

    // --- 渲染逻辑分支 (全景渲染核心) ---
    if (isRenderMode) {
        if (isPreviewing360 && debugCamera) {
            // A. 360 预览模式：使用调试相机旋转查看球体内部
            renderer.render(scene, debugCamera);
        } else {
            // B. 标准全景模式 (2:1 展开图)
            if (cubeCamera) {
                // 渲染前隐藏全景投影平面，防止遮挡立方体相机
                if (panoMesh) panoMesh.visible = false;
                
                // 让立方体相机捕捉 360 度场景
                cubeCamera.update(renderer, scene);
                
                // 恢复投影平面并渲染到屏幕
                if (panoMesh) {
                    panoMesh.visible = true;
                    renderer.render(panoMesh, panoCamera);
                }
            }
        }
    } else {
        // C. 普通模式
        renderer.render(scene, camera);
    }
    
    // 5. VMC 发送逻辑
    const now = performance.now();
    if (window.vmcAPI && (now - vmcLastSent >= VMC_SEND_INTERVAL)) {
        vmcLastSent = now;
        const bones = getVMCBoneData();
        const blends = getVMCBlendData();
        if (bones.length > 0) {
            window.vmcAPI.sendVMCFrame({
                bones: bones,
                blends: blends
            });
        }
    }

    // 6. UI 字幕维护
    if (subtitleElement && !isDraggingSubtitle) {
        const rect = subtitleElement.getBoundingClientRect();
        if (rect.bottom > window.innerHeight || rect.right > window.innerWidth) {
            subtitleElement.style.left = '50%';
            subtitleElement.style.bottom = '30%';
            subtitleElement.style.top = 'auto';
            subtitleElement.style.transform = 'translateX(-50%)';
        }
    }
}

// --- 全景预览切换逻辑：完整对齐高度版本 ---

window.addEventListener('keydown', (e) => {
    // 只有在渲染模式下才响应 V 键
    if (e.key.toLowerCase() === 'v' && isRenderMode) {
        if (!isPreviewing360) {
            // 1. 创建全景调试球体
            // 半径设为 5 即可，不要太大也不要太小
            const geometry = new THREE.SphereGeometry(5, 60, 40);
            geometry.scale(-1, 1, 1); // 翻转球体，从内向外看
            
            const material = new THREE.MeshBasicMaterial({
                map: cubeRenderTarget.texture, // 实时采样全景相机的内容
                side: THREE.BackSide // 确保只渲染内侧
            });
            
            debugSphere = new THREE.Mesh(geometry, material);
            
            // --- 关键同步：球体中心也要设在 1.5m ---
            debugSphere.position.set(0, 1.5, 1);
            scene.add(debugSphere);
            
            // 2. 创建预览用的调试相机
            debugCamera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 100);
            
            // --- 关键同步：相机位置与 cubeCamera 保持完全一致 ---
            debugCamera.position.set(0, 1.5, 1); 
            
            // 3. 初始化控制器
            debugControls = new OrbitControls(debugCamera, renderer.domElement);
            
            // --- 核心修复：将目标点设在 1.5m，实现平视视角 ---
            debugControls.target.set(0, 1.5, 0); 
            
            debugControls.enableZoom = false; // 禁用缩放，模拟固定点视角
            debugControls.enablePan = false;  // 禁用平移，防止跑出球体
            debugControls.update(); // 必须调用，否则 target 不生效
            
            isPreviewing360 = true;
            console.log("进入 360 预览模式：已同步至 1.5m 平视高度");
        } else {
            // 退出预览模式
            if (debugSphere) {
                scene.remove(debugSphere);
                if (debugSphere.geometry) debugSphere.geometry.dispose();
                if (debugSphere.material) debugSphere.material.dispose();
            }
            if (debugControls) {
                debugControls.dispose();
            }
            isPreviewing360 = false;
            console.log("退出预览，恢复 2:1 平面图输出");
        }
    }
});

async function setVMCReceive (enable, syncExpr = false) {
  if (vmcReceiveEnabled!= enable){
    if (enable) {
      // 进入 VMC 模式：停止本地一切动画
      if (idleAnimationManager) idleAnimationManager.stopAllAnimations();
      if (breathAction) breathAction.stop();
      if (blinkAction)  blinkAction.stop();
      if (currentMixer) currentMixer.stopAllAction();
      // 清空缓存，防止旧数据“跳变”
      vmcBoneBuffer.clear();
      vmcBlendBuffer.clear();

      // 开启程序化呼吸和眨眼
      currentMixer = new THREE.AnimationMixer(currentVrm.scene);
      const breathClip = createBreathClip(currentVrm);
      breathAction = currentMixer.clipAction(breathClip);
      breathAction.setLoop(THREE.LoopRepeat);
      breathAction.play();

      const blinkClip = createBlinkClip(currentVrm);
      blinkAction = currentMixer.clipAction(blinkClip);
      blinkAction.setLoop(THREE.LoopRepeat);
      blinkAction.play();


    } else {
      switchToModel(currentModelIndex, true);
    }
  };

  vmcReceiveEnabled = enable;
  vmcSyncExpression = syncExpr;
	console.log(`VMC receive enabled: ${enable}, sync expression: ${syncExpr}`);


};
let pointerLocked = false;          // 当前是否处于 PointerLock 模式
let orbitControlsSaved = null;      // 保存 OrbitControls 实例
let pointerLockControls = null;     // PointerLockControls 实例
const keyState = {};               // 按键记录
const moveSpeed = 5;               // 每秒移动速度（米/秒）

// 监听按键
function onKeyDown(e) {
    keyState[e.code] = true;
}
function onKeyUp(e) {
    keyState[e.code] = false;
}

// 每帧更新相机位置
function updatePointerLockMovement(delta) {
    if (!pointerLocked || !pointerLockControls) return;

    const direction = new THREE.Vector3();
    const head = pointerLockControls.getObject();   // 相机容器

    // 前后
    if (keyState['KeyW']) direction.z -= 1;
    if (keyState['KeyS']) direction.z += 1;
    // 左右
    if (keyState['KeyA']) direction.x -= 1;
    if (keyState['KeyD']) direction.x += 1;
    // 上下
    if (keyState['KeyQ']) direction.y -= 1;
    if (keyState['KeyE']) direction.y += 1;

    if (direction.lengthSq() === 0) return;

    direction.normalize().applyQuaternion(head.quaternion); // 转成世界方向
    head.position.addScaledVector(direction, moveSpeed * delta);
}

// 进入 PointerLock 时绑定事件
function enablePointerLockMovement() {
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('keyup', onKeyUp);
}

// 退出 PointerLock 时清理
function disablePointerLockMovement() {
    document.removeEventListener('keydown', onKeyDown);
    document.removeEventListener('keyup', onKeyUp);
    // 清空按键缓存
    for (const k in keyState) delete keyState[k];
}

const pttStyle = document.createElement('style');
pttStyle.textContent = `
    #ptt-floating-btn {
        position: fixed;
        bottom: 80px; /* 抬高一点防底部小白条 */
        left: 50%;
        transform: translateX(-50%) scale(0);
        width: 80px;   /* 增大尺寸，符合触控 */
        height: 80px;  /* 增大尺寸 */
        background: linear-gradient(135deg, #ff6b35, #ff8c5a);
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 6px 16px rgba(255, 107, 53, 0.4); 
        cursor: pointer;
        z-index: 10002;
        transition: transform 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.3s;
        user-select: none;
        touch-action: none; /* 必须，防止滑动页面 */
        -webkit-touch-callout: none; /* 防止长按弹出菜单 */
        opacity: 0;
        pointer-events: none;
    }
    #ptt-floating-btn.visible { 
        transform: translateX(-50%) scale(1); 
        opacity: 1;
        pointer-events: auto;
    }
    #ptt-floating-btn:active, #ptt-floating-btn.active { 
        transform: translateX(-50%) scale(0.9); 
        background: #e65c2b; 
    }
    #ptt-floating-btn i { 
        color: white; 
        font-size: 36px; /* 图标放大 */
    }
    .ptt-recording-pulse {
        position: absolute;
        width: 100%; height: 100%;
        border-radius: 50%;
        background: rgba(255, 107, 53, 0.5);
        animation: ptt-pulse-ring 1.2s infinite;
        z-index: -1;
    }
    @keyframes ptt-pulse-ring {
        0% { transform: scale(1); opacity: 0.5; }
        100% { transform: scale(2.2); opacity: 0; }
    }
`;
document.head.appendChild(pttStyle);

// 全局状态
let pttMainWs = null;
let pttAsrWs = null;
let pttMediaRecorder = null;
let pttAudioChunks = [];
let isPttActive = false;
let pttVisible = false;
// 初始化通往主界面的控制 WS
function initPttMainWs() {
    if (pttMainWs && pttMainWs.readyState === WebSocket.OPEN) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    pttMainWs = new WebSocket(`${protocol}//${window.location.host}/ws`);
    pttMainWs.onclose = () => setTimeout(initPttMainWs, 3000);
}

// 初始化 ASR WS
async function initPttAsrWs() {
    if (pttAsrWs && pttAsrWs.readyState === WebSocket.OPEN) return pttAsrWs;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    pttAsrWs = new WebSocket(`${protocol}//${window.location.host}/ws/asr`);
    
    return new Promise((resolve) => {
        pttAsrWs.onopen = () => {
            pttAsrWs.send(JSON.stringify({ type: "init" }));
            resolve(pttAsrWs);
        };
        pttAsrWs.onmessage = (e) => {
            const msg = JSON.parse(e.data);
            if (msg.type === "transcription" && msg.text && msg.is_final) {
                // 识别成功，通过控制 WS 发送给主界面
                if (pttMainWs && pttMainWs.readyState === WebSocket.OPEN) {
                    pttMainWs.send(JSON.stringify({ type: "set_user_input", data: { text: msg.text } }));
                    setTimeout(() => {
                        pttMainWs.send(JSON.stringify({ type: "trigger_send_message", data: {} }));
                    }, 300);
                }
            }
        };
    });
}

// 核心转码：WebM -> 16kHz WAV (Sherpa 专用)
async function pttEncodeWav(blob) {
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const arrayBuffer = await blob.arrayBuffer();
    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
    const channelData = audioBuffer.getChannelData(0);
    
    const wavBuffer = new ArrayBuffer(44 + channelData.length * 2);
    const view = new DataView(wavBuffer);
    
    const writeString = (offset, string) => {
        for (let i = 0; i < string.length; i++) view.setUint8(offset + i, string.charCodeAt(i));
    };

    writeString(0, 'RIFF');
    view.setUint32(4, 36 + channelData.length * 2, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true);
    view.setUint32(28, 16000 * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, 'data');
    view.setUint32(40, channelData.length * 2, true);

    for (let i = 0; i < channelData.length; i++) {
        const s = Math.max(-1, Math.min(1, channelData[i]));
        view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return new Blob([view], { type: 'audio/wav' });
}

// 初始化 PTT 按钮与录音逻辑
// 初始化 PTT 按钮与录音逻辑 (支持长按、防快按误触、XR射线)
function setupPttInteraction() {
    const floatingBtn = document.createElement('div');
    floatingBtn.id = 'ptt-floating-btn';
    floatingBtn.innerHTML = '<i class="fa-solid fa-microphone"></i>';
    document.body.appendChild(floatingBtn);

    let isRecordingRequested = false; // 防极快点击导致异步 Bug

    const startRecording = async (e) => {
        if (e.cancelable) e.preventDefault();
        if (isPttActive) return;
        isPttActive = true;
        isRecordingRequested = true;
        pttAudioChunks = [];
        
        floatingBtn.classList.add('active'); // 视觉反馈
        floatingBtn.innerHTML = '<i class="fa-solid fa-microphone"></i><div class="ptt-recording-pulse"></div>';

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            // 如果用户松手太快，授权结束后立刻清理，不开始录音
            if (!isRecordingRequested) {
                stream.getTracks().forEach(t => t.stop());
                isPttActive = false;
                floatingBtn.classList.remove('active');
                floatingBtn.innerHTML = '<i class="fa-solid fa-microphone"></i>';
                return;
            }

            pttMediaRecorder = new MediaRecorder(stream);
            pttMediaRecorder.ondataavailable = (ev) => pttAudioChunks.push(ev.data);
            pttMediaRecorder.onstop = async () => {
                stream.getTracks().forEach(t => t.stop());
                
                // 只有录到了数据才发送（防秒松手发空包）
                if (pttAudioChunks.length > 0) {
                    const webmBlob = new Blob(pttAudioChunks, { type: 'audio/webm' });
                    const wavBlob = await pttEncodeWav(webmBlob);
                    
                    const ws = await initPttAsrWs();
                    const reader = new FileReader();
                    reader.readAsDataURL(wavBlob);
                    reader.onloadend = () => {
                        ws.send(JSON.stringify({
                            type: 'audio_complete',
                            audio: reader.result.split(',')[1],
                            format: 'wav'
                        }));
                    };
                }
            };
            pttMediaRecorder.start();
        } catch (err) {
            console.error("Mic error:", err);
            isPttActive = false;
            floatingBtn.classList.remove('active');
            floatingBtn.innerHTML = '<i class="fa-solid fa-microphone"></i>';
        }
    };

    const stopRecording = (e) => {
        if (e && e.cancelable) e.preventDefault();
        isRecordingRequested = false;
        if (!isPttActive) return;
        isPttActive = false;
        
        floatingBtn.classList.remove('active');
        floatingBtn.innerHTML = '<i class="fa-solid fa-microphone"></i>';
        
        if (pttMediaRecorder && pttMediaRecorder.state === 'recording') {
            pttMediaRecorder.stop();
        }
    };

    // 使用 Pointer Events，完美兼顾 鼠标、触摸屏、XR射线
    floatingBtn.addEventListener('pointerdown', startRecording);
    floatingBtn.addEventListener('pointerup', stopRecording);
    floatingBtn.addEventListener('pointercancel', stopRecording);
    // 监听全局，防止手指/鼠标滑出按钮区域外松开导致没停止
    window.addEventListener('pointerup', stopRecording);
    
    initPttMainWs();
}

let isTextInputReady = false;
function setupTextInteraction() {
    if (isTextInputReady) return;
    isTextInputReady = true;

    const textInputContainer = document.createElement('div');
    textInputContainer.id = 'text-input-container';
    textInputContainer.style.cssText = `
        position: fixed;
        bottom: 20px; /* 错开在橙色 PTT 按钮下方 */
        left: 50%;
        transform: translateX(-50%) translateY(20px);
        display: flex;
        align-items: center;
        background: rgba(0, 0, 0, 0.6);
        backdrop-filter: blur(10px);
        padding: 8px 16px;
        border-radius: 24px;
        z-index: 10001;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        width: 80%;
        max-width: 500px;
        transition: opacity 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275), transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        opacity: 0;
        pointer-events: none;
    `;

    const textInputField = document.createElement('input');
    textInputField.id = 'text-input-field';
    textInputField.type = 'text';
    textInputField.placeholder = '输入文字与模型对话...';
    textInputField.style.cssText = `
        flex: 1;
        background: transparent;
        border: none;
        color: white;
        font-size: 15px;
        outline: none;
        padding: 8px 4px;
    `;

    // 隔离事件：防止输入框打字时触发3D场景漫游等快捷键 (如 W, A, S, D, T, R等)
    ['keydown', 'keyup', 'keypress'].forEach(evt => {
        textInputField.addEventListener(evt, (e) => {
            e.stopPropagation();
            if (evt === 'keydown' && e.key === 'Enter') {
                e.preventDefault();
                sendTextInputMessage();
            }
        });
    });

    const textSendBtn = document.createElement('button');
    textSendBtn.id = 'text-send-btn';
    textSendBtn.innerHTML = '<i class="fa-solid fa-paper-plane"></i>';
    textSendBtn.style.cssText = `
        background: #ff6b35;
        color: white;
        border: none;
        border-radius: 50%;
        width: 32px;
        height: 32px;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        margin-left: 8px;
        transition: transform 0.2s, background 0.2s;
        outline: none;
    `;
    textSendBtn.addEventListener('mouseenter', () => { textSendBtn.style.transform = 'scale(1.1)'; textSendBtn.style.background = '#e65c2b'; });
    textSendBtn.addEventListener('mouseleave', () => { textSendBtn.style.transform = 'scale(1)'; textSendBtn.style.background = '#ff6b35'; });
    textSendBtn.addEventListener('click', sendTextInputMessage);

    textInputContainer.appendChild(textInputField);
    textInputContainer.appendChild(textSendBtn);
    document.body.appendChild(textInputContainer);

    function sendTextInputMessage() {
        const text = textInputField.value.trim();
        if (!text) return;
        if (pttMainWs && pttMainWs.readyState === WebSocket.OPEN) {
            // 1. 发送用户输入的文本
            pttMainWs.send(JSON.stringify({ type: "set_user_input", data: { text: text } }));
            // 2. 稍微延迟确保主程序录入后，触发对话生成指令
            setTimeout(() => {
                pttMainWs.send(JSON.stringify({ type: "trigger_send_message", data: {} }));
            }, 300);
            textInputField.value = ''; 
        } else {
            console.warn("WS 未连接，尝试重连...");
            initPttMainWs();
        }
    }
    
    // 阻止点击面板时触发父级的交互折叠事件等
    textInputContainer.addEventListener('mousedown', (e) => e.stopPropagation());
    textInputContainer.addEventListener('touchstart', (e) => e.stopPropagation(), {passive: true});
}

const btn_width = 28;
const btn_height = 28;

function addcontrolPanel() {
    if (isRenderMode) {
        console.log('全景渲染模式：已跳过控制面板生成');
        return;
    }

    // 等待一小段时间确保页面完全加载
    setTimeout(async () => {
        // 创建控制面板容器
        const controlPanel = document.createElement('div');
        controlPanel.id = 'control-panel';
        controlPanel.style.cssText = `
        position: fixed;
        top: 10px;
        right: 10px;
        display: flex;
        flex-direction: column;
        gap: 8px;
        z-index: 9999;
        opacity: 0;
        visibility: hidden;
        transform: translateX(20px);
        transition: opacity 0.3s ease, transform 0.3s ease, visibility 0.3s;
        pointer-events: none;
        `;

        // 注入全局 CSS 清理移动端点击方块高亮
        const globalStyle = document.createElement('style');
        globalStyle.textContent = `
            #control-panel div, #sub-control-panel div {
                -webkit-tap-highlight-color: transparent;
            }
        `;
        document.head.appendChild(globalStyle);

        // ==========================================
        // ======= 创建左侧子面板 (用于收纳更多按钮) =======
        // ==========================================
        const subPanel = document.createElement('div');
        subPanel.id = 'sub-control-panel';
        subPanel.style.cssText = `
            position: absolute;
            right: 100%;
            top: 0;
            margin-right: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            opacity: 0;
            visibility: hidden;
            /* 修复点 1: 将 0 改为具体的 translateX，并加上 scale(1) 锁定比例 */
            transform: translateX(10px) scale(1); 
            /* 修复点 2: 明确指定过渡属性，绝对不要用 all */
            transition: opacity 0.3s ease, transform 0.3s ease; 
            /* 修复点 3: 锁定变形原点在右侧，这样它展开时是向左伸展，而不是中心放大 */
            transform-origin: right center; 
            pointer-events: none;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            backdrop-filter: none !important;
        `;

        // 创建工具提示容器
        const tooltipContainer = document.createElement('div');
        tooltipContainer.id = 'control-tooltip-container';
        tooltipContainer.style.cssText = `
            position: fixed;
            z-index: 10000;
            pointer-events: none;
            opacity: 0;
            transform: translateX(-10px);
            transition: all 0.3s ease;
        `;
        
        const tooltip = document.createElement('div');
        tooltip.id = 'control-tooltip';
        tooltip.style.cssText = `
            background: rgba(0, 0, 0, 0.85);
            color: white;
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            white-space: nowrap;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            backdrop-filter: blur(8px);
        `;
        
        tooltipContainer.appendChild(tooltip);
        document.body.appendChild(tooltipContainer);
        
        // 工具提示显示函数 - 在左侧显示
        function showTooltip(button, text) {
            const rect = button.getBoundingClientRect();
            tooltip.textContent = text;
            const topPosition = rect.top + (rect.height - tooltip.offsetHeight) / 2;
            tooltipContainer.style.left = `${rect.left - tooltip.offsetWidth - 15}px`;
            tooltipContainer.style.top = `${topPosition}px`;
            tooltipContainer.style.opacity = '1';
            tooltipContainer.style.transform = 'translateX(0)';
        }
        
        function hideTooltip() {
            tooltipContainer.style.opacity = '0';
            tooltipContainer.style.transform = 'translateX(-10px)';
        }
        
        const addHoverEffect = (button, text) => {
            button.addEventListener('mouseenter', (e) => {
                showTooltip(button, text);
            });
            button.addEventListener('mousemove', (e) => {
                const rect = button.getBoundingClientRect();
                const topPosition = rect.top + (rect.height - tooltip.offsetHeight) / 2;
                tooltipContainer.style.left = `${rect.left - tooltip.offsetWidth - 15}px`;
                tooltipContainer.style.top = `${topPosition}px`;
            });
            button.addEventListener('mouseleave', () => {
                hideTooltip();
            });
        };

        // 【新增】绑定点击与触摸事件，解决移动端点击无响应或需要双击的问题
        function bindTapEvent(element, callback) {
            let touchMoved = false;
            element.addEventListener('touchstart', () => { touchMoved = false; }, { passive: true });
            element.addEventListener('touchmove', () => { touchMoved = true; }, { passive: true });
            element.addEventListener('touchend', (e) => {
                if (!touchMoved) {
                    e.preventDefault();
                    e.stopPropagation();
                    callback(e);
                }
            }, { passive: false });
            element.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                callback(e);
            });
        }

        // 1. 拖拽按钮
        const dragButton = document.createElement('div');
        dragButton.id = 'drag-handle';
        dragButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px;
            background: rgba(255,255,255,0.95); border: 2px solid rgba(0,0,0,0.1);
            border-radius: 50%; color: #333; cursor: pointer; -webkit-app-region: drag;
            display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transform 0.2s; user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;
        const dragArea = document.createElement('div');
        dragArea.style.cssText = `position: absolute; top: 0; left: 0; width: 100%; height: 100%; -webkit-app-region: drag; z-index: 1;`;
        const iconContainer = document.createElement('div');
        iconContainer.innerHTML = '<i class="fa-solid fa-arrows-up-down-left-right"></i>';
        iconContainer.style.cssText = `position: relative; z-index: 2; pointer-events: none; display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; -webkit-app-region: drag;`;
        dragButton.appendChild(dragArea);
        dragButton.appendChild(iconContainer);

        // 2. 锁定按钮逻辑
        const lockButton = document.createElement('div');
        lockButton.id = 'lock-handle';
        let isMouseLocked = false;
        const controlButtons = []; // 保存所有需要被隐藏的按钮

        async function initLockButton() {
            lockButton.innerHTML = '<i class="fas fa-lock-open"></i>';
            lockButton.style.cssText = `
                width: ${btn_width}px; height: ${btn_height}px;
                background: rgba(255,255,255,0.95); border: 2px solid rgba(0,0,0,0.1);
                border-radius: 50%; color: #28a745; cursor: pointer; -webkit-app-region: no-drag;
                display: flex; align-items: center; justify-content: center; font-size: 14px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
                user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
            `;
            lockButton.title = await t('UnlockWindow');
            updateLockButtonState();
        }

        async function updateLockButtonState() {
            if (isMouseLocked) {
                lockButton.innerHTML = '<i class="fas fa-lock"></i>';
                lockButton.style.color = '#dc3545';
                lockButton.title = await t('UnlockWindow');
            } else {
                lockButton.innerHTML = '<i class="fas fa-lock-open"></i>';
                lockButton.style.color = '#28a745';
                lockButton.title = await t('LockWindow');
            }
        }

        function hideOtherButtons() {
            // 如果子面板开着，强制关闭
            if (isSubPanelOpen) {
                isSubPanelOpen = false;
                updateMoreButtonState();
            }
            controlButtons.forEach(button => {
                if (button && button !== lockButton) {
                    button.style.display = 'none'; // 直接隐藏，让布局自然收缩
                }
            });
            lockButton.style.marginBottom = '0';
            lockButton.style.marginTop = 'auto';
        }

        function showAllButtons() {
            controlButtons.forEach(button => {
                if (button && button !== lockButton) {
                    button.style.display = 'flex';
                    button.style.opacity = '1';
                    button.style.visibility = 'visible';
                    button.style.transform = 'scale(1)';
                }
            });
            lockButton.style.marginBottom = '';
            lockButton.style.marginTop = '';
        }

        lockButton.addEventListener('mouseenter', () => {
            lockButton.style.background = 'rgba(255,255,255,1)';
            lockButton.style.transform = 'scale(1.1)';
            lockButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
        });
        lockButton.addEventListener('mouseleave', () => {
            lockButton.style.background = 'rgba(255,255,255,0.95)';
            lockButton.style.transform = 'scale(1)';
            lockButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });
        
        bindTapEvent(lockButton, (e) => {
            toggleMouseLock();
        });

        async function toggleMouseLock() {
            isMouseLocked = !isMouseLocked;
            if (isMouseLocked) {
                window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
                hideOtherButtons();
            } else {
                window.electronAPI.setIgnoreMouseEvents(false);
                showAllButtons();
            }
            updateLockButtonState();
            sendToMain('mouseLockStatus', { locked: isMouseLocked });
            updateButtonTooltips();
        }
        await initLockButton();

        // 3. 自动隐藏按钮
        const hideButton = document.createElement('div');
        hideButton.id = 'hide-handle';
        let isAutoHideActive = false; 
        let autoHideDisabledByPointerLock = false; 

        async function initHideButton() {
            hideButton.innerHTML = '<i class="fas fa-eye"></i>';
            hideButton.style.cssText = `
                width: ${btn_width}px; height: ${btn_height}px;
                background: rgba(255,255,255,0.95); border: 2px solid rgba(0,0,0,0.1);
                border-radius: 50%; color: #6c757d; cursor: pointer; -webkit-app-region: no-drag;
                display: flex; align-items: center; justify-content: center; font-size: 14px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
                user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
            `;
            const hideDesc = await t('AutoHideDescription');
            hideButton.title = hideDesc || '鼠标悬停自动隐藏';
            updateHideButtonState();
        }

        async function updateHideButtonState() {
            if (isAutoHideActive) {
                hideButton.innerHTML = '<i class="fas fa-eye-slash"></i>';
                hideButton.style.color = '#ffc107';
                hideButton.title = await t('AutoHideEnabled') || '自动隐藏已启用，点击关闭';
            } else {
                hideButton.innerHTML = '<i class="fas fa-eye"></i>';
                hideButton.style.color = '#6c757d';
                hideButton.title = await t('AutoHideDescription') || '鼠标悬停自动隐藏，点击启用';
            }
        }

        async function toggleAutoHide() {
            if (pointerLocked && !isAutoHideActive) {
                console.warn('Auto hide is disabled in first-person mode');
                return;
            }
            isAutoHideActive = !isAutoHideActive;
            if (isAutoHideActive) enableAutoHide();
            else disableAutoHide();
            updateHideButtonState();
            sendToMain('autoHideStatus', { enabled: isAutoHideActive });
        }

        hideButton.addEventListener('mouseenter', () => {
            hideButton.style.background = 'rgba(255,255,255,1)';
            hideButton.style.transform = 'scale(1.1)';
            hideButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
        });
        hideButton.addEventListener('mouseleave', () => {
            hideButton.style.background = 'rgba(255,255,255,0.95)';
            hideButton.style.transform = 'scale(1)';
            hideButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });

        bindTapEvent(hideButton, (e) => {
            toggleAutoHide();
        });

        await initHideButton();

        function handleModelHoverDetection(event) {
            if (!currentVrm || !isAutoHideEnabled || pointerLocked) return;
            if (hoverCheckTimeout) clearTimeout(hoverCheckTimeout);
            hoverCheckTimeout = setTimeout(() => {
                mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
                mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;
                raycaster.setFromCamera(mouse, camera);
                const intersects = raycaster.intersectObject(currentVrm.scene, true);
                const nowHovered = intersects.length > 0;
                if (nowHovered !== isModelHiddenByHover) {
                    isModelHiddenByHover = nowHovered;
                    if (nowHovered) hideModelWithTransition();
                    else showModelWithTransition();
                }
            }, HOVER_CHECK_INTERVAL);
        }

        function handleMouseLeaveWindow(event) {
            if (!event.relatedTarget && isAutoHideEnabled) {
                isModelHiddenByHover = false;
                showModelWithTransition();
            }
        }

        function hideModelWithTransition() {
            if (!renderer || !renderer.domElement) return;
            const canvas = renderer.domElement;
            canvas.style.transition = `opacity ${FADE_DURATION}ms ease`;
            pauseModelAnimationsForHide();
            if (hideTransitionTimer) { clearTimeout(hideTransitionTimer); hideTransitionTimer = null; }
            requestAnimationFrame(() => { canvas.style.opacity = '0'; });
            hideTransitionTimer = setTimeout(() => {
                canvas.style.pointerEvents = 'none';
                if (currentVrm) currentVrm.scene.visible = false;
                if (isElectron && !isMouseLocked && window.electronAPI?.setIgnoreMouseEvents) {
                    window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
                }
                hideTransitionTimer = null;
            }, FADE_DURATION + 10);
        }

        function showModelWithTransition() {
            if (!renderer || !renderer.domElement) return;
            const canvas = renderer.domElement;
            canvas.style.transition = `opacity ${FADE_DURATION}ms ease`;
            canvas.style.pointerEvents = 'auto';
            canvas.style.opacity = '0'; 
            if (currentVrm) currentVrm.scene.visible = true;
            resumeModelAnimationsAfterHide();
            if (hideTransitionTimer) { clearTimeout(hideTransitionTimer); hideTransitionTimer = null; }
            requestAnimationFrame(() => { canvas.style.opacity = '1'; });
            setTimeout(() => {
                canvas.style.pointerEvents = 'auto';
                if (isElectron && !isMouseLocked && window.electronAPI?.setIgnoreMouseEvents) {
                    window.electronAPI.setIgnoreMouseEvents(false);
                }
            }, FADE_DURATION + 10);
        }

        function enableAutoHide() {
            if (isAutoHideEnabled) return;
            isAutoHideEnabled = true;
            isModelHiddenByHover = false;
            controlsEnabledBeforeAutoHide = controls.enabled;
            controls.enabled = false;
            document.addEventListener('mousemove', handleModelHoverDetection);
            document.addEventListener('mouseleave', handleMouseLeaveWindow);
        }

        function disableAutoHide() {
            if (!isAutoHideEnabled) return;
            isAutoHideEnabled = false;
            isModelHiddenByHover = false;
            controls.enabled = controlsEnabledBeforeAutoHide;
            document.removeEventListener('mousemove', handleModelHoverDetection);
            document.removeEventListener('mouseleave', handleMouseLeaveWindow);
            if (hoverCheckTimeout) { clearTimeout(hoverCheckTimeout); hoverCheckTimeout = null; }
            showModelWithTransition();
        }

        // 4. 切换模型按钮
        await getAllModels();
        const prevModelButton = document.createElement('div');
        prevModelButton.id = 'prev-model-handle';
        prevModelButton.innerHTML = '<i class="fas fa-chevron-up"></i>';
        prevModelButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;
        
        const nextModelButton = document.createElement('div');
        nextModelButton.id = 'next-model-handle';
        nextModelButton.innerHTML = '<i class="fas fa-chevron-down"></i>';
        nextModelButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;

        prevModelButton.addEventListener('mouseenter', async () => {
            prevModelButton.style.background = 'rgba(255,255,255,1)';
            prevModelButton.style.transform = 'scale(1.1)';
            prevModelButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
            const prevModel = getPrevModelInfo();
            if (prevModel) prevModelButton.title = `${await t('Previous')}: ${prevModel.name}`;
        });
        prevModelButton.addEventListener('mouseleave', () => {
            prevModelButton.style.background = 'rgba(255,255,255,0.95)';
            prevModelButton.style.transform = 'scale(1)';
            prevModelButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });
        nextModelButton.addEventListener('mouseenter', async () => {
            nextModelButton.style.background = 'rgba(255,255,255,1)';
            nextModelButton.style.transform = 'scale(1.1)';
            nextModelButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
            const nextModel = getNextModelInfo();
            if (nextModel) nextModelButton.title = `${await t('Next')}: ${nextModel.name}`;
        });
        nextModelButton.addEventListener('mouseleave', () => {
            nextModelButton.style.background = 'rgba(255,255,255,0.95)';
            nextModelButton.style.transform = 'scale(1)';
            nextModelButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });

        bindTapEvent(prevModelButton, (e) => { 
            if (allModels.length > 1) switchToModel(currentModelIndex - 1); 
        });
        bindTapEvent(nextModelButton, (e) => { 
            if (allModels.length > 1) switchToModel(currentModelIndex + 1); 
        });

        async function initModelButtons() {
            if (allModels.length <= 1) {
                prevModelButton.style.opacity = '0.5'; prevModelButton.style.cursor = 'not-allowed'; prevModelButton.title = 'No other models available';
                nextModelButton.style.opacity = '0.5'; nextModelButton.style.cursor = 'not-allowed'; nextModelButton.title = 'No other models available';
            } else {
                const prevModel = getPrevModelInfo();
                const nextModel = getNextModelInfo();
                prevModelButton.title = prevModel ? `Previous: ${prevModel.name}` : 'Previous Model';
                nextModelButton.title = nextModel ? `Next: ${nextModel.name}` : 'Next Model';
            }
        }
        initModelButtons();

        // 5. 新增的“更多功能”按钮
        const moreButton = document.createElement('div');
        moreButton.id = 'more-handle';
        let isSubPanelOpen = false;
        moreButton.innerHTML = '<i class="fas fa-ellipsis-v"></i>';
        moreButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;

        async function updateMoreButtonState() {
            if (isSubPanelOpen) {
                subPanel.style.opacity = '1';
                subPanel.style.visibility = 'visible';
                subPanel.style.transform = 'translateX(0) scale(1)'; 
                subPanel.style.pointerEvents = 'auto';
                moreButton.innerHTML = '<i class="fas fa-caret-right"></i>';
                moreButton.style.color = '#007bff';
                moreButton.title = await t('collapse') || '收起面板';
            } else {
                subPanel.style.opacity = '0';
                subPanel.style.visibility = 'hidden';
                subPanel.style.transform = 'translateX(10px) scale(1)';
                subPanel.style.pointerEvents = 'none';
                moreButton.innerHTML = '<i class="fas fa-ellipsis-v"></i>';
                moreButton.style.color = '#333';
                moreButton.title = await t('MoreOptions') || '更多功能';
            }
            showTooltip(moreButton, moreButton.title);
        }

        bindTapEvent(moreButton, async (e) => {
            isSubPanelOpen = !isSubPanelOpen;
            updateMoreButtonState();
        });

        moreButton.addEventListener('mouseenter', async () => {
            moreButton.style.background = 'rgba(255,255,255,1)';
            moreButton.style.transform = 'scale(1.1)';
            moreButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
            if (!moreButton.title) moreButton.title = await t('MoreOptions') || '更多功能';
            showTooltip(moreButton, moreButton.title);
        });
        moreButton.addEventListener('mouseleave', () => {
            moreButton.style.background = 'rgba(255,255,255,0.95)';
            moreButton.style.transform = 'scale(1)';
            moreButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
            hideTooltip();
        });

        const subtitleButton = document.createElement('div');
        subtitleButton.id = 'subtitle-handle';
        subtitleButton.innerHTML = '<i class="fas fa-closed-captioning"></i>';
        subtitleButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
            color: ${isSubtitleEnabled ? '#28a745' : '#dc3545'};
        `;
        subtitleButton.addEventListener('mouseenter', () => { subtitleButton.style.background = 'rgba(255,255,255,1)'; subtitleButton.style.transform = 'scale(1.1)'; subtitleButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)'; });
        subtitleButton.addEventListener('mouseleave', () => { subtitleButton.style.background = 'rgba(255,255,255,0.95)'; subtitleButton.style.transform = 'scale(1)'; subtitleButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)'; });
        
        bindTapEvent(subtitleButton, async (e) => {
            isSubtitleEnabled = !isSubtitleEnabled;
            toggleSubtitle(isSubtitleEnabled);
            subtitleButton.style.color = isSubtitleEnabled ? '#28a745' : '#dc3545';
            subtitleButton.title = isSubtitleEnabled ? await t('SubtitleEnabled') : await t('SubtitleDisabled');
        });

        // 6. 刷新与关闭按钮
        const refreshButton = document.createElement('div');
        refreshButton.id = 'refresh-handle';
        refreshButton.innerHTML = '<i class="fas fa-redo-alt"></i>';
        refreshButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;
        refreshButton.addEventListener('mouseenter', () => {
            refreshButton.style.background = 'rgba(255,255,255,1)'; refreshButton.style.transform = 'scale(1.1)'; refreshButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
        });
        refreshButton.addEventListener('mouseleave', () => {
            refreshButton.style.background = 'rgba(255,255,255,0.95)'; refreshButton.style.transform = 'scale(1)'; refreshButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });
        bindTapEvent(refreshButton, (e) => { window.location.reload(); });

        const closeButton = document.createElement('div');
        closeButton.id = 'close-handle';
        closeButton.innerHTML = '<i class="fas fa-times"></i>';
        closeButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;
        closeButton.addEventListener('mouseenter', () => {
            closeButton.style.background = 'rgba(255,255,255,1)'; closeButton.style.transform = 'scale(1.1)'; closeButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
        });
        closeButton.addEventListener('mouseleave', () => {
            closeButton.style.background = 'rgba(255,255,255,0.95)'; closeButton.style.transform = 'scale(1)'; closeButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });
        bindTapEvent(closeButton, (e) => { window.close(); });

        // ======= 以下为收纳在子面板中的按钮 =======

        // 子 1. 调整模式按钮
        const moveModeBtn = document.createElement('div');
        moveModeBtn.id = 'move-mode-handle';
        let transformState = 0; 
        moveModeBtn.innerHTML = '<i class="fa-solid fa-cube"></i>'; 
        moveModeBtn.title = await t('ModeOff') || 'Mode: Off'; 
        moveModeBtn.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer; 
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center; 
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;
        bindTapEvent(moveModeBtn, async (e) => {
            if (!currentVrm) return;
            transformState = (transformState + 1) % 4;
            updateTransformState();
        });
        async function updateTransformState() {
            if (typeof transformControl === 'undefined') return;
            if (transformState !== 0 && currentVrmWrapper) transformControl.attach(currentVrmWrapper);
            switch (transformState) {
                case 0: transformControl.detach(); moveModeBtn.style.color = '#333'; moveModeBtn.style.background = 'rgba(255,255,255,0.95)'; moveModeBtn.innerHTML = '<i class="fa-solid fa-cube"></i>'; moveModeBtn.title = await t('ModeOff') || 'Mode: Off'; break;
                case 1: transformControl.setMode('translate'); transformControl.setSpace('world'); moveModeBtn.style.color = '#ff6b35'; moveModeBtn.style.background = 'rgba(255,255,255,1)'; moveModeBtn.innerHTML = '<i class="fa-solid fa-arrows-left-right-to-line"></i>'; moveModeBtn.title = await t('ModeMove') || 'Move Mode'; break;
                case 2: transformControl.setMode('rotate'); transformControl.setSpace('local'); moveModeBtn.style.color = '#007bff'; moveModeBtn.style.background = 'rgba(255,255,255,1)'; moveModeBtn.innerHTML = '<i class="fas fa-sync-alt"></i>'; moveModeBtn.title = await t('ModeRotate') || 'Rotate Mode'; break;
                case 3: transformControl.setMode('scale'); transformControl.setSpace('local'); moveModeBtn.style.color = '#e83e8c'; moveModeBtn.style.background = 'rgba(255,255,255,1)'; moveModeBtn.innerHTML = '<i class="fas fa-compress-arrows-alt"></i>'; moveModeBtn.title = await t('ModeScale') || 'Scale Mode'; break;
            }
        }
        moveModeBtn.addEventListener('mouseenter', () => { moveModeBtn.style.transform = 'scale(1.1)'; moveModeBtn.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)'; showTooltip(moveModeBtn, moveModeBtn.title); });
        moveModeBtn.addEventListener('mouseleave', () => { moveModeBtn.style.transform = 'scale(1)'; moveModeBtn.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)'; hideTooltip(); });
        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            if (!currentVrm || typeof transformControl === 'undefined') return;
            if (e.code === 'Escape') { transformState = 0; updateTransformState(); return; }
            if (transformState !== 0) {
                switch(e.code) { case 'KeyT': transformState = 1; updateTransformState(); break; case 'KeyR': transformState = 2; updateTransformState(); break; case 'KeyS': transformState = 3; updateTransformState(); break; }
            }
        });

        // 子 2. WS 状态
        const wsStatusButton = document.createElement('div');
        wsStatusButton.id = 'ws-status-handle';
        wsStatusButton.innerHTML = '<i class="fas fa-wifi"></i>';
        wsStatusButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
            color: ${wsConnected ? '#28a745' : '#dc3545'};
        `;
        bindTapEvent(wsStatusButton, (e) => {
            if (wsConnected) { if (ttsWebSocket) ttsWebSocket.close(); } else { initTTSWebSocket(); }
        });
        wsStatusButton.addEventListener('mouseenter', () => { wsStatusButton.style.background = 'rgba(255,255,255,1)'; wsStatusButton.style.transform = 'scale(1.1)'; wsStatusButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)'; });
        wsStatusButton.addEventListener('mouseleave', () => { wsStatusButton.style.background = 'rgba(255,255,255,0.95)'; wsStatusButton.style.transform = 'scale(1)'; wsStatusButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)'; });
        async function updateWSStatus() { wsStatusButton.style.color = wsConnected ? '#28a745' : '#dc3545'; wsStatusButton.title = wsConnected ? await t('WebSocketConnected') : await t('WebSocketDisconnected'); }
        setInterval(updateWSStatus, 1000);

        // 子 4. 闲置动画按钮
        const idleAnimationButton = document.createElement('div');
        idleAnimationButton.id = 'idle-animation-handle';
        idleAnimationButton.innerHTML = useVRMAIdleAnimations ? '<i class="fas fa-stop"></i>' : '<i class="fas fa-play"></i>';
        idleAnimationButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: ${useVRMAIdleAnimations ? '#ff6b35' : '#28a745'};
            cursor: pointer; -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;
        idleAnimationButton.addEventListener('mouseenter', () => { idleAnimationButton.style.background = 'rgba(255,255,255,1)'; idleAnimationButton.style.transform = 'scale(1.1)'; idleAnimationButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)'; });
        idleAnimationButton.addEventListener('mouseleave', () => { idleAnimationButton.style.background = 'rgba(255,255,255,0.95)'; idleAnimationButton.style.transform = 'scale(1)'; idleAnimationButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)'; });
        
        bindTapEvent(idleAnimationButton, async (e) => {
            if (isIdleAnimationModeChanging) return;
            await toggleIdleAnimationMode();
        });

        // 子 5. XR 按钮
        const xrAutoBtn = document.createElement('div');
        xrAutoBtn.id = 'xr-auto-btn';
        xrAutoBtn.innerHTML = '<i class="fa-solid fa-vr-cardboard"></i>';
        xrAutoBtn.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer; 
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center; 
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px); display: none;`;
        let canAR = false, canVR = false;
        Promise.all([
            navigator.xr.isSessionSupported('immersive-ar').then(yes=>{ canAR=yes; }),
            navigator.xr.isSessionSupported('immersive-vr').then(yes=>{ canVR=yes; })
        ]).then(()=>{ xrAutoBtn.style.display = (canAR || canVR) ? 'flex' : 'none'; });
        let xrSession = null;
        let xrRefSpace = null;
        
        bindTapEvent(xrAutoBtn, async (e) => {
            if (renderer.xr.isPresenting) {
                await renderer.xr.getSession().end();
                return;
            }

            // 自动检测模式
            const mode = canAR ? 'immersive-ar' : 'immersive-vr';
            
            // 关键点：指定 UI 容器，而不是整个 body，这样交互更精准
            const sessionInit = {
                optionalFeatures: ['local-floor', 'hit-test', 'dom-overlay'],
                domOverlay: { root: document.body } 
            };

            try {
                const session = await navigator.xr.requestSession(mode, sessionInit);
                renderer.xr.setSession(session);
                xrSession = session;

                renderer.setAnimationLoop(xrAnimate);
                
                document.getElementById('control-panel').style.display = 'none'; 
                document.getElementById('ptt-floating-btn').classList.add('visible');

                if (currentVrm) {
                    // 在 VR 中将模型稍微放远一点，否则会“贴脸”
                    currentVrm.scene.position.set(0, 0, -1.5);
                }

                // 处理 XR 输入（点击控制器触发点击 UI）
                session.addEventListener('select', (event) => {
                    console.log('XR Select triggered');
                });

            } catch (e) {
                console.error('Failed to start XR session:', e);
                alert('无法进入 XR 模式: ' + e.message);
            }
        });
        renderer.xr.addEventListener('sessionend', () => { 
            renderer.setAnimationLoop(null); 
            animate(); 
            xrSession = null; 
            
            // === 退出 XR 恢复 UI ===
            document.getElementById('control-panel').style.display = 'flex';
            // 如果进 XR 之前没开语音，退出来后也把它隐藏回去
            if (!pttVisible) {
                document.getElementById('ptt-floating-btn').classList.remove('visible');
            }
        });
        function xrAnimate(time, frame) {
          const delta = clock.getDelta();
          if (currentVrm) currentVrm.update(delta);
          if (currentMixer) currentMixer.update(delta);
          renderer.render(scene, camera);
        }

        // 子 6. VMC 按钮
        const vmcButton = document.createElement('div');
        vmcButton.id = 'vmc-handle';
        vmcButton.innerHTML = '<i class="fas fa-broadcast-tower"></i>';
        vmcButton.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333;
            cursor: pointer; -webkit-app-region: no-drag; display: flex;
            align-items: center; justify-content: center; font-size: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);`;
        let vmcApp = null;
        let vmcWrapper = null;
        
        bindTapEvent(vmcButton, async (e) => {
            if (vmcApp) {
                vmcApp.unmount(); document.body.removeChild(vmcWrapper); vmcApp = null; vmcWrapper = null; return;
            }
            const cfg = await window.electronAPI.getVMCConfig();
            const { ElDialog, ElForm, ElFormItem, ElInput, ElSwitch, ElButton, ElInputNumber } = ElementPlus;
            vmcWrapper = document.createElement('div');
            document.body.appendChild(vmcWrapper);
            vmcApp = Vue.createApp({
                data() {
                    return {
                        dialogVisible: true,
                        form: {
                            receive: { enable: cfg.receive.enable, port: cfg.receive.port, syncExpression: cfg.receive.syncExpression },
                            send: { enable: cfg.send.enable, host: cfg.send.host, port: cfg.send.port }
                        },
                        translations: { title: '', receiveEnable: '', receivePort: '', sendEnable: '', sendHost: '', sendPort: '', cancelButton: '', saveButton: '' }
                    }
                },
                async mounted() {
                    this.translations.title = await t('vmcSettings'); this.translations.receiveEnable = await t('vmcReceiveEnable'); this.translations.receivePort = await t('vmcReceivePort');
                    this.translations.sendEnable = await t('vmcSendEnable'); this.translations.sendHost = await t('vmcSendHost'); this.translations.sendPort = await t('vmcSendPort');
                    this.translations.cancelButton = await t('cancel'); this.translations.saveButton = await t('save'); this.translations.syncExpression = await t('syncExpression');
                },
                methods: {
                    async saveConfig() {
                        await window.electronAPI.setVMCConfig({
                            receive: { enable: this.form.receive.enable, port: this.form.receive.port, syncExpression: this.form.receive.syncExpression },
                            send: { enable: this.form.send.enable, host: this.form.send.host, port: this.form.send.port }
                        });
                        setVMCReceive(this.form.receive.enable, this.form.receive.syncExpression);
                        this.close();
                    },
                    cancel() { this.close(); },
                    close() { this.dialogVisible = false; vmcApp.unmount(); document.body.removeChild(vmcWrapper); vmcApp = null; vmcWrapper = null; }
                },
                template: `
                    <el-dialog v-model="dialogVisible" :title="translations.title" width="420px" :modal="false" :close-on-click-modal="false" append-to-body custom-class="vmc-dialog" @close="close" style="background: rgba(255, 255, 255, 0.25) !important;backdrop-filter: blur(20px);border-radius: 20px !important;">
                        <div style="padding: 0 10px;">
                            <div style="margin-bottom: 20px; padding: 15px; background: rgba(245, 247, 250, 0.75)!important; border-radius: 20px;">
                                <div style="display: flex; align-items: center; margin-bottom: 15px;"><el-switch v-model="form.receive.enable"></el-switch><span style="margin-left: 10px; font-weight: 500;">{{ translations.receiveEnable }}</span></div>
                                <div style="display:flex;align-items:center;margin-top:8px;"><el-switch v-model="form.receive.syncExpression"></el-switch><span style="margin-left:10px;font-size:14px;">{{ translations.syncExpression }}</span></div>
                                <div style="display: flex; align-items: center; gap: 10px;"><span style="width: 100px;margin-right:30px; font-size: 14px;">{{ translations.receivePort }}:</span><el-input-number v-model="form.receive.port" :min="1024" :max="65535" controls-position="right" style="width: 200px;"></el-input-number></div>
                            </div>
                            <div style="margin-bottom: 20px; padding: 15px; background: rgba(245, 247, 250, 0.75)!important; border-radius: 20px;">
                                <div style="display: flex; align-items: center; margin-bottom: 15px;"><el-switch v-model="form.send.enable"></el-switch><span style="margin-left: 10px;margin-right:30px; font-weight: 500;">{{ translations.sendEnable }}</span></div>
                                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;"><span style="width: 100px; margin-right:30px;font-size: 14px;">{{ translations.sendHost }}:</span><el-input v-model="form.send.host" style="width: 200px;"></el-input></div>
                                <div style="display: flex; align-items: center; gap: 10px;"><span style="width: 100px;margin-right:30px; font-size: 14px;">{{ translations.sendPort }}:</span><el-input-number v-model="form.send.port" :min="1024" :max="65535" controls-position="right" style="width: 200px;"></el-input-number></div>
                            </div>
                        </div>
                        <template #footer><div style="text-align: right;"><el-button @click="cancel" style="margin-right: 10px;">{{ translations.cancelButton }}</el-button><el-button type="primary" @click="saveConfig">{{ translations.saveButton }}</el-button></div></template>
                    </el-dialog>
                `
            });
            vmcApp.use(ElementPlus); vmcApp.mount(vmcWrapper);
        });

        // 子 7. 第一人称视角按钮
        const switchCtrlBtn = document.createElement('div');
        switchCtrlBtn.id = 'switch-controls-handle';
        switchCtrlBtn.innerHTML = '<i class="fas fa-gamepad"></i>';
        switchCtrlBtn.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333; cursor: pointer; -webkit-app-region: no-drag;
            display: flex; align-items: center; justify-content: center; font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transform 0.2s; user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;
        function createPointerLockControls() {
            pointerLockControls = new PointerLockControls(camera, renderer.domElement);
            scene.add(pointerLockControls.getObject());
        }
        function toggleControls() {
            if (!pointerLockControls) createPointerLockControls();
            if (!pointerLocked) {
                orbitControlsSaved = controls; orbitControlsSaved.enabled = false;
                pointerLockControls.lock(); enablePointerLockMovement(); pointerLocked = true;
                if (isAutoHideActive) { disableAutoHide(); isAutoHideActive = false; autoHideDisabledByPointerLock = true; updateHideButtonState(); }
                switchCtrlBtn.style.color = '#ffc73bff';
            } else {
                pointerLockControls.unlock(); disablePointerLockMovement(); pointerLocked = false;
                if (autoHideDisabledByPointerLock) { enableAutoHide(); isAutoHideActive = true; autoHideDisabledByPointerLock = false; updateHideButtonState(); }
                switchCtrlBtn.style.color = '#333';
                if (orbitControlsSaved) orbitControlsSaved.enabled = true;
            }
        }
        
        bindTapEvent(switchCtrlBtn, (e) => { toggleControls(); });

        switchCtrlBtn.addEventListener('mouseenter', async () => {
            switchCtrlBtn.style.background = 'rgba(255,255,255,1)'; switchCtrlBtn.style.transform = 'scale(1.1)'; switchCtrlBtn.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
            switchCtrlBtn.title = pointerLocked ? await t('ExitFirstPerson') || 'Exit First-Person' : await t('EnterFirstPerson') || 'Enter First-Person';
        });
        switchCtrlBtn.addEventListener('mouseleave', () => { switchCtrlBtn.style.background = 'rgba(255,255,255,0.95)'; switchCtrlBtn.style.transform = 'scale(1)'; switchCtrlBtn.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)'; });
        document.addEventListener('pointerlockchange', () => {
            if (document.pointerLockElement !== renderer.domElement && pointerLocked) toggleControls();
        });

        // 1. 创建按钮并设置初始属性
        const voiceControlBtn = document.createElement('div');
        voiceControlBtn.id = 'voice-toggle-handle';
        voiceControlBtn.innerHTML = '<i class="fa-solid fa-microphone"></i>';
        voiceControlBtn.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #000000; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;

        // 2. 初始 Hover 提示 (使用你代码里的翻译函数 t)
        (async () => {
            const initialTitle = await t('EnableVoiceInput') || '开启语音输入';
            voiceControlBtn.title = initialTitle;
            addHoverEffect(voiceControlBtn, initialTitle); // 调用你现有的提示增强函数
        })();

        // 3. 点击事件中增加标题动态更新
        bindTapEvent(voiceControlBtn, async (e) => {
            pttVisible = !pttVisible;
            const fBtn = document.getElementById('ptt-floating-btn');
            
            // 获取新的标题文本
            const activeTitle = pttVisible 
                ? (await t('DisableVoiceInput') || '关闭语音输入') 
                : (await t('EnableVoiceInput') || '开启语音输入');

           if (pttVisible) {
                fBtn.classList.add('visible');
                voiceControlBtn.style.background = '#ff6b35';
                voiceControlBtn.style.color = '#ff6b35';
            } else {
                fBtn.classList.remove('visible');
                voiceControlBtn.style.background = '#000000';
                voiceControlBtn.style.color = '#000000';
            }

            // 更新原生标题和自定义 Tooltip
            voiceControlBtn.title = activeTitle;
            showTooltip(voiceControlBtn, activeTitle); // 立即更新当前显示的黑色气泡
        });

        // 1. 创建文字控制按钮
        const textControlBtn = document.createElement('div');
        textControlBtn.id = 'text-toggle-handle';
        textControlBtn.innerHTML = '<i class="fas fa-keyboard"></i>'; // 统一使用 fas
        textControlBtn.style.cssText = `
            width: ${btn_width}px; height: ${btn_height}px; background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1); border-radius: 50%; color: #333333; cursor: pointer;
            -webkit-app-region: no-drag; display: flex; align-items: center; justify-content: center;
            font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform 0.2s;
            user-select: none; pointer-events: auto; backdrop-filter: blur(10px);
        `;

        // 补上与其他按钮一致的悬浮动效，防止 Hover 时变色冲突
        textControlBtn.addEventListener('mouseenter', () => { 
            textControlBtn.style.background = 'rgba(255,255,255,1)'; 
            textControlBtn.style.transform = 'scale(1.1)'; 
            textControlBtn.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)'; 
        });
        textControlBtn.addEventListener('mouseleave', () => { 
            textControlBtn.style.background = 'rgba(255,255,255,0.95)'; 
            textControlBtn.style.transform = 'scale(1)'; 
            textControlBtn.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)'; 
        });

        let textInputVisible = false;
        (async () => {
            const initialTitle = await t('EnableTextInput') || '开启文字输入';
            textControlBtn.title = initialTitle;
            addHoverEffect(textControlBtn, initialTitle);
        })();

        bindTapEvent(textControlBtn, async (e) => {
            textInputVisible = !textInputVisible;
            const container = document.getElementById('text-input-container');
            
            const activeTitle = textInputVisible 
                ? (await t('DisableTextInput') || '关闭文字输入') 
                : (await t('EnableTextInput') || '开启文字输入');

            if (textInputVisible) {
                // 显示输入框，图标变蓝，背景维持白色
                container.style.opacity = '1';
                container.style.pointerEvents = 'auto';
                container.style.transform = 'translateX(-50%) translateY(0)';
                textControlBtn.style.color = '#007bff'; 
                setTimeout(() => document.getElementById('text-input-field').focus(), 300);
            } else {
                // 隐藏输入框，图标恢复黑灰
                container.style.opacity = '0';
                container.style.pointerEvents = 'none';
                container.style.transform = 'translateX(-50%) translateY(20px)';
                textControlBtn.style.color = '#333333'; 
                document.getElementById('text-input-field').blur();
            }

            textControlBtn.title = activeTitle;
            showTooltip(textControlBtn, activeTitle);
        });

        // ==========================================
        // ======= 组装所有面板与按钮 ===================
        // ==========================================
        
        // 1. 组装主面板（按顺序排列）
        controlPanel.appendChild(dragButton);          // 拖拽
        controlPanel.appendChild(lockButton);          // 锁定穿透
        controlPanel.appendChild(hideButton);          // 模型不遮挡
        controlPanel.appendChild(prevModelButton);     // 上一个模型
        controlPanel.appendChild(nextModelButton);     // 下一个模型
        controlPanel.appendChild(subtitleButton);          // 字幕开关
        controlPanel.appendChild(voiceControlBtn);        // 语音控制
        controlPanel.appendChild(textControlBtn); 
        controlPanel.appendChild(moreButton);          // 🌟 更多按钮
        controlPanel.appendChild(refreshButton);       // 刷新
        controlPanel.appendChild(closeButton);         // 关闭


        // 2. 组装子面板（收纳次要按钮）
        if (isElectron) {
            subPanel.appendChild(vmcButton);           // VMC 设置
        }
        subPanel.appendChild(idleAnimationButton);     // 闲置动画
        subPanel.appendChild(switchCtrlBtn);           // 第一人称
        subPanel.appendChild(moveModeBtn);             // 物体平移缩放
        subPanel.appendChild(wsStatusButton);          // WS 状态
        subPanel.appendChild(xrAutoBtn);               // XR

        // 3. 把子面板挂载到主面板内部
        controlPanel.appendChild(subPanel);
        
        // 4. 将所有需要被“锁定”操作隐藏的按钮放入数组
        controlButtons.push(
            dragButton,
            hideButton,
            prevModelButton, 
            nextModelButton, 
            voiceControlBtn,
            textControlBtn,
            moreButton,          // 让"更多"按钮受锁定控制
            refreshButton, 
            closeButton,
            // 以下为子面板内的按钮，一同加入数组确保状态一致
            subtitleButton, 
            idleAnimationButton, 
            switchCtrlBtn,
            moveModeBtn,
            wsStatusButton,
            xrAutoBtn
        );
        if (isElectron) controlButtons.push(vmcButton);

        // 5. 添加到页面
        document.body.appendChild(controlPanel);

        // 初始化所有提示文本
        dragButton.title = await t('dragWindow');
        refreshButton.title = await t('refreshWindow');
        closeButton.title = await t('closeWindow');
        
        addHoverEffect(dragButton, await t('dragWindow'));
        addHoverEffect(lockButton, isMouseLocked ? await t('UnlockWindow') : await t('LockWindow'));
        addHoverEffect(hideButton, isAutoHideActive ? await t('AutoHideEnabled') : await t('AutoHideDescription'));
        addHoverEffect(refreshButton, await t('refreshWindow'));
        addHoverEffect(closeButton, await t('closeWindow'));
        
        addHoverEffect(wsStatusButton, wsConnected ? await t('WebSocketConnected') : await t('WebSocketDisconnected'));
        addHoverEffect(subtitleButton, isSubtitleEnabled ? await t('SubtitleEnabled') : await t('SubtitleDisabled'));
        addHoverEffect(idleAnimationButton, useVRMAIdleAnimations ? await t('UsingVRMAAnimations') : await t('UsingProceduralAnimations'));
        addHoverEffect(xrAutoBtn, await t('EnterXR') || 'Enter XR');
        addHoverEffect(switchCtrlBtn, pointerLocked ? await t('ExitFirstPerson') || 'Exit First-Person' : await t('EnterFirstPerson') || 'Enter First-Person');
        
        if (isElectron) {
            addHoverEffect(vmcButton, await t('vmcSettings') || 'VMC Settings');
        }

        async function updateButtonTooltips() {
            addHoverEffect(lockButton, isMouseLocked ? await t('UnlockWindow') : await t('LockWindow'));
            addHoverEffect(hideButton, isAutoHideActive ? await t('AutoHideEnabled') : await t('AutoHideDescription'));
            addHoverEffect(wsStatusButton, wsConnected ? await t('WebSocketConnected') : await t('WebSocketDisconnected'));
            addHoverEffect(subtitleButton, isSubtitleEnabled ? await t('SubtitleEnabled') : await t('SubtitleDisabled'));
            addHoverEffect(switchCtrlBtn, pointerLocked ? await t('ExitFirstPerson') || 'Exit First-Person (WASD+QE)' : await t('EnterFirstPerson') || 'Enter First-Person (WASD+QE)');
            addHoverEffect(idleAnimationButton, useVRMAIdleAnimations ? await t('UsingVRMAAnimations') : await t('UsingProceduralAnimations'));
            
            const prevModel = getPrevModelInfo();
            const nextModel = getNextModelInfo();
            addHoverEffect(prevModelButton, prevModel ? `${await t('Previous')}: ${prevModel.name}` : await t('NoPreviousModel'));
            addHoverEffect(nextModelButton, nextModel ? `${await t('Next')}: ${nextModel.name}` : await t('NoNextModel'));
            switch (transformState) {
                case 0: moveModeBtn.title = await t('ModeOff') || 'Mode: Off'; break;
                case 1: moveModeBtn.title = await t('ModeMove') || 'Move Mode'; break;
                case 2: moveModeBtn.title = await t('ModeRotate') || 'Rotate Mode'; break;
                case 3: moveModeBtn.title = await t('ModeScale') || 'Scale Mode'; break;
            }
            const vText = pttVisible ? await t('DisableVoiceInput') : await t('EnableVoiceInput');
            addHoverEffect(voiceControlBtn, vText || (pttVisible ? '关闭语音输入' : '开启语音输入'));
            const tText = textInputVisible ? await t('DisableTextInput') : await t('EnableTextInput');
            addHoverEffect(textControlBtn, tText || (textInputVisible ? '关闭文字输入' : '开启文字输入'));
        }
        setInterval(updateButtonTooltips, 1000);

        // ======= 显示/隐藏控制逻辑 (整个面板的自动淡出) =======
        let hideTimeout;
        let isControlPanelHovered = false;
        
        function showControlPanel() {
            clearTimeout(hideTimeout);
            controlPanel.style.opacity = '1';
            controlPanel.style.visibility = 'visible';
            controlPanel.style.transform = 'translateX(0)';
            controlPanel.style.pointerEvents = 'auto';
        }
        
        function hideControlPanel() {
            if (!isControlPanelHovered) {
                // 如果鼠标静止，自动折叠子面板
                if (isSubPanelOpen) {
                    isSubPanelOpen = false;
                    updateMoreButtonState();
                }
                controlPanel.style.opacity = '0';
                controlPanel.style.visibility = 'hidden';
                controlPanel.style.transform = 'translateX(20px)';
                controlPanel.style.pointerEvents = 'none';
            }
        }
        
        function scheduleHide() {
            clearTimeout(hideTimeout);
            hideTimeout = setTimeout(hideControlPanel, 3000); // 移动端建议延长时间到3秒更方便操作
        }
        
        document.body.addEventListener('mouseenter', () => { showControlPanel(); });
        document.body.addEventListener('mousemove', () => { showControlPanel(); scheduleHide(); });
        document.body.addEventListener('mouseleave', () => { if (!isControlPanelHovered) scheduleHide(); });

        // 【新增移动端优化】点击屏幕呼出控制面板并隐藏 Tooltip
        document.body.addEventListener('touchstart', (e) => {
            hideTooltip();
            // 如果点击的是控制面板内的元素，不处理，让面板自己处理
            if (controlPanel.contains(e.target)) return;
            showControlPanel(); 
            scheduleHide(); 
        }, { passive: true });
        
        controlPanel.addEventListener('mouseenter', () => {
            if (renderer.xr.isPresenting) return; 
            isControlPanelHovered = true;
            clearTimeout(hideTimeout);
            showControlPanel();
            if (isMouseLocked && window.electronAPI) window.electronAPI.setIgnoreMouseEvents(false);
        });
        
        controlPanel.addEventListener('mouseleave', () => {
            isControlPanelHovered = false;
            scheduleHide();
            if (isMouseLocked && window.electronAPI) window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
        });

        // 【新增移动端优化】触控面板时保持面板显示
        controlPanel.addEventListener('touchstart', () => {
            if (renderer.xr.isPresenting) return; 
            isControlPanelHovered = true;
            clearTimeout(hideTimeout);
            showControlPanel();
            if (isMouseLocked && window.electronAPI) window.electronAPI.setIgnoreMouseEvents(false);
        }, { passive: true });

        controlPanel.addEventListener('touchend', () => {
            isControlPanelHovered = false;
            scheduleHide();
            if (isMouseLocked && window.electronAPI) window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
        }, { passive: true });
        
        let mouseStopTimeout;
        document.body.addEventListener('mousemove', () => {
            clearTimeout(mouseStopTimeout);
            mouseStopTimeout = setTimeout(() => {
                if (!isControlPanelHovered) hideControlPanel();
            }, 3000); 
        });
        
        scheduleHide();
        setupPttInteraction();
        setupTextInteraction();

        // ======= 【新增】锁定状态下放行底部交互组件的鼠标穿透 =======
        const pttBtn = document.getElementById('ptt-floating-btn');
        if (pttBtn) {
            pttBtn.addEventListener('mouseenter', () => {
                if (isMouseLocked && window.electronAPI) window.electronAPI.setIgnoreMouseEvents(false);
            });
            pttBtn.addEventListener('mouseleave', () => {
                if (isMouseLocked && window.electronAPI) window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
            });
        }

        const textInputContainer = document.getElementById('text-input-container');
        const textInputField = document.getElementById('text-input-field');
        if (textInputContainer && textInputField) {
            textInputContainer.addEventListener('mouseenter', () => {
                if (isMouseLocked && window.electronAPI) window.electronAPI.setIgnoreMouseEvents(false);
            });
            textInputContainer.addEventListener('mouseleave', () => {
                // 如果鼠标移出且输入框没有聚焦，才恢复鼠标穿透
                if (isMouseLocked && window.electronAPI && document.activeElement !== textInputField) {
                    window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
                }
            });
            textInputField.addEventListener('blur', () => {
                // 输入框失去焦点时，如果鼠标已经不在容器内，恢复穿透
                if (isMouseLocked && window.electronAPI && !textInputContainer.matches(':hover')) {
                    window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
                }
            });
            textInputField.addEventListener('focus', () => {
                // 输入框聚焦时强制取消穿透，防止打字打一半鼠标乱晃导致键盘失焦
                if (isMouseLocked && window.electronAPI) window.electronAPI.setIgnoreMouseEvents(false);
            });
        }
        // ==============================================================

        console.log('控制面板已加载，更多功能折叠完毕。');

    }, 1000);
}

addcontrolPanel();
// 在全局变量区域添加
let ttsWebSocket = null;
let wsConnected = false;
let currentAudioContext = null; // 用于管理音频处理
const chunkAnimations = new Map(); // 用于存储每个语音块的动画状态
let vrmAudioQueue = [];            // 排序后的待播放队列
let vrmReceiveBuffer = new Map();   // 排序缓冲区
let nextExpectedIndex = 0;         // 期望的下一个 Index
let isVrmPlaying = false;          // 播放状态锁

function initTTSWebSocket() {
    const http_protocol = window.location.protocol;
    const ws_protocol = http_protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${ws_protocol}//${window.location.host}/ws/vrm`;
    
    ttsWebSocket = new WebSocket(wsUrl);
    ttsWebSocket.binaryType = 'arraybuffer'; // 必须！

    ttsWebSocket.onopen = () => { wsConnected = true; console.log('VRM Binary Connected'); };

    ttsWebSocket.onmessage = async (event) => {
        if (event.data instanceof ArrayBuffer) {
            // 1. 解析二进制
            const buffer = event.data;
            const view = new DataView(buffer);
            const jsonLen = view.getUint32(0, true);
            const metadata = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, jsonLen)));
            const audioDataBytes = new Uint8Array(buffer, 4 + jsonLen);

            if (metadata.type === 'omni_chunk') {
                // ======= 【复用你的逻辑】 =======
                isOmniMode = true;
                isAudioStreaming = true;
                if (metadata.text) fullTargetText = metadata.text;
                
                // 将原始字节转为 Base64 喂给你的 processOmniStreaming
                const b64 = btoa(String.fromCharCode.apply(null, audioDataBytes));
                processOmniStreaming({
                    audioData: b64,
                    sampleRate: metadata.sampleRate
                });
                
                // 启动你的打字机
                startTypewriterLoop();
                // ===============================
            } else if (metadata.type === 'audio_chunk') {
                // 标准 TTS 排序逻辑
                const audioUrl = URL.createObjectURL(new Blob([audioDataBytes], { type: metadata.mimeType }));
                addToVrmSortBuffer({
                    audioDataUrl: audioUrl, chunkIndex: metadata.chunkIndex,
                    expressions: metadata.expressions, text: metadata.text, isBinary: true
                });
            }
        } else {
            // 处理指令 (JSON)
            try {
                const message = JSON.parse(event.data);
                handleVrmCoreLogic(message);
            } catch (e) {}
        }
    };
    ttsWebSocket.onclose = () => { wsConnected = false; setTimeout(initTTSWebSocket, 3000); };
}

// --- 处理指令的函数 ---
function handleVrmCoreLogic(message) {
    const { type, data } = message;
    
    // 【核心修复】当对话开始或停止时，重置所有你定义的打字机变量
    if (type === 'ttsStarted' || type === 'stopSpeaking') {
        // 1. 重置你写好的打字机变量
        isOmniMode = false;
        isAudioStreaming = false;
        fullTargetText = "";
        currentVisibleCount = 0;
        displayStartIndex = 0;
        stopTypewriterLoop(); // 调用你的停止函数
        clearSubtitle();     // 调用你的清理函数

        // 2. 重置标准 TTS 队列
        vrmAudioQueue = [];
        vrmReceiveBuffer.clear();
        nextExpectedIndex = 0;
        isVrmPlaying = false;
        haltCurrentAudio();
    }
    
    // 兼容静音块指令
    if (type === 'startSpeaking' && data.voice === 'silence') {
        addToVrmSortBuffer({ ...data, isSilence: true });
    }
}

// --- 标准 TTS 排序函数 (完整版) ---
function addToVrmSortBuffer(task) {
    vrmReceiveBuffer.set(task.chunkIndex, task);
    while (vrmReceiveBuffer.has(nextExpectedIndex)) {
        const nextTask = vrmReceiveBuffer.get(nextExpectedIndex);
        vrmAudioQueue.push(nextTask);
        vrmReceiveBuffer.delete(nextExpectedIndex);
        nextExpectedIndex++;
    }
    if (!isVrmPlaying && vrmAudioQueue.length > 0) processVrmQueue();
}

// --- 标准 TTS 播放队列 (完整版) ---
async function processVrmQueue() {
    if (vrmAudioQueue.length === 0) { isVrmPlaying = false; return; }
    isVrmPlaying = true;
    const task = vrmAudioQueue.shift();

    // 复用你的字幕渲染
    if (task.text) renderSubtitleUI(task.text);

    if (task.isSilence) {
        await new Promise(r => setTimeout(r, 600));
    } else {
        await startLipSyncForChunk(task); // 调用你那个 F1/F2 算法函数
    }

    if (task.isBinary && task.audioDataUrl) URL.revokeObjectURL(task.audioDataUrl);
    processVrmQueue();
}

initTTSWebSocket();

const VMCToVRMBlend = {
  Joy:      'happy',
  Angry:    'angry',
  Sorrow:   'sad',
  Fun:      'relaxed',
  A:        'aa',
  I:        'ih',
  U:        'ou',
  E:        'ee',
  O:        'oh',
  Blink:    'blink',
  Blink_L:  'blinkLeft',
  Blink_R:  'blinkRight',
  Surprised:'surprised',
  LookDown:   'lookDown',
  LookUp:     'lookUp',
  LookLeft:   'lookLeft',
  LookRight:  'lookRight'
};
let vmcReceiveEnabled = false;   // 是否正在 VMC 接收模式
let vmcSyncExpression = false;   // 是否同步表情（面板开关）
let vmcBoneBuffer = new Map();   // 缓存最新骨骼数据
let vmcBlendBuffer = new Map();  // 缓存最新表情数据

/* ========== VMC 接收：骨骼 + 表情 一次性完整版 ========== */
if (window.vmcAPI) {
  window.vmcAPI.onVMCOscRaw((oscMsg) => {
    if (!vmcReceiveEnabled) return;          // 总开关

    const { address, args } = oscMsg;

    /* -------- 1. 骨骼 /VMC/Ext/Bone/Pos -------- */
    if (address === '/VMC/Ext/Bone/Pos') {
      // 兼容两种常见 osc 库格式：{type,value} 或直接原始值
      const boneName = args[0].value ?? args[0];
      const x   = args[1].value ?? args[1];
      const y   = args[2].value ?? args[2];
      const z   = args[3].value ?? args[3];
      const qx  = args[4].value ?? args[4];
      const qy  = - args[5].value ?? args[5];
      const qz  = - args[6].value ?? args[6];
      const qw  = args[7].value ?? args[7];

      vmcBoneBuffer.set(boneName, {
        position: new THREE.Vector3(x, y, z),
        rotation: new THREE.Quaternion(qx, qy, qz, qw)
      });
      return;
    }

    /* -------- 2. 表情 /VMC/Ext/Blend/Val -------- */
    if (address === '/VMC/Ext/Blend/Val') {
      const blendName = args[0].value ?? args[0];
      const weight  = args[1].value ?? args[1];
      vmcBlendBuffer.set(blendName, weight);
      return;
    }

    /* -------- 3. 表情 Apply -------- */
    if (address === '/VMC/Ext/Blend/Apply') {
      if (!currentVrm?.expressionManager || !vmcSyncExpression) return;
      for (const [vmcName, w] of vmcBlendBuffer) {
        const vrmName = VMCToVRMBlend[vmcName];   // 官方表情映射表
        if (vrmName) currentVrm.expressionManager.setValue(vrmName, w);
      }
    }
  });
}



// 发送消息到主界面
function sendToMain(type, data) {
    if (ttsWebSocket && wsConnected) {
        ttsWebSocket.send(JSON.stringify({
            type,
            data,
            timestamp: Date.now()
        }));
    }
}

let fullTargetText = "";          // 记录当前对话收到的所有文本
let currentVisibleCount = 0;      // 当前已显示的字符数
let displayStartIndex = 0; // 新增：锁定当前显示的起始位置
const MAX_WINDOW_SIZE = 60;  // 一屏最多显示约40个字（视UI宽度而定）
const OVERLAP_SIZE = 30;     // 翻页时保留的字数（即“半页”重叠）
const SAFE_PUNC_LIST = /[，。！？；：、“”（）《》,.!?;:()]/; // 定义安全分割的标点符号

let typewriterTimer = null;       // 打字机计时器
let isAudioStreaming = false;
let isOmniMode = false;           // 是否处于 Omni 流模式
let omniNextStartTime = 0;        // 预估的音频流结束时间点
let omniTotalAudioDuration = 0;   // 记录当前句子已接收到的音频总时长
let omniPlaybackStartTime = 0;    // 记录这句音频开始播放的绝对时间
/**
 * 强制停止所有音频播放并重置音频上下文
 * 解决“上一句话还没说完，下一句话就开始了”产生的重叠问题
 */
async function haltCurrentAudio() {
    // 1. 停止音频上下文（最关键的一步）
    if (currentAudioContext) {
        try {
            // suspend() 会立即停止音频输出
            await currentAudioContext.suspend();
            // close() 释放硬件资源，强迫下次创建新的 Context，避免时间戳错乱
            await currentAudioContext.close();
        } catch (e) {
            console.warn("AudioContext cleanup warning:", e);
        }
        currentAudioContext = null; // 置空，以便下次 processOmniStreaming 重新创建
    }

    // 2. 重置音频流时间戳
    omniNextStartTime = 0;
    
    // 3. 停止所有动画与分析器连接
    stopAllChunkAnimations();
    chunkAnimations.clear(); // 清空 Map，防止残留状态
}

function handleTTSMessage(message) {
    const { type, data } = message;

    switch (type) {
        case 'ttsStarted':
            // 建议：在这里也调用一次 haltCurrentAudio() 以防万一，
            // 但如果这里是同步调用，可能会导致上一句结尾被切断太快。
            // 只要 stopSpeaking 处理得当，这里重置变量即可。
            isOmniMode = false;
            fullTargetText = "";
            currentVisibleCount = 0;
            displayStartIndex = 0;
            isAudioStreaming = false;
            omniNextStartTime = 0;
            omniTotalAudioDuration = 0;  // 重置
            omniPlaybackStartTime = 0;   // 重置
            // 确保如果有旧的打字机在跑，立即停止
            stopTypewriterLoop();
            stopAllChunkAnimations();
            clearSubtitle();
            
            // 如果希望极其保险，防止上一句尾音残留，取消下面这行的注释：
            // haltCurrentAudio(); 
            break;

        case 'omniStreaming':
            if (windowName === 'default') {
                if (!isOmniMode || (data.text && data.text.length < fullTargetText.length)) {
                    fullTargetText = "";
                    currentVisibleCount = 0;
                    displayStartIndex = 0;
                    omniNextStartTime = 0;
                    stopTypewriterLoop();
                    clearSubtitle();
                }

                isOmniMode = true;
                isAudioStreaming = true; // 标记正在接收流
                if (data.text) fullTargetText = data.text;
                if (data.audioData) processOmniStreaming(data);
                startTypewriterLoop();
            }
            break;

        case 'startSpeaking':
            // 传统 TTS 逻辑
            if (windowName === 'default' || windowName === data.voice) {
                isOmniMode = false;
                startLipSyncForChunk(data); 
                if (data.text) {
                    updateSubtitle(data.text, data.chunkIndex);
                }
            }
            break;

        // ==========================================
        // 修改点 1: 强制打断 (用户停止或新对话开始前)
        // ==========================================
        case 'stopSpeaking':
            isOmniMode = false;
            isAudioStreaming = false;
            displayStartIndex = 0;
            
            // 1. 停止打字机
            stopTypewriterLoop();
            
            // 2. 【核心修复】强制销毁音频上下文，立即静音
            // 不加这一步，浏览器缓冲区里已调度的音频还会继续播放几秒
            haltCurrentAudio(); 

            // 3. 清理 UI
            finalizeSpeech(true); 
            break;

        // ==========================================
        // 修改点 2: 流传输结束 (自然播放结束)
        // ==========================================
        case 'allChunksCompleted':
            // 标记流已结束，不再接收新数据
            isOmniMode = false; 
            isAudioStreaming = false; 
            
            // 注意：这里不要调用 haltCurrentAudio()，
            // 否则句子最后几秒的语音会被切断（因为音频播放通常滞后于数据接收）。
            // 音频的自然结束交给 AudioContext 自己跑完，或者等待下一次 ttsStarted/stopSpeaking 清理。
            
            // 如果文字已经打完，触发收尾；
            // 如果文字没打完，打字机循环通过 isAudioStreaming=false 判断会进入加速收尾模式，
            // 跑完所有字后会自动调用 finalizeSpeech(false)。
            if (currentVisibleCount >= fullTargetText.length) {
                finalizeSpeech(false);
            }
            break;
            
        case 'chunkEnded':
            if (currentSubtitleChunkIndex === data.chunkIndex && !isOmniMode) {
                clearSubtitle();
            }
            break;
    }
}

// ==========================================
// 3. 动态打字机逻辑
// ==========================================
/**
 * 完整改进版打字机循环
 * 支持：动态语速、半页重叠翻页、标点符号安全分割、视觉反馈
 */
function startTypewriterLoop() {
    if (typewriterTimer) return; // 防止重复启动

    function syncTextToAudio() {
        if (!isOmniMode || !currentAudioContext) {
            typewriterTimer = null;
            return;
        }

        const now = currentAudioContext.currentTime;
        const totalChars = fullTargetText.length;

        if (totalChars > 0 && omniTotalAudioDuration > 0) {
            // 计算当前音频播放了多长时间
            const playedTime = Math.max(0, now - omniPlaybackStartTime);
            
            // 计算播放进度比例 (稍微给文本一点提前量，乘以1.05感觉更贴脸)
            let progress = (playedTime / omniTotalAudioDuration) * 1.05; 
            progress = Math.min(1.0, progress); // 最高 100%

            // 如果音频流还在传输，限制进度不能超过当前文本的最大长度
            // 如果音频断流结束了（!isAudioStreaming），则允许跑满
            let targetCharCount = Math.floor(progress * totalChars);

            // 如果已经接收完毕，但还有音频没播完，正常按照比例走
            // 如果音频播完了（now > omniNextStartTime），直接显示全部
            if (!isAudioStreaming && now >= omniNextStartTime) {
                targetCharCount = totalChars;
            }

            // 平滑递增，不能倒退
            if (targetCharCount > currentVisibleCount) {
                currentVisibleCount = targetCharCount;
                
                // --- 你的智能翻页逻辑 (完全保留) ---
                const currentDisplayLength = currentVisibleCount - displayStartIndex;
                if (currentDisplayLength > MAX_WINDOW_SIZE) {
                    let targetStartIndex = currentVisibleCount - OVERLAP_SIZE;
                    const lookbackRange = Math.floor(MAX_WINDOW_SIZE * 0.6); 
                    const searchText = fullTargetText.slice(currentVisibleCount - lookbackRange, currentVisibleCount);
                    let lastPuncIndex = -1;
                    for (let i = searchText.length - 1; i >= 0; i--) {
                        if (SAFE_PUNC_LIST.test(searchText[i])) {
                            lastPuncIndex = i;
                            break;
                        }
                    }
                    if (lastPuncIndex !== -1) {
                        const foundIndex = (currentVisibleCount - lookbackRange) + lastPuncIndex + 1;
                        const newOverlap = currentVisibleCount - foundIndex;
                        if (newOverlap >= 5 && newOverlap <= MAX_WINDOW_SIZE * 0.8) {
                            targetStartIndex = foundIndex;
                        }
                    }
                    displayStartIndex = targetStartIndex;
                }

                // --- 渲染 ---
                const displayText = fullTargetText.slice(displayStartIndex, currentVisibleCount);
                const prefix = displayStartIndex > 0 ? "..." : "";
                renderSubtitleUI(prefix + displayText);
            }
        }

        // 判断是否结束
        if (!isOmniMode || (!isAudioStreaming && currentVisibleCount >= totalChars)) {
            typewriterTimer = null;
            if (!isOmniMode) finalizeSpeech(false);
        } else {
            // 使用 requestAnimationFrame 帧同步，性能远高于 setTimeout
            typewriterTimer = requestAnimationFrame(syncTextToAudio);
        }
    }

    typewriterTimer = requestAnimationFrame(syncTextToAudio);
}


function stopTypewriterLoop() {
    if (typewriterTimer) {
        clearTimeout(typewriterTimer);
        typewriterTimer = null;
    }
}

// ==========================================
// 4. 音频流处理 (Omni 模式)
// ==========================================
async function processOmniStreaming(data) {
    const chunkId = 'omni_live_stream';
    
    try {
        if (!currentAudioContext) {
            currentAudioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (currentAudioContext.state === 'suspended') await currentAudioContext.resume();

        let state = chunkAnimations.get(chunkId);
        if (!state) {
            state = { 
                isPlaying: true, 
                analyser: currentAudioContext.createAnalyser(), 
                expression: 'neutral' 
            };
            state.analyser.fftSize = 256;
            state.analyser.connect(currentAudioContext.destination);
            chunkAnimations.set(chunkId, state);
            startChunkAnimation(chunkId, state);
            omniNextStartTime = currentAudioContext.currentTime;
        }

        const raw = atob(data.audioData);
        const pcm16 = new Int16Array(raw.length / 2);
        for (let i = 0; i < raw.length; i += 2) {
            pcm16[i >> 1] = raw.charCodeAt(i) | (raw.charCodeAt(i + 1) << 8);
        }
        
        const buffer = currentAudioContext.createBuffer(1, pcm16.length, data.sampleRate || 24000);
        const floatData = buffer.getChannelData(0);
        for (let i = 0; i < pcm16.length; i++) {
            floatData[i] = pcm16[i] / 32768;
        }

        const source = currentAudioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(state.analyser);

        const now = currentAudioContext.currentTime;

        if (omniNextStartTime === 0 || omniPlaybackStartTime === 0) {
            omniPlaybackStartTime = Math.max(now, omniNextStartTime);
        }

        if (omniNextStartTime < now) omniNextStartTime = now;
        
        source.start(omniNextStartTime);

        omniTotalAudioDuration += buffer.duration;
        omniNextStartTime += buffer.duration;
    } catch (e) {
        console.error('Omni Streaming Error:', e);
    }
}

// ==========================================
// 5. 字幕渲染与清理
// ==========================================
function renderSubtitleUI(text) {
    if (!isSubtitleEnabled) return;
    if (!subtitleElement) initSubtitleElement();
    subtitleElement.textContent = text;
    subtitleElement.style.opacity = '1';
    if (typeof adjustSubtitleSize === 'function') adjustSubtitleSize();
}

function updateSubtitle(text, chunkIndex) {
    // 兼容传统 TTS 的字幕显示
    if (!isSubtitleEnabled || !text.trim()) return;
    renderSubtitleUI(text);
    currentSubtitleChunkIndex = chunkIndex;
}

function clearSubtitle() {
    if (subtitleElement) {
        subtitleElement.style.transition = 'opacity 0.5s ease';
        subtitleElement.style.opacity = '0';
    }
}

function finalizeSpeech(immediate = false) {
    // 停止动画驱动
    stopAllChunkAnimations();
    if (chunkAnimations.has('omni_live_stream')) {
        stopChunkAnimation('omni_live_stream');
        chunkAnimations.delete('omni_live_stream');
    }

    if (immediate) {
        clearSubtitle();
        fullTargetText = "";
        currentVisibleCount = 0;
        displayStartIndex = 0;
    } else {
        // --- 优化点：文字全部打完后，多留 2.5 秒的“静止阅读时间” ---
        if (subtitleTimeout) clearTimeout(subtitleTimeout);
        subtitleTimeout = setTimeout(() => {
            // 确保这期间没有开启新的对话
            if (!isOmniMode && !typewriterTimer) {
                clearSubtitle();
                fullTargetText = "";
                currentVisibleCount = 0;
                displayStartIndex = 0;
            }
        }, 2000); 
    }
}


// 在页面加载完成后初始化 WebSocket
document.addEventListener('DOMContentLoaded', () => {
    // 延迟初始化，确保其他组件已经准备好
    setTimeout(() => {
        initTTSWebSocket();
    }, 2000);
});

if (isElectron) {
  // 禁用 Chromium 的自动播放限制
  const disableAutoplayPolicy = () => {
    if (window.chrome && chrome.webview) {
      chrome.webview.setAutoplayPolicy('no-user-gesture-required');
    }
  };
  
  // 在用户交互后执行
  document.addEventListener('click', () => {
    disableAutoplayPolicy();
    if (currentAudioContext) {
      currentAudioContext.resume();
    }
  });
}

// 在全局变量区域添加模型切换相关变量
let currentModelIndex = 0;
let allModels = [];
let modelsInitialized = false;

// 获取所有可用模型的函数（只执行一次）
async function getAllModels() {
    if (modelsInitialized) {
        return allModels;
    }
    
    const vrmConfig = await fetchVRMConfig();
    const defaultModels = vrmConfig.defaultModels || [];
    const userModels = vrmConfig.userModels || [];
    allModels = [...defaultModels, ...userModels];
    
    // 找到当前选中模型的索引
    const selectedModelId = vrmConfig.selectedModelId;
    currentModelIndex = Math.max(0, allModels.findIndex(model => model.id === selectedModelId));
    
    modelsInitialized = true;
    console.log(`Models initialized: ${allModels.length} models available, current index: ${currentModelIndex}`);
    
    return allModels;
}

// 切换到指定索引的模型（纯前端切换）
async function switchToModel(index,isRefresh = false) {
    if (!modelsInitialized) {
        await getAllModels();
    }
    
    if (allModels.length === 0) {
        console.error('No models available');
        return;
    }
    
    // 确保索引在有效范围内（循环切换）
    const newIndex = ((index % allModels.length) + allModels.length) % allModels.length;
    
    // 如果是同一个模型，不需要切换
    if (newIndex === currentModelIndex && !isRefresh) {
        console.log('Same model selected, no need to switch');
        return;
    }
    
    currentModelIndex = newIndex;
    const selectedModel = allModels[currentModelIndex];
    // 替换userModel.path中的protocol和host
    let userModelURL = new URL(selectedModel.path);
    userModelURL.protocol = window.location.protocol;
    userModelURL.host = window.location.host;
    selectedModel.path = userModelURL.href;
    console.log(`Switching to model: ${selectedModel.name} (${selectedModel.id}) - Index: ${currentModelIndex}`);
    // 切换模型前确保画布和交互恢复为可见状态
    isModelHiddenByHover = false;
    resumeModelAnimationsAfterHide();
    if (renderer?.domElement) {
        const canvas = renderer.domElement;
        canvas.style.opacity = '1';
        canvas.style.pointerEvents = 'auto';
        canvas.style.transition = '';
        if (currentVrm) currentVrm.scene.visible = true;
    }
    
    try {
        // 显示加载提示（可选）
        showModelSwitchingIndicator(selectedModel.name);
        // 🔥 添加：停止当前的闲置动画
        if (idleAnimationManager) {
            idleAnimationManager.stopAllAnimations();
        }
        
        // 🔥 添加：重置闲置动画管理器
        idleAnimationManager = null;

        // 移除当前VRM模型
        if (currentVrm) {
            if (typeof transformControl !== 'undefined') {
                transformControl.detach();
            }
            // scene.remove(currentVrm.scene); <-- 删除这行
            currentVrmWrapper.remove(currentVrm.scene); // 从 Wrapper 移除
            currentVrm = undefined;
        }
        
        // 加载新模型
        const modelPath = selectedModel.path;
        
        loader.load(
            modelPath,
            (gltf) => {
                const vrm = gltf.userData.vrm;
                currentMixer = new THREE.AnimationMixer(vrm.scene); // 创建动画混合器
                isVRM1 = vrm.meta.metaVersion === '1';
                VRMUtils.rotateVRM0(vrm); // 旋转 VRM 使其面向正前方
                // 优化性能
                VRMUtils.removeUnnecessaryVertices(gltf.scene);
                // 添加材质修复
                gltf.scene.traverse((obj) => {
                if (obj.isMesh && obj.material) {
                    // 解决透明材质黑边问题
                    if (obj.material.transparent) {
                        obj.material.alphaTest = 0.01;
                        obj.material.depthWrite = true;
                        obj.material.needsUpdate = true;
                    }
                    
                    // 确保正确混合模式
                    obj.material.blending = THREE.NormalBlending;
                    obj.material.premultipliedAlpha = false;
                    
                    // 设置渲染顺序
                    obj.renderOrder = obj.material.transparent ? 1 : 0;
                }
                });

                VRMUtils.combineSkeletons(gltf.scene);
                VRMUtils.combineMorphs(vrm);
                
                // 启用 Spring Bone 物理模拟
                if (vrm.springBoneManager) {
                    console.log('Spring Bone Manager found:', vrm.springBoneManager);
                }
                
                // 禁用视锥体剔除
                vrm.scene.traverse((obj) => {
                    obj.frustumCulled = false;
                });
                
                vrm.lookAt.target = camera;

                if (vrm.lookAt.applier) {
                    vrm.lookAt.applier.yawLimit = 60.0;   // 左右转头最大 60 度
                    vrm.lookAt.applier.pitchLimit = 30.0; // 上下抬头最大 30 度
                }

                currentVrm = vrm;
                console.log('New VRM loaded:', vrm);
                currentVrmWrapper.add(vrm.scene);
                // 让模型投射阴影
                vrm.scene.traverse((obj) => {
                    if (obj.isMesh) {
                        obj.castShadow = true;
                        obj.receiveShadow = true;   // 如需让模型本身也接收阴影可保留
                    }
                });
                // 设置自然姿势
                setNaturalPose(vrm);

                if (vrm.expressionManager) {
                    vrm.expressionManager.setValue('neutral', 1.0);
                }

                const breathClip = createBreathClip(vrm);
                breathAction = currentMixer.clipAction(breathClip);
                breathAction.setLoop(THREE.LoopRepeat);
                breathAction.play();

                const blinkClip = createBlinkClip(vrm);
                blinkAction = currentMixer.clipAction(blinkClip);
                blinkAction.setLoop(THREE.LoopRepeat);
                blinkAction.play();
                
                // 🔥 关键修复：重新创建闲置动画管理器并重新设置动画队列
                idleAnimationManager = new IdleAnimationManager(vrm, currentMixer);
                
                // 🔥 重要：重新设置VRMA动画队列（如果之前已经加载过）
                if (useVRMAIdleAnimations && idleAnimations.length > 0) {
                    idleAnimationManager.setAnimationQueue(idleAnimations);
                }
                
                // 🔥 重新启动闲置动画循环
                startIdleAnimationLoop();

                // 隐藏加载提示
                hideModelSwitchingIndicator();
                
                if (typeof transformControl !== 'undefined' && transformControl.object) {
                    transformControl.attach(currentVrmWrapper);
                }

                console.log(`Successfully switched to model: ${selectedModel.name}`);
            },
            (progress) => {
                console.log('Loading model...', 100.0 * (progress.loaded / progress.total), '%');
                // 可以在这里更新加载进度
                updateModelLoadingProgress(progress.loaded / progress.total);
            },
            (error) => {
                console.error('Error loading model:', error);
                hideModelSwitchingIndicator();
                
                // 如果加载失败，尝试回到之前的模型
                if (allModels.length > 1) {
                    console.log('Attempting to load fallback model...');
                    // 尝试加载第一个模型作为备用
                    if (currentModelIndex !== 0) {
                        switchToModel(0);
                    }
                }
            }
        );
        
    } catch (error) {
        console.error('Error switching model:', error);
        hideModelSwitchingIndicator();
    }
}

// 显示模型切换指示器（可选功能）
function showModelSwitchingIndicator(modelName) {
    // 创建或显示加载提示
    let indicator = document.getElementById('model-switching-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.id = 'model-switching-indicator';
        indicator.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 20px;
            border-radius: 10px;
            font-size: 16px;
            z-index: 10000;
            text-align: center;
            backdrop-filter: blur(10px);
            transition: opacity 0.3s ease;
        `;
        document.body.appendChild(indicator);
    }
    
    indicator.innerHTML = `
        <div style="margin-bottom: 10px;">
            <i class="fas fa-sync-alt fa-spin"></i>
        </div>
        <div>Loading ${modelName}...</div>
        <div id="loading-progress" style="margin-top: 10px; font-size: 14px; opacity: 0.8;"></div>
    `;
    indicator.style.display = 'block';
    indicator.style.opacity = '1';
}

// 更新加载进度
function updateModelLoadingProgress(progress) {
    const progressElement = document.getElementById('loading-progress');
    if (progressElement) {
        progressElement.textContent = `${Math.round(progress * 100)}%`;
    }
}

// 隐藏模型切换指示器
function hideModelSwitchingIndicator() {
    const indicator = document.getElementById('model-switching-indicator');
    if (indicator) {
        indicator.style.opacity = '0';
        setTimeout(() => {
            indicator.style.display = 'none';
        }, 300);
    }
}

// 获取当前模型信息
function getCurrentModelInfo() {
    if (allModels.length > 0 && currentModelIndex >= 0 && currentModelIndex < allModels.length) {
        return allModels[currentModelIndex];
    }
    return null;
}

// 获取下一个模型信息（用于预览）
function getNextModelInfo() {
    if (allModels.length === 0) return null;
    const nextIndex = ((currentModelIndex + 1) % allModels.length + allModels.length) % allModels.length;
    return allModels[nextIndex];
}

// 获取上一个模型信息（用于预览）
function getPrevModelInfo() {
    if (allModels.length === 0) return null;
    const prevIndex = ((currentModelIndex - 1) % allModels.length + allModels.length) % allModels.length;
    return allModels[prevIndex];
}

animate();