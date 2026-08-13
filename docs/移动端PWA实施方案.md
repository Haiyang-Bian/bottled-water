# AgentHub 移动端 PWA 实施方案

> 本文说明如何将现有 PC Web 前端改造为支持手机浏览器的响应式应用，并封装为 PWA（Progressive Web App），使用户可以将应用添加到手机主屏幕，获得接近原生 App 的 IM 聊天体验。
>
> 方案定位：不动后端，只动前端；不做功能新增，只做体验适配。

---

## 0. 当前版本对齐

本方案是移动端体验实施说明。落地时需要与当前 Web 版本保持以下行为一致：

- 新建会话默认只选择一个 Daily Chat Agent，移动端不应默认选中所有专业 Agent。
- 用户手动创建多 Agent 群聊后，复杂任务由 Team Leader 自动组织合适 Agent，并展示短任务进度、Agent 报告和按需最终交付。
- 简单聊天或单 Agent 任务不应出现 Team Leader 结尾复述。
- 启用 workflow 的群聊按保存的画布执行；未启用 workflow 的群聊由 Team Lead Policy 通过 Runtime Kernel 调度。
- 移动端若隐藏工作流画布和复杂配置页，仍需保留消息流、工具摘要、协作进度、产物卡片、预览/导出入口的核心反馈。

---

## 1. 方案概述

### 1.1 目标

让甲方在手机上可以正常使用 AgentHub 的核心 IM 功能（单聊、群聊、消息收发、Agent 对话），体验接近原生 App，且不需要上架应用商店。

### 1.2 核心原则

| 原则 | 说明 |
|------|------|
| **后端零改动** | 复用现有 FastAPI 接口，不新增、不修改 API |
| **渐进式改造** | PC 端体验不受影响，移动端逐步适配 |
| **功能收敛** | 移动端只保留 IM 核心功能，隐藏工作流画布、复杂表格等 |
| **一套代码** | 不新建项目，在现有 React + Vite 代码基础上改造 |

### 1.3 技术选型

| 层面 | 选型 | 说明 |
|------|------|------|
| 响应式框架 | CSS Media Query + Ant Design Mobile 适配 | 不引入新 UI 框架，复用 Ant Design 的移动端适配能力 |
| 路由适配 | React Router + 条件渲染 | 根据屏幕宽度决定布局方式 |
| PWA 基础 | Vite PWA Plugin (`vite-plugin-pwa`) | Vite 生态官方插件，自动生成 manifest 和 Service Worker |
| 移动端检测 | `navigator.userAgent` + CSS `hover: none` | 区分触屏设备和鼠标设备 |
| 手势交互 | CSS `touch-action` + 少量自定义事件 | 下拉刷新、左滑返回等原生手势 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     用户设备                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   PC 浏览器   │  │ 手机浏览器   │  │  PWA（主屏）  │      │
│  │   ≥1024px    │  │  375~768px   │  │  375~768px   │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
└─────────┼─────────────────┼─────────────────┼──────────────┘
          │                 │                 │
          └─────────────────┴─────────────────┘
                            │
                    ┌───────┴───────┐
                    │  Vite 构建产物 │
                    │    (dist/)    │
                    │  同一套代码    │
                    └───────┬───────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
     PC 布局分支      移动端布局分支      PWA 配置分支
   (三栏工作台)      (单栏聊天界面)      (manifest + SW)
          │                 │                 │
          └─────────────────┴─────────────────┘
                            │
                    ┌───────┴───────┐
                    │  Nginx / CDN  │
                    └───────┬───────┘
                            │
                    ┌───────┴───────┐
                    │  FastAPI 后端  │
                    └───────────────┘
```

---

## 3. 实施阶段

### Phase 1：响应式骨架（1 周）

**目标**：让现有页面在手机浏览器上能正常显示，不崩不错位。

#### 3.1.1 全局断点定义

在现有 SCSS 系统中新增移动端断点变量（复用现有 `_variables.scss`）：

| 断点名称 | 范围 | 对应设备 |
|----------|------|----------|
| `xs` | < 576px | 手机竖屏 |
| `sm` | 576px ~ 767px | 手机横屏 / 小平板 |
| `md` | 768px ~ 991px | 平板竖屏 |
| `lg` | ≥ 992px | PC（现有布局） |

> 现有 PC 布局以 `lg` 为基准，不做改动。所有改造在 `lg` 以下生效。

#### 3.1.2 工作台布局改造

现有 `WorkbenchLayout` 是三栏布局（侧边栏 + 聊天区 + 预览区），移动端改为**单栏堆叠**：

```
PC (lg):
┌────────┬────────────────────┬────────────┐
│ Sidebar│    ChatPanel       │  Preview   │
│        │                    │            │
└────────┴────────────────────┴────────────┘

