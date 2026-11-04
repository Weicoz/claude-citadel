---
name: claude-citadel
description: 自动化配置与更新 Claude Citadel（云端全托管优先，亦支持本地配置与编译）。引导用户通过 GitHub Actions + Secret Gist 实现云端免环境自动构建与定时更新；亦支持本地克隆、本地 profile.yaml 编译校验与一键发布至 Gist。
---

# Claude Citadel 自动化配置与更新指南

本 Skill 旨在协助用户与 AI 助手配置和维护 Claude Citadel 跨平台分流与订阅系统。
系统支持**两种运行模式**：
- **云端全托管模式（优先推荐）**：零本地依赖，基于 **GitHub Actions + 私有 Secret Gist** 实现云端闭环编译、离线内核校验与全自动定时更新。
- **本地配置与编译模式（开发者 / 本地调试支持）**：在本地克隆代码与编写 `profile.yaml`，即时渲染 6 大客户端配置、执行离线测试校验，或一键直接同步至远程 Gist。

---

## 🎯 模式选择决策

| 模式 | 适用场景 | 依赖条件 | 特性优势 |
| :--- | :--- | :--- | :--- |
| **云端全托管（优先推荐）** | 90% 以上日常用户、移动端维护、不想在电脑配置 Python/内核环境 | 仅需 GitHub 账号与浏览器 | 零本地代码、免本地环境、定时自动拉取权威规则与机场新节点、手机网页随时修改 |
| **本地配置与编译（支持）** | 开发者、定制分流规则调试、本地离线客户端直读、批量测试 | Python 3.10+、PyYAML、Mihomo 内核 | 即时编译秒级反馈、本地断网可用、支持一键推送云端 Gist |

---

## 🚀 模式一：云端全托管配置（优先推荐）

### 第一步：获取 GitHub PAT (Personal Access Token)
云端 GitHub Actions 需要权限将编译好的 6 大客户端配置文件回写到用户的 Secret Gist。

