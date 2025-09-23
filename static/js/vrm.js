import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { createVRMAnimationClip, VRMAnimationLoaderPlugin } from '@pixiv/three-vrm-animation';
let isVRM1 = true;
let currentMixer = null;
let idleAction = null;
let breathAction = null;
let blinkAction = null;

// renderer
// 检测运行环境
const isElectron = typeof require !== 'undefined' || navigator.userAgent.includes('Electron');

// 根据环境添加 class
document.body.classList.add(isElectron ? 'electron' : 'web');

// 优化渲染器设置 - 启用透明背景
const renderer = new THREE.WebGLRenderer({ 
  alpha: true, 
  antialias: true,
  preserveDrawingBuffer: true 
});
// 添加性能优化设置
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.max(1, window.devicePixelRatio));
renderer.setClearColor(0x000000, 0); // 完全透明背景

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
        return data.VRMConfig;
    } catch (error) {
        console.error('Error fetching VRMConfig:', error);
        return   {
            enabledExpressions: false,
            selectedModelId: 'alice', // 默认选择Alice模型
            defaultModels: [], // 存储默认模型
            userModels: [],     // 存储用户上传的模型
            defaultMotions: [], // 存储默认动作
            userMotions: []     // 存储用户上传的动作
        };
    }
}

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
const camera = new THREE.PerspectiveCamera( 30.0, window.innerWidth / window.innerHeight, 0.1, 20.0 );
camera.position.set( 0.0, 1.0, 5.0 );

// camera controls
const controls = new OrbitControls( camera, renderer.domElement );
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

// 隐形阴影接收平面
const groundGeo = new THREE.PlaneGeometry(20, 20);
const shadowMat = new THREE.ShadowMaterial({ opacity: 0.4 }); // 透明度可调
const ground = new THREE.Mesh(groundGeo, shadowMat);
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene.add(ground);


// lookat target
const lookAtTarget = new THREE.Object3D();
camera.add( lookAtTarget );

// 添加环境光，让整体更柔和
const ambientLight = new THREE.AmbientLight( 0xffffff, 0.1 );
scene.add( ambientLight );

// gltf and vrm
let currentVrm = undefined;
const loader = new GLTFLoader();
loader.crossOrigin = 'anonymous';

