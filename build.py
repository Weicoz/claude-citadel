#!/usr/bin/env python3
"""Claude Sub Hub - 全平台多机场与分层规则构建引擎。

支持客户端：
1. Clash / Mihomo (clash.yaml)
2. Stash (stash.yaml)
3. Surge (surge.conf)
4. Loon (loon.conf)
5. Shadowrocket (shadowrocket.conf)
6. Quantumult X (qx.conf)

核心能力：
1. Claude 独立出口物理隔离，支持代理链开关（直连拨号 vs 中转链式代理）。
2. 多机场（1~4+ 家）+ 手动节点自动聚合防重名。
3. 分层规则扩展：直连、中转、海外出口均支持远端规则集 URL 与本地自定义单条规则。
4. 离线校验与熔断保护：出问题时中断退出，绝不发布脏配置。
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "output"

DEFAULT_EXCLUDE_FILTER = "(?i)剩余流量|套餐到期|重置|官网|过期|公告|通知|距离下次|traffic|expire|reset"


def log(msg, level="INFO"):
    prefix = {
        "INFO": "[INFO]",
        "WARN": "[WARN] ⚠️",
        "ERROR": "[ERROR] ❌",
        "SUCCESS": "[OK] ✅"
    }.get(level, "[LOG]")
    print(f"{prefix} {msg}")


def load_profile(profile_path=None, gist_id=None, gh_token=None):
    """加载配置源：优先从 Gist 读取，否则读取本地 profile.yaml。"""
    raw_content = None

    if gist_id:
        log(f"正在从 GitHub Gist ({gist_id}) 获取 profile.yaml ...")
        res = subprocess.run(["gh", "gist", "view", gist_id, "-f", "profile.yaml"],
                             capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            raw_content = res.stdout
        else:
            token = gh_token or os.environ.get("GH_TOKEN") or os.environ.get("GIST_PAT")
            headers = {"User-Agent": "Claude-Sub-Hub-Builder"}
            if token:
                headers["Authorization"] = f"token {token}"
            url = f"https://api.github.com/gists/{gist_id}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    gist_data = json.loads(resp.read().decode("utf-8"))
                    files = gist_data.get("files", {})
                    target_file = files.get("profile.yaml") or files.get("profile.yml")
                    if target_file and "content" in target_file:
                        raw_content = target_file["content"]
            except Exception as e:
                log(f"API 获取 Gist 失败: {e}", "ERROR")

        if not raw_content:
            log(f"未能从 Gist ({gist_id}) 提取到有效 profile.yaml 内容！", "ERROR")
            sys.exit(1)

    else:
        path = Path(profile_path) if profile_path else (ROOT / "profile.yaml")
        if not path.is_file():
            fallback_example = ROOT / "profile.example.yaml"
            if fallback_example.is_file():
                log(f"未找到 {path}，使用示例配置 {fallback_example}", "WARN")
                path = fallback_example
            else:
                log(f"未找到输入配置文件: {path}", "ERROR")
                sys.exit(1)
        raw_content = path.read_text(encoding="utf-8")

    try:
        data = yaml.safe_load(raw_content)
        if not isinstance(data, dict):
            raise ValueError("配置内容根层级必须是字典结构。")
        return data
    except yaml.YAMLError as e:
        log(f"profile.yaml 语法解析错误:\n{e}", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"配置文件读取异常: {e}", "ERROR")
        sys.exit(1)


def sanitize_id(name):
    cleaned = re.sub(r'[^a-zA-Z0-9_-]', '_', name).strip('_')
    return cleaned or "sub"



def format_clash_stash_rule(raw_rule, default_target):
    raw = str(raw_rule).strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    flags = []
    while parts and parts[-1].lower() in ["no-resolve"]:
        flags.append(parts.pop().lower())

    if len(parts) >= 3:
        res = ",".join(parts)
    elif len(parts) == 2:
        res = f"{parts[0]},{parts[1]},{default_target}"
    elif len(parts) == 1:
        res = f"{parts[0]},{default_target}"
    else:
        res = raw

    if flags:
        res += "," + ",".join(flags)
    return res


def format_ini_rule(raw_rule, default_target):
    raw = str(raw_rule).strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    flags = []
    while parts and parts[-1].lower() in ["no-resolve"]:
        flags.append(parts.pop().lower())

    if len(parts) >= 3:
        res = ",".join(parts)
    elif len(parts) == 2:
        res = f"{parts[0]},{parts[1]},{default_target}"
    elif len(parts) == 1:
        res = f"{parts[0]},{default_target}"
    else:
        res = raw
    if flags:
        res += "," + ",".join(flags)
    return res


def format_qx_rule(raw_rule, default_target):
    raw = str(raw_rule).strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    while parts and parts[-1].lower() in ["no-resolve"]:
        parts.pop()

    if len(parts) == 2:
        rtype, pattern = parts[0].lower(), parts[1]
        target = default_target
    elif len(parts) >= 3:
        rtype, pattern, target = parts[0].lower(), parts[1], parts[2]
    else:
        return raw

    type_map = {
        "domain-suffix": "host-suffix",
        "domain": "host",
        "domain-keyword": "host-keyword",
        "ip-cidr": "ip-cidr",
        "ip-cidr6": "ip6-cidr",
        "ip6-cidr": "ip6-cidr",
        "geoip": "geoip",
        "user-agent": "user-agent",
    }
    qx_type = type_map.get(rtype, rtype)
    if target.upper() == "DIRECT":
        target = "direct"
    elif target.upper() == "REJECT":
        target = "reject"
    return f"{qx_type}, {pattern}, {target}"

def apply_custom_rules_clash(config, custom_rules):
    """注入用户自定义分流规则到 Clash / Stash。"""
    if not custom_rules:
        return config

    rules = config.get("rules", [])
    rule_providers = config.get("rule-providers", {})
    injected_rules = []

    # 1. 注入 Claude 专属自定义规则
    claude_cfg = custom_rules.get("claude_exit", {}) or {}
    for idx, r_url in enumerate(claude_cfg.get("remote_rulesets", []) or []):
        tag = f"custom-claude-{idx+1}"
        rule_providers[tag] = {
            "type": "http",
            "behavior": "classical",
            "format": "yaml" if r_url.endswith((".yaml", ".yml")) else "text",
            "path": f"./ruleset/{tag}.yaml",
            "url": r_url,
            "interval": 86400,
            "proxy": "DIRECT"
        }
        injected_rules.append(f"RULE-SET,{tag},🇺🇸 海外出口")
    for r in claude_cfg.get("rules", []) or []:
        formatted = format_clash_stash_rule(r, "🇺🇸 海外出口")
        if formatted:
            injected_rules.append(formatted)

    # 2. 注入直连自定义规则
    direct_cfg = custom_rules.get("direct", {}) or {}
    for idx, r_url in enumerate(direct_cfg.get("remote_rulesets", []) or []):
        tag = f"custom-direct-{idx+1}"
        rule_providers[tag] = {
            "type": "http",
            "behavior": "classical",
            "format": "yaml" if r_url.endswith((".yaml", ".yml")) else "text",
            "path": f"./ruleset/{tag}.yaml",
            "url": r_url,
            "interval": 86400,
            "proxy": "DIRECT"
        }
        injected_rules.append(f"RULE-SET,{tag},DIRECT")
    for r in direct_cfg.get("rules", []) or []:
        formatted = format_clash_stash_rule(r, "DIRECT")
        if formatted:
            injected_rules.append(formatted)

    # 3. 注入中转自定义规则
    relay_cfg = custom_rules.get("relay", {}) or {}
    for idx, r_url in enumerate(relay_cfg.get("remote_rulesets", []) or []):
        tag = f"custom-relay-{idx+1}"
        rule_providers[tag] = {
            "type": "http",
            "behavior": "classical",
            "format": "yaml" if r_url.endswith((".yaml", ".yml")) else "text",
            "path": f"./ruleset/{tag}.yaml",
            "url": r_url,
            "interval": 86400,
            "proxy": "DIRECT"
        }
        injected_rules.append(f"RULE-SET,{tag},🔀 中转节点")
    for r in relay_cfg.get("rules", []) or []:
        formatted = format_clash_stash_rule(r, "🔀 中转节点")
        if formatted:
            injected_rules.append(formatted)

    config["rules"] = injected_rules + rules
    config["rule-providers"] = rule_providers
    return config


def build_clash_stash(template_path, profile_data, client_type="clash"):
    """渲染 Clash / Stash 配置。"""
    with open(template_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    claude_cfg = profile_data.get("claude_exits", {}) or {}
    relays_cfg = profile_data.get("relays", {}) or {}
    custom_rules = profile_data.get("custom_rules", {}) or {}

    use_relay_chain = claude_cfg.get("use_relay_chain", False)
    claude_custom = claude_cfg.get("custom_proxies", []) or []
    claude_subs = claude_cfg.get("subscriptions", []) or []

    relay_subs = relays_cfg.get("subscriptions", []) or []
    relay_custom = relays_cfg.get("custom_proxies", []) or []

    all_proxies = []
    claude_custom_names = []
    relay_custom_names = []

    # 处理 Claude 节点代理链
    for p in claude_custom:
        if isinstance(p, dict) and "name" in p:
            p_copy = p.copy()
            if use_relay_chain and "dialer-proxy" not in p_copy:
                p_copy["dialer-proxy"] = "🔀 中转节点"
            all_proxies.append(p_copy)
            claude_custom_names.append(p_copy["name"])

    for p in relay_custom:
        if isinstance(p, dict) and "name" in p:
            all_proxies.append(p)
            relay_custom_names.append(p["name"])

    config["proxies"] = all_proxies

    providers = {}
    relay_provider_ids = []
    claude_provider_ids = []

    for idx, s in enumerate(relay_subs, 1):
        if not isinstance(s, dict) or not s.get("url"):
            continue
        pid = s.get("id") or f"{sanitize_id(s.get('name', f'sub_{idx}'))}_{idx}"
        relay_provider_ids.append(pid)
        prefix = f"[{s.get('name', pid)}] "

        prov = {
            "type": "http",
            "url": s["url"],
            "path": f"./proxy-providers/{pid}.yaml",
            "interval": 86400,
            "proxy": "DIRECT",
            "health-check": {
                "enable": True,
                "url": "http://www.apple.com/library/test/success.html",
                "interval": 300,
                "timeout": 5000,
                "lazy": True
            },
            "exclude-type": "direct|reject|compatible|pass",
            "exclude-filter": DEFAULT_EXCLUDE_FILTER,
            "override": {
                "additional-prefix": prefix,
                "dialer-proxy": ""
            }
        }
        if s.get("user_agent"):
            prov["header"] = {"User-Agent": [s["user_agent"]]}
            prov["headers"] = {"User-Agent": [s["user_agent"]]}
        providers[pid] = prov

    for idx, s in enumerate(claude_subs, 1):
        if not isinstance(s, dict) or not s.get("url"):
            continue
        pid = s.get("id") or f"claude_{sanitize_id(s.get('name', f'sub_{idx}'))}_{idx}"
        claude_provider_ids.append(pid)
        prefix = f"[Claude-{s.get('name', pid)}] "

        prov = {
            "type": "http",
            "url": s["url"],
            "path": f"./proxy-providers/{pid}.yaml",
            "interval": 86400,
            "proxy": "DIRECT",
            "health-check": {
                "enable": True,
                "url": "http://www.apple.com/library/test/success.html",
                "interval": 300,
                "timeout": 5000,
                "lazy": True
            },
            "exclude-type": "direct|reject|compatible|pass",
            "override": {
                "additional-prefix": prefix
            }
        }
        if use_relay_chain:
            prov["override"]["dialer-proxy"] = "🔀 中转节点"
        providers[pid] = prov

    config["proxy-providers"] = providers

    airport_auto_groups = []
    airport_select_groups = []

    for s, pid in zip(relay_subs, relay_provider_ids):
        name = s.get("name", pid)
        emoji = s.get("emoji", "✈️")
        opt_name = f"⚡ {name} 自动择优"
        select_name = f"{emoji} {name}"

        airport_auto_groups.append({
            "name": opt_name,
            "type": "url-test",
            "url": "http://www.apple.com/library/test/success.html",
            "interval": 300,
            "tolerance": 50,
            "use": [pid]
        })
        airport_select_groups.append({
            "name": select_name,
            "type": "select",
            "use": [pid],
            "proxies": [opt_name]
        })

    relay_proxies_summary = ["⚡ 自动择优", "🛟 Fallback"]
    for s in relay_subs:
        name = s.get("name", "")
        emoji = s.get("emoji", "✈️")
        relay_proxies_summary.extend([f"⚡ {name} 自动择优", f"{emoji} {name}"])
    relay_proxies_summary.extend(relay_custom_names)

    groups = config.get("proxy-groups", [])
    new_groups = []

    region_regexes = {
        "🇭🇰 香港节点": r"(?i)港|hk|hongkong|hong kong",
        "🇯🇵 日本节点": r"(?i)日|jp|japan",
        "🇺🇸 美国节点": r"(?i)美|us|united states",
        "🇸🇬 新加坡节点": r"(?i)新|sg|singapore",
        "🇹🇼 台湾节点": r"(?i)台|tw|taiwan",
    }

    for g in groups:
        g_name = g.get("name", "")

        if g_name == "🇺🇸 海外出口":
            claude_pool = claude_custom_names.copy()
            if claude_provider_ids:
                g["use"] = claude_provider_ids
            if claude_pool or claude_provider_ids:
                g["proxies"] = claude_pool if claude_pool else ["⚡ 自动择优"]
            else:
                g["proxies"] = ["🇺🇸 美国节点", "🔀 中转节点", "DIRECT"]
            new_groups.append(g)

        elif g_name == "🔀 中转节点":
            if relay_provider_ids:
                g["use"] = relay_provider_ids
            g["proxies"] = relay_proxies_summary
            new_groups.append(g)

        elif g_name == "⚡ 自动择优":
            if relay_provider_ids:
                g["use"] = relay_provider_ids
            g["proxies"] = relay_custom_names.copy()
            new_groups.append(g)
            new_groups.extend(airport_auto_groups)

        elif g_name == "🛟 Fallback":
            if relay_provider_ids:
                g["use"] = relay_provider_ids
            g["proxies"] = relay_custom_names.copy()
            new_groups.append(g)

        elif g_name == "🎮 Steam":
            new_groups.append(g)
            new_groups.extend(airport_select_groups)

        elif g_name in region_regexes:
            if relay_provider_ids:
                g["use"] = relay_provider_ids
            pattern = region_regexes[g_name]
            matched_custom = [p for p in relay_custom_names if re.search(pattern, p)]
            g["proxies"] = matched_custom
            new_groups.append(g)

        else:
            new_groups.append(g)

    # 保证每个策略组都合法，无空 use 或空 proxies 导致内核崩溃
    for g in new_groups:
        has_use = bool(g.get("use"))
        has_proxies = bool(g.get("proxies"))
        if not has_use and not has_proxies:
            g["proxies"] = ["DIRECT"]
        elif g.get("type") in ["url-test", "fallback"] and not has_use and len(g.get("proxies", [])) == 0:
            g["proxies"] = ["DIRECT"]

    config["proxy-groups"] = new_groups
    return apply_custom_rules_clash(config, custom_rules)


def build_ini_based_client(template_path, profile_data, client_type="surge"):
    """渲染 Surge / Loon / Shadowrocket / Quantumult X 配置。"""
    content = template_path.read_text(encoding="utf-8")
    claude_cfg = profile_data.get("claude_exits", {}) or {}
    relays_cfg = profile_data.get("relays", {}) or {}
    custom_rules = profile_data.get("custom_rules", {}) or {}

    use_relay_chain = claude_cfg.get("use_relay_chain", False)
    claude_custom = claude_cfg.get("custom_proxies", []) or []
    relay_subs = relays_cfg.get("subscriptions", []) or []
    relay_custom = relays_cfg.get("custom_proxies", []) or []

    # 1. 生成自定义节点行
    proxy_lines = []
    claude_names = []
    relay_names = []

    for p in claude_custom:
        name = p.get("name", "Claude_Proxy")
        p_type = p.get("type", "socks5").lower()
        server = p.get("server", "127.0.0.1")
        port = p.get("port", 1080)
        claude_names.append(name)

        is_chained = bool(p.get("dialer-proxy")) if "dialer-proxy" in p and p.get("dialer-proxy") is not None else use_relay_chain

        if client_type == "surge":
            chain_opt = ', underlying-proxy="🔀 中转节点"' if is_chained else ""
            proxy_lines.append(f"{name} = {p_type}, {server}, {port}{chain_opt}")
        elif client_type == "shadowrocket":
            chain_opt = ', chain="🔀 中转节点"' if is_chained else ""
            proxy_lines.append(f"{name} = {p_type}, {server}, {port}{chain_opt}")
        elif client_type == "loon":
            proxy_lines.append(f"{name} = {p_type.capitalize()},{server},{port}")
        elif client_type == "qx":
            proxy_lines.append(f"{p_type}={server}:{port}, tag={name}")

    for p in relay_custom:
        name = p.get("name", "Relay_Proxy")
        p_type = p.get("type", "ss").lower()
        server = p.get("server", "127.0.0.1")
        port = p.get("port", 8388)
        relay_names.append(name)

        if client_type in ["surge", "shadowrocket"]:
            proxy_lines.append(f"{name} = {p_type}, {server}, {port}")
        elif client_type == "loon":
            proxy_lines.append(f"{name} = {p_type.capitalize()},{server},{port}")
        elif client_type == "qx":
            proxy_lines.append(f"{p_type}={server}:{port}, tag={name}")

    # 2. 插入节点
    if client_type in ["surge", "shadowrocket", "loon"]:
        content = content.replace("[Proxy]\n", "[Proxy]\n" + "\n".join(proxy_lines) + "\n" if proxy_lines else "[Proxy]\n")
    elif client_type == "qx":
        content = content.replace("[server_local]\n", "[server_local]\n" + "\n".join(proxy_lines) + "\n" if proxy_lines else "[server_local]\n")

    # 3. 插入远程订阅
    if client_type == "loon":
        remote_lines = [f"{s.get('name', f'sub_{i}')} = {s['url']}" for i, s in enumerate(relay_subs, 1) if s.get("url")]
        content = content.replace("[Remote Proxy]\n", "[Remote Proxy]\n" + "\n".join(remote_lines) + "\n")
        # 填充 Loon 的 [Proxy Chain]
        chain_lines = []
        if use_relay_chain:
            for name in claude_names:
                chain_lines.append(f"{name} = 🔀 中转节点")
        chain_block = "[Proxy Chain]\n" + ("\n".join(chain_lines) + "\n" if chain_lines else "")
        content = content.replace("[Proxy Chain]\n", chain_block)

    elif client_type == "qx":
        remote_lines = [f"{s['url']}, tag={s.get('name', f'sub_{i}')}, enabled=true" for i, s in enumerate(relay_subs, 1) if s.get("url")]
        content = content.replace("[server_remote]\n", "[server_remote]\n" + "\n".join(remote_lines) + "\n")

    elif client_type == "surge":
        sub_group_lines = []
        sub_names = []
        for i, s in enumerate(relay_subs, 1):
            if s.get("url"):
                s_name = s.get("name", f"Sub_{i}")
                sub_names.append(s_name)
                sub_group_lines.append(f"{s_name} = select, policy-path=\"{s['url']}\"")
        if sub_group_lines:
            content = content.replace("[Proxy Group]\n", "[Proxy Group]\n" + "\n".join(sub_group_lines) + "\n")
            if sub_names:
                content = content.replace("🔀 中转节点 = select, ⚡ 自动择优, 🛟 Fallback", f"🔀 中转节点 = select, {', '.join(sub_names)}, ⚡ 自动择优, 🛟 Fallback")

    elif client_type == "shadowrocket":
        sub_group_lines = []
        sub_names = []
        for i, s in enumerate(relay_subs, 1):
            if s.get("url"):
                s_name = s.get("name", f"Sub_{i}")
                sub_names.append(s_name)
                sub_group_lines.append(f"{s_name} = select, policy-path=\"{s['url']}\"")
        if sub_group_lines:
            content = content.replace("[Proxy Group]\n", "[Proxy Group]\n" + "\n".join(sub_group_lines) + "\n")
            if sub_names:
                content = content.replace("🔀 中转节点 = select,⚡ 自动择优,🛟 Fallback", f"🔀 中转节点 = select,{','.join(sub_names)},⚡ 自动择优,🛟 Fallback")

    # 4. 插入 Claude 出口指定节点
    if claude_names:
        target_line = ", ".join(claude_names)
        content = content.replace("🇺🇸 海外出口 = select, DIRECT", f"🇺🇸 海外出口 = select, {target_line}")
        content = content.replace("🇺🇸 海外出口 = select,DIRECT", f"🇺🇸 海外出口 = select,{target_line}")
        content = content.replace("static=🇺🇸 海外出口, direct", f"static=🇺🇸 海外出口, {target_line}")

    # 5. 注入自定义分流规则 (Direct / Relay / Claude Exit)
    injected_rules = []
    if custom_rules:
        def format_rule_for_client(r_text, default_tgt):
            if client_type == "qx":
                return format_qx_rule(r_text, default_tgt)
            else:
                return format_ini_rule(r_text, default_tgt)

        # Claude Exit
        for r_url in (custom_rules.get("claude_exit", {}) or {}).get("remote_rulesets", []) or []:
            if client_type == "loon":
                content = content.replace("[Remote Rule]\n", f"[Remote Rule]\n{r_url},policy=🇺🇸 海外出口,tag=custom-claude,enabled=true\n")
            elif client_type in ["surge", "shadowrocket"]:
                injected_rules.append(f"RULE-SET,{r_url},🇺🇸 海外出口")
            elif client_type == "qx":
                content = content.replace("[filter_remote]\n", f"[filter_remote]\n{r_url}, tag=custom-claude, force-remote-spec=true\n")
        for r in (custom_rules.get("claude_exit", {}) or {}).get("rules", []) or []:
            fmt = format_rule_for_client(r, "🇺🇸 海外出口")
            if fmt:
                injected_rules.append(fmt)

        # Direct
        for r_url in (custom_rules.get("direct", {}) or {}).get("remote_rulesets", []) or []:
            if client_type == "loon":
                content = content.replace("[Remote Rule]\n", f"[Remote Rule]\n{r_url},policy=DIRECT,tag=custom-direct,enabled=true\n")
            elif client_type in ["surge", "shadowrocket"]:
                injected_rules.append(f"RULE-SET,{r_url},DIRECT")
            elif client_type == "qx":
                content = content.replace("[filter_remote]\n", f"[filter_remote]\n{r_url}, tag=custom-direct, force-remote-spec=true\n")
        for r in (custom_rules.get("direct", {}) or {}).get("rules", []) or []:
            fmt = format_rule_for_client(r, "DIRECT")
            if fmt:
                injected_rules.append(fmt)

        # Relay
        for r_url in (custom_rules.get("relay", {}) or {}).get("remote_rulesets", []) or []:
            if client_type == "loon":
                content = content.replace("[Remote Rule]\n", f"[Remote Rule]\n{r_url},policy=🔀 中转节点,tag=custom-relay,enabled=true\n")
            elif client_type in ["surge", "shadowrocket"]:
                injected_rules.append(f"RULE-SET,{r_url},🔀 中转节点")
            elif client_type == "qx":
                content = content.replace("[filter_remote]\n", f"[filter_remote]\n{r_url}, tag=custom-relay, force-remote-spec=true\n")
        for r in (custom_rules.get("relay", {}) or {}).get("rules", []) or []:
            fmt = format_rule_for_client(r, "🔀 中转节点")
            if fmt:
                injected_rules.append(fmt)

    if injected_rules:
        rule_block = "\n".join(injected_rules) + "\n"
        if client_type in ["surge", "shadowrocket", "loon"]:
            content = content.replace("[Rule]\n", f"[Rule]\n{rule_block}")
        elif client_type == "qx":
            content = content.replace("[filter_local]\n", f"[filter_local]\n{rule_block}")

    return content

def validate_with_mihomo(config_path):
    """使用内置或系统 mihomo 内核执行离线配置校验。"""
    mihomo_bin = ROOT / "bin" / "mihomo"
    if not mihomo_bin.is_file() or not os.access(mihomo_bin, os.X_OK):
        import shutil
        sys_mihomo = shutil.which("mihomo")
        if sys_mihomo:
            mihomo_bin = sys_mihomo
        else:
            log("未检测到可执行的 mihomo 二进制，跳过内核静态验证。", "WARN")
            return True

    log(f"正在使用 {mihomo_bin} 静态检验 {config_path.name} ...")
    res = subprocess.run([str(mihomo_bin), "-t", "-f", str(config_path)],
                         capture_output=True, text=True)
    if res.returncode == 0:
        log(f"{config_path.name} 内核语法校验通过！", "SUCCESS")
        return True
    else:
        log(f"{config_path.name} 语法校验失败:\n{res.stderr}", "ERROR")
        return False


def publish_all_to_gist(gist_id, output_files, gh_token=None):
    """将所有客户端配置同步发布至 Secret Gist。"""
    log(f"正在发布全量 6 客户端配置到 Gist ({gist_id}) ...")
    cmd = ["gh", "gist", "edit", gist_id]
    for filename, path in output_files.items():
        cmd.extend(["-a", filename, str(path)])

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        log("通过 gh CLI 成功发布全部客户端配置至 Gist！", "SUCCESS")
        return True

    token = gh_token or os.environ.get("GH_TOKEN") or os.environ.get("GIST_PAT")
    if not token:
        log("缺少 GitHub Token，无法调用 REST API 更新 Gist！", "ERROR")
        return False

    url = f"https://api.github.com/gists/{gist_id}"
    payload = {"files": {}}
    for filename, path in output_files.items():
        payload["files"][filename] = {"content": path.read_text(encoding="utf-8")}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "User-Agent": "Claude-Sub-Hub-Builder",
            "Content-Type": "application/json"
        },
        method="PATCH"
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            if resp.status == 200:
                log("通过 REST API 成功更新全部客户端配置至 Gist！", "SUCCESS")
                return True
            else:
                log(f"API 更新失败，HTTP 状态码: {resp.status}", "ERROR")
                return False
    except Exception as e:
        log(f"API 推送 Gist 发生异常: {e}", "ERROR")
        return False


def main():
    parser = argparse.ArgumentParser(description="全平台 6 大客户端多机场与 Claude 独立节点自动化构建工具")
    parser.add_argument("--profile", help="本地 profile.yaml 文件路径")
    parser.add_argument("--gist-id", help="从指定 GitHub Gist 读取 profile.yaml")
    parser.add_argument("--publish-gist", help="构建成功后将结果发布回指定 Gist")
    parser.add_argument("--skip-validation", action="store_true", help="跳过 mihomo 静态内核校验")
    args = parser.parse_args()

    profile_data = load_profile(profile_path=args.profile, gist_id=args.gist_id)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_files = {
        "clash.yaml": OUTPUT_DIR / "clash.yaml",
        "stash.yaml": OUTPUT_DIR / "stash.yaml",
        "surge.conf": OUTPUT_DIR / "surge.conf",
        "loon.conf": OUTPUT_DIR / "loon.conf",
        "shadowrocket.conf": OUTPUT_DIR / "shadowrocket.conf",
        "qx.conf": OUTPUT_DIR / "qx.conf"
    }

    # 1. 渲染 Clash 与 Stash
    log("正在渲染 Clash / Mihomo 配置...")
    clash_cfg = build_clash_stash(TEMPLATES_DIR / "clash.yaml", profile_data, client_type="clash")
    with open(output_files["clash.yaml"], "w", encoding="utf-8") as f:
        yaml.dump(clash_cfg, f, allow_unicode=True, sort_keys=False)

    log("正在渲染 Stash 配置...")
    stash_cfg = build_clash_stash(TEMPLATES_DIR / "stash.yaml", profile_data, client_type="stash")
    with open(output_files["stash.yaml"], "w", encoding="utf-8") as f:
        yaml.dump(stash_cfg, f, allow_unicode=True, sort_keys=False)

    # 2. 渲染 Surge, Loon, Shadowrocket, Quantumult X
    log("正在渲染 Surge / Loon / Shadowrocket / Quantumult X 配置...")
    output_files["surge.conf"].write_text(build_ini_based_client(TEMPLATES_DIR / "surge.conf", profile_data, "surge"), encoding="utf-8")
    output_files["loon.conf"].write_text(build_ini_based_client(TEMPLATES_DIR / "loon.conf", profile_data, "loon"), encoding="utf-8")
    output_files["shadowrocket.conf"].write_text(build_ini_based_client(TEMPLATES_DIR / "shadowrocket.conf", profile_data, "shadowrocket"), encoding="utf-8")
    output_files["qx.conf"].write_text(build_ini_based_client(TEMPLATES_DIR / "qx.conf", profile_data, "qx"), encoding="utf-8")

    # 3. 离线内核校验
    if not args.skip_validation:
        ok_clash = validate_with_mihomo(output_files["clash.yaml"])
        ok_stash = validate_with_mihomo(output_files["stash.yaml"])
        if not (ok_clash and ok_stash):
            log("配置内核检验未通过，紧急熔断终止，未发布到任何远程端！", "ERROR")
            sys.exit(1)

    log("全平台 6 大客户端配置全部生成完毕！", "SUCCESS")

    if args.publish_gist:
        success = publish_all_to_gist(args.publish_gist, output_files)
        if not success:
            sys.exit(1)


if __name__ == "__main__":
    main()
