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

    def test_ui_login_redirects_to_sso_and_disables_environment_credentials(self):
        self.assertEqual(
            self.values["envVars"]["AUTO_REDIRECT_UI_LOGIN_TO_SSO"], "true",
        )
        self.assertIs(
            self.values["proxy_config"]["general_settings"]["disable_env_credential_login"],
            True,
        )

    def test_studioflow_tts_backends_use_matching_tailscale_ports(self):
        models = {
            entry["model_name"]: entry
            for entry in self.values["proxy_config"]["model_list"]
            if entry["model_name"] in {
                "tts-christopher-fish", "tts-1-hd", "whisper-large-v3",
            }
        }
        expected = {
            "tts-christopher-fish": ("openai/fish-speech-1.5", 8001, "audio_speech"),
            "tts-1-hd": ("openai/zonos-v0.1-transformer", 8002, "audio_speech"),
            "whisper-large-v3": ("openai/whisper-large-v3", 8003, "audio_transcription"),
        }
        self.assertEqual(set(models), set(expected))
        for model_name, (model, port, mode) in expected.items():
            with self.subTest(model=model_name):
                entry = models[model_name]
                self.assertEqual(entry["litellm_params"], {
                    "model": model,
                    "api_base": f"https://mlops-node-3.tailb11f97.ts.net:{port}/v1",
                    "api_key": "sk-rx9070",
                })
                expected_model_info = {"mode": mode}
                if mode == "audio_speech":
                    expected_model_info["health_check_voice"] = "af_alloy"
                self.assertEqual(entry["model_info"], expected_model_info)

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
        self.assertEqual(len(deepseek), 6)
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
                node7 = "mlops-node-7." in params["api_base"]
                self.assertEqual(info["max_input_tokens"], 24576 if node7 else 49152)
                self.assertEqual(info["max_output_tokens"], 8192 if node7 else 16384)
                self.assertEqual(info["max_tokens"], 8192 if node7 else 16384)
                self.assertEqual(info["max_input_tokens"] + info["max_output_tokens"],
                                 32768 if node7 else 65536)
                self.assertTrue(info["supports_vision"])
                self.assertTrue(info["supports_reasoning"])
        direct = [e["model_name"] for e in deepseek if e["model_name"].startswith("mlops-node-")]
        self.assertEqual(
            set(direct),
            {"mlops-node-7/DeepSeek-4.1-Flash-1017-32K",
             "mlops-node-9/DeepSeek-4.1-Flash-1017-64K"},
        )

    def test_strata_replaces_all_node4_and_node6_routes(self):
        routes = [
            entry for entry in self.values["proxy_config"]["model_list"]
            if any(f"mlops-node-{node}." in entry["litellm_params"].get("api_base", "")
                   for node in (4, 6))
        ]
        self.assertEqual(len(routes), 14)
        for entry in routes:
            with self.subTest(model=entry["model_name"]):
                params = entry["litellm_params"]
                self.assertEqual(params["model"], "openai/qwen3.8-flash-next-iq3_s")
                self.assertEqual(params["api_key"], "os.environ/STRATA_API_KEY")
                self.assertIn(params["api_base"], {
                    f"https://mlops-node-{node}.tailb11f97.ts.net/v1"
                    for node in (4, 6)
                })
                extra_body = params.get("extra_body", {})
                if "reasoning_budget" in extra_body:
                    self.assertEqual(extra_body["reasoning_budget"], 0)
                    self.assertFalse(
                        extra_body["chat_template_kwargs"]["enable_thinking"]
                    )
                info = entry["model_info"]
                self.assertEqual(info["max_input_tokens"],
                                 98304 if "mlops-node-6." in params["api_base"] else 229376)
                self.assertEqual(info["max_output_tokens"], 32768)
                self.assertEqual(info["max_tokens"], 32768)
                self.assertTrue(info["supports_vision"])
                if entry["model_name"].startswith("mlops-node-"):
                    self.assertIn("/Strata-Qwen3.8-Flash-Next-IQ3_S-", entry["model_name"])

    def test_nodes4_and6_have_current_strata_presets(self):
        models = {entry["model_name"]: entry
                  for entry in self.values["proxy_config"]["model_list"]}
        suffixes = {"Low", "Medium", "NoThink"}
        for node, max_input_tokens in ((4, 229376), (6, 98304)):
            prefix = f"mlops-node-{node}/Strata-Qwen3.8-Flash-Next-IQ3_S-"
            with self.subTest(node=node):
                self.assertEqual(
                    {name.removeprefix(prefix) for name in models if name.startswith(prefix)},
                    suffixes,
                )
                for suffix in suffixes:
                    entry = models[f"{prefix}{suffix}"]
                    info = entry["model_info"]
                    self.assertEqual(info["max_input_tokens"], max_input_tokens)
                    self.assertEqual(info["max_output_tokens"], 32768)
                    self.assertTrue(info["supports_vision"])
                no_think = models[f"{prefix}NoThink"]["litellm_params"]["extra_body"]
                self.assertFalse(no_think["chat_template_kwargs"]["enable_thinking"])
                self.assertEqual(no_think["reasoning_budget"], 0)

    def test_deepseek_pool_priority_and_aliases_are_explicit(self):
        config = self.values["proxy_config"]
        pool = [e for e in config["model_list"] if e["model_name"] == "deepseek"]
        self.assertEqual(len(pool), 2)
        self.assertEqual(
            {e["litellm_params"]["api_base"] for e in pool},
            {f"https://mlops-node-{node}.tailb11f97.ts.net/v1" for node in (7, 9)},
        )
        for entry in pool:
            expected_order = 1 if "mlops-node-9." in entry["litellm_params"]["api_base"] else 2
            self.assertEqual(entry["litellm_params"]["order"], expected_order)
            self.assertEqual(entry["litellm_params"]["max_parallel_requests"], 2)
        router = config["router_settings"]
        self.assertEqual(router["routing_strategy"], "least-busy")
        self.assertEqual(
            router["model_group_alias"]["o3-mini"]["model"],
            "deepseek",
        )
        self.assertIn({"deepseek": ["architect", "coder"]}, router["fallbacks"])

    def test_long_context_pool_uses_only_strata_hosts(self):
        config = self.values["proxy_config"]
        name = "long-context"
        pool = [entry for entry in config["model_list"] if entry["model_name"] == name]
        self.assertEqual(len(pool), 2)
        self.assertEqual(
            {entry["litellm_params"]["api_base"] for entry in pool},
            {
                f"https://mlops-node-{node}.tailb11f97.ts.net/v1"
                for node in (4, 6)
            },
        )
        for entry in pool:
            params = entry["litellm_params"]
            expected_order = 1 if "mlops-node-4." in params["api_base"] else 2
            self.assertEqual(params["order"], expected_order)
            self.assertEqual(params["max_parallel_requests"], 2)
            self.assertEqual(params["extra_body"]["chat_template_kwargs"]["reasoning_effort"],
                             "low")
            self.assertTrue(entry["model_info"]["supports_function_calling"])
        self.assertIn(
            {name: ["coder"]},
            config["router_settings"]["fallbacks"],
        )

    def test_auto_router_tiers_and_all_references_exist(self):
        config = self.values["proxy_config"]
        names = {entry["model_name"] for entry in config["model_list"]}
        for entry in config["model_list"]:
            params = entry["litellm_params"]
            if params["model"] != "auto_router/complexity_router":
                continue
            tiers = params["complexity_router_config"]["tiers"]
            self.assertEqual(tiers["COMPLEX"], "architect")
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