移动端 (md 以下):
┌──────────────────────────────┐
│  Header（标题 + 菜单按钮）    │
├──────────────────────────────┤
│                              │
│      ChatPanel               │
│      （全屏单栏）             │
│                              │
├──────────────────────────────┤
│  BottomInput（底部固定输入）  │
└──────────────────────────────┘
```

改造要点：
- 侧边栏改为**抽屉式**（Drawer），从左侧滑出
- 预览区（右侧）在移动端**隐藏**，相关功能入口移入聊天区顶部菜单
- 底部输入框**固定定位**，键盘弹出时不被遮挡（需处理 iOS Safari 的 `visualViewport`）

#### 3.1.3 需要改造的文件清单

| 文件 | 改造内容 |
|------|----------|
| `frontend/src/styles/_variables.scss` | 新增移动端断点变量 |
| `frontend/src/styles/_mixins.scss` | 新增 `respond-to` mixin（如没有） |
| `frontend/src/pages/WorkbenchPage/WorkbenchLayout.tsx` | 条件渲染：lg 以上三栏，以下单栏 + Drawer |
| `frontend/src/features/chat/components/ChatPanel/index.tsx` | 调整消息列表高度、输入框定位 |
| `frontend/src/features/sidebar/...` | 侧边栏组件支持 Drawer 模式 |

---

### Phase 2：移动端 IM 体验优化（1 周）

**目标**：让聊天体验接近微信/钉钉等原生 IM。

#### 3.2.1 消息气泡布局

现有消息列表在 PC 端是左右对齐的（用户左、Agent 右），移动端改为：
- **用户消息**：右对齐，绿色气泡
- **Agent 消息**：左对齐，白色/灰色气泡
- **系统消息**：居中，灰色小字
- 头像缩小至 32px，间距收紧

#### 3.2.2 输入区域

```
┌────────────────────────────────────┐
│ [+]  输入消息...          [发送]   │  ← 收起状态
├────────────────────────────────────┤
│ [图片] [文件] [快捷指令]           │  ← 展开状态（点击 +）
└────────────────────────────────────┘
```

- 底部输入栏**固定定位**（`position: fixed; bottom: 0`）
- 支持**长按输入框**调起语音输入（如有需要，V2 迭代）
- 发送按钮在输入非空时高亮
- 键盘弹出时页面**不滚动**（通过 `visualViewport` API 监听）

#### 3.2.3 会话列表（Sidebar 移动端版）

Drawer 内的会话列表：
- 每个会话项显示：头像、名称、最后一条消息摘要、时间、未读角标
- 支持**左滑删除/置顶**（CSS `touch-action: pan-y` + JS 手势识别）
- 顶部搜索框（固定）
- 底部"Agent 广场"和"文档"入口按钮

#### 3.2.4 功能收敛（移动端隐藏）

以下模块在移动端（`md` 以下）**完全隐藏**：

| 模块 | 隐藏原因 |
|------|----------|
| 工作流画布（Workflow Canvas）| 节点拖拽在小屏上不可用 |
| 文件预览区（Preview Panel）| 右侧栏收起，入口移入菜单 |
| 复杂表格/设置面板 | 改弹窗或简化 |
| Agent 广场的详细配置页 | 只保留列表和基础操作 |

以下模块**简化显示**：

| 模块 | 简化方式 |
|------|----------|
| Agent 广场 | 网格 → 列表，减少每行信息 |
| 模型设置 | 改为底部弹窗（Bottom Sheet） |
| 消息中的代码块 | 横向滚动，不折行 |

#### 3.2.5 需要改造的文件清单

| 文件 | 改造内容 |
|------|----------|
| `frontend/src/features/chat/components/MessageList/...` | 消息气泡移动端样式 |
| `frontend/src/features/chat/components/MessageItem/...` | 单条消息的响应式布局 |
| `frontend/src/features/chat/components/ChatInput/...` | 底部固定输入栏 |
| `frontend/src/features/sidebar/components/ConversationList/...` | 移动端会话列表样式 |
| `frontend/src/features/agentSquare/...` | 网格 → 列表适配 |
| `frontend/src/styles/_chat.scss` | 新增移动端聊天样式 |
| `frontend/src/styles/_mobile.scss` | 新增全局移动端覆盖样式 |

---

### Phase 3：PWA 封装（3~4 天）

**目标**：让用户可以将应用添加到手机主屏幕，离线时显示缓存页面。

#### 3.3.1 Vite PWA 插件集成

安装 `vite-plugin-pwa`：

```powershell
cd frontend
pnpm add -D vite-plugin-pwa
```

在 `vite.config.ts` 中配置：

```typescript
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    // ... existing plugins
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'AgentHub',
        short_name: 'AgentHub',
        description: '多 Agent 协作 IM 工作台',
        theme_color: '#1677ff',
        background_color: '#ffffff',
        display: 'standalone',
        scope: '/',
        start_url: '/app',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
      workbox: {
        // 缓存策略：运行时缓存 API 响应，预缓存静态资源
        runtimeCaching: [
          {
            urlPattern: /^https:\/\/api\./,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',
              expiration: { maxEntries: 100, maxAgeSeconds: 86400 },
            },
          },
        ],
      },
    }),
  ],
})
```

#### 3.3.2 图标资源

需要准备以下图标（放 `frontend/public/`）：

| 文件名 | 尺寸 | 用途 |
|--------|------|------|
| `icon-192.png` | 192×192 | 安卓主屏图标 |
| `icon-512.png` | 512×512 | PWA 安装弹窗大图标 |
| `apple-touch-icon.png` | 180×180 | iOS 主屏图标 |
| `favicon.ico` | 多尺寸 | 浏览器标签页 |

> 图标设计建议：使用项目 Logo 的简化版本，纯色背景，确保在小尺寸下可识别。

#### 3.3.3 iOS 专属适配

iOS Safari 对 PWA 支持有限，需要额外的 `<meta>` 标签：

```html
<!-- 全屏显示（无 Safari 工具栏） -->
<meta name="apple-mobile-web-app-capable" content="yes">
<!-- 状态栏颜色 -->
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<!-- 主屏图标 -->
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<!-- 禁止缩放（可选，看产品决策） -->
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
```

#### 3.3.4 安全区域适配（刘海屏）

使用 CSS `env()` 函数适配 iPhone 刘海屏和底部手势条：

```css
.mobile-container {
  padding-top: env(safe-area-inset-top);
  padding-bottom: env(safe-area-inset-bottom);
  padding-left: env(safe-area-inset-left);
  padding-right: env(safe-area-inset-right);
}
```

同时需要在 `index.html` 中加 viewport-fit：

```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
```

#### 3.3.5 需要改造的文件清单

| 文件 | 改造内容 |
|------|----------|
| `frontend/vite.config.ts` | 集成 `vite-plugin-pwa` |
| `frontend/index.html` | 添加 iOS PWA meta 标签、viewport-fit |
| `frontend/public/icon-*.png` | 新增图标资源 |
| `frontend/public/manifest.json` | 由插件自动生成，或手动放置 |
| `frontend/src/App.tsx` | 注册 Service Worker（插件自动处理，可能需要微调） |

---

## 4. 移动端设计规范

### 4.1 字体与间距

| 元素 | PC 端 | 移动端 |
|------|-------|--------|
| 基础字号 | 14px | 16px（iOS 最小可读字号） |
| 消息正文 | 14px | 16px |
| 会话标题 | 16px | 18px |
| 消息气泡内边距 | 12px 16px | 10px 12px |
| 列表项高度 | 56px | 64px（增大触控区域） |
| 按钮最小触控区域 | — | 44×44px |

### 4.2 颜色与主题

复用现有 Ant Design 主题色，移动端不引入新配色：
- 主色：`#1677ff`（Ant Design 蓝）
- 用户气泡：`#1677ff`（蓝底白字）
- Agent 气泡：`#f0f0f0`（灰底黑字）
- 背景：`#f5f5f5`（浅灰）