1. 指引用户访问 GitHub Token 生成页面：
   👉 **[GitHub Settings -> Personal Access Tokens (classic)](https://github.com/settings/tokens)**
2. 点击 **"Generate new token"** -> 选择 **"Generate new token (classic)"**。
3. 参数设置：
   - **Note**: `claude-citadel-gist-sync`
   - **Expiration**: 建议选择 `No expiration` 或长周期。
   - **Scopes（关键）**：仅需勾选 **`gist`**（Create gists）。
4. 点击 **"Generate token"**，复制生成的 `ghp_...` 访问令牌。

---

### 第二步：创建私有 Secret Gist 并编写 `profile.yaml`
1. 打开 **[https://gist.github.com](https://gist.github.com)**。
2. 填写内容：
   - **Gist description**: `Claude Citadel Profile`
   - **Filename**: `profile.yaml`
   - **文件内容**：按需填入以下模板：

```yaml
# Claude Citadel 集中配置文件 (维护于 Secret Gist)

claude_exits:
  # 代理链开关：true 表示流量先经由机场节点中转再发往专用出口（防止直连被阻断）；false 表示直接直连拨号
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

3. 点击右下角 **"Create secret gist"**（**务必选择 Secret Gist**）。
4. 记录创建后浏览器 URL 最后的 32 位哈希值作为 **`GIST_ID`**。

---

### 第三步：配置 GitHub 仓库 Actions Secrets
1. 打开用户的 `claude-citadel` 个人仓库（Fork 或 Template 创建）。
2. 点击 **Settings** -> **Secrets and variables** -> **Actions**。
3. 添加两个 Repository Secrets：
   - `GIST_ID`：第二步获取的 32 位 Gist ID
   - `GIST_PAT`：第一步获取的 `ghp_...` 访问令牌
   *(若本地已安装 `gh` CLI，亦可直接执行 `gh secret set GIST_ID -b "<ID>"` 与 `gh secret set GIST_PAT -b "<PAT>" `)*

---

### 第四步：触发云端编译与状态监控
1. 进入仓库 **Actions** 选项卡。
2. 选择 **"自动构建与同步订阅"**（`update.yml`） -> 点击 **"Run workflow"**。
3. 容器自动执行检出、下载 Mihomo、离线语法校验（`mihomo -t`）并同步回写至 Gist。

---

### 第五步：获取 6 大客户端订阅链接并导入
编译成功后，刷新 Secret Gist 页面，点击对应文件右上角 **Raw** 复制地址：

| 客户端 | Gist 配置文件名 | 订阅类型 / 导入方式 |
| :--- | :--- | :--- |
| **Clash / Mihomo** | `claude-citadel-clash.yaml` | 订阅 / Profile 导入 |
| **Stash** | `claude-citadel-stash.yaml` | 从 URL 下载配置 |
| **Surge** | `claude-citadel-surge.conf` | 从 URL 安装配置 (Profile) |
| **Loon** | `claude-citadel-loon.conf` | 配置 (Configuration) -> 远程配置 |
| **Shadowrocket** | `claude-citadel-shadowrocket.conf` | 类型选择 Subscribe (订阅) |
| **Quantumult X** | `claude-citadel-qx.conf` | 下载远程配置文件 |

订阅 URL 格式：
`https://gist.githubusercontent.com/<用户名>/<GIST_ID>/raw/claude-citadel-<客户端文件名>`

---

## 💻 模式二：本地配置与编译（本地模式支持）

当用户希望在本地电脑直接调试、脱离云端运行，或在本地即时验证时使用本模式：

### 1. 环境准备
```sh
# 1. 克隆代码仓库
git clone https://github.com/Weicoz/claude-citadel.git
cd claude-citadel

# 2. 安装 Python 依赖
pip install pyyaml

# 3. 准备 Mihomo 校验器 (macOS / Linux 示例)
# 仓库已内置 bin/mihomo，若不存在可一键获取：
mkdir -p bin
curl -sL https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-darwin-arm64-v1.19.32.gz -o bin/mihomo.gz
gzip -dc bin/mihomo.gz > bin/mihomo && chmod +x bin/mihomo
```

### 2. 创建本地 `profile.yaml`
```sh
# 基于模板创建本地配置
cp profile.example.yaml profile.yaml
# 使用文本编辑器或由 AI 助手协助填充你的机场订阅和 Claude 住宅 IP
```
> [!NOTE]
> 仓库中的 `.gitignore` 已默认忽略 `profile.yaml` 与 `output/`，避免任何敏感信息误提交进 Git。

### 3. 本地编译与执行命令
```sh
# 运行本地完整单元回归测试 (验证 6 大客户端生成与内核离线语法)
python3 -m unittest discover -s tests -v

# 场景 A：纯本地构建 (输出到 output/claude-citadel-*.yaml/conf)
python3 build.py --profile profile.yaml

# 场景 B：本地编译后直接同步发布到云端 Secret Gist (使用本机 gh 凭据或环境变量 GH_TOKEN)
python3 build.py --profile profile.yaml --publish-gist <你的GIST_ID>

# 场景 C：从远程 Gist 拉取 profile.yaml 并在本地预览/调试
python3 build.py --gist-id <你的GIST_ID>
```

### 4. 本地客户端直读
在本地生成 `output/` 后，电脑端的代理客户端（如 Clash Verge Rev、Mihomo Party、Surge Mac）可以直接以**本地文件 (Local File)** 形式导入 `output/claude-citadel-clash.yaml` 或 `output/claude-citadel-surge.conf`，无需等待网络请求。

---

## 🔄 云端自动更新与日常维护机制

无论是云端配置还是本地配置，系统均提供高可靠的自动更新：

1. **定时云端自动轮询**：
   - GitHub Actions 每日定时运行，自动拉取上游社区权威规则（Anthropic 新域名、OpenAI、Apple 路由等）与各机场最新节点列表。
2. **零代码日常修改**：
   - 更换节点或新增内部网段时，只需在网页端打开 Secret Gist 修改 `profile.yaml` 保存。
   - 手动点一次 "Run workflow"（或等待定时），全客户端配置静默同步最新分流。
3. **离线内核熔断保护**：
   - 若上游源异常或 YAML 缩进出错，Mihomo 静态校验拦截报错并触发熔断，**绝不覆写 Gist 中的旧可用配置**，客户端永不断网。
