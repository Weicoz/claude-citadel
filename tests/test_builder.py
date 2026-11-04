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


class TestClaudeSubHubBuilder(unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()