### 4.3 动画与交互

| 交互 | 实现方式 |
|------|----------|
| 页面切换 | React Router 默认过渡，移动端不加复杂动画 |
| 消息进入 | 简单淡入（opacity 0→1），不左右滑入 |
| 抽屉打开 | CSS `transform: translateX`，300ms ease-out |
| 下拉刷新 | 聊天列表顶部下拉 → 触发历史消息加载 |
| 左滑操作 | 会话列表项左滑显示删除/置顶按钮 |

---

## 5. 关键交互细节

### 5.1 键盘弹出处理（iOS 痛点）

iOS Safari 在输入框聚焦时：
1. 视口缩小（`visualViewport` 变化）
2. 页面可能会被推上去，导致输入框遮挡消息

**解决策略**：
- 监听 `window.visualViewport` 的 `resize` 事件
- 键盘弹出时，将消息列表滚动到底部
- 输入框使用 `position: fixed` + `bottom: 0`，确保始终在键盘上方

### 5.2 下拉刷新

聊天页面支持下拉刷新加载历史消息：
- 使用 CSS `overscroll-behavior-y: contain` 防止页面整体下拉
- 消息列表容器顶部加一个刷新指示器
- 下拉距离超过 60px 触发 `loadMoreHistory()`

### 5.3 返回手势与路由