loader.register( ( parser ) => {

    return new VRMLoaderPlugin(parser,{
        lookAt: { type: 'bone' }
    });

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
    // 左臂自然下垂
    vrm.humanoid.getNormalizedBoneNode( 'leftUpperArm' ).rotation.z = -0.4 * Math.PI * v;

    // 右臂自然下垂
    vrm.humanoid.getNormalizedBoneNode( 'rightUpperArm' ).rotation.z = 0.4 * Math.PI * v;
    
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
        this.currentIdleAction = null;
        this.defaultPoseAction = null;
        this.proceduralIdleAction = null;
        this.isTransitioning = false;
        this.animationQueue = [];
        this.currentIndex = 0;
        this.transitionDuration = 0.8; // 稍微延长过渡时间
        this.pauseBetweenAnimations = 1.5; // 减少暂停时间
        this.idleWeight = 1.0; // 增加权重确保完全控制
        this.isActive = false;
        this.currentMode = 'none';
        
        // 创建默认姿势动作
        this.createDefaultPoseAction();
        // 创建程序化闲置动画
        this.createProceduralIdleAction();
        
        console.log('IdleAnimationManager initialized');
    }
    
    // 创建默认姿势动作 - 改进版本
    createDefaultPoseAction() {
        try {
            const defaultPoseClip = this.createDefaultPoseClip();
            this.defaultPoseAction = this.mixer.clipAction(defaultPoseClip);
            this.defaultPoseAction.setLoop(THREE.LoopOnce);
            this.defaultPoseAction.clampWhenFinished = true;
            this.defaultPoseAction.setEffectiveWeight(0);
            console.log('Default pose action created');
        } catch (error) {
            console.error('Error creating default pose action:', error);
        }
    }
    
    // 创建程序化闲置动画
    createProceduralIdleAction() {
        try {
            console.log('Creating procedural idle action...');
            const idleClip = createIdleClip(this.vrm);
            if (!idleClip) {
                console.error('Failed to create idle clip');
                return;
            }
            
            this.proceduralIdleAction = this.mixer.clipAction(idleClip);
            this.proceduralIdleAction.setLoop(THREE.LoopRepeat);
            this.proceduralIdleAction.setEffectiveWeight(0); // 初始权重为0
            
            console.log('Procedural idle action created successfully');
        } catch (error) {
            console.error('Error creating procedural idle action:', error);
        }
    }
    
    // 改进的默认姿势clip创建
    createDefaultPoseClip() {
        const tracks = [];
        const duration = 1.0;
        const fps = 30;
        const frameCount = duration * fps;
        
        const times = [];
        for (let i = 0; i <= frameCount; i++) {
            times.push(i / fps);
        }
        
        // 扩展需要重置的骨骼列表，包含更多骨骼
        const bonesToReset = [
            'hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
            'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand',
            'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand',
            'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'leftToes',
            'rightUpperLeg', 'rightLowerLeg', 'rightFoot', 'rightToes',
            // 手指骨骼
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
        
        bonesToReset.forEach(boneName => {
            const bone = this.vrm.humanoid.getNormalizedBoneNode(boneName);
            if (!bone) return;
            
            const naturalRotation = this.getNaturalRotation(boneName);
            const values = [];
            
            // 创建从当前状态到自然姿势的平滑过渡
            times.forEach((time, index) => {
                let targetRotation = naturalRotation.clone();
                
                // 如果是第一帧，使用当前骨骼的旋转作为起点
                if (index === 0) {
                    // 保持当前旋转
                    values.push(...bone.quaternion.toArray());
                } else {
                    // 平滑过渡到目标旋转
                    const progress = time / duration;
                    const easedProgress = this.easeInOutCubic(progress);
                    
                    const currentQuat = new THREE.Quaternion().fromArray(
                        values.slice((index - 1) * 4, index * 4)
                    );
                    
                    const interpolatedQuat = currentQuat.clone().slerp(targetRotation, easedProgress);
                    values.push(...interpolatedQuat.toArray());
                }
            });
            
            const track = new THREE.QuaternionKeyframeTrack(
                bone.name + '.quaternion',
                times,
                values
            );
            
            tracks.push(track);
        });
        
        return new THREE.AnimationClip('defaultPose', duration, tracks);
    }
    
    // 缓动函数
    easeInOutCubic(t) {
        return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    }
    
    // 获取自然姿势的旋转值 - 改进版本
    getNaturalRotation(boneName) {
        const euler = new THREE.Euler(0, 0, 0);
        const v = isVRM1 ? 1 : -1;
        
        switch (boneName) {
            case 'hips':
                euler.set(0, 0, 0); // 髋部保持中性
                break;
            case 'spine':
                euler.set(0, 0, 0); // 脊柱保持直立
                break;
            case 'chest':
                euler.set(0, 0, 0); // 胸部保持中性
                break;
            case 'upperChest':
                euler.set(0, 0, 0); // 上胸部保持中性
                break;
            case 'neck':
                euler.set(0, 0, 0); // 脖子保持中性
                break;
            case 'head':
                euler.set(0, 0, 0); // 头部保持中性
                break;
            case 'leftShoulder':
            case 'rightShoulder':
                euler.set(0, 0, 0); // 肩膀保持中性
                break;
            case 'leftUpperArm':
                euler.set(0, 0, -0.4 * Math.PI * v);
                break;
            case 'rightUpperArm':
                euler.set(0, 0, 0.4 * Math.PI * v);
                break;
            case 'leftLowerArm':
            case 'rightLowerArm':
                euler.set(0, 0, 0); // 前臂保持自然下垂
                break;
            case 'leftHand':
                euler.set(0.05, 0, 0.1 * v);
                break;
            case 'rightHand':
                euler.set(0.05, 0, -0.1 * v);
                break;
            case 'leftUpperLeg':
            case 'rightUpperLeg':
            case 'leftLowerLeg':
            case 'rightLowerLeg':
            case 'leftFoot':
            case 'rightFoot':
            case 'leftToes':
            case 'rightToes':
                euler.set(0, 0, 0); // 腿部保持中性
                break;
            // 手指的自然姿势
            case 'leftThumbProximal':
            case 'rightThumbProximal':
                euler.set(0, boneName.includes('left') ? 0.35 : -0.35, 0);
                break;
            case 'leftIndexProximal':
            case 'leftMiddleProximal':
            case 'leftRingProximal':
            case 'leftLittleProximal':
                euler.set(0, 0, -0.35 * v);
                break;
            case 'rightIndexProximal':
            case 'rightMiddleProximal':
            case 'rightRingProximal':
            case 'rightLittleProximal':
                euler.set(0, 0, 0.35 * v);
                break;
            case 'leftIndexIntermediate':
            case 'leftMiddleIntermediate':
            case 'leftRingIntermediate':
            case 'leftLittleIntermediate':
                euler.set(0, 0, -0.45 * v);
                break;
            case 'rightIndexIntermediate':
            case 'rightMiddleIntermediate':
            case 'rightRingIntermediate':
            case 'rightLittleIntermediate':
                euler.set(0, 0, 0.45 * v);
                break;
            case 'leftIndexDistal':
            case 'leftMiddleDistal':
            case 'leftRingDistal':
            case 'leftLittleDistal':
                euler.set(0, 0, -0.3 * v);
                break;
            case 'rightIndexDistal':
            case 'rightMiddleDistal':
            case 'rightRingDistal':
            case 'rightLittleDistal':
                euler.set(0, 0, 0.3 * v);
                break;
            default:
                euler.set(0, 0, 0);
                break;
        }
        
        const quaternion = new THREE.Quaternion();
        quaternion.setFromEuler(euler);
        return quaternion;
    }
    
    // 设置动画队列
    setAnimationQueue(animations) {
        this.animationQueue = [...animations]; // 创建副本
        this.currentIndex = 0;
        console.log(`Idle animation queue set with ${animations.length} animations`);
    }
    
    // 开始闲置动画循环（VRMA模式）
    startIdleLoop() {
        if (this.animationQueue.length === 0) {
            console.warn('No idle animations available, switching to procedural mode');
            this.switchToProceduralMode();
            return;
        }
        
        console.log('Starting VRMA idle animation loop');
        this.currentMode = 'vrma';
        this.isActive = true;
        this.playNextVRMAAnimation();
    }
    
    // 播放下一个VRMA动画
    playNextVRMAAnimation() {
        if (!this.isActive || this.currentMode !== 'vrma' || this.animationQueue.length === 0) {
            return;
        }
        
        // 如果正在过渡中，等待过渡完成
        if (this.isTransitioning) {
            setTimeout(() => this.playNextVRMAAnimation(), 100);
            return;
        }
        
        const animation = this.animationQueue[this.currentIndex];
        console.log(`Playing VRMA animation: ${animation.name} (${this.currentIndex + 1}/${this.animationQueue.length})`);
        
        this.playVRMAAnimation(animation);
        
        // 更新索引（循环）
        //  this.currentIndex = (this.currentIndex + 1) % this.animationQueue.length;
        // 更新索引（随机，且不与上一次相同）
        const previousIndex = this.currentIndex;
        const length = this.animationQueue.length;

        // 如果队列长度小于2，无法保证不重复，直接返回或不做处理
        if (length < 2) {
            this.currentIndex = 0; // 或保持原值，视需求而定
        } else {
            let newIndex;
            do {
                newIndex = Math.floor(Math.random() * length);
            } while (newIndex === previousIndex);
            this.currentIndex = newIndex;
        }
    }
    
    // 播放指定的VRMA动画 - 改进版本
    playVRMAAnimation(animationData) {
        if (!animationData || !animationData.animation) {
            console.error('Invalid VRMA animation data');
            this.scheduleNextVRMAAnimation();
            return;
        }
        
        try {
            // 创建VRM动画剪辑
            const clip = createVRMAnimationClip(animationData.animation, this.vrm);
            if (!clip) {
                console.error('Failed to create VRMA animation clip');
                this.scheduleNextVRMAAnimation();
                return;
            }
            
            // 创建新的动作
            this.currentIdleAction = this.mixer.clipAction(clip);
            this.currentIdleAction.setLoop(THREE.LoopOnce);
            this.currentIdleAction.clampWhenFinished = true;
            this.currentIdleAction.fadeIn(1.0);
            this.currentIdleAction.play();
            
            // 监听动画结束事件
            const onFinished = (event) => {
                if (event.action === this.currentIdleAction) {
                    console.log(`VRMA animation ${animationData.name} finished`);
                    this.onVRMAAnimationFinished();
                    this.mixer.removeEventListener('finished', onFinished);
                }
            };
            
            this.mixer.addEventListener('finished', onFinished);
            
        } catch (error) {
            console.error(`Error playing VRMA animation ${animationData.name}:`, error);
            this.scheduleNextVRMAAnimation();
        }
    }
    
    // VRMA动画结束后的处理 - 改进版本
    onVRMAAnimationFinished() {
        if (this.currentMode !== 'vrma' || !this.isActive) {
            return;
        }
        
        console.log('VRMA animation finished, transitioning to default pose');
        
        this.isTransitioning = true;
        
        // 立即开始淡出当前动画并淡入默认姿势
        if (this.currentIdleAction) {
            this.currentIdleAction.fadeOut(1.0);
        }
        
        // 立即开始默认姿势过渡
        if (this.defaultPoseAction) {
            this.defaultPoseAction.reset();
            this.defaultPoseAction.setEffectiveWeight(0);
            this.defaultPoseAction.play();
            this.defaultPoseAction.fadeIn(this.transitionDuration * 0.5);
            
            // 确保权重足够高
            setTimeout(() => {
                if (this.defaultPoseAction) {
                    this.defaultPoseAction.setEffectiveWeight(this.idleWeight);
                }
            }, this.transitionDuration * 250);
        }
        
        // 等待默认姿势稳定后再进行下一步
        setTimeout(() => {
            if (this.currentMode !== 'vrma' || !this.isActive) {
                this.isTransitioning = false;
                return;
            }
            
            console.log('Default pose established, preparing for next animation');
            
            // 保持默认姿势一段时间
            setTimeout(() => {
                if (this.currentMode !== 'vrma' || !this.isActive) {
                    this.isTransitioning = false;
                    return;
                }
                
                // 开始淡出默认姿势
                if (this.defaultPoseAction) {
                    this.defaultPoseAction.fadeOut(this.transitionDuration * 0.3);
                }
                
                this.isTransitioning = false;
                
                // 稍等片刻后播放下一个动画
                setTimeout(() => {
                    if (this.currentMode === 'vrma' && this.isActive) {
                        this.playNextVRMAAnimation();
                    }
                }, 300); // 300ms缓冲时间
                
            }, this.pauseBetweenAnimations * 1000);
            
        }, this.transitionDuration * 600); // 等待过渡完成
    }
    
    // 安排下一个VRMA动画（错误恢复用）
    scheduleNextVRMAAnimation() {
        if (this.currentMode === 'vrma' && this.isActive) {
            setTimeout(() => {
                this.playNextVRMAAnimation();
            }, this.pauseBetweenAnimations * 1000);
        }
    }
    
    // 切换到VRMA动画模式
    switchToVRMAMode() {
        console.log('Switching to VRMA idle animations');
        
        // 只停止程序化动画
        this.stopProceduralAnimations();
        
        if (this.animationQueue.length > 0) {
            this.startIdleLoop();
        } else {
            console.warn('No VRMA animations available, falling back to procedural');
            this.switchToProceduralMode();
        }
    }
    
    // 切换到程序化动画模式
    switchToProceduralMode() {
        console.log('Switching to procedural idle animation');
        
        // 只停止非程序化的动画，不停止程序化动画
        this.stopVRMAAnimations();
        
        this.currentMode = 'procedural';
        this.isActive = true;
        
        if (this.proceduralIdleAction) {
            console.log('Starting procedural idle animation...');
            
            // 如果程序化动画已经在运行，就不要重新启动
            if (this.proceduralIdleAction.isRunning()) {
                console.log('Procedural animation already running, adjusting weight...');
                this.proceduralIdleAction.setEffectiveWeight(this.idleWeight);
            } else {
                // 重置并启动动画
                this.proceduralIdleAction.reset();
                this.proceduralIdleAction.setEffectiveWeight(this.idleWeight);
                this.proceduralIdleAction.play();
            }
            
            console.log('Procedural idle animation started with weight:', this.idleWeight);
            console.log('Animation is running:', this.proceduralIdleAction.isRunning());
            console.log('Animation time:', this.proceduralIdleAction.time);
        } else {
            console.error('Procedural idle action not available, recreating...');
            this.createProceduralIdleAction();
            if (this.proceduralIdleAction) {
                this.proceduralIdleAction.setEffectiveWeight(this.idleWeight);
                this.proceduralIdleAction.play();
            }
        }
    }
    
    // 只停止VRMA动画的方法
    stopVRMAAnimations() {
        console.log('Stopping VRMA animations only');
        
        const fadeTime = 0.5;
        
        // 停止当前VRMA动画
        if (this.currentIdleAction && this.currentIdleAction.isRunning()) {
            this.currentIdleAction.fadeOut(fadeTime);
            setTimeout(() => {
                if (this.currentIdleAction) {
                    this.currentIdleAction.stop();
                    this.currentIdleAction = null;
                }
            }, fadeTime * 1000);
        }
        
        // 停止默认姿势动画
        if (this.defaultPoseAction && this.defaultPoseAction.isRunning()) {
            this.defaultPoseAction.fadeOut(fadeTime);
        }
    }
    
    // 只停止程序化动画的方法
    stopProceduralAnimations() {
        console.log('Stopping procedural animations only');
        
        const fadeTime = 0.5;
        
        // 停止程序化动画
        if (this.proceduralIdleAction && this.proceduralIdleAction.isRunning()) {
            this.proceduralIdleAction.fadeOut(fadeTime);
            setTimeout(() => {
                if (this.proceduralIdleAction) {
                    this.proceduralIdleAction.stop();
                }
            }, fadeTime * 1000);
        }
    }
    
    // 改进的淡出当前动作方法
    fadeOutCurrentActions(fadeTime = null) {
        const actualFadeTime = fadeTime || (this.transitionDuration * 0.5);
        
        if (this.currentIdleAction && this.currentIdleAction.isRunning()) {
            this.currentIdleAction.fadeOut(actualFadeTime);
        }
        
        if (this.proceduralIdleAction && this.proceduralIdleAction.isRunning()) {
            this.proceduralIdleAction.fadeOut(actualFadeTime);
        }
        
        if (this.defaultPoseAction && this.defaultPoseAction.isRunning()) {
            this.defaultPoseAction.fadeOut(actualFadeTime);
        }
    }
    
    // 停止所有动画 - 只在真正需要时使用
    stopAllAnimations() {
        console.log('Stopping all idle animations');
        
        this.isActive = false;
        this.isTransitioning = false;
        
        const fadeTime = 0.5;
        
        // 停止当前VRMA动画
        if (this.currentIdleAction && this.currentIdleAction.isRunning()) {
            this.currentIdleAction.fadeOut(fadeTime);
            setTimeout(() => {
                if (this.currentIdleAction) {
                    this.currentIdleAction.stop();
                    this.currentIdleAction = null;
                }
            }, fadeTime * 1000);
        }
        
        // 停止程序化动画
        if (this.proceduralIdleAction && this.proceduralIdleAction.isRunning()) {
            this.proceduralIdleAction.fadeOut(fadeTime);
            setTimeout(() => {
                if (this.proceduralIdleAction) {
                    this.proceduralIdleAction.stop();
                }
            }, fadeTime * 1000);
        }
        
        // 停止默认姿势动画
        if (this.defaultPoseAction && this.defaultPoseAction.isRunning()) {
            this.defaultPoseAction.fadeOut(fadeTime);
            setTimeout(() => {
                if (this.defaultPoseAction) {
                    this.defaultPoseAction.stop();
                }
            }, fadeTime * 1000);
        }
        
        this.currentMode = 'none';
        console.log('All idle animations stopped');
    }
    
    // 刷新默认姿势动作
    refreshDefaultPoseAction() {
        try {
            // 重新创建默认姿势动作，基于当前骨骼状态
            if (this.defaultPoseAction) {
                this.defaultPoseAction.stop();
                this.defaultPoseAction = null;
            }
            
            this.createDefaultPoseAction();
            console.log('Default pose action refreshed');
        } catch (error) {
            console.error('Error refreshing default pose action:', error);
        }
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
    // 1. 获取当前桌宠配置
    const cfg = await fetchVRMConfig();   // { selectedMotionIds:[...], defaultMotions:[...], userMotions:[...] }

    // 2. 把两个数组合并成“动作池”
    const motionPool = [...cfg.defaultMotions, ...cfg.userMotions];

    // 3. 取出被选中的动作，并转成可访问的完整 URL
    const urls = cfg.selectedMotionIds
      .map(id => motionPool.find(m => m.id === id)) // 找到对应条目
      .filter(Boolean)                              // 过滤不存在的 id
      .map(item => {
        // 构造绝对 URL（同 VRM 模型做法）
        const urlObj = new URL(item.path);
        urlObj.protocol = window.location.protocol;
        urlObj.host     = window.location.host;
        return urlObj.toString();
      });

    // 4. 如果没有任何选中，给个兜底
    if (urls.length === 0) {
      const fallback = 
      [
        `${window.location.protocol}//${window.location.host}/vrm/animations/akimbo.vrma`,
       `${window.location.protocol}//${window.location.host}/vrm/animations/play_fingers.vrma`,
       `${window.location.protocol}//${window.location.host}/vrm/animations/scratch_head.vrma`,
       `${window.location.protocol}//${window.location.host}/vrm/animations/stretch.vrma`
      ];
      console.warn('没有选中任何动作，使用兜底动画');
      return fallback;
    }

    console.log('本次要加载的 VRMA：', urls);
    return urls;

  } catch (err) {
    console.error('获取动画列表失败：', err);
    // 兜底
    return [`${window.location.protocol}//${window.location.host}/vrm/animations/akimbo.vrma`];
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
                        Math.sin(cycleTime * 1.4 + idleOffsets.head) * 0.02,     
                        0                                                     
                    );
                    break;
                    
                case 'head':
                    euler.set(
                        Math.sin(cycleTime * 1.0 + idleOffsets.head) * 0.02,     
                        Math.sin(cycleTime * 1.4 + idleOffsets.head) * 0.03,     
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
        if (time >= 1.4 && time <= 1.6) {
            const progress = (time - 1.4) / 0.2;
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
    }
}

/**
 * 单个语音块的动画循环，用于驱动口型
 * @param {string|number} chunkId 
 * @param {object} chunkState 
 */
function startChunkAnimation(chunkId, chunkState) {
    if (!chunkState || !chunkState.isPlaying || !chunkState.analyser) {
        console.log(`无法为 Chunk ${chunkId} 启动动画`);
        return;
    }

    const dataArray = new Uint8Array(chunkState.analyser.frequencyBinCount);
    let frameCount = 0;

    function animateChunk() {
        const currentState = chunkAnimations.get(chunkId);
        if (!currentState || !currentState.isPlaying) {
            console.log(`因状态改变，停止 Chunk ${chunkId} 的动画`);
            return;
        }

        frameCount++;

        // 从分析器获取实时音频频率数据
        chunkState.analyser.getByteFrequencyData(dataArray);

        // 计算音量强度
        let sum = 0;
        // 人声主要集中在低频区域，可以只分析这部分以获得更准确的结果
        const relevantData = dataArray.slice(0, dataArray.length * 0.5);
        for (let i = 0; i < relevantData.length; i++) {
            sum += relevantData[i];
        }
        const average = sum / relevantData.length;

        // 应用口型动画
        if (currentVrm && currentVrm.expressionManager) {
            let max_mouthOpen = 0.8; // 默认最大张嘴程度
            const expression = chunkState.expression;
            
            // 处理其他表情
            if (expression) {
                // 1. 将 口型动画 添加到 mouthExpressionNames（作为被覆盖者）
                currentVrm.expressionManager.mouthExpressionNames = ['aa'];

                // 2. 为'happy', 'surprised'表情设置 overrideMouth 属性（作为覆盖者）
                const mouthExpressions = ['happy', 'surprised'];

                mouthExpressions.forEach(expressionName => {
                    const exp = currentVrm.expressionManager.getExpression(expressionName);
                    if (exp) {
                        exp.overrideMouth = 'block'; 
                    }
                });
                if (['surprised','happy','angry', 'sad', 'neutral', 'relaxed'].includes(expression)) {
                    currentVrm.expressionManager.setValue(expression, 1.0);
                } else if (['blink', 'blinkLeft', 'blinkRight'].includes(expression)) {
                    // 简单的眨眼动画，持续1秒
                    const progress = (frameCount % 30) / 30;
                    const blinkValue = Math.sin(progress * Math.PI);
                    currentVrm.expressionManager.setValue(expression, blinkValue);
                }
            }

            // 根据音量驱动口型
            const intensity = Math.min(average / 6, 1.0); // 40是敏感度系数，可调整
            if (intensity > 0.05) { // 阈值，防止背景噪音导致嘴动
                const mouthOpen = Math.min(intensity * 1.5, max_mouthOpen);
                currentVrm.expressionManager.setValue('aa', mouthOpen); 
                // 添加一些'ih'口型作为变化
                const variation = Math.sin(frameCount * 0.2) * 0.1;
                currentVrm.expressionManager.setValue('ih', Math.min(Math.max(0, mouthOpen * 0.5 + variation), max_mouthOpen));
            } else {
                // 平滑地闭上嘴巴
                const currentAA = currentVrm.expressionManager.getValue('aa') || 0;
                const currentIH = currentVrm.expressionManager.getValue('ih') || 0;
                currentVrm.expressionManager.setValue('aa', Math.max(0, currentAA * 0.8 - 0.05));
                currentVrm.expressionManager.setValue('ih', Math.max(0, currentIH * 0.7 - 0.03));
            }
        }

        currentState.animationId = requestAnimationFrame(animateChunk);
    }

    console.log(`为 Chunk ${chunkId} 启动动画循环`);
    chunkState.animationId = requestAnimationFrame(animateChunk);
}

/**
 * 为单个语音块启动基于音频分析的口型同步
 * @param {object} data 包含音频和表情信息的数据对象
 */
async function startLipSyncForChunk(data) {
    const chunkId = data.chunkIndex;

    if (chunkAnimations.has(chunkId)) {
        stopChunkAnimation(chunkId);
    }

    if (!currentVrm || !currentVrm.expressionManager) {
        console.error('VRM 或表情管理器尚未准备好');
        return;
    }
    
    // 后端必须提供 Base64 编码的音频数据
    if (!data.audioDataUrl) {
        console.error(`Chunk ${chunkId} 缺少 'audioDataUrl'`);
        return;
    }

    try {
        const chunkState = {
            isPlaying: true,
            animationId: null,
            audio: null,
            audioSource: null,
            analyser: null,
            expression: null,
        };
        chunkAnimations.set(chunkId, chunkState);

        // 初始化 Web Audio API 上下文
        if (!currentAudioContext) {
            currentAudioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        // 激活 AudioContext
        if (currentAudioContext.state === 'suspended') {
            await currentAudioContext.resume();
        }

        // 处理表情
        const expressions = data.expressions || [];
        if (expressions.length > 0) {
            chunkState.expression = expressions[0].replace(/<|>/g, '');
        }

        // 创建音频元素
        const audio = new Audio();
        audio.crossOrigin = 'anonymous';
        audio.src = data.audioDataUrl;
        audio.volume = 0.001; // 静音播放，我们只关心数据
        chunkState.audio = audio;

        await new Promise((resolve, reject) => {
            audio.addEventListener('canplaythrough', resolve, { once: true });
            audio.addEventListener('error', reject, { once: true });
            audio.load();
        });

        if (!chunkAnimations.has(chunkId)) {
            return; // 在加载时被取消
        }

        // 创建分析器节点 (Web Audio API)
        const analyser = currentAudioContext.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.3;
        chunkState.analyser = analyser;

        // 创建媒体源节点并连接 (Web Audio API)
        const audioSource = currentAudioContext.createMediaElementSource(audio);
        audioSource.connect(analyser);
        analyser.connect(currentAudioContext.destination); // 必须连接到输出才能处理
        chunkState.audioSource = audioSource;

        await audio.play();

        startChunkAnimation(chunkId, chunkState);

        audio.addEventListener('ended', () => {
            console.log(`Chunk ${chunkId} 音频结束`);
            stopChunkAnimation(chunkId);
        }, { once: true });

    } catch (error) {
        console.error(`为 Chunk ${chunkId} 启动口型同步时出错:`, error);
        stopChunkAnimation(chunkId);
    }
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
            obj.material.alphaTest = 0.5;
            obj.material.depthWrite = false;
            obj.material.needsUpdate = true;
            }
            
            // 确保正确混合模式
            obj.material.blending = THREE.NormalBlending;
            obj.material.premultipliedAlpha = true;
            
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
        currentVrm = vrm;
        console.log( vrm );
        scene.add( vrm.scene );
        // 让模型投射阴影
        vrm.scene.traverse((obj) => {
            if (obj.isMesh) {
                obj.castShadow = true;
                obj.receiveShadow = true;   // 如需让模型本身也接收阴影可保留
            }
        });
        // 设置自然姿势
        setNaturalPose(vrm);

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
        bottom: 30%;
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

function updateSubtitle(text, chunkIndex) {
    if (!isSubtitleEnabled) return;
    
    if (!subtitleElement) initSubtitleElement();
    // 如果text只包含空白字符，则清除字幕
    if (!text.trim()) {
        clearSubtitle();
        return;
    }
    currentSubtitleChunkIndex = chunkIndex;
    
    subtitleElement.style.opacity = '0';
    setTimeout(() => {
        subtitleElement.textContent = text;
        
        // 自动调整宽度
        const maxWidth = window.innerWidth * 0.8;
        subtitleElement.style.width = 'max-content';
        subtitleElement.style.minWidth = '100px';
        
        const rect = subtitleElement.getBoundingClientRect();
        if (rect.width > maxWidth) {
            subtitleElement.style.width = `${maxWidth}px`;
        }
        
        subtitleElement.style.opacity = '1';
    }, 300);
    
    if (subtitleTimeout) clearTimeout(subtitleTimeout);
}

// 清除字幕
function clearSubtitle() {
    if (subtitleElement) {
        subtitleElement.style.opacity = '0';
        currentSubtitleChunkIndex = -1;
    }
}

// animate
const clock = new THREE.Clock();
clock.start();

// 在animate函数中替换原来的眨眼动画代码
function animate() {
    requestAnimationFrame(animate);
    
    const deltaTime = clock.getDelta();
    
    if (currentVrm) {
        // 只需要更新 VRM 和 Mixer
        currentVrm.update(deltaTime);
        if (currentVrm.lookAt) {
            currentVrm.lookAt.update(deltaTime);
        }
    }
    
    if (currentMixer) {
        currentMixer.update(deltaTime);
    }
    
    renderer.render(scene, camera);
    
    // 处理窗口大小变化时字幕位置
    if (subtitleElement && !isDraggingSubtitle) {
        const rect = subtitleElement.getBoundingClientRect();
        
        // 如果字幕在窗口外，重置到默认位置
        if (rect.bottom > window.innerHeight || rect.right > window.innerWidth) {
            subtitleElement.style.left = '50%';
            subtitleElement.style.bottom = '30%';
            subtitleElement.style.top = 'auto';
            subtitleElement.style.transform = 'translateX(-50%)';
        }
    }
}
     
if (isElectron) {
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
        transition: all 0.3s ease;
        pointer-events: none;
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
        
        // 工具提示显示函数 - 现在在左侧显示
        function showTooltip(button, text) {
            const rect = button.getBoundingClientRect();
            tooltip.textContent = text;
            
            // 计算位置（在按钮左侧）
            const topPosition = rect.top + (rect.height - tooltip.offsetHeight) / 2;
            tooltipContainer.style.left = `${rect.left - tooltip.offsetWidth - 15}px`;
            tooltipContainer.style.top = `${topPosition}px`;
            
            // 显示工具提示
            tooltipContainer.style.opacity = '1';
            tooltipContainer.style.transform = 'translateX(0)';
        }
        
        // 隐藏工具提示
        function hideTooltip() {
            tooltipContainer.style.opacity = '0';
            tooltipContainer.style.transform = 'translateX(-10px)';
        }
        
        // 为所有按钮添加悬浮效果
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

        // 拖拽按钮
        const dragButton = document.createElement('div');
        dragButton.id = 'drag-handle';
        dragButton.style.cssText = `
                width: 36px;
                height: 36px;
                background: rgba(255,255,255,0.95);
                border: 2px solid rgba(0,0,0,0.1);
                border-radius: 50%;
                color: #333;
                cursor: pointer;
                -webkit-app-region: drag;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 14px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                transition: all 0.2s ease;
                user-select: none;
                pointer-events: auto;
                backdrop-filter: blur(10px);
        `;

        // 创建一个内部拖拽区域来确保拖拽功能正常
        const dragArea = document.createElement('div');
        dragArea.style.cssText = `
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            -webkit-app-region: drag;
            z-index: 1;
        `;

        // 图标容器
        const iconContainer = document.createElement('div');
        iconContainer.innerHTML = '<el-icon class="logo-icon"><img src="./source/icon.png" /></el-icon>';
        iconContainer.style.cssText = `
            position: relative;
            z-index: 2;
            pointer-events: none;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            height: 100%;
            -webkit-app-region: drag;
        `;

        // 直接设置img样式
        const img = iconContainer.querySelector('img');
        if (img) {
            img.style.cssText = `
                width: 24px;
                height: 24px;
                border: none;
                vertical-align: middle;
                object-fit: contain;
            `;
        }

        // 组装拖拽按钮
        dragButton.innerHTML = '';
        dragButton.appendChild(dragArea);
        dragButton.appendChild(iconContainer);
        // WebSocket 状态按钮
        const wsStatusButton = document.createElement('div');
        wsStatusButton.id = 'ws-status-handle';
        wsStatusButton.innerHTML = '<i class="fas fa-wifi"></i>';
        wsStatusButton.style.cssText = `
            width: 36px;
            height: 36px;
            background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1);
            border-radius: 50%;
            color: #333;
            cursor: pointer;
            -webkit-app-region: no-drag;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: all 0.2s ease;
            user-select: none;
            pointer-events: auto;
            backdrop-filter: blur(10px);
            color: ${wsConnected ? '#28a745' : '#dc3545'};
        `;
        // WebSocket 状态按钮事件
        wsStatusButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (wsConnected) {
                // 断开连接
                if (ttsWebSocket) {
                    ttsWebSocket.close();
                }
            } else {
                // 重新连接
                initTTSWebSocket();
            }
        });
        // 添加悬停效果
        wsStatusButton.addEventListener('mouseenter', () => {
            wsStatusButton.style.background = 'rgba(255,255,255,1)';
            wsStatusButton.style.transform = 'scale(1.1)';
            wsStatusButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
        });
        
        wsStatusButton.addEventListener('mouseleave', () => {
            wsStatusButton.style.background = 'rgba(255,255,255,0.95)';
            wsStatusButton.style.transform = 'scale(1)';
            wsStatusButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });
        // 更新 WebSocket 状态显示
        async function updateWSStatus() {
            wsStatusButton.style.color = wsConnected ? '#28a745' : '#dc3545';
            wsStatusButton.title = wsConnected ? await t('WebSocketConnected') :await t('WebSocketDisconnected');
        }

        // 定期更新状态
        setInterval(updateWSStatus, 1000);
        
        

            // 字幕开关按钮
            const subtitleButton = document.createElement('div');
            subtitleButton.id = 'subtitle-handle';
            subtitleButton.innerHTML = '<i class="fas fa-closed-captioning"></i>';
            subtitleButton.style.cssText = `
                width: 36px;
                height: 36px;
                background: rgba(255,255,255,0.95);
                border: 2px solid rgba(0,0,0,0.1);
                border-radius: 50%;
                color: #333;
                cursor: pointer;
                -webkit-app-region: no-drag;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 14px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                transition: all 0.2s ease;
                user-select: none;
                pointer-events: auto;
                backdrop-filter: blur(10px);
                color: ${isSubtitleEnabled ? '#28a745' : '#dc3545'};
            `;

            // 添加悬停效果
            subtitleButton.addEventListener('mouseenter', () => {
                subtitleButton.style.background = 'rgba(255,255,255,1)';
                subtitleButton.style.transform = 'scale(1.1)';
                subtitleButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
            });

            subtitleButton.addEventListener('mouseleave', () => {
                subtitleButton.style.background = 'rgba(255,255,255,0.95)';
                subtitleButton.style.transform = 'scale(1)';
                subtitleButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
            });

            // 点击事件
            subtitleButton.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                isSubtitleEnabled = !isSubtitleEnabled;
                toggleSubtitle(isSubtitleEnabled);
                subtitleButton.style.color = isSubtitleEnabled ? '#28a745' : '#dc3545';
                subtitleButton.title = isSubtitleEnabled ? await t('SubtitleEnabled') : await t('SubtitleDisabled');
            });

            // 初始状态
            subtitleButton.title = isSubtitleEnabled ? await t('SubtitleEnabled') : await t('SubtitleDisabled');

            // 添加到控制面板

        // 闲置动画模式切换按钮
        const idleAnimationButton = document.createElement('div');
        idleAnimationButton.id = 'idle-animation-handle';
        idleAnimationButton.innerHTML = useVRMAIdleAnimations ? 
            '<i class="fas fa-stop"></i>' : 
            '<i class="fas fa-play"></i>';
        idleAnimationButton.style.cssText = `
            width: 36px;
            height: 36px;
            background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1);
            border-radius: 50%;
            color: ${useVRMAIdleAnimations ? '#ff6b35' : '#28a745'};
            cursor: pointer;
            -webkit-app-region: no-drag;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: all 0.2s ease;
            user-select: none;
            pointer-events: auto;
            backdrop-filter: blur(10px);
        `;

        // 添加悬停效果
        idleAnimationButton.addEventListener('mouseenter', () => {
            idleAnimationButton.style.background = 'rgba(255,255,255,1)';
            idleAnimationButton.style.transform = 'scale(1.1)';
            idleAnimationButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
        });

        idleAnimationButton.addEventListener('mouseleave', () => {
            idleAnimationButton.style.background = 'rgba(255,255,255,0.95)';
            idleAnimationButton.style.transform = 'scale(1)';
            idleAnimationButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });

        // 点击事件
        idleAnimationButton.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            // 防止重复点击
            if (isIdleAnimationModeChanging) return;
            
            await toggleIdleAnimationMode();
        });

        // 初始状态
        idleAnimationButton.title = useVRMAIdleAnimations ? 
            await t('UsingVRMAAnimations') || 'Using VRMA Animations' : 
            await t('UsingProceduralAnimations') || 'Using Procedural Animations';

        // 添加到控制面板（在字幕按钮之后）

        // 刷新按钮
        const refreshButton = document.createElement('div');
        refreshButton.id = 'refresh-handle';
        refreshButton.innerHTML = '<i class="fas fa-redo-alt"></i>';
        refreshButton.style.cssText = `
                width: 36px;
                height: 36px;
                background: rgba(255,255,255,0.95);
                border: 2px solid rgba(0,0,0,0.1);
                border-radius: 50%;
                color: #333;
                cursor: pointer;
                -webkit-app-region: no-drag;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 14px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                transition: all 0.2s ease;
                user-select: none;
                pointer-events: auto;
                backdrop-filter: blur(10px);
        `;
        // 获取所有模型（只执行一次）
        await getAllModels();
        
        // 向上箭头按钮（切换到上一个模型）
        const prevModelButton = document.createElement('div');
        prevModelButton.id = 'prev-model-handle';
        prevModelButton.innerHTML = '<i class="fas fa-chevron-up"></i>';
        prevModelButton.style.cssText = `
            width: 36px;
            height: 36px;
            background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1);
            border-radius: 50%;
            color: #333;
            cursor: pointer;
            -webkit-app-region: no-drag;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: all 0.2s ease;
            user-select: none;
            pointer-events: auto;
            backdrop-filter: blur(10px);
        `;
        
        // 向下箭头按钮（切换到下一个模型）
        const nextModelButton = document.createElement('div');
        nextModelButton.id = 'next-model-handle';
        nextModelButton.innerHTML = '<i class="fas fa-chevron-down"></i>';
        nextModelButton.style.cssText = `
            width: 36px;
            height: 36px;
            background: rgba(255,255,255,0.95);
            border: 2px solid rgba(0,0,0,0.1);
            border-radius: 50%;
            color: #333;
            cursor: pointer;
            -webkit-app-region: no-drag;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: all 0.2s ease;
            user-select: none;
            pointer-events: auto;
            backdrop-filter: blur(10px);
        `;
        
        // 添加悬停效果和工具提示 - 上一个模型按钮
        prevModelButton.addEventListener('mouseenter', async () => {
            prevModelButton.style.background = 'rgba(255,255,255,1)';
            prevModelButton.style.transform = 'scale(1.1)';
            prevModelButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
            
            // 显示下一个模型的名称
            const prevModel = getPrevModelInfo();
            if (prevModel) {
                prevModelButton.title = `${await t('Previous')}: ${prevModel.name}`;
            }
        });
        
        prevModelButton.addEventListener('mouseleave', () => {
            prevModelButton.style.background = 'rgba(255,255,255,0.95)';
            prevModelButton.style.transform = 'scale(1)';
            prevModelButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });
        
        // 添加悬停效果和工具提示 - 下一个模型按钮
        nextModelButton.addEventListener('mouseenter', async () => {
            nextModelButton.style.background = 'rgba(255,255,255,1)';
            nextModelButton.style.transform = 'scale(1.1)';
            nextModelButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
            
            // 显示下一个模型的名称
            const nextModel = getNextModelInfo();
            if (nextModel) {
                nextModelButton.title = `${await t('Next')}: ${nextModel.name}`;
            }
        });
        
        nextModelButton.addEventListener('mouseleave', () => {
            nextModelButton.style.background = 'rgba(255,255,255,0.95)';
            nextModelButton.style.transform = 'scale(1)';
            nextModelButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });
        
        // 上一个模型按钮点击事件
        prevModelButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (allModels.length > 1) {
                switchToModel(currentModelIndex - 1);
            }
        });
        
        // 下一个模型按钮点击事件
        nextModelButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (allModels.length > 1) {
                switchToModel(currentModelIndex + 1);
            }
        });
        
        // 设置按钮初始状态
        async function initModelButtons() {
            if (allModels.length <= 1) {
                // 如果只有一个或没有模型，禁用按钮
                prevModelButton.style.opacity = '0.5';
                prevModelButton.style.cursor = 'not-allowed';
                prevModelButton.title = 'No other models available';
                
                nextModelButton.style.opacity = '0.5';
                nextModelButton.style.cursor = 'not-allowed';
                nextModelButton.title = 'No other models available';
            } else {
                // 设置初始工具提示
                const prevModel = getPrevModelInfo();
                const nextModel = getNextModelInfo();
                
                prevModelButton.title = prevModel ? `Previous: ${prevModel.name}` : 'Previous Model';
                nextModelButton.title = nextModel ? `Next: ${nextModel.name}` : 'Next Model';
            }
            
            console.log(`Model buttons initialized. Current: ${getCurrentModelInfo()?.name || 'Unknown'} (${currentModelIndex + 1}/${allModels.length})`);
        }
        
        initModelButtons();
        

        
        console.log(`Model switching buttons added. Available models: ${allModels.length}`);
        
        // 关闭按钮
        const closeButton = document.createElement('div');
        closeButton.id = 'close-handle';
        closeButton.innerHTML = '<i class="fas fa-times"></i>';
        closeButton.style.cssText = `
                width: 36px;
                height: 36px;
                background: rgba(255,255,255,0.95);
                border: 2px solid rgba(0,0,0,0.1);
                border-radius: 50%;
                color: #333;
                cursor: pointer;
                -webkit-app-region: no-drag;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 14px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                transition: all 0.2s ease;
                user-select: none;
                pointer-events: auto;
                backdrop-filter: blur(10px);
        `;
        
        // 添加悬停效果 - 刷新按钮
        refreshButton.addEventListener('mouseenter', () => {
            refreshButton.style.background = 'rgba(255,255,255,1)';
            refreshButton.style.transform = 'scale(1.1)';
            refreshButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
        });
        
        refreshButton.addEventListener('mouseleave', () => {
            refreshButton.style.background = 'rgba(255,255,255,0.95)';
            refreshButton.style.transform = 'scale(1)';
            refreshButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });

        // 刷新按钮点击事件
        refreshButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            // 刷新页面
            window.location.reload();
        });
        
        // 添加悬停效果 - 关闭按钮
        closeButton.addEventListener('mouseenter', () => {
            closeButton.style.background = 'rgba(255,255,255,1)';
            closeButton.style.transform = 'scale(1.1)';
            closeButton.style.boxShadow = '0 6px 16px rgba(0,0,0,0.2)';
        });
        
        closeButton.addEventListener('mouseleave', () => {
            closeButton.style.background = 'rgba(255,255,255,0.95)';
            closeButton.style.transform = 'scale(1)';
            closeButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        });

        // 关闭按钮点击事件
        closeButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            window.close();
        });
        async function initbutton() {
            dragButton.title = await t('dragWindow');
            refreshButton.title = await t('refreshWindow');
            closeButton.title = await t('closeWindow');
        }
        initbutton();
        // 保存所有需要隐藏的按钮引用
        const controlButtons = [];
        // 鼠标穿透锁定按钮
        const lockButton = document.createElement('div');
        lockButton.id = 'lock-handle';
        let isMouseLocked = false; // 初始状态为解锁（不穿透）

        // 初始化状态
        async function initLockButton() {
            lockButton.innerHTML = '<i class="fas fa-lock-open"></i>';
            lockButton.style.cssText = `
                width: 36px;
                height: 36px;
                background: rgba(255,255,255,0.95);
                border: 2px solid rgba(0,0,0,0.1);
                border-radius: 50%;
                color: #28a745;
                cursor: pointer;
                -webkit-app-region: no-drag;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 14px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                transition: all 0.2s ease;
                user-select: none;
                pointer-events: auto;
                backdrop-filter: blur(10px);
                margin-bottom: 8px;
            `;
            
            lockButton.title = await t('UnlockWindow');
            updateLockButtonState();
        }

        // 更新锁定按钮状态
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

        // 隐藏所有其他按钮（除了锁定按钮）
        function hideOtherButtons() {
            controlButtons.forEach(button => {
                if (button !== lockButton) {
                    button.style.opacity = '0';
                    button.style.visibility = 'hidden';
                    button.style.pointerEvents = 'none';
                    button.style.transform = 'scale(0.8)';
                }
            });
            
            // 调整锁定按钮位置到中心
            lockButton.style.marginBottom = '0';
            lockButton.style.marginTop = 'auto';
        }

        // 显示所有按钮
        function showAllButtons() {
            controlButtons.forEach(button => {
                button.style.opacity = '1';
                button.style.visibility = 'visible';
                button.style.pointerEvents = 'auto';
                button.style.transform = 'scale(1)';
            });
            
            // 恢复锁定按钮位置
            lockButton.style.marginBottom = '8px';
            lockButton.style.marginTop = '0';
        }

        // 切换锁定状态
        async function toggleMouseLock() {
            isMouseLocked = !isMouseLocked;
            
            if (isMouseLocked) {
                // 锁定模式：窗口穿透，隐藏其他按钮
                window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
                hideOtherButtons();
            } else {
                // 解锁模式：窗口正常交互，显示所有按钮
                window.electronAPI.setIgnoreMouseEvents(false);
                showAllButtons();
            }
            
            updateLockButtonState();
            
            // 发送状态更新到主窗口
            sendToMain('mouseLockStatus', { locked: isMouseLocked });
            
            // 更新工具提示
            updateButtonTooltips();
        }


        // 添加事件监听
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

        lockButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleMouseLock();
        });

        // 在控制面板鼠标事件中添加特殊处理
        controlPanel.addEventListener('mouseenter', () => {
            if (isMouseLocked) {
                // 在锁定模式下，控制面板保持可交互
                window.electronAPI.setIgnoreMouseEvents(false);
            }
        });

        controlPanel.addEventListener('mouseleave', () => {
            if (isMouseLocked) {
                // 离开控制面板时恢复穿透
                window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
            }
        });

        // 在组装控制面板时添加锁定按钮
        await initLockButton();

        // 组装控制面板
        controlPanel.appendChild(dragButton);
        controlPanel.appendChild(lockButton);
        controlPanel.appendChild(wsStatusButton);
        controlPanel.appendChild(subtitleButton);
        controlPanel.appendChild(idleAnimationButton);
        controlPanel.appendChild(prevModelButton);
        controlPanel.appendChild(nextModelButton);
        controlPanel.appendChild(refreshButton);
        controlPanel.appendChild(closeButton);
        
        // 收集所有需要隐藏的按钮（除了锁定按钮）
        controlButtons.push(
            dragButton, 
            wsStatusButton, 
            subtitleButton, 
            idleAnimationButton, 
            prevModelButton, 
            nextModelButton, 
            refreshButton, 
            closeButton
        );

        // 添加到页面
        document.body.appendChild(controlPanel);

        // 为每个按钮添加悬浮提示
        addHoverEffect(dragButton, await t('dragWindow'));
        addHoverEffect(lockButton, isMouseLocked ? await t('UnlockWindow') : await t('LockWindow'));
        addHoverEffect(wsStatusButton, wsConnected ? await t('WebSocketConnected') : await t('WebSocketDisconnected'));
        addHoverEffect(subtitleButton, isSubtitleEnabled ? await t('SubtitleEnabled') : await t('SubtitleDisabled'));
        addHoverEffect(idleAnimationButton, useVRMAIdleAnimations ? 
            await t('UsingVRMAAnimations') : 
            await t('UsingProceduralAnimations'));
        
        // 模型切换按钮
        const prevModel = getPrevModelInfo();
        const nextModel = getNextModelInfo();
        addHoverEffect(prevModelButton, prevModel ? `${await t('Previous')}: ${prevModel.name}` : await t('NoPreviousModel'));
        addHoverEffect(nextModelButton, nextModel ? `${await t('Next')}: ${nextModel.name}` : await t('NoNextModel'));
        
        addHoverEffect(refreshButton, await t('refreshWindow'));
        addHoverEffect(closeButton, await t('closeWindow'));
        
        // 当状态变化时更新工具提示
        async function updateButtonTooltips() {
            // 更新锁定按钮提示
            addHoverEffect(lockButton, isMouseLocked ? await t('UnlockWindow') : await t('LockWindow'));
            
            // 更新WebSocket状态提示
            addHoverEffect(wsStatusButton, wsConnected ? await t('WebSocketConnected') : await t('WebSocketDisconnected'));
            
            // 更新字幕按钮提示
            addHoverEffect(subtitleButton, isSubtitleEnabled ? await t('SubtitleEnabled') : await t('SubtitleDisabled'));
            
            // 更新闲置动画按钮提示
            addHoverEffect(idleAnimationButton, useVRMAIdleAnimations ? 
                await t('UsingVRMAAnimations') : 
                await t('UsingProceduralAnimations'));
            
            // 更新模型切换按钮提示
            const prevModel = getPrevModelInfo();
            const nextModel = getNextModelInfo();
            addHoverEffect(prevModelButton, prevModel ? `${await t('Previous')}: ${prevModel.name}` : await t('NoPreviousModel'));
            addHoverEffect(nextModelButton, nextModel ? `${await t('Next')}: ${nextModel.name}` : await t('NoNextModel'));
        }
        
        // 定期更新提示（状态变化时也需要调用）
        setInterval(updateButtonTooltips, 1000);

        // 显示/隐藏控制逻辑
        let hideTimeout;
        let isControlPanelHovered = false;
        
        // 显示控制面板
        function showControlPanel() {
            clearTimeout(hideTimeout);
            controlPanel.style.opacity = '1';
            controlPanel.style.visibility = 'visible';
            controlPanel.style.transform = 'translateX(0)';
            controlPanel.style.pointerEvents = 'auto';
        }
        
        // 隐藏控制面板
        function hideControlPanel() {
            if (!isControlPanelHovered) {
                controlPanel.style.opacity = '0';
                controlPanel.style.visibility = 'hidden';
                controlPanel.style.transform = 'translateX(20px)';
                controlPanel.style.pointerEvents = 'none';
            }
        }
        
        // 延迟隐藏控制面板
        function scheduleHide() {
            clearTimeout(hideTimeout);
            hideTimeout = setTimeout(hideControlPanel, 2000); // 2秒后隐藏
        }
        
        // 窗口鼠标进入事件
        document.body.addEventListener('mouseenter', () => {
            showControlPanel();
        });
        
        // 窗口鼠标移动事件（重置隐藏计时器）
        document.body.addEventListener('mousemove', () => {
            showControlPanel();
            scheduleHide();
        });
        
        // 窗口鼠标离开事件
        document.body.addEventListener('mouseleave', () => {
            if (!isControlPanelHovered) {
                scheduleHide();
            }
        });
        
        // 控制面板鼠标进入事件
        controlPanel.addEventListener('mouseenter', () => {
            isControlPanelHovered = true;
            clearTimeout(hideTimeout);
            showControlPanel();
        });
        
        // 控制面板鼠标离开事件
        controlPanel.addEventListener('mouseleave', () => {
            isControlPanelHovered = false;
            scheduleHide();
        });
        
        // 鼠标静止检测
        let mouseStopTimeout;
        document.body.addEventListener('mousemove', () => {
            clearTimeout(mouseStopTimeout);
            mouseStopTimeout = setTimeout(() => {
                if (!isControlPanelHovered) {
                    hideControlPanel();
                }
            }, 3000); // 鼠标静止3秒后隐藏
        });
        
        // 初始状态：隐藏控制面板
        scheduleHide();

        console.log('控制面板已添加到页面');
    }, 1000);
}

