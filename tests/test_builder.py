#!/usr/bin/env python3
"""全平台多客户端自动化测试套件：验证 6 大客户端输出、分层规则注入、代理链与离线内核校验。"""

import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from build import build_clash_stash, build_ini_based_client, validate_with_mihomo

TEMPLATES_DIR = ROOT / "templates"


class TestClaudeCitadelBuilder(unittest.TestCase):

    def setUp(self):
        self.clash_tpl = TEMPLATES_DIR / "clash.yaml"
        self.stash_tpl = TEMPLATES_DIR / "stash.yaml"
        self.surge_tpl = TEMPLATES_DIR / "surge.conf"
        self.loon_tpl = TEMPLATES_DIR / "loon.conf"
        self.shadowrocket_tpl = TEMPLATES_DIR / "shadowrocket.conf"
        self.qx_tpl = TEMPLATES_DIR / "qx.conf"

    def test_six_clients_generation(self):
        """测试 6 大主流客户端配置全部正确生成且结构完整。"""
        profile = {
            "claude_exits": {
                "use_relay_chain": True,
                "custom_proxies": [
                    {"name": "🇺🇸 波特兰出口", "type": "socks5", "server": "198.51.100.1", "port": 1080}
                ]
            },
            "relays": {
                "subscriptions": [
                    {"name": "猫咪云", "emoji": "🐱", "url": "https://sub.a.com/sub"},
                    {"name": "良心云", "emoji": "❤️", "url": "https://sub.b.com/sub"}
                ],
                "custom_proxies": [
                    {"name": "🇯🇵 自建日本直连", "type": "ss", "server": "198.51.100.2", "port": 8388}
                ]
            },
            "custom_rules": {
                "direct": {
                    "rules": ["DOMAIN-SUFFIX,mycompany.internal"]
                },
                "relay": {
                    "rules": ["DOMAIN-SUFFIX,custom-blocked.com"]
                },
                "claude_exit": {
                    "rules": ["DOMAIN-SUFFIX,custom-claude-api.com"]
                }
            }
        }

        # 1. Clash
        clash_cfg = build_clash_stash(self.clash_tpl, profile, "clash")
        self.assertIn("🇺🇸 波特兰出口", [p["name"] for p in clash_cfg["proxies"]])
        self.assertEqual(clash_cfg["proxies"][0].get("dialer-proxy"), "🔀 中转节点")
        self.assertIn("DOMAIN-SUFFIX,mycompany.internal,DIRECT", clash_cfg["rules"])
        self.assertIn("DOMAIN-SUFFIX,custom-claude-api.com,🇺🇸 海外出口", clash_cfg["rules"])

        # 2. Stash
        stash_cfg = build_clash_stash(self.stash_tpl, profile, "stash")
        self.assertIn("DOMAIN-SUFFIX,mycompany.internal,DIRECT", stash_cfg["rules"])

        # 3. Surge
        surge_out = build_ini_based_client(self.surge_tpl, profile, "surge")
        self.assertIn("🇺🇸 波特兰出口", surge_out)
        self.assertIn('underlying-proxy="🔀 中转节点"', surge_out)
        self.assertIn("DOMAIN-SUFFIX,mycompany.internal,DIRECT", surge_out)
        self.assertIn("猫咪云 = select, policy-path=", surge_out)

        # 4. Loon
        loon_out = build_ini_based_client(self.loon_tpl, profile, "loon")
        self.assertIn("🇺🇸 波特兰出口", loon_out)
        self.assertIn("DOMAIN-SUFFIX,mycompany.internal,DIRECT", loon_out)
        self.assertIn("🇺🇸 波特兰出口 = 🔀 中转节点", loon_out)

        # 5. Shadowrocket
        sr_out = build_ini_based_client(self.shadowrocket_tpl, profile, "shadowrocket")
        self.assertIn("🇺🇸 波特兰出口", sr_out)
        self.assertIn('chain="🔀 中转节点"', sr_out)
        self.assertIn("DOMAIN-SUFFIX,mycompany.internal,DIRECT", sr_out)
        self.assertIn("猫咪云 = select, policy-path=", sr_out)

        # 6. Quantumult X
        qx_out = build_ini_based_client(self.qx_tpl, profile, "qx")
        self.assertIn("tag=🇺🇸 波特兰出口", qx_out)
        self.assertIn("host-suffix, mycompany.internal, direct", qx_out)
        self.assertIn("host-suffix, custom-claude-api.com, 🇺🇸 海外出口", qx_out)

    def test_relay_chain_toggle_disabled(self):
        """测试禁用代理链时，节点保持直连拨号。"""
        profile = {
            "claude_exits": {
                "use_relay_chain": False,
                "custom_proxies": [
                    {"name": "🇺🇸 直连住宅IP", "type": "socks5", "server": "198.51.100.1", "port": 1080}
                ]
            }
        }
        clash_cfg = build_clash_stash(self.clash_tpl, profile, "clash")
        self.assertNotIn("dialer-proxy", clash_cfg["proxies"][0])

        surge_out = build_ini_based_client(self.surge_tpl, profile, "surge")
        self.assertNotIn("underlying-proxy", surge_out)

        loon_out = build_ini_based_client(self.loon_tpl, profile, "loon")
        self.assertNotIn("🇺🇸 直连住宅IP = 🔀 中转节点", loon_out)

    def test_custom_rules_with_no_resolve_and_qx_format(self):
        """测试带 no-resolve 的规则解析与 QX 格式自动转换。"""
        profile = {
            "custom_rules": {
                "direct": {
                    "rules": ["IP-CIDR,10.0.0.0/8,no-resolve", "DOMAIN-SUFFIX,internal.corp,DIRECT"]
                }
            }
        }
        clash_cfg = build_clash_stash(self.clash_tpl, profile, "clash")
        self.assertIn("IP-CIDR,10.0.0.0/8,DIRECT,no-resolve", clash_cfg["rules"])
        self.assertIn("DOMAIN-SUFFIX,internal.corp,DIRECT", clash_cfg["rules"])

        qx_out = build_ini_based_client(self.qx_tpl, profile, "qx")
        self.assertIn("ip-cidr, 10.0.0.0/8, direct", qx_out)
        self.assertIn("host-suffix, internal.corp, direct", qx_out)

    def test_empty_profile_validation(self):
        """测试空配置文件时不产生死循环策略组，且能安全通过 Mihomo 内核校验。"""
        cfg_clash = build_clash_stash(self.clash_tpl, {}, client_type="clash")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg_clash, f, allow_unicode=True)
            p = Path(f.name)
        try:
            self.assertTrue(validate_with_mihomo(p))
        finally:
            if p.is_file():
                p.unlink()

    def test_mihomo_offline_syntax_pass(self):
        """测试生成配置 100% 通过 mihomo -t 离线静态校验。"""
        profile = {
            "claude_exits": {
                "custom_proxies": [
                    {"name": "🇺🇸 波特兰专用出口", "type": "socks5", "server": "198.51.100.1", "port": 1080}
                ]
            },
            "relays": {
                "subscriptions": [
                    {"name": "测试机场1", "emoji": "🐱", "url": "https://sub.test1.com/sub"}
                ]
            }
        }
        cfg_clash = build_clash_stash(self.clash_tpl, profile, client_type="clash")
        cfg_stash = build_clash_stash(self.stash_tpl, profile, client_type="stash")

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".yaml", delete=False) as f_clash:
            yaml.dump(cfg_clash, f_clash, allow_unicode=True)
            f_clash_path = Path(f_clash.name)

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".yaml", delete=False) as f_stash:
            yaml.dump(cfg_stash, f_stash, allow_unicode=True)
            f_stash_path = Path(f_stash.name)

        try:
            self.assertTrue(validate_with_mihomo(f_clash_path))
            self.assertTrue(validate_with_mihomo(f_stash_path))
        finally:
            if f_clash_path.is_file():
                f_clash_path.unlink()
            if f_stash_path.is_file():
                f_stash_path.unlink()

    def test_mesh_proxies_and_regional_groups(self):
        """测试 EasyTier 等组网独立节点不污染常规中转池，并验证 Apple/Steam 包含日美新等区域组。"""
        profile = {
            "mesh_proxies": [
                {"name": "EasyTier SOCKS5", "type": "socks5", "server": "127.0.0.1", "port": 15555}
            ],
            "custom_rules": {
                "mesh": {
                    "rules": ["IP-CIDR,10.200.0.0/16,no-resolve"]
                }
            }
        }
        cfg_clash = build_clash_stash(self.clash_tpl, profile, client_type="clash")
        groups = {g["name"]: g for g in cfg_clash["proxy-groups"]}

        # 1. 验证 EasyTier 节点在 🕸️ 组网访问 中，但绝不在 🔀 中转节点 或 ⚡ 自动择优 中
        self.assertIn("EasyTier SOCKS5", groups["🕸️ 组网访问"]["proxies"])
        self.assertNotIn("EasyTier SOCKS5", groups["🔀 中转节点"]["proxies"])
        self.assertNotIn("EasyTier SOCKS5", groups["⚡ 自动择优"]["proxies"])

        # 2. 验证 Apple 与 Steam 包含日、美、新、港等策略组
        for g_name in ["🍎 Apple", "🎮 Steam"]:
            self.assertIn("🇯🇵 日本节点", groups[g_name]["proxies"])
            self.assertIn("🇺🇸 美国节点", groups[g_name]["proxies"])
            self.assertIn("🇸🇬 新加坡节点", groups[g_name]["proxies"])
            self.assertIn("🇭🇰 香港节点", groups[g_name]["proxies"])

        # 3. 验证规则注入正确指向 🕸️ 组网访问
        self.assertIn("IP-CIDR,10.200.0.0/16,🕸️ 组网访问,no-resolve", cfg_clash["rules"])

        # 4. 验证 Surge / Loon / SR / QX 包含组网节点且 Apple/Steam 包含区域节点
        surge_out = build_ini_based_client(self.surge_tpl, profile, client_type="surge")
        self.assertIn("EasyTier SOCKS5", surge_out)
        self.assertIn("🕸️ 组网访问 = select, DIRECT, EasyTier SOCKS5", surge_out)
        self.assertIn("🍎 Apple = select, DIRECT, 🔀 中转节点, ⚡ 自动择优, 🇭🇰 香港节点, 🇯🇵 日本节点, 🇺🇸 美国节点, 🇸🇬 新加坡节点, 🇹🇼 台湾节点", surge_out)

    def test_per_node_dialer_proxy_and_custom_hop(self):
        """测试节点级独立代理链开关，以及显式指定经过特定前置中转节点/策略组。"""
        profile = {
            "claude_exits": {
                "use_relay_chain": True,  # 全局虽然开启，但允许节点级独立覆盖或指定
                "custom_proxies": [
                    {
                        "name": "🇺🇸 专属出口A (指定走Amy择优)",
                        "type": "socks5",
                        "server": "198.51.100.1",
                        "port": 1080,
                        "dialer-proxy": "⚡ Amy 自动择优"  # 👈 显式指定经过特定组
                    },
                    {
                        "name": "🇺🇸 专属出口B (指定走具体节点)",
                        "type": "socks5",
                        "server": "198.51.100.2",
                        "port": 1080,
                        "relay": "[Amy] 🇭🇰 香港 01"  # 👈 友好别名 relay
                    },
                    {
                        "name": "🇺🇸 专属出口C (显式直连)",
                        "type": "socks5",
                        "server": "198.51.100.3",
                        "port": 1080,
                        "dialer-proxy": False  # 👈 覆盖全局开启，强制直连
                    },
                    {
                        "name": "🇺🇸 专属出口D (继承全局中转)",
                        "type": "socks5",
                        "server": "198.51.100.4",
                        "port": 1080
                    }
                ]
            }
        }

        # 1. Clash 验证
        clash_cfg = build_clash_stash(self.clash_tpl, profile, "clash")
        clash_proxies = {p["name"]: p for p in clash_cfg["proxies"]}

        self.assertEqual(clash_proxies["🇺🇸 专属出口A (指定走Amy择优)"].get("dialer-proxy"), "⚡ Amy 自动择优")
        self.assertEqual(clash_proxies["🇺🇸 专属出口B (指定走具体节点)"].get("dialer-proxy"), "[Amy] 🇭🇰 香港 01")
        self.assertNotIn("dialer-proxy", clash_proxies["🇺🇸 专属出口C (显式直连)"])
        self.assertEqual(clash_proxies["🇺🇸 专属出口D (继承全局中转)"].get("dialer-proxy"), "🔀 中转节点")

        # 2. Surge 验证 (underlying-proxy)
        surge_out = build_ini_based_client(self.surge_tpl, profile, "surge")
        self.assertIn('🇺🇸 专属出口A (指定走Amy择优) = socks5, 198.51.100.1, 1080, underlying-proxy="⚡ Amy 自动择优"', surge_out)
        self.assertIn('🇺🇸 专属出口B (指定走具体节点) = socks5, 198.51.100.2, 1080, underlying-proxy="[Amy] 🇭🇰 香港 01"', surge_out)
        self.assertIn('🇺🇸 专属出口C (显式直连) = socks5, 198.51.100.3, 1080\n', surge_out)
        self.assertIn('🇺🇸 专属出口D (继承全局中转) = socks5, 198.51.100.4, 1080, underlying-proxy="🔀 中转节点"', surge_out)

        # 3. Shadowrocket 验证 (chain)
        sr_out = build_ini_based_client(self.shadowrocket_tpl, profile, "shadowrocket")
        self.assertIn('🇺🇸 专属出口A (指定走Amy择优) = socks5, 198.51.100.1, 1080, chain="⚡ Amy 自动择优"', sr_out)
        self.assertIn('🇺🇸 专属出口B (指定走具体节点) = socks5, 198.51.100.2, 1080, chain="[Amy] 🇭🇰 香港 01"', sr_out)
        self.assertIn('🇺🇸 专属出口C (显式直连) = socks5, 198.51.100.3, 1080\n', sr_out)
        self.assertIn('🇺🇸 专属出口D (继承全局中转) = socks5, 198.51.100.4, 1080, chain="🔀 中转节点"', sr_out)

        # 4. Loon 验证 ([Proxy Chain])
        loon_out = build_ini_based_client(self.loon_tpl, profile, "loon")
        self.assertIn("🇺🇸 专属出口A (指定走Amy择优) = ⚡ Amy 自动择优", loon_out)
        self.assertIn("🇺🇸 专属出口B (指定走具体节点) = [Amy] 🇭🇰 香港 01", loon_out)
        self.assertNotIn("🇺🇸 专属出口C (显式直连) =", loon_out.split("[Proxy Chain]")[1].split("[Proxy Group]")[0])
        self.assertIn("🇺🇸 专属出口D (继承全局中转) = 🔀 中转节点", loon_out)

    def test_custom_fallback_and_group_order(self):
        """测试在 diy_groups 中自定义 🛟 Fallback 策略组的节点优先级顺序与去重。"""
        profile = {
            "relays": {
                "subscriptions": [
                    {"name": "良心专线", "emoji": "❤️", "url": "https://sub.liangxin.com/sub"},
                    {"name": "TNT专线", "emoji": "🚀", "url": "https://sub.tnt.com/sub"},
                    {"name": "猫咪云", "emoji": "🐱", "url": "https://sub.miaona.com/sub"},
                ]
            },
            "diy_groups": [
                {
                    "name": "⚡ Amy 自动择优",
                    "type": "url-test",
                    "proxies": ["Amy 01", "Amy 02"]
                },
                {
                    "name": "🛟 Fallback",
                    "type": "fallback",
                    "proxies": [
                        "⚡ 良心专线 自动择优",
                        "⚡ TNT专线 自动择优",
                        "⚡ 猫咪云 自动择优",
                        "⚡ Amy 自动择优"
                    ]
                }
            ]
        }

        # 1. Clash 验证
        clash_cfg = build_clash_stash(self.clash_tpl, profile, "clash")
        groups = {g["name"]: g for g in clash_cfg["proxy-groups"]}
        self.assertIn("🛟 Fallback", groups)
        fb_group = groups["🛟 Fallback"]
        self.assertEqual(fb_group["proxies"], [
            "⚡ 良心专线 自动择优",
            "⚡ TNT专线 自动择优",
            "⚡ 猫咪云 自动择优",
            "⚡ Amy 自动择优"
        ])
        self.assertNotIn("use", fb_group)
        # 验证 🛟 Fallback 没有被重复添加
        all_fb = [g for g in clash_cfg["proxy-groups"] if g["name"] == "🛟 Fallback"]
        self.assertEqual(len(all_fb), 1)

        # 2. Surge 验证
        surge_out = build_ini_based_client(self.surge_tpl, profile, "surge")
        self.assertIn("🛟 Fallback = fallback, ⚡ 良心专线 自动择优, ⚡ TNT专线 自动择优, ⚡ 猫咪云 自动择优, ⚡ Amy 自动择优, url=", surge_out)

        # 3. Shadowrocket 验证
        sr_out = build_ini_based_client(self.shadowrocket_tpl, profile, "shadowrocket")
        self.assertIn("🛟 Fallback = fallback,⚡ 良心专线 自动择优,⚡ TNT专线 自动择优,⚡ 猫咪云 自动择优,⚡ Amy 自动择优,url=", sr_out)

    def test_golden_ratio_health_check_parameters(self):
        """测试健康检查黄金平衡参数：Cloudflare 204、4000ms 超时、100ms 容差、分级周期与后台温备。"""
        profile = {
            "relays": {
                "subscriptions": [
                    {"name": "主用机场", "emoji": "❤️", "url": "https://sub.primary.com/sub"},
                    {"name": "备用机场", "emoji": "🚀", "url": "https://sub.standby.com/sub"},
                ]
            }
        }

        # 1. Clash / Mihomo 校验
        clash_cfg = build_clash_stash(self.clash_tpl, profile, "clash")
        providers = clash_cfg["proxy-providers"]

        # 主用机场：90s 探测，后台保活
        p_primary = providers[list(providers.keys())[0]]
        self.assertEqual(p_primary["health-check"]["url"], "http://cp.cloudflare.com/generate_204")
        self.assertEqual(p_primary["health-check"]["expected-status"], "204")
        self.assertEqual(p_primary["health-check"]["interval"], 90)
        self.assertEqual(p_primary["health-check"]["timeout"], 4000)
        self.assertFalse(p_primary["health-check"]["lazy"])

        # 备用机场：300s 探测，强制温备 (lazy: false)
        p_standby = providers[list(providers.keys())[1]]
        self.assertEqual(p_standby["health-check"]["interval"], 300)
        self.assertFalse(p_standby["health-check"]["lazy"])

        # 策略组校验：expected-status 与 4000ms
        groups = {g["name"]: g for g in clash_cfg["proxy-groups"]}
        g_auto = groups["⚡ 自动择优"]
        self.assertEqual(g_auto["url"], "http://cp.cloudflare.com/generate_204")
        self.assertEqual(g_auto["expected-status"], "204")
        self.assertEqual(g_auto["tolerance"], 100)

        # 自选节点组校验：不包含自动择优组，纯净展示单节点
        g_select = groups["❤️ 主用机场"]
        self.assertNotIn("proxies", g_select)
        self.assertEqual(g_select["use"], [list(providers.keys())[0]])

    def test_rule_providers_proxy_routing(self):
        """规则集按正常分流走中转组；订阅保持直连；格式声明与去重正确。"""
        profile = {
            "claude_exits": {
                "use_relay_chain": False,
                "custom_proxies": [
                    {"name": "🇺🇸 专属出口", "type": "socks5", "server": "198.51.100.1", "port": 1080}
                ],
            },
            "relays": {
                "subscriptions": [
                    {"name": "测试机场", "url": "https://sub.example.com/api/v1/client/subscribe?token=x"}
                ],
            },
            "custom_rules": {
                "claude_exit": {
                    "remote_rulesets": ["https://raw.githubusercontent.com/test/claude.yaml"],
                    # 与模板内置规则重复，用于验证去重
                    "rules": ["DOMAIN-SUFFIX,ipify.org,🇺🇸 海外出口"],
                },
                "direct": {
                    "remote_rulesets": ["https://raw.githubusercontent.com/test/direct.yaml"]
                },
                "relay": {
                    "remote_rulesets": ["https://raw.githubusercontent.com/test/relay.yaml"]
                },
            },
            "diy_groups": [
                {
                    "name": "🏢 公司专网",
                    "type": "select",
                    "remote_rulesets": ["https://raw.githubusercontent.com/test/corp.yaml"],
                    "proxies": [
                        {"name": "🏢 公司前置", "type": "socks5", "server": "192.0.2.10", "port": 1080}
                    ],
                }
            ],
        }
        clash_cfg = build_clash_stash(self.clash_tpl, profile, "clash")
        stash_cfg = build_clash_stash(self.stash_tpl, profile, "stash")

        # 规则集走正常中转组，且该组必须真实存在
        for cfg, label in ((clash_cfg, "clash"), (stash_cfg, "stash")):
            group_names = {g["name"] for g in cfg["proxy-groups"]}
            self.assertIn("🔀 中转节点", group_names, f"{label} 缺少中转组")

        # 四个注入分支都要覆盖到
        tags = set(clash_cfg["rule-providers"])
        for expected in (
            "claude-citadel-custom-claude-1",
            "claude-citadel-custom-direct-1",
            "claude-citadel-custom-relay-1",
        ):
            self.assertIn(expected, tags, f"缺少注入分支 {expected}")
        self.assertTrue(
            any(t.startswith("claude-citadel-diy-") for t in tags), "缺少 DIY 策略组规则集分支"
        )

        # Clash: 规则集按正常分流走中转组，不得回退 DIRECT
        for rp_name, rp in clash_cfg["rule-providers"].items():
            self.assertEqual(rp.get("proxy"), "🔀 中转节点", f"{rp_name} 下载出口错误")

        # 负例：任何 rule-provider 的 proxy 必须是已存在的策略组名
        group_names = {g["name"] for g in clash_cfg["proxy-groups"]}
        for rp_name, rp in clash_cfg["rule-providers"].items():
            self.assertIn(rp.get("proxy"), group_names, f"{rp_name} 指向不存在的组")

        # 订阅下载必须保持 DIRECT：若指向依赖订阅节点的组，会形成自举环
        for pp_name, pp in clash_cfg["proxy-providers"].items():
            self.assertEqual(pp.get("proxy"), "DIRECT", f"{pp_name} 订阅下载应直连")

        # 注入规则与模板规则重复时只保留一份
        self.assertEqual(
            clash_cfg["rules"].count("DOMAIN-SUFFIX,ipify.org,🇺🇸 海外出口"), 1, "规则未去重"
        )

        # 去重只针对完全一致的行；标志或大小写不同代表不同语义，注入版保持置顶
        dedup_cfg = build_clash_stash(
            self.clash_tpl,
            {"custom_rules": {"direct": {"rules": [
                "GEOIP,CN,DIRECT,no-resolve",
                "IP-CIDR,10.0.0.0/8,DIRECT,src",
                "PROCESS-NAME,Claude,DIRECT",
            ]}}},
            "clash",
        )
        dedup_rules = dedup_cfg["rules"]
        self.assertIn("GEOIP,CN,DIRECT,no-resolve", dedup_rules, "注入的更严格版本丢失")
        self.assertIn("GEOIP,CN,DIRECT", dedup_rules, "模板版本不应被删除")
        self.assertLess(
            dedup_rules.index("GEOIP,CN,DIRECT,no-resolve"),
            dedup_rules.index("GEOIP,CN,DIRECT"),
            "注入规则应置于模板规则之前",
        )
        self.assertIn("IP-CIDR,10.0.0.0/8,DIRECT,src", dedup_rules, "src 规则被误合并")
        self.assertIn("PROCESS-NAME,Claude,DIRECT", dedup_rules, "大小写敏感规则被误合并")
        self.assertIn("PROCESS-NAME,claude,🇺🇸 海外出口", dedup_rules, "模板小写规则被误删")

        # 上游规则集格式声明：Loyalsoldier/blackmatrix7 的 .txt 实际是 YAML payload
        expected_formats = {
            "claude-citadel-apple": ("domain", "yaml"),
            "claude-citadel-direct": ("domain", "yaml"),
            "claude-citadel-gfw": ("domain", "yaml"),
            "claude-citadel-telegram": ("ipcidr", "yaml"),
            "claude-citadel-cn-ip": ("ipcidr", "yaml"),
        }
        for name, (behavior, fmt) in expected_formats.items():
            rp = clash_cfg["rule-providers"][name]
            self.assertEqual(rp["behavior"], behavior, f"{name} behavior 错误")
            self.assertEqual(rp["format"], fmt, f"{name} format 错误")

        # Stash 侧：同样的格式声明、no-resolve 与镜像地址
        for name, (behavior, fmt) in expected_formats.items():
            rp = stash_cfg["rule-providers"][name]
            self.assertEqual((rp["behavior"], rp["format"]), (behavior, fmt), f"stash {name} 声明错误")
        for name in ("claude-citadel-telegram", "claude-citadel-cn-ip"):
            self.assertTrue(
                any(r.startswith(f"RULE-SET,{name},") and r.endswith(",no-resolve")
                    for r in stash_cfg["rules"]),
                f"stash {name} 缺少 no-resolve",
            )
        for name, rp in stash_cfg["rule-providers"].items():
            if name.startswith(("claude-citadel-custom-", "claude-citadel-diy-")):
                continue  # 用户自定义远端规则集保持原地址
            self.assertIn("cdn.jsdelivr.net", rp["url"], f"stash {name} 未使用镜像地址")

        # ipcidr 规则集必须带 no-resolve，避免无谓解析
        for name in ("claude-citadel-telegram", "claude-citadel-cn-ip"):
            self.assertTrue(
                any(r.startswith(f"RULE-SET,{name},") and r.endswith(",no-resolve")
                    for r in clash_cfg["rules"]),
                f"{name} 缺少 no-resolve",
            )

        # Stash 不支持 rule-providers 的 proxy 字段
        for rp_name, rp in stash_cfg["rule-providers"].items():
            self.assertNotIn("proxy", rp, f"Stash {rp_name} 不应包含 proxy 字段")


    def test_ini_remote_rules_order(self):
        """Loon / QX 远端规则集按 claude→direct→relay 顺序注入；无自定义规则时不残留空段。"""
        profile = {
            "custom_rules": {
                "claude_exit": {"remote_rulesets": ["https://example.com/claude.list"]},
                "direct": {"remote_rulesets": ["https://example.com/direct.list"]},
                "relay": {"remote_rulesets": ["https://example.com/relay.list"]},
            }
        }
        for tpl, client in ((self.loon_tpl, "loon"), (self.qx_tpl, "qx")):
            text = build_ini_based_client(tpl, profile, client)
            pos = [text.find(f"https://example.com/{name}.list") for name in ("claude", "direct", "relay")]
            self.assertTrue(all(p >= 0 for p in pos), f"{client} 缺少注入的远端规则集")
            self.assertEqual(pos, sorted(pos), f"{client} 远端规则集注入顺序错误")

        empty_loon = build_ini_based_client(self.loon_tpl, {}, "loon")
        self.assertNotIn("[Remote Rule]", empty_loon, "无自定义规则时残留空段")

    def test_business_groups_expanded(self):
        """业务组必须包含全局自动择优、Fallback、各机场自动择优与机场自选组。"""
        profile = {"relays": {"subscriptions": [
            {"name": "机场A", "emoji": "✈️", "url": "https://sub.example.com/a"},
            {"name": "机场B", "emoji": "❤️", "url": "https://sub.example.com/b"},
        ]}}
        cfg = build_clash_stash(self.clash_tpl, profile, "clash")
        groups = {g["name"]: g for g in cfg["proxy-groups"]}
        for name in ("🌐 海外服务", "🤖 OpenAI", "📧 邮件收件", "🍎 Apple", "🔗 直连改道"):
            members = groups[name]["proxies"]
            for expected in (
                "⚡ 自动择优", "🛟 Fallback",
                "⚡ 机场A 自动择优", "✈️ 机场A",
                "⚡ 机场B 自动择优", "❤️ 机场B",
            ):
                self.assertIn(expected, members, f"{name} 缺少 {expected}")

    def test_network_overrides_and_cn_domains(self):
        """profile 的 network 覆盖生效；Clash 使用 MRS 版国内域名规则集。"""
        profile = {
            "network": {
                "tun_exclude_address": ["203.0.113.7/32"],
                "fake_ip_filter_extra": ["+.example-mesh.test"],
            }
        }
        clash = build_clash_stash(self.clash_tpl, profile, "clash")
        self.assertEqual(clash["tun"]["route-exclude-address"], ["203.0.113.7/32"])
        self.assertIn("+.example-mesh.test", clash["dns"]["fake-ip-filter"])

        profile["network"]["nameserver_policy_extra"] = {
            "+.example.test": ["https://223.5.5.5/dns-query"]
        }
        clash2 = build_clash_stash(self.clash_tpl, profile, "clash")
        self.assertEqual(
            clash2["dns"]["nameserver-policy"]["+.example.test"],
            ["https://223.5.5.5/dns-query"],
        )

        # 邮件与 Discord 的显式规则、IMAP 端口定向
        for rule in (
            "DOMAIN-SUFFIX,gmail.com,🌐 海外服务",
            "DOMAIN,mail.google.com,🌐 海外服务",
            "PROCESS-NAME,Discord.exe,🌐 海外服务",
            "DST-PORT,993,📧 邮件收件",
            "DST-PORT,995,📧 邮件收件",
        ):
            self.assertIn(rule, clash["rules"], f"缺少规则 {rule}")

        rp = clash["rule-providers"]["cn-domains"]
        self.assertEqual(rp["format"], "mrs")
        self.assertEqual(rp["behavior"], "domain")
        self.assertIn("RULE-SET,cn-domains,DIRECT", clash["rules"])

    def test_cross_client_hardening(self):
        """各端都必须具备等价的防泄漏加固项。"""
        clash = build_clash_stash(self.clash_tpl, {}, "clash")
        stash = build_clash_stash(self.stash_tpl, {}, "stash")
        for cfg, label in ((clash, "clash"), (stash, "stash")):
            # 不得拦截 STUN 端口，避免影响通话与游戏语音
            self.assertFalse(
                any(r.startswith("DST-PORT,3478,REJECT") for r in cfg["rules"]),
                f"{label} 不应拦截 STUN 端口",
            )
            self.assertTrue(cfg["tun"]["enable"], f"{label} TUN 未启用")
            self.assertTrue(cfg["tun"]["auto-route"], f"{label} 未接管全部路由")
            self.assertTrue(cfg["tun"]["strict-route"], f"{label} 未启用严格路由")
            self.assertIn("any:53", cfg["tun"]["dns-hijack"], f"{label} 未劫持 DNS")

        surge = build_ini_based_client(self.surge_tpl, {}, "surge")
        for key in (
            "include-all-networks = true",
            "include-local-networks = false",
            "udp-policy-not-supported-behaviour = REJECT",
            "tun-excluded-routes =",
        ):
            self.assertIn(key, surge, f"Surge 缺少 {key}")

        loon = build_ini_based_client(self.loon_tpl, {}, "loon")
        self.assertIn("bypass-tun =", loon, "Loon 缺少 bypass-tun")
        self.assertIn("hijack-dns = *:53", loon, "Loon 缺少 DNS 劫持")

        qx = build_ini_based_client(self.qx_tpl, {}, "qx")
        self.assertIn("udp_drop_list = STUN", qx, "QX 缺少 STUN 丢弃")
        self.assertIn("excluded_routes =", qx, "QX 缺少 excluded_routes")
        self.assertIn("fallback_udp_policy = reject", qx, "QX 缺少 UDP 回退策略")

    def test_shadowrocket_hardening(self):
        """Shadowrocket 模板必须包含移动端防泄漏加固参数。"""
        text = build_ini_based_client(self.shadowrocket_tpl, {}, "shadowrocket")
        for line in (
            "compatibility-mode = 3",
            "include-all-networks = true",
            "include-local-networks = false",
            "hijack-dns = *:53",
            "stun-response-ip = 1.1.1.1",
            "stun-response-ipv6 = ::1",
            "udp-policy-not-supported-behaviour = REJECT",
        ):
            self.assertIn(line, text, f"Shadowrocket 缺少 {line}")

    def test_ini_cncidr_and_validator(self):
        """ini 四端使用 Surge 格式的 cn CIDR；内核校验失败时必须返回 False。"""
        for tpl, client in (
            (self.surge_tpl, "surge"),
            (self.shadowrocket_tpl, "shadowrocket"),
            (self.qx_tpl, "qx"),
        ):
            text = build_ini_based_client(tpl, {}, client)
            self.assertIn("surge-rules/release/ruleset/cncidr.txt", text, f"{client} 未使用 Surge 格式规则集")
            self.assertNotIn("clash-rules/release/cncidr.txt", text, f"{client} 仍在用 Clash 格式规则集")

        bad = Path(tempfile.mkstemp(suffix=".yaml")[1])
        bad.write_text("rules:\n- BOGUS-RULE,foo\n", encoding="utf-8")
        try:
            self.assertFalse(validate_with_mihomo(bad), "坏配置应校验失败")
        finally:
            bad.unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