移动端物理返回键/手势需要正确处理：
- Drawer 打开时 → 返回键关闭 Drawer（不退出页面）
- 在 Agent 广场子页面 → 返回键回到上一页
- 在聊天页 → 返回键无操作（或弹出确认退出）

实现方式：监听 `popstate` 事件，或 React Router 的 `useBlocker`。

---

## 6. 功能收敛清单

以下功能在移动端**保留**：

- [x] 用户登录/注册
- [x] 会话列表（侧边栏 Drawer）
- [x] 单聊/群聊聊天界面
- [x] 消息发送（文字）
- [x] 流式消息接收（SSE）
- [x] Agent 选择/切换
- [x] 停止生成
- [x] 文件上传（简化版）
- [x] Agent 广场（列表浏览、基础操作）
- [x] 个人设置（简化版）

以下功能在移动端**隐藏/禁用**：

- [ ] 工作流画布（Workflow Canvas）
- [ ] 右侧预览面板（Preview Panel）
- [ ] 复杂文件管理（上传后的预览和编辑）
- [ ] MCP/Skill 的详细配置页
- [ ] 产物生成器的可视化编辑
- [ ] 审计日志/RBAC 管理面板

---

## 7. 测试验收标准

### 7.1 设备覆盖

| 设备 | 系统 | 浏览器 | 必测项 |
|------|------|--------|--------|
| iPhone 14/15 | iOS 17+ | Safari | PWA 安装、键盘、刘海屏 |
| iPhone SE | iOS 17+ | Safari | 小屏适配 |
| 华为/小米旗舰 | Android 13+ | Chrome | PWA 安装、返回键 |
| iPad Pro | iPadOS | Safari | 平板横竖屏切换 |

### 7.2 验收 checklist

**基础功能**：
- [ ] 手机浏览器访问 `/app` 能正常显示登录页
- [ ] 登录后进入工作台，布局为单栏
- [ ] 点击汉堡菜单可展开侧边栏 Drawer
- [ ] 侧栏中点击会话可切换聊天
- [ ] 底部输入框可输入文字并发送
- [ ] Agent 回复以流式方式显示
- [ ] 消息气泡样式正确（用户右/Agent 左）

**PWA 功能**：
- [ ] Chrome/Safari 中出现"添加到主屏幕"提示
- [ ] 添加后主屏图标正确
- [ ] 从主屏打开无浏览器地址栏（standalone 模式）
- [ ] 离线时显示缓存的登录页（或提示页）

**体验细节**：
- [ ] 输入框聚焦时页面不异常跳动
- [ ] 键盘弹出后输入框可见
- [ ] 下拉可加载历史消息
- [ ] 页面切换无白屏/闪烁
- [ ] 长消息列表滚动流畅（不卡顿）

---

## 8. 工期估算

