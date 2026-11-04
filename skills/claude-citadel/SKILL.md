---
name: claude-citadel
description: 零本地环境自动化配置与云端更新 Claude Citadel。通过 GitHub Secret Gist 与 GitHub Actions，引导用户完成云端 Secret 授权、编写 profile.yaml、触发云端 Action 自动编译并获取 6 大客户端订阅链接，同时提供全自动定时更新运维指导。
---

# Claude Citadel 云端自动化配置与更新指南

本 Skill 专为**不想在本地克隆代码或配置 Python/Mihomo 环境**的用户与 AI 助手设计。
整个系统基于 **GitHub Actions + 私有 GitHub Secret Gist** 实现云端闭环：节点与规则完全托管在用户的私有 Gist 中，GitHub Actions 负责自动拉取上游权威规则库、执行编译和内核语法校验，并将 6 大客户端配置回写到该 Gist，实现秒级免密消费。

---

## 🎯 核心交互原则

1. **零本地依赖（Zero Local Dependencies）**：
   - 用户与 AI 助手无需在本地拉取代码、无需安装 Python 依赖、无需下载 Mihomo 校验内核。
   - 所有编译与校验均在 GitHub 提供的云端 Ubuntu 容器中执行。
2. **私密与隔离（Zero Credential Leak）**：
   - 机场订阅地址、自建住宅 IP、内部网络 CIDR 等敏感信息仅保存在用户本人的私有 GitHub Secret Gist 中，绝不公开，不进入任何公共 Git 历史。
3. **全自动云端热更新（Cloud Auto-Update）**：
   - **定时轮询**：GitHub Actions 每日定时运行，自动拉取各机场最新节点列表与社区权威分流规则（Claude、OpenAI、Apple 等）。
   - **改动即生效**：用户无论在电脑还是手机网页，只要在 Secret Gist 中修改 `profile.yaml`，触发 Actions（或等待定时），全客户端订阅静默同步最新配置。

---

## 🚀 5 步零本地环境云端配置流程

当用户需要配置或更新 Claude Citadel 时，AI 助手按以下流程引导执行：

### 第一步：获取 GitHub PAT (Personal Access Token)

云端 GitHub Actions 需要权限将编译好的 6 大客户端配置文件回写到用户的 Secret Gist。