// 在全局变量区域添加
let ttsWebSocket = null;
let wsConnected = false;
let currentAudioContext = null; // 用于管理音频处理
const chunkAnimations = new Map(); // 用于存储每个语音块的动画状态

// 初始化 WebSocket 连接
function initTTSWebSocket() {
    const http_protocol = window.location.protocol;
    const ws_protocol = http_protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${ws_protocol}//${window.location.host}/ws/vrm`;
    ttsWebSocket = new WebSocket(wsUrl);
    
    ttsWebSocket.onopen = () => {
        console.log('VRM TTS WebSocket connected');
        wsConnected = true;
        
        // 发送连接确认
        sendToMain('vrmConnected', { status: 'ready' });
    };
    
    ttsWebSocket.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            handleTTSMessage(message);
        } catch (error) {
            console.error('Error parsing WebSocket message:', error);
        }
    };
    
    ttsWebSocket.onclose = () => {
        console.log('VRM TTS WebSocket disconnected');
        wsConnected = false;
        
        // 自动重连
        setTimeout(() => {
            if (!wsConnected) {
                initTTSWebSocket();
            }
        }, 3000);
    };
    
    ttsWebSocket.onerror = (error) => {
        console.error('VRM TTS WebSocket error:', error);
    };
}
initTTSWebSocket();

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