| 阶段 | 内容 | 工期 | 依赖 |
|------|------|------|------|
| Phase 1 | 响应式骨架（断点、布局改造） | 1 周 | 无 |
| Phase 2 | IM 体验优化（气泡、输入、侧栏） | 1 周 | Phase 1 |
| Phase 3 | PWA 封装（图标、manifest、SW） | 3~4 天 | Phase 2 |
| 测试修复 | 多设备测试 + bug 修复 | 3~4 天 | Phase 3 |
| **总计** | | **约 3 周** | |

---

## 9. 风险与回退策略

| 风险 | 影响 | 对策 |
|------|------|------|
| iOS Safari 键盘弹出导致布局错乱 | 高 | Phase 2 预留 1 天专门处理 `visualViewport`；如无法完美解决，降级为"键盘弹出时列表自动滚到底部" |
| Ant Design 组件移动端表现不佳 | 中 | 关键组件（Drawer、Input）用原生 CSS 重写；非关键组件接受现有表现 |
| PWA 安装提示被用户忽略 | 低 | 首次访问时显示浮层引导"添加到主屏幕"；不强制 |
| SSE 在后台断连 | 中 | 页面 `visibilitychange` 时恢复连接；PWA 后台运行能力有限，这是已知限制 |
| 甲方临时要求上架 App Store | 中 | PWA 代码可作为 React Native 的 UI 参考；API 层完全复用，切换成本可控 |

---

## 10. 关键文件索引

### 需要修改的现有文件

| 文件路径 | 修改内容 |
|----------|----------|
| `frontend/vite.config.ts` | 添加 `vite-plugin-pwa` |
| `frontend/index.html` | 添加 viewport、iOS meta、theme-color |
| `frontend/src/App.tsx` | 可能需调整根布局 |
| `frontend/src/pages/WorkbenchPage/WorkbenchLayout.tsx` | 移动端单栏布局 |
| `frontend/src/features/chat/components/ChatPanel/index.tsx` | 移动端高度和输入框适配 |
| `frontend/src/features/chat/components/MessageList/...` | 消息列表移动端样式 |
| `frontend/src/features/sidebar/...` | 侧栏 Drawer 模式 |
| `frontend/src/router/AppRouter.tsx` | 可能需要移动端路由调整 |
| `frontend/src/styles/_variables.scss` | 新增断点变量 |
| `frontend/src/styles/_chat.scss` | 新增移动端聊天样式 |

### 需要新增的文件

| 文件路径 | 说明 |
|----------|------|
| `frontend/src/styles/_mobile.scss` | 全局移动端覆盖样式 |
| `frontend/src/hooks/useMobileDetect.ts` | 移动端检测 hook |
| `frontend/src/hooks/useKeyboardHeight.ts` | 键盘高度监听 hook |
| `frontend/public/icon-192.png` | PWA 图标 |
| `frontend/public/icon-512.png` | PWA 大图标 |
| `frontend/public/apple-touch-icon.png` | iOS 主屏图标 |

---

## 11. 与后端的约定

本文档承诺**后端零改动**。但以下事项需要前后端对齐：

| 事项 | 现状 | 是否需要后端配合 |
|------|------|------------------|
| API CORS | 已有 | 无需改动 |
| SSE 流式消息 | 已有 | 无需改动 |
| 文件上传 | 已有 | 无需改动 |
| 图片/文件预览 URL | 已有 | 无需改动 |
| **推送通知** | ❌ 无 | 如需推送，后端需增加 Web Push 接口（V2 迭代） |

> 推送通知不是 Phase 1~3 的范围。如甲方后续需要，可引入 Firebase Cloud Messaging（FCM）或第三方推送服务。

---

## 12. 后续可扩展方向

PWA 上线后，根据用户反馈可考虑：

1. **推送通知**：接入 FCM + Web Push API，实现消息到达通知
2. **语音输入**：Web Speech API（浏览器原生）或接入第三方语音识别
3. **图片/文件预览优化**：移动端图片查看器（ pinch-zoom 缩放）
4. **暗黑模式**：跟随系统 `prefers-color-scheme`
5. **React Native 迁移**：如甲方后续要求上架 App Store，PWA 的 UI 逻辑和 API 调用层可直接复用
