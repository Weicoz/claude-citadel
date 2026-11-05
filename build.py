#!/usr/bin/env python3
"""Claude Citadel - 全平台多机场与分层规则构建引擎。

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
DEFAULT_PROBE_URL = "http://cp.cloudflare.com/generate_204"
DEFAULT_EXPECTED_STATUS = "204"
DEFAULT_TIMEOUT = 4000
DEFAULT_TOLERANCE = 100
DEFAULT_PRIMARY_INTERVAL = 90
DEFAULT_STANDBY_INTERVAL = 300


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
            headers = {"User-Agent": "Claude-Citadel-Builder"}
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


def resolve_node_dialer_proxy(proxy_dict, default_chain=False):
    """
    解析单个节点的代理链前置中转 (支持 dialer-proxy / relay / underlying-proxy / chain / via)。
    支持:
    - 具体的节点名或策略组名 (字符串): 如 "⚡ Amy 自动择优", "🔀 中转节点", "[Amy] 🇭🇰 香港 01", "🇯🇵 日本节点" 等
    - 布尔值 True: 启用中转，默认走 "🔀 中转节点"
    - 布尔值 False / None / "" / "DIRECT": 显式直连拨号，不走代理链
    - 未配置时: 遵循传入的 default_chain (True 则走 "🔀 中转节点"，否则直连)
    """
    if not isinstance(proxy_dict, dict):
        return None

    # 支持的字段名优先级: dialer-proxy > relay > underlying-proxy > underlying_proxy > chain > via
    val = None
    for key in ["dialer-proxy", "relay", "underlying-proxy", "underlying_proxy", "chain", "via"]:
        if key in proxy_dict:
            val = proxy_dict[key]
            break

    if val is not None:
        if isinstance(val, bool):
            return "🔀 中转节点" if val else None
        if isinstance(val, str):
            val_clean = val.strip()
            if not val_clean or val_clean.upper() in ["DIRECT", "FALSE", "NONE", "NO", "OFF"]:
                return None
            if val_clean.upper() in ["TRUE", "YES", "ON"]:
                return "🔀 中转节点"
            return val_clean

    return "🔀 中转节点" if default_chain else None


def extract_diy_groups(profile_data):
    """提取用户自定义 DIY 策略组、独立节点与专属规则。"""
    diy_groups = profile_data.get("diy_groups", []) or []
    # 兼容回退：若旧字段 mesh_proxies 或 custom_rules.mesh 存在，自动转为 DIY 组
    if not diy_groups and (profile_data.get("mesh_proxies") or (profile_data.get("custom_rules", {}) or {}).get("mesh")):
        mesh_nodes = profile_data.get("mesh_proxies", []) or []
        mesh_rules_cfg = (profile_data.get("custom_rules", {}) or {}).get("mesh", {}) or {}
        diy_groups.append({
            "name": "🕸️ 组网访问",
            "type": "select",
            "proxies": mesh_nodes,
            "rules": mesh_rules_cfg.get("rules", []),
            "remote_rulesets": mesh_rules_cfg.get("remote_rulesets", [])
        })
    return diy_groups


def apply_custom_rules_clash(config, custom_rules, diy_groups=None, client_type="clash"):
    """注入用户自定义分流规则到 Clash / Stash。"""
    if not custom_rules and not diy_groups:
        return config

    rules = config.get("rules", [])
    rule_providers = config.get("rule-providers", {})
    injected_rules = []

    # 1. 注入 Claude 专属自定义规则
    claude_cfg = custom_rules.get("claude_exit", {}) or {}
    for idx, r_url in enumerate(claude_cfg.get("remote_rulesets", []) or []):
        tag = f"claude-citadel-custom-claude-{idx+1}"
        rp = {
            "type": "http",
            "behavior": "classical",
            "format": "yaml" if r_url.endswith((".yaml", ".yml")) else "text",
            "path": f"./ruleset/{tag}.yaml",
            "url": r_url,
            "interval": 86400,
        }
        if client_type == "clash":
            rp["proxy"] = "🔀 中转节点"
        rule_providers[tag] = rp
        injected_rules.append(f"RULE-SET,{tag},🇺🇸 海外出口")
    for r in claude_cfg.get("rules", []) or []:
        formatted = format_clash_stash_rule(r, "🇺🇸 海外出口")
        if formatted:
            injected_rules.append(formatted)

    # 2. 注入直连自定义规则
    direct_cfg = custom_rules.get("direct", {}) or {}
    for idx, r_url in enumerate(direct_cfg.get("remote_rulesets", []) or []):
        tag = f"claude-citadel-custom-direct-{idx+1}"
        rp = {
            "type": "http",
            "behavior": "classical",
            "format": "yaml" if r_url.endswith((".yaml", ".yml")) else "text",
            "path": f"./ruleset/{tag}.yaml",
            "url": r_url,
            "interval": 86400,
        }
        if client_type == "clash":
            rp["proxy"] = "🔀 中转节点"
        rule_providers[tag] = rp
        injected_rules.append(f"RULE-SET,{tag},DIRECT")
    for r in direct_cfg.get("rules", []) or []:
        formatted = format_clash_stash_rule(r, "DIRECT")
        if formatted:
            injected_rules.append(formatted)

    # 3. 注入中转自定义规则
    relay_cfg = custom_rules.get("relay", {}) or {}
    for idx, r_url in enumerate(relay_cfg.get("remote_rulesets", []) or []):
        tag = f"claude-citadel-custom-relay-{idx+1}"
        rp = {
            "type": "http",
            "behavior": "classical",
            "format": "yaml" if r_url.endswith((".yaml", ".yml")) else "text",
            "path": f"./ruleset/{tag}.yaml",
            "url": r_url,
            "interval": 86400,
        }
        if client_type == "clash":
            rp["proxy"] = "🔀 中转节点"
        rule_providers[tag] = rp
        injected_rules.append(f"RULE-SET,{tag},🔀 中转节点")
    for r in relay_cfg.get("rules", []) or []:
        formatted = format_clash_stash_rule(r, "🔀 中转节点")
        if formatted:
            injected_rules.append(formatted)

    # 4. 注入 DIY 自定义策略组分流规则
    for g_idx, g_def in enumerate(diy_groups or []):
        g_name = g_def.get("name")
        if not g_name:
            continue
        tag_prefix = sanitize_id(g_name) or f"diy_{g_idx+1}"
        for idx, r_url in enumerate(g_def.get("remote_rulesets", []) or []):
            tag = f"claude-citadel-diy-{tag_prefix}-{idx+1}"
            rp = {
                "type": "http",
                "behavior": "classical",
                "format": "yaml" if r_url.endswith((".yaml", ".yml")) else "text",
                "path": f"./ruleset/{tag}.yaml",
                "url": r_url,
                "interval": 86400,
            }
            if client_type == "clash":
                rp["proxy"] = "🔀 中转节点"
            rule_providers[tag] = rp
            injected_rules.append(f"RULE-SET,{tag},{g_name}")
        for r in g_def.get("rules", []) or []:
            formatted = format_clash_stash_rule(r, g_name)
            if formatted:
                injected_rules.append(formatted)

    # 去重：只删除与模板或彼此「完全一致」的行，避免同一规则重复两遍。
    # 不按大小写或尾部标志归一化：PROCESS-NAME / PROCESS-NAME-REGEX / 策略组名大小写敏感，
    # 尾部标志（no-resolve 等）不同代表不同语义；注入版保持置顶，用户可用它覆盖模板版。
    existing = set(rules)
    seen = set()
    deduped = []
    for r in injected_rules:
        if r in existing or r in seen:
            continue
        seen.add(r)
        deduped.append(r)

    config["rules"] = deduped + rules
    config["rule-providers"] = rule_providers
    return config


def build_clash_stash(template_path, profile_data, client_type="clash"):
    """渲染 Clash / Stash 配置。"""
    with open(template_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    claude_cfg = profile_data.get("claude_exits", {}) or {}
    relays_cfg = profile_data.get("relays", {}) or {}
    custom_rules = profile_data.get("custom_rules", {}) or {}
    # 网络层覆盖：TUN 排除地址与 fake-ip 白名单由 profile 提供，避免把私有地址写进仓库
    net_cfg = profile_data.get("network", {}) or {}
    if net_cfg.get("tun_exclude_address"):
        config.setdefault("tun", {})["route-exclude-address"] = list(net_cfg["tun_exclude_address"])
    extra_ns_policy = net_cfg.get("nameserver_policy_extra") or {}
    if isinstance(extra_ns_policy, dict) and extra_ns_policy:
        policy = dict((config.setdefault("dns", {}) or {}).get("nameserver-policy") or {})
        for domain, servers in extra_ns_policy.items():
            if isinstance(domain, str) and isinstance(servers, list) and servers:
                policy[domain] = [str(x) for x in servers]
        config["dns"]["nameserver-policy"] = policy
    extra_fake_ip = net_cfg.get("fake_ip_filter_extra") or []
    if extra_fake_ip:
        dns_cfg = config.setdefault("dns", {})
        filters = list(dns_cfg.get("fake-ip-filter") or [])
        for item in extra_fake_ip:
            if item not in filters:
                filters.append(item)
        dns_cfg["fake-ip-filter"] = filters

    use_relay_chain = claude_cfg.get("use_relay_chain", False)
    claude_custom = claude_cfg.get("custom_proxies", []) or []
    claude_subs = claude_cfg.get("subscriptions", []) or []

    relay_subs = relays_cfg.get("subscriptions", []) or []
    relay_custom = relays_cfg.get("custom_proxies", []) or []

    all_proxies = []
    claude_custom_names = []
    relay_custom_names = []

    # 处理 Claude 节点代理链 (支持节点级独立代理链与自定义前置中转)
    for p in claude_custom:
        if isinstance(p, dict) and "name" in p:
            p_copy = p.copy()
            hop = resolve_node_dialer_proxy(p_copy, default_chain=use_relay_chain)
            for k in ["relay", "underlying-proxy", "underlying_proxy", "chain", "via"]:
                p_copy.pop(k, None)
            if hop:
                p_copy["dialer-proxy"] = hop
            else:
                p_copy.pop("dialer-proxy", None)
            all_proxies.append(p_copy)
            claude_custom_names.append(p_copy["name"])

    for p in relay_custom:
        if isinstance(p, dict) and "name" in p:
            p_copy = p.copy()
            hop = resolve_node_dialer_proxy(p_copy, default_chain=False)
            for k in ["relay", "underlying-proxy", "underlying_proxy", "chain", "via"]:
                p_copy.pop(k, None)
            if hop:
                p_copy["dialer-proxy"] = hop
            else:
                p_copy.pop("dialer-proxy", None)
            all_proxies.append(p_copy)
            relay_custom_names.append(p_copy["name"])

    # 处理 DIY 自定义策略组中的独立专属节点 (不参与日常中转测速，同名节点自动去重)
    diy_groups = extract_diy_groups(profile_data)
    seen_proxy_names = {p["name"] for p in all_proxies if isinstance(p, dict) and "name" in p}
    for g_def in diy_groups:
        for p in g_def.get("proxies", []) or []:
            if isinstance(p, dict) and "name" in p and p["name"] not in seen_proxy_names:
                p_copy = p.copy()
                hop = resolve_node_dialer_proxy(p_copy, default_chain=False)
                for k in ["relay", "underlying-proxy", "underlying_proxy", "chain", "via"]:
                    p_copy.pop(k, None)
                if hop:
                    p_copy["dialer-proxy"] = hop
                else:
                    p_copy.pop("dialer-proxy", None)
                all_proxies.append(p_copy)
                seen_proxy_names.add(p_copy["name"])

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

        is_primary = (idx == 1)
        prov = {
            "type": "http",
            "url": s["url"],
            "path": f"./proxy-providers/{pid}.yaml",
            "interval": 86400,
            "proxy": "DIRECT",
            "health-check": {
                "enable": True,
                "url": DEFAULT_PROBE_URL,
                "expected-status": DEFAULT_EXPECTED_STATUS,
                "interval": DEFAULT_PRIMARY_INTERVAL if is_primary else DEFAULT_STANDBY_INTERVAL,
                "timeout": DEFAULT_TIMEOUT,
                "lazy": False
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
                "url": DEFAULT_PROBE_URL,
                "expected-status": DEFAULT_EXPECTED_STATUS,
                "interval": DEFAULT_PRIMARY_INTERVAL,
                "timeout": DEFAULT_TIMEOUT,
                "lazy": False
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

    for idx, (s, pid) in enumerate(zip(relay_subs, relay_provider_ids), 1):
        name = s.get("name", pid)
        emoji = s.get("emoji", "✈️")
        opt_name = f"⚡ {name} 自动择优"
        select_name = f"{emoji} {name}"

        is_primary = (idx == 1)
        airport_auto_groups.append({
            "name": opt_name,
            "type": "url-test",
            "url": DEFAULT_PROBE_URL,
            "expected-status": DEFAULT_EXPECTED_STATUS,
            "interval": DEFAULT_PRIMARY_INTERVAL if is_primary else DEFAULT_STANDBY_INTERVAL,
            "timeout": DEFAULT_TIMEOUT,
            "tolerance": DEFAULT_TOLERANCE,
            "use": [pid]
        })
        airport_select_groups.append({
            "name": select_name,
            "type": "select",
            "use": [pid]
        })

    # 从 diy_groups 提取自定义机场/测速组与专网分流组
    custom_auto_groups = []
    custom_select_groups = []
    custom_routing_groups = []
    custom_fallback_def = None
    custom_relay_def = None

    for g_def in diy_groups:
        g_name = g_def.get("name")
        if not g_name:
            continue
        if g_name in ["🛟 Fallback", "Fallback"]:
            custom_fallback_def = g_def
            continue
        if g_name in ["🔀 中转节点"]:
            custom_relay_def = g_def
            continue

        g_type = g_def.get("type", "select")
        has_rules = bool(g_def.get("rules")) or bool(g_def.get("remote_rulesets"))

        g_nodes = []
        for p in g_def.get("proxies", []) or []:
            if isinstance(p, dict) and "name" in p:
                g_nodes.append(p["name"])
            elif isinstance(p, str):
                g_nodes.append(p)
        if g_def.get("filter"):
            pat = g_def["filter"]
            for p_name in relay_custom_names:
                if re.search(pat, p_name) and p_name not in g_nodes:
                    g_nodes.append(p_name)

        grp_dict = {
            "name": g_name,
            "type": g_type
        }
        if g_type in ["url-test", "fallback"]:
            grp_dict["url"] = g_def.get("url", DEFAULT_PROBE_URL)
            grp_dict["expected-status"] = str(g_def.get("expected-status", DEFAULT_EXPECTED_STATUS))
            grp_dict["interval"] = g_def.get("interval", DEFAULT_PRIMARY_INTERVAL)
            grp_dict["timeout"] = g_def.get("timeout", DEFAULT_TIMEOUT)
            grp_dict["tolerance"] = g_def.get("tolerance", DEFAULT_TOLERANCE)
            grp_dict["proxies"] = g_nodes if g_nodes else ["DIRECT"]
            if "filter" in g_def:
                grp_dict["filter"] = g_def["filter"]
            custom_auto_groups.append(grp_dict)
        elif not has_rules:
            grp_dict["proxies"] = g_nodes if g_nodes else ["DIRECT"]
            if "filter" in g_def:
                grp_dict["filter"] = g_def["filter"]
            custom_select_groups.append(grp_dict)
        else:
            proxies_list = ["DIRECT"] + [n for n in g_nodes if n != "DIRECT"] if g_nodes else ["DIRECT"]
            grp_dict["proxies"] = proxies_list
            custom_routing_groups.append(grp_dict)

    relay_proxies_summary = ["⚡ 自动择优", "🛟 Fallback"]
    for s in relay_subs:
        name = s.get("name", "")
        emoji = s.get("emoji", "✈️")
        relay_proxies_summary.extend([f"⚡ {name} 自动择优", f"{emoji} {name}"])
    for g in custom_auto_groups:
        if g["name"] not in relay_proxies_summary:
            relay_proxies_summary.append(g["name"])
    for g in custom_select_groups:
        if g["name"] not in relay_proxies_summary:
            relay_proxies_summary.append(g["name"])
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
            if custom_relay_def:
                if "proxies" in custom_relay_def:
                    r_proxies = []
                    for p in custom_relay_def["proxies"]:
                        if isinstance(p, dict) and "name" in p:
                            r_proxies.append(p["name"])
                        elif isinstance(p, str):
                            r_proxies.append(p)
                    g["proxies"] = r_proxies
                else:
                    g["proxies"] = relay_proxies_summary
                if "use" in custom_relay_def:
                    g["use"] = custom_relay_def["use"]
                elif "use" in g and not custom_relay_def.get("use") and not relay_provider_ids:
                    del g["use"]
            else:
                if relay_provider_ids:
                    g["use"] = relay_provider_ids
                g["proxies"] = relay_proxies_summary
            new_groups.append(g)

        elif g_name == "⚡ 自动择优":
            if relay_provider_ids:
                g["use"] = relay_provider_ids
            g["proxies"] = relay_custom_names.copy()
            g["url"] = DEFAULT_PROBE_URL
            g["expected-status"] = DEFAULT_EXPECTED_STATUS
            g["interval"] = DEFAULT_PRIMARY_INTERVAL
            g["timeout"] = DEFAULT_TIMEOUT
            g["tolerance"] = DEFAULT_TOLERANCE
            new_groups.append(g)
            new_groups.extend(airport_auto_groups)
            new_groups.extend(custom_auto_groups)

        elif g_name == "🛟 Fallback":
            if custom_fallback_def:
                if "proxies" in custom_fallback_def:
                    fb_proxies = []
                    for p in custom_fallback_def["proxies"]:
                        if isinstance(p, dict) and "name" in p:
                            fb_proxies.append(p["name"])
                        elif isinstance(p, str):
                            fb_proxies.append(p)
                    g["proxies"] = fb_proxies
                elif relay_custom_names:
                    g["proxies"] = relay_custom_names.copy()

                if "use" in custom_fallback_def:
                    g["use"] = custom_fallback_def["use"]
                elif not custom_fallback_def.get("proxies") and relay_provider_ids:
                    g["use"] = relay_provider_ids
                elif "use" in g and not custom_fallback_def.get("use"):
                    del g["use"]

                for k in ["url", "interval", "timeout", "tolerance", "expected-status", "filter"]:
                    if k in custom_fallback_def:
                        g[k] = custom_fallback_def[k]
                if "url" not in g:
                    g["url"] = DEFAULT_PROBE_URL
                if "expected-status" not in g:
                    g["expected-status"] = DEFAULT_EXPECTED_STATUS
                if "interval" not in g:
                    g["interval"] = DEFAULT_PRIMARY_INTERVAL
            else:
                if relay_provider_ids:
                    g["use"] = relay_provider_ids
                g["proxies"] = relay_custom_names.copy()
                g["url"] = DEFAULT_PROBE_URL
                g["expected-status"] = DEFAULT_EXPECTED_STATUS
                g["interval"] = DEFAULT_PRIMARY_INTERVAL
            new_groups.append(g)

        elif g_name == "🎮 Steam":
            new_groups.append(g)
            new_groups.extend(airport_select_groups)
            new_groups.extend(custom_select_groups)

        elif g_name in region_regexes:
            if relay_provider_ids:
                g["use"] = relay_provider_ids
            pattern = region_regexes[g_name]
            matched_custom = [p for p in relay_custom_names if re.search(pattern, p)]
            g["proxies"] = matched_custom
            new_groups.append(g)

        else:
            new_groups.append(g)

    # 动态插入 DIY 自定义专网策略组 (带专属分流规则)
    if custom_routing_groups:
        insert_idx = len(new_groups)
        for idx, g in enumerate(new_groups):
            if g.get("name") == "🔗 直连改道":
                insert_idx = idx + 1
                break
        for grp in custom_routing_groups:
            new_groups.insert(insert_idx, grp)
            insert_idx += 1

    # 业务组统一补齐：全局自动择优、Fallback、各机场自动择优与机场自选组
    business_group_names = (
        "🌐 海外服务", "🤖 OpenAI", "🚀 Google AI", "📧 邮件收件",
        "✈️ Telegram", "🍎 Apple", "🎮 Steam", "🔗 直连改道",
    )
    extra_members = ["⚡ 自动择优", "🛟 Fallback"]
    for ag, sg in zip(airport_auto_groups, airport_select_groups):
        extra_members.append(ag["name"])
        extra_members.append(sg["name"])
    extra_members.extend(ag["name"] for ag in custom_auto_groups)
    extra_members.extend(sg["name"] for sg in custom_select_groups)

    for g in new_groups:
        if g.get("name") in business_group_names and g.get("type") == "select":
            members = list(g.get("proxies") or [])
            for m in extra_members:
                if m not in members:
                    members.append(m)
            g["proxies"] = members

    # 保证每个策略组都合法，无空 use 或空 proxies 导致内核崩溃
    for g in new_groups:
        has_use = bool(g.get("use"))
        has_proxies = bool(g.get("proxies"))
        if not has_use and not has_proxies:
            g["proxies"] = ["DIRECT"]
        elif g.get("type") in ["url-test", "fallback"] and not has_use and len(g.get("proxies", [])) == 0:
            g["proxies"] = ["DIRECT"]

    config["proxy-groups"] = new_groups
    return apply_custom_rules_clash(config, custom_rules, diy_groups, client_type=client_type)


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
    claude_chained_map = {}

    for p in claude_custom:
        name = p.get("name", "Claude_Proxy")
        p_type = p.get("type", "socks5").lower()
        server = p.get("server", "127.0.0.1")
        port = p.get("port", 1080)
        claude_names.append(name)

        hop = resolve_node_dialer_proxy(p, default_chain=use_relay_chain)
        if hop:
            claude_chained_map[name] = hop

        if client_type == "surge":
            chain_opt = f', underlying-proxy="{hop}"' if hop else ""
            proxy_lines.append(f"{name} = {p_type}, {server}, {port}{chain_opt}")
        elif client_type == "shadowrocket":
            chain_opt = f', chain="{hop}"' if hop else ""
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

        hop = resolve_node_dialer_proxy(p, default_chain=False)
        chain_opt_surge = f', underlying-proxy="{hop}"' if hop else ""
        chain_opt_sr = f', chain="{hop}"' if hop else ""

        # 协议兼容性过滤：跳过 INI 类客户端不支持的现代核心专有协议 (如 AnyTLS)
        if client_type == "surge":
            if p_type in ["ss", "socks5", "http", "vmess", "trojan", "snell", "wireguard", "hysteria2"]:
                relay_names.append(name)
                proxy_lines.append(f"{name} = {p_type}, {server}, {port}{chain_opt_surge}")
        elif client_type == "shadowrocket":
            if p_type in ["ss", "socks5", "http", "vmess", "trojan", "snell", "wireguard", "hysteria2"]:
                relay_names.append(name)
                proxy_lines.append(f"{name} = {p_type}, {server}, {port}{chain_opt_sr}")
        elif client_type == "loon":
            if p_type in ["ss", "socks5", "http", "vmess", "trojan", "wireguard", "hysteria2"]:
                relay_names.append(name)
                proxy_lines.append(f"{name} = {p_type.capitalize()},{server},{port}")
        elif client_type == "qx":
            if p_type in ["ss", "socks5", "http", "vmess", "trojan"]:
                relay_names.append(name)
                proxy_lines.append(f"{p_type}={server}:{port}, tag={name}")

    diy_groups = extract_diy_groups(profile_data)
    seen_diy_names = set()
    for g_def in diy_groups:
        for p in g_def.get("proxies", []) or []:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "DIY_Proxy")
            if name in seen_diy_names:
                continue
            seen_diy_names.add(name)
            p_type = p.get("type", "socks5").lower()
            server = p.get("server", "127.0.0.1")
            port = p.get("port", 1080)
            hop = resolve_node_dialer_proxy(p, default_chain=False)
            chain_opt_surge = f', underlying-proxy="{hop}"' if hop else ""
            chain_opt_sr = f', chain="{hop}"' if hop else ""
            if client_type == "surge":
                proxy_lines.append(f"{name} = {p_type}, {server}, {port}{chain_opt_surge}")
            elif client_type == "shadowrocket":
                proxy_lines.append(f"{name} = {p_type}, {server}, {port}{chain_opt_sr}")
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
        for name, hop in claude_chained_map.items():
            chain_lines.append(f"{name} = {hop}")
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

    # 动态插入 DIY 自定义策略组：完全由用户自主定义组名、独立节点与专属规则
    if diy_groups:
        custom_fallback_def = None
        for g_def in diy_groups:
            if g_def.get("name") in ["🛟 Fallback", "Fallback"]:
                custom_fallback_def = g_def
                break

        if custom_fallback_def:
            fb_nodes = []
            for p in custom_fallback_def.get("proxies", []) or []:
                if isinstance(p, dict) and "name" in p:
                    fb_nodes.append(p["name"])
                elif isinstance(p, str):
                    fb_nodes.append(p)
            if fb_nodes:
                if client_type == "surge":
                    content = content.replace("🛟 Fallback = fallback, url=", f"🛟 Fallback = fallback, {', '.join(fb_nodes)}, url=")
                elif client_type in ["shadowrocket", "loon"]:
                    content = content.replace("🛟 Fallback = fallback,url=", f"🛟 Fallback = fallback,{','.join(fb_nodes)},url=")
                elif client_type == "qx":
                    content = content.replace("fallback=🛟 Fallback, server-tag-regex=.*,", f"fallback=🛟 Fallback, {', '.join(fb_nodes)},")

        for g_def in diy_groups:
            g_name = g_def.get("name")
            if not g_name or g_name in ["🛟 Fallback", "Fallback", "🔀 中转节点"]:
                continue
            g_type = g_def.get("type", "select")
            has_rules = bool(g_def.get("rules")) or bool(g_def.get("remote_rulesets"))
            g_nodes = []
            for p in g_def.get("proxies", []) or []:
                if isinstance(p, dict) and "name" in p:
                    g_nodes.append(p["name"])
                elif isinstance(p, str):
                    g_nodes.append(p)
            if g_def.get("filter"):
                pat = g_def["filter"]
                for p_name in relay_names:
                    if re.search(pat, p_name) and p_name not in g_nodes:
                        g_nodes.append(p_name)

            if has_rules and "DIRECT" not in g_nodes:
                g_nodes = ["DIRECT"] + g_nodes if g_nodes else ["DIRECT"]
            elif not g_nodes:
                g_nodes = ["DIRECT"]

            if client_type in ["surge", "shadowrocket", "loon"]:
                if g_type == "url-test":
                    diy_line = f"{g_name} = url-test, {', '.join(g_nodes)}, url={DEFAULT_PROBE_URL}, interval={DEFAULT_PRIMARY_INTERVAL}, tolerance={DEFAULT_TOLERANCE}"
                else:
                    target_str = ", ".join(g_nodes)
                    diy_line = f"{g_name} = select, {target_str}"
                content = content.replace("🔗 直连改道", f"{diy_line}\n🔗 直连改道")
            elif client_type == "qx":
                if g_type == "url-test":
                    diy_line = f"url-test={g_name}, {', '.join(g_nodes)}, url={DEFAULT_PROBE_URL}, interval={DEFAULT_PRIMARY_INTERVAL}, tolerance={DEFAULT_TOLERANCE}"
                else:
                    target_str = ", ".join(g_nodes)
                    diy_line = f"static={g_name}, {target_str}, img-url=https://raw.githubusercontent.com/Koolson/Qure/master/IconSet/Color/Round_Robin.png"
                content = content.replace("static=🔗 直连改道", f"{diy_line}\nstatic=🔗 直连改道")

    # 5. 注入自定义分流规则 (Direct / Relay / Claude Exit)
    injected_rules = []
    loon_remote = []
    qx_remote = []
    if custom_rules:
        def format_rule_for_client(r_text, default_tgt):
            if client_type == "qx":
                return format_qx_rule(r_text, default_tgt)
            else:
                return format_ini_rule(r_text, default_tgt)

        # Claude Exit
        for idx, r_url in enumerate((custom_rules.get("claude_exit", {}) or {}).get("remote_rulesets", []) or []):
            tag = f"claude-citadel-custom-claude-{idx+1}"
            if client_type == "loon":
                loon_remote.append(f"{r_url},policy=🇺🇸 海外出口,tag={tag},enabled=true")
            elif client_type in ["surge", "shadowrocket"]:
                injected_rules.append(f"RULE-SET,{r_url},🇺🇸 海外出口")
            elif client_type == "qx":
                qx_remote.append(f"{r_url}, tag={tag}, force-remote-spec=true")
        for r in (custom_rules.get("claude_exit", {}) or {}).get("rules", []) or []:
            fmt = format_rule_for_client(r, "🇺🇸 海外出口")
            if fmt:
                injected_rules.append(fmt)

        # Direct
        for idx, r_url in enumerate((custom_rules.get("direct", {}) or {}).get("remote_rulesets", []) or []):
            tag = f"claude-citadel-custom-direct-{idx+1}"
            if client_type == "loon":
                loon_remote.append(f"{r_url},policy=DIRECT,tag={tag},enabled=true")
            elif client_type in ["surge", "shadowrocket"]:
                injected_rules.append(f"RULE-SET,{r_url},DIRECT")
            elif client_type == "qx":
                qx_remote.append(f"{r_url}, tag={tag}, force-remote-spec=true")
        for r in (custom_rules.get("direct", {}) or {}).get("rules", []) or []:
            fmt = format_rule_for_client(r, "DIRECT")
            if fmt:
                injected_rules.append(fmt)

        # Relay
        for idx, r_url in enumerate((custom_rules.get("relay", {}) or {}).get("remote_rulesets", []) or []):
            tag = f"claude-citadel-custom-relay-{idx+1}"
            if client_type == "loon":
                loon_remote.append(f"{r_url},policy=🔀 中转节点,tag={tag},enabled=true")
            elif client_type in ["surge", "shadowrocket"]:
                injected_rules.append(f"RULE-SET,{r_url},🔀 中转节点")
            elif client_type == "qx":
                qx_remote.append(f"{r_url}, tag={tag}, force-remote-spec=true")
        for r in (custom_rules.get("relay", {}) or {}).get("rules", []) or []:
            fmt = format_rule_for_client(r, "🔀 中转节点")
            if fmt:
                injected_rules.append(fmt)

        # DIY 规则与远端规则集 (支持自定义任何策略组、任意节点与分流)
        for g_idx, g_def in enumerate(diy_groups or []):
            g_name = g_def.get("name")
            if not g_name:
                continue
            tag_prefix = sanitize_id(g_name) or f"diy_{g_idx+1}"
            for idx, r_url in enumerate(g_def.get("remote_rulesets", []) or []):
                tag = f"claude-citadel-diy-{tag_prefix}-{idx+1}"
                if client_type == "loon":
                    loon_remote.append(f"{r_url},policy={g_name},tag={tag},enabled=true")
                elif client_type in ["surge", "shadowrocket"]:
                    injected_rules.append(f"RULE-SET,{r_url},{g_name}")
                elif client_type == "qx":
                    qx_remote.append(f"{r_url}, tag={tag}, force-remote-spec=true")
            for r in g_def.get("rules", []) or []:
                fmt = format_rule_for_client(r, g_name)
                if fmt:
                    injected_rules.append(fmt)

    if loon_remote:
        content = content.replace("[Remote Rule]\n", "[Remote Rule]\n" + "\n".join(loon_remote) + "\n")
    elif client_type == "loon":
        # 无自定义远端规则时移除空段，避免部分客户端对空 section 报错
        content = content.replace("[Remote Rule]\n\n", "")
    if qx_remote:
        content = content.replace("[filter_remote]\n", "[filter_remote]\n" + "\n".join(qx_remote) + "\n")

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
    try:
        res = subprocess.run([str(mihomo_bin), "-t", "-f", str(config_path)],
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        log(f"{config_path.name} 内核校验超时（120s），已中断。", "ERROR")
        return False
    if res.returncode == 0:
        log(f"{config_path.name} 内核语法校验通过！", "SUCCESS")
        return True
    # mihomo 的报错可能写在 stdout 也可能在 stderr，两者都要保留，避免互相遮蔽
    detail = "\n".join(x.strip() for x in (res.stdout, res.stderr) if x and x.strip())
    log(f"{config_path.name} 语法校验失败:\n{detail or '(内核未输出错误详情)'}", "ERROR")
    return False


def publish_all_to_gist(gist_id, output_files, gh_token=None):
    """将所有客户端配置同步发布至 Secret Gist。"""
    log(f"正在发布全量 6 客户端配置到 Gist ({gist_id}) ...")
    payload = {"files": {}}
    for filename, path in output_files.items():
        payload["files"][filename] = {"content": path.read_text(encoding="utf-8")}

    # 1. 尝试使用本机 gh CLI API
    try:
        res = subprocess.run(
            ["gh", "api", "-X", "PATCH", f"/gists/{gist_id}", "--input", "-"],
            input=json.dumps(payload),
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            log("通过 gh CLI 成功发布全部客户端配置至 Gist！", "SUCCESS")
            return True
        else:
            log(f"gh API 调用未成功: {res.stderr.strip()}，尝试回退至 REST API...", "WARN")
    except Exception as e:
        log(f"gh CLI 运行异常: {e}，尝试回退至 REST API...", "WARN")

    # 2. 回退使用 Python 原生 urllib REST API
    token = gh_token or os.environ.get("GH_TOKEN") or os.environ.get("GIST_PAT")
    if not token:
        log("缺少 GitHub Token，无法调用 REST API 更新 Gist！", "ERROR")
        return False

    url = f"https://api.github.com/gists/{gist_id}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "User-Agent": "Claude-Citadel-Builder",
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
        "claude-citadel-clash.yaml": OUTPUT_DIR / "claude-citadel-clash.yaml",
        "claude-citadel-stash.yaml": OUTPUT_DIR / "claude-citadel-stash.yaml",
        "claude-citadel-surge.conf": OUTPUT_DIR / "claude-citadel-surge.conf",
        "claude-citadel-loon.conf": OUTPUT_DIR / "claude-citadel-loon.conf",
        "claude-citadel-shadowrocket.conf": OUTPUT_DIR / "claude-citadel-shadowrocket.conf",
        "claude-citadel-qx.conf": OUTPUT_DIR / "claude-citadel-qx.conf"
    }

    # 1. 渲染 Clash 与 Stash
    log("正在渲染 Clash / Mihomo 配置...")
    clash_cfg = build_clash_stash(TEMPLATES_DIR / "clash.yaml", profile_data, client_type="clash")
    with open(output_files["claude-citadel-clash.yaml"], "w", encoding="utf-8") as f:
        yaml.dump(clash_cfg, f, allow_unicode=True, sort_keys=False)

    log("正在渲染 Stash 配置...")
    stash_cfg = build_clash_stash(TEMPLATES_DIR / "stash.yaml", profile_data, client_type="stash")
    with open(output_files["claude-citadel-stash.yaml"], "w", encoding="utf-8") as f:
        yaml.dump(stash_cfg, f, allow_unicode=True, sort_keys=False)

    # 2. 渲染 Surge, Loon, Shadowrocket, Quantumult X
    log("正在渲染 Surge / Loon / Shadowrocket / Quantumult X 配置...")
    output_files["claude-citadel-surge.conf"].write_text(build_ini_based_client(TEMPLATES_DIR / "surge.conf", profile_data, "surge"), encoding="utf-8")
    output_files["claude-citadel-loon.conf"].write_text(build_ini_based_client(TEMPLATES_DIR / "loon.conf", profile_data, "loon"), encoding="utf-8")
    output_files["claude-citadel-shadowrocket.conf"].write_text(build_ini_based_client(TEMPLATES_DIR / "shadowrocket.conf", profile_data, "shadowrocket"), encoding="utf-8")
    output_files["claude-citadel-qx.conf"].write_text(build_ini_based_client(TEMPLATES_DIR / "qx.conf", profile_data, "qx"), encoding="utf-8")

    # 3. 离线内核校验
    if not args.skip_validation:
        ok_clash = validate_with_mihomo(output_files["claude-citadel-clash.yaml"])
        ok_stash = validate_with_mihomo(output_files["claude-citadel-stash.yaml"])
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