1. 指引用户访问 GitHub Token 生成页面：
   👉 **[GitHub Settings -> Personal Access Tokens (classic)](https://github.com/settings/tokens)**
2. 点击 **"Generate new token"** -> 选择 **"Generate new token (classic)"**。
3. 设置参数：
   - **Note**: `claude-citadel-gist-sync`
   - **Expiration**: 建议选择 `No expiration`（无有效期限制）或长周期。
   - **Scopes（关键）**：仅需勾选 **`gist`**（Create gists）。
4. 点击底部 **"Generate token"**，复制生成的以 `ghp_` 开头的 Token。
   > [!IMPORTANT]
   > 该 Token 仅在生成时显示一次，请提示用户安全保存，后续配置到仓库 Secret 后即可关闭。

---

### 第二步：创建私有 Secret Gist 并编写 `profile.yaml`

1. 指引用户访问 Gist 创建页面：
   👉 **[https://gist.github.com](https://gist.github.com)**
2. 填写内容：
   - **Gist description**: `Claude Citadel Profile`
   - **Filename**: `profile.yaml`
   - **文件内容**：引导用户按需填入以下模板：

```yaml
# Claude Citadel 集中配置文件 (维护于 Secret Gist)

claude_exits:
  # 代理链开关：true 表示流量先经由机场节点中转再发往专用出口（防止直连被墙）；false 表示直接直连拨号
  use_relay_chain: true
  custom_proxies:
    # 填入你的 Claude 专属出口（住宅 IP / 独立 VPS / Socks5 / Shadowsocks / Vless 等）
    - name: "🇺🇸 住宅出口-波特兰"
      type: socks5
      server: 198.51.100.1
      port: 1080

relays:
  # 填入你的 1~4 家机场常规订阅链接
  subscriptions:
    - name: "机场A"
      emoji: "🐱"
      url: "https://sub.example.com/api/v1/client/subscribe?token=xxx"
    # - name: "机场B"
    #   emoji: "⚡"
    #   url: "https://sub2.example.com/link"

# DIY 自定义策略组（可选，按需扩展）：
# 支持挂载任意独立节点（如公司专网、EasyTier/Tailscale 组网、专属服务等），绝不混入机场常规池
# 未配置 diy_groups 时，客户端配置保持 100% 干净，零多余分组
diy_groups: []
# 示例：
# diy_groups:
#   - name: "🏢 公司专网"
#     type: select
#     rules:
#       - "IP-CIDR,192.168.10.0/24"
#       - "DOMAIN-SUFFIX,corp.internal"
#     proxies:
#       - name: "🏢 公司前置"
#         type: socks5
#         server: 192.168.10.1
#         port: 1080
```

3. 点击右下角 **"Create secret gist"**（**务必选择 Secret Gist**，不要选择 Public Gist）。
4. 创建成功后，浏览器地址栏格式为：
   `https://gist.github.com/<用户名>/<32位GIST_ID>`
   其中最后的 32 位哈希即为 **`GIST_ID`**。

---

### 第三步：配置 GitHub 仓库 Actions Secrets

让用户的 `claude-citadel` 仓库流水线关联到刚创建的 Gist。

1. 打开用户的 `claude-citadel` 仓库页面（从官方仓库 Fork 或点击 "Use this template" 创建的个人仓库）。
2. 点击仓库顶部的 **Settings** -> 左侧菜单选择 **Secrets and variables** -> **Actions**。
3. 点击 **"New repository secret"**，依次添加两个 Secret：
   - **名称**: `GIST_ID`
     **内容**: 第二步获取的 32 位 Gist ID
   - **名称**: `GIST_PAT`
     **内容**: 第一步获取的 `ghp_...` 访问令牌
4. 若用户已在本地终端登录了 GitHub CLI（`gh`），助手亦可提示一键命令行录入：
   ```sh
   gh secret set GIST_ID -b "<你的GIST_ID>"
   gh secret set GIST_PAT -b "<你的GIST_PAT>"
   ```

---

### 第四步：触发云端编译与校验

Secrets 配置完成后，首次即可手动触发云端编译：

1. 进入仓库的 **Actions** 选项卡。
2. 在左侧工作流列表中选择 **"自动构建与同步订阅"**（`update.yml`）。
3. 点击右侧 **"Run workflow"** -> 点击绿色 **"Run workflow"** 按钮启动构建。
4. 点击进入运行记录，观察执行流程：
   - ✅ 检出代码与初始化环境
   - ✅ 下载并执行 Mihomo 内核离线语法校验（`mihomo -t`）
   - ✅ 校验通过，调用 API 同步写回 Gist
   > [!NOTE]
   > 如果用户在 `profile.yaml` 中格式书写有误，Actions 中的静态校验器会自动熔断拦截，绝不污染现有的可用订阅。

---

### 第五步：获取 6 大客户端订阅链接并导入

构建成功后，用户刷新自己的 Secret Gist 页面，会看到 6 大客户端配置文件已全部自动生成并写回该 Gist：

| 客户端 | Gist 配置文件名 | 订阅类型 / 导入方式 |
| :--- | :--- | :--- |
| **Clash / Mihomo** | `claude-citadel-clash.yaml` | 订阅 / Profile 导入 |
| **Stash** | `claude-citadel-stash.yaml` | 从 URL 下载配置 |
| **Surge** | `claude-citadel-surge.conf` | 从 URL 安装配置 (Profile) |
| **Loon** | `claude-citadel-loon.conf` | 配置 (Configuration) -> 远程配置 |
| **Shadowrocket** | `claude-citadel-shadowrocket.conf` | 类型选择 Subscribe (订阅) |
| **Quantumult X** | `claude-citadel-qx.conf` | 下载远程配置文件 |

#### 订阅链接获取方法：
点击 Gist 对应文件右上角的 **Raw** 按钮，复制地址栏 URL，格式形如：
```text
https://gist.githubusercontent.com/<用户名>/<GIST_ID>/raw/claude-citadel-<客户端文件名>
```

---

## 🔄 云端自动更新机制详解（Auto-Update）

配置完成后，用户**完全不需要再做任何手动编译操作**，系统具备以下三层自动更新机制：

### 1. 定时全自动云端轮询 (Scheduled Cron)
- **触发周期**：仓库 `.github/workflows/update.yml` 默认配置为每天定时自动运行（可根据需要修改 cron 表达式，如每 6 小时 `0 */6 * * *`）。
- **更新内容**：
  1. 重新解析用户在 `profile.yaml` 中配置的机场订阅，自动捕获机场新增、下线或重命名的中转节点。
  2. 自动拉取上游社区权威规则集（包含 Anthropic 官方新增域名/CDN、OpenAI 新 IP、Apple 区域路由等）。
  3. 通过内核检验后直接热更新至 Secret Gist。客户端在下一次日常刷新订阅时即可自动获得最新规则。

### 2. 用户修改节点或规则即刻生效 (Zero-Code Maintenance)
- 当用户需要：
  - 更换 Claude 专属出口 IP 或端口
  - 添加或更换新的中转机场
  - 添加内部网段或特定分流规则
- **操作步骤**：
  1. 手机或电脑打开该 Secret Gist 网页，点击 `Edit` 编辑 `profile.yaml` 保存。
  2. 进入仓库 Actions 页面点击一次 **"Run workflow"**（或者等待下一次定时任务自动跑）。
  3. 全平台 6 大客户端配置即刻同步生效，全程不接触任何代码与命令行。

### 3. 熔断与容灾保护 (Circuit Breaker)
- 如果上游规则源短暂不可用，或者用户在 Gist 里编辑 YAML 缩进出错：
- GitHub Actions 会在 `mihomo -t` 内核检验阶段捕获错误并触发熔断，**绝不覆写 Gist 中的旧配置**。
- 客户端始终保持上一次成功构建的稳定配置，网络绝不中断。

---

## 🛠️ 常见问题与排查指南

- **Q: Actions 运行报错 `未检测到 GIST_ID Secret`？**
  - 请确认是否在个人仓库的 `Settings -> Secrets and variables -> Actions` 中正确录入了 `GIST_ID`。
- **Q: Actions 运行报错 `403 Forbidden` 或 `Bad credentials`？**
  - 说明 `GIST_PAT` 填写有误或已过期，请重新生成具备 `gist` 权限的 Personal Access Token 并更新 Secret。
- **Q: 为什么生成的配置文件都带有 `claude-citadel-` 前缀？**
  - 便于用户在客户端列表中清晰识别订阅来源，避免与系统其他配置或单文件重名冲突。
- **Q: 我的公司网段 / 组网节点会泄漏吗？**
  - 只要你的 Gist 保持为 **Secret**（非 Public），Gist 链接与内容不会被搜索引擎检索，完全属于你的私有资源。