// 修改 handleTTSMessage 函数
function handleTTSMessage(message) {
    const { type, data } = message;

    switch (type) {
        case 'ttsStarted':
            console.log('TTS 流程开始');
            stopAllChunkAnimations(); // 停止所有之前的口型动画
            clearSubtitle();
            break;

        case 'startSpeaking':
            console.log('收到播放指令, Chunk:', data.chunkIndex);
            // 调用新的口型同步函数
            startLipSyncForChunk(data); 
            if (data.text) {
                updateSubtitle(data.text, data.chunkIndex);
            }
            break;

        case 'chunkEnded':
            // 注意：现在音频播放结束时会自动停止，所以这个消息的处理可以简化
            console.log('后端通知 Chunk 结束:', data.chunkIndex);
            // 如果字幕仍然显示的是这个 chunk 的，就清除它
            if (currentSubtitleChunkIndex === data.chunkIndex) {
                clearSubtitle();
            }
            break;

        case 'stopSpeaking':
            console.log('收到停止指令');
            stopAllChunkAnimations();
            clearSubtitle();
            break;

        case 'allChunksCompleted':
            console.log('所有 TTS 语音块处理完成');
            // stopAllChunkAnimations 会在最后一个 chunk 结束时自动调用并重置表情
            // 这里可以确保万无一失
            stopAllChunkAnimations();
            clearSubtitle();
            sendToMain('animationComplete', { status: 'completed' });
            break;
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
async function switchToModel(index) {
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
    if (newIndex === currentModelIndex) {
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
    
    try {
        // 显示加载提示（可选）
        showModelSwitchingIndicator(selectedModel.name);
        // 🔥 添加：停止当前的闲置动画
        if (idleAnimationManager) {
            idleAnimationManager.stopAllAnimations();
        }
        
        // 移除当前VRM模型
        if (currentVrm) {
            scene.remove(currentVrm.scene);
            currentVrm = undefined;
        }
        
        // 🔥 添加：重置闲置动画管理器
        idleAnimationManager = null;

        // 移除当前VRM模型
        if (currentVrm) {
            scene.remove(currentVrm.scene);
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
                    obj.material.alphaTest = 0.5;
                    obj.material.depthWrite = false;
                    obj.material.needsUpdate = true;
                    }
                    
                    // 确保正确混合模式
                    obj.material.blending = THREE.NormalBlending;
                    obj.material.premultipliedAlpha = true;
                    
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
                currentVrm = vrm;
                console.log('New VRM loaded:', vrm);
                scene.add(vrm.scene);
                // 让模型投射阴影
                vrm.scene.traverse((obj) => {
                    if (obj.isMesh) {
                        obj.castShadow = true;
                        obj.receiveShadow = true;   // 如需让模型本身也接收阴影可保留
                    }
                });
                // 设置自然姿势
                setNaturalPose(vrm);

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