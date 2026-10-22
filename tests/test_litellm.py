import subprocess
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class LiteLLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = yaml.safe_load((ROOT / "apps/litellm/values.yaml").read_text())

    def test_values_have_valid_litellm_structure(self):
        self.assertIsInstance(self.values, dict)

        proxy_config = self.values.get("proxy_config")
        self.assertIsInstance(proxy_config, dict)

        model_list = proxy_config.get("model_list")
        self.assertIsInstance(model_list, list)
        self.assertTrue(model_list)

        for entry in model_list:
            with self.subTest(model=entry.get("model_name") if isinstance(entry, dict) else None):
                self.assertIsInstance(entry, dict)
                if not isinstance(entry, dict):
                    continue
                self.assertIsInstance(entry.get("model_name"), str)
                self.assertTrue(entry["model_name"].strip())

                params = entry.get("litellm_params")
                self.assertIsInstance(params, dict)
                if not isinstance(params, dict):
                    continue
                for key in ("model",):
                    with self.subTest(parameter=key):
                        self.assertIsInstance(params.get(key), str)
                        self.assertTrue(params[key].strip())

                if params["model"] == "auto_router/complexity_router":
                    self.assertIsInstance(
                        params.get("complexity_router_default_model"), str
                    )
                    self.assertIsInstance(params.get("complexity_router_config"), dict)
                else:
                    for key in ("api_base", "api_key"):
                        with self.subTest(parameter=key):
                            self.assertIsInstance(params.get(key), str)
                            self.assertTrue(params[key].strip())

                if "model_info" in entry:
                    self.assertIsInstance(entry["model_info"], dict)

    def test_application_manifests_render_as_valid_yaml(self):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/applications")],
            text=True,
        )
        resources = [resource for resource in yaml.safe_load_all(rendered) if resource]

        self.assertTrue(resources)
        for resource in resources:
            with self.subTest(kind=resource.get("kind") if isinstance(resource, dict) else None):
                self.assertIsInstance(resource, dict)
                if not isinstance(resource, dict):
                    continue
                for key in ("apiVersion", "kind", "metadata"):
                    self.assertIn(key, resource)
                self.assertIsInstance(resource["metadata"], dict)

    def test_deepseek_replaces_every_node7_and_node9_deployment(self):
        deployments = self.values["proxy_config"]["model_list"]
        deepseek = [
            entry for entry in deployments
            if any(
                f"mlops-node-{node}." in entry["litellm_params"].get("api_base", "")
                for node in (7, 9)
            )
        ]
        self.assertEqual(len(deepseek), 12)
        for entry in deepseek:
            with self.subTest(model=entry["model_name"]):
                params = entry["litellm_params"]
                self.assertEqual(params["model"], "openai/deepseek-v4.1-flash")
                self.assertIn(
                    params["api_base"],
                    {
                        f"https://mlops-node-{node}.tailb11f97.ts.net/v1"
                        for node in (7, 9)
                    },
                )
                self.assertNotIn("extra_body", params)
                self.assertNotIn("ssl_verify", params)
                info = entry["model_info"]
                self.assertEqual(info["max_input_tokens"] + info["max_output_tokens"], 65536)
                self.assertTrue(info["supports_vision"])
                self.assertTrue(info["supports_reasoning"])
        direct = [e["model_name"] for e in deepseek if e["model_name"].startswith("mlops-node-")]
        self.assertEqual(
            set(direct),
            {f"mlops-node-{node}/DeepSeek-4.1-Flash-1017-64K" for node in (7, 9)},
        )

    def test_deepseek_pool_and_aliases_never_fall_back_to_other_families(self):
        config = self.values["proxy_config"]
        pool = [e for e in config["model_list"] if e["model_name"] == "deepseek"]
        self.assertEqual(len(pool), 2)
        self.assertEqual(
            {e["litellm_params"]["api_base"] for e in pool},
            {f"https://mlops-node-{node}.tailb11f97.ts.net/v1" for node in (7, 9)},
        )
        for entry in pool:
            self.assertEqual(entry["litellm_params"]["order"], 1)
            self.assertEqual(entry["litellm_params"]["max_parallel_requests"], 2)
        router = config["router_settings"]
        self.assertEqual(router["routing_strategy"], "least-busy")
        self.assertEqual(
            router["routing_groups"],
            [{
                "group_name": "deepseek-balanced",
                "models": ["deepseek"],
                "routing_strategy": "simple-shuffle",
            }],
        )
        for alias in ("high-performance", "deepseek-flash"):
            self.assertEqual(router["model_group_alias"][alias], "deepseek")
        self.assertFalse(any("deepseek" in fallback for fallback in router["fallbacks"]))

    def test_auto_router_tiers_and_all_references_exist(self):
        config = self.values["proxy_config"]
        names = {entry["model_name"] for entry in config["model_list"]}
        for entry in config["model_list"]:
            params = entry["litellm_params"]
            if params["model"] != "auto_router/complexity_router":
                continue
            tiers = params["complexity_router_config"]["tiers"]
            self.assertEqual(tiers["COMPLEX"], "deepseek")
            self.assertEqual(tiers["REASONING"], "deepseek")
            self.assertTrue(set(tiers.values()) <= names)
            self.assertIn(params["complexity_router_default_model"], names)
        for alias in config["router_settings"]["model_group_alias"].values():
            self.assertIn(alias["model"] if isinstance(alias, dict) else alias, names)
        for fallback in config["router_settings"]["fallbacks"]:
            for source, targets in fallback.items():
                self.assertIn(source, names)
                self.assertTrue(set(targets) <= names)

    def test_semantic_groups_have_no_duplicate_hosts(self):
        groups = {}
        for entry in self.values["proxy_config"]["model_list"]:
            params = entry["litellm_params"]
            if "api_base" in params:
                groups.setdefault(entry["model_name"], []).append(params["api_base"])
        for name, hosts in groups.items():
            with self.subTest(group=name):
                self.assertEqual(len(hosts), len(set(hosts)))
