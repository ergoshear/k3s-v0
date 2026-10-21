import subprocess
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class LiteLLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/applications")], text=True
        )
        cls.applications = {
            (resource["kind"], resource["metadata"]["name"]): resource
            for resource in yaml.safe_load_all(rendered) if resource
        }
        cls.application = cls.applications[("Application", "litellm")]
        cls.values = yaml.safe_load((ROOT / "apps/litellm/values.yaml").read_text())

    def test_application_uses_pinned_oci_chart_and_repository_values(self):
        chart, values = self.application["spec"]["sources"]
        self.assertEqual(chart["repoURL"], "ghcr.io/berriai")
        self.assertEqual(chart["chart"], "litellm-helm")
        self.assertEqual(chart["targetRevision"], "1.106.0")
        self.assertEqual(
            chart["helm"]["valueFiles"], ["$values/apps/litellm/values.yaml"]
        )
        self.assertEqual(values["repoURL"], "git@github.com:ergoshear/k3s.git")
        self.assertEqual(values["targetRevision"], "HEAD")
        self.assertEqual(values["ref"], "values")

    def test_application_enables_sync_in_litellm_namespace(self):
        spec = self.application["spec"]
        self.assertEqual(spec["destination"]["namespace"], "litellm")
        self.assertTrue(spec["syncPolicy"]["automated"]["enabled"])
        self.assertTrue(spec["syncPolicy"]["automated"]["prune"])
        self.assertTrue(spec["syncPolicy"]["automated"]["selfHeal"])
        self.assertIn("CreateNamespace=true", spec["syncPolicy"]["syncOptions"])
        self.assertIn("ServerSideApply=true", spec["syncPolicy"]["syncOptions"])

    def test_ingress_serves_requested_tls_endpoint_through_traefik(self):
        ingress = self.values["ingress"]
        self.assertTrue(ingress["enabled"])
        self.assertEqual(ingress["className"], "traefik")
        self.assertEqual(ingress["hosts"][0]["host"], "litellm.ergoshear.dev")
        self.assertEqual(ingress["hosts"][0]["paths"][0]["path"], "/")
        self.assertEqual(ingress["tls"][0]["secretName"], "litellm-tls")
        self.assertEqual(
            ingress["tls"][0]["hosts"], ["litellm.ergoshear.dev"]
        )
        self.assertEqual(
            ingress["annotations"]["cert-manager.io/cluster-issuer"],
            "letsencrypt-route53",
        )
        self.assertEqual(
            ingress["annotations"]["external-dns.kubernetes.io/target"],
            "k3s.ergoshear.dev",
        )
        self.assertEqual(
            ingress["annotations"][
                "traefik.ingress.kubernetes.io/router.entrypoints"
            ],
            "websecure",
        )

    def test_argocd_preserves_existing_encryption_key_during_sync(self):
        spec = self.application["spec"]
        self.assertIn(
            {
                "group": "",
                "kind": "Secret",
                "name": "litellm-masterkey",
                "namespace": "litellm",
                "jsonPointers": ["/data/masterkey"],
            },
            spec["ignoreDifferences"],
        )
        self.assertIn(
            "RespectIgnoreDifferences=true", spec["syncPolicy"]["syncOptions"]
        )

    def test_root_redirects_to_dashboard_and_keeps_api_docs(self):
        self.assertEqual(
            {key: self.values["envVars"][key] for key in (
                "DOCS_URL", "ROOT_REDIRECT_URL", "STORE_MODEL_IN_DB"
            )},
            {
                "DOCS_URL": "/docs",
                "ROOT_REDIRECT_URL": "/ui/",
                "STORE_MODEL_IN_DB": "True",
            },
        )

    def test_saml_sso_persists_with_explicit_admin_identity(self):
        env = self.values["envVars"]
        self.assertEqual(
            env["SAML_IDP_METADATA_URL"],
            "https://portal.sso.us-east-2.amazonaws.com/saml/metadata/"
            "MjA0NDU5MDM4Njc2X2lucy02Njg0ZTBmZGY2OTFiNjMx",
        )
        self.assertEqual(env["PROXY_BASE_URL"], "https://litellm.ergoshear.dev")
        self.assertEqual(
            env["SAML_SP_ENTITY_ID"],
            "https://litellm.ergoshear.dev/sso/saml/metadata",
        )
        self.assertEqual(env["SAML_ATTRIBUTE_USER_ID"], "email")
        self.assertEqual(env["PROXY_ADMIN_ID"], "christopher@agilityqa.com")

    def test_proxy_registers_concrete_models_from_all_tailscale_backends(self):
        model_list = [
            entry for entry in self.values["proxy_config"]["model_list"]
            if entry["model_name"].startswith("mlops-node-")
        ]
        presets = {
            "mlops-node-4": (
                "Gemma-4-31B-QAT-Vision",
                "Gemma-4-31B-QAT-NoThink",
                "Qwen3.6-35B-A3B-DFlash-Low",
                "Qwen3.6-35B-A3B-DFlash-NoThink",
            ),
            "mlops-node-6": (
                "Muse-Glimmer-30B-128K",
                "Muse-Glimmer-30B-256K-VRAM",
                "Muse-Glimmer-30B-512K-Hybrid",
                "Muse-Glimmer-30B-Vision",
            ),
            "mlops-node-7": (
                "Qwen3.6-35B-A3B-DFlash-Low",
                "Qwen3.6-35B-A3B-DFlash-Medium",
                "Qwen3.6-35B-A3B-DFlash-NoThink",
            ),
            "mlops-node-9": (
                "Qwen3.6-35B-A3B-DFlash-512K-Low",
                "Qwen3.6-35B-A3B-DFlash-512K-NoThink",
                "Qwen3.8-27B-Low",
                "Qwen3.8-27B-Medium",
                "Qwen3.8-27B-NoThink",
            ),
        }

        self.assertEqual(len(model_list), 16)
        self.assertEqual(
            {entry["model_name"] for entry in model_list},
            {f"{node}/{model}" for node, models in presets.items() for model in models},
        )
        for entry in model_list:
            node, model = entry["model_name"].split("/", 1)
            expected_model = model
            if node == "mlops-node-7":
                expected_model = "Qwen3.6-35B-A3B-DFlash"
            elif node == "mlops-node-9" and model.startswith("Qwen3.6-"):
                expected_model = "Qwen3.6-35B-A3B-DFlash-512K"
            elif node == "mlops-node-9" and model.startswith("Qwen3.8-"):
                expected_model = "Qwen3.8-27B"
            self.assertEqual(
                entry["litellm_params"]["model"], f"openai/{expected_model}"
            )
            self.assertEqual(
                entry["litellm_params"]["api_base"],
                "http://192.168.202.17:8080/v1"
                if node == "mlops-node-7"
                else f"http://{node}.hermes-workspace.svc.cluster.local:8080/v1",
            )
            self.assertEqual(
                entry["litellm_params"]["api_key"],
                "none" if node == "mlops-node-7" else "os.environ/INFERENCE_BACKEND_KEY",
            )
        node7 = {
            entry["model_name"].split("/", 1)[1]: entry
            for entry in model_list
            if entry["model_name"].startswith("mlops-node-7/")
        }
        self.assertEqual(
            set(node7),
            {
                "Qwen3.6-35B-A3B-DFlash-Low",
                "Qwen3.6-35B-A3B-DFlash-Medium",
                "Qwen3.6-35B-A3B-DFlash-NoThink",
            },
        )
        self.assertEqual(
            node7["Qwen3.6-35B-A3B-DFlash-Low"]["litellm_params"]["extra_body"],
            {"chat_template_kwargs": {"reasoning_effort": "low"}},
        )
        self.assertEqual(
            node7["Qwen3.6-35B-A3B-DFlash-Medium"]["litellm_params"]["extra_body"],
            {"chat_template_kwargs": {"reasoning_effort": "medium"}},
        )
        self.assertEqual(
            node7["Qwen3.6-35B-A3B-DFlash-NoThink"]["litellm_params"],
            {
                "model": "openai/Qwen3.6-35B-A3B-DFlash",
                "api_base": "http://192.168.202.17:8080/v1",
                "api_key": "none",
                "temperature": 0.6,
                "top_p": 0.80,
                "extra_body": {
                    "reasoning_format": "none",
                    "chat_template_kwargs": {
                        "enable_thinking": False,
                        "reasoning_effort": "none",
                    },
                },
            },
        )
        self.assertEqual(
            self.values["proxy_config"]["general_settings"]["master_key"],
            "os.environ/PROXY_MASTER_KEY",
        )

    def test_direct_model_metadata_matches_context_budgets_and_capabilities(self):
        direct = {
            entry["model_name"]: entry["model_info"]
            for entry in self.values["proxy_config"]["model_list"]
            if entry["model_name"].startswith("mlops-node-")
        }
        self.assertEqual(len({info["id"] for info in direct.values()}), 16)
        for name, info in direct.items():
            node, model = name.split("/", 1)
            output, context = (32768, 262144)
            if node == "mlops-node-7":
                output, context = (32768, 262144)
            elif model == "Muse-Glimmer-30B-128K":
                output, context = (16384, 131072)
            elif model == "Muse-Glimmer-30B-Vision":
                output, context = (16384, 98304)
            elif model == "Muse-Glimmer-30B-512K-Hybrid" or model.startswith(
                "Qwen3.6-35B-A3B-DFlash-512K-"
            ):
                output, context = (32768, 524288)
            self.assertEqual(info["max_tokens"], output, name)
            if node != "mlops-node-7":
                self.assertEqual(info["max_output_tokens"], output, name)
            self.assertEqual(info["max_input_tokens"], context - output, name)
            self.assertTrue(info["supports_function_calling"], name)
            vision = "Vision" in model or model == "Gemma-4-31B-QAT-NoThink"
            if node != "mlops-node-7":
                self.assertEqual(info.get("supports_vision", False), vision, name)
            reasoning = "NoThink" not in model and model != "Qwen3-Coder-30B-A3B"
            if model == "Qwen3.6-35B-A3B-DFlash-NoThink" and node == "mlops-node-7":
                self.assertNotIn("supports_reasoning", info, name)
            elif node != "mlops-node-7":
                self.assertEqual(info.get("supports_reasoning", False), reasoning, name)

    def test_semantic_groups_reuse_backend_routes_and_metadata(self):
        entries = self.values["proxy_config"]["model_list"]
        expected = {
            "ergoshear-fast-edit": [
                ("mlops-node-4/Qwen3.6-35B-A3B-DFlash-NoThink", 1, 2),
            ],
            "ergoshear-coder": [
                ("mlops-node-4/Qwen3.6-35B-A3B-DFlash-Low", 1, None),
                ("mlops-node-6/Muse-Glimmer-30B-128K", 2, None),
                ("mlops-node-9/Qwen3.6-35B-A3B-DFlash-512K-Low", 2, None),
            ],
            "ergoshear-architect": [
                ("mlops-node-9/Qwen3.8-27B-Medium", 1, None),
            ],
            "ergoshear-vision": [
                ("mlops-node-6/Muse-Glimmer-30B-Vision", 1, None),
                ("mlops-node-4/Gemma-4-31B-QAT-Vision", 2, None),
            ],
            "ergoshear-long-context": [
                ("mlops-node-9/Qwen3.6-35B-A3B-DFlash-512K-Low", 1, None),
                ("mlops-node-4/Qwen3.6-35B-A3B-DFlash-Low", 2, None),
                ("mlops-node-6/Muse-Glimmer-30B-256K-VRAM", 3, None),
            ],
        }
        self.assertEqual(len(entries), 26)
        direct_by_name = {
            entry["model_name"]: entry for entry in entries
            if entry["model_name"].startswith("mlops-node-")
        }
        for group, deployments in expected.items():
            actual = [entry for entry in entries if entry["model_name"] == group]
            self.assertEqual(len(actual), len(deployments), group)
            for entry, (backend_name, order, weight) in zip(actual, deployments):
                params = entry["litellm_params"]
                backend_params = direct_by_name[backend_name]["litellm_params"]
                self.assertEqual(params["order"], order)
                self.assertEqual(params.get("weight"), weight)
                self.assertEqual(params["api_key"], "os.environ/INFERENCE_BACKEND_KEY")
                self.assertEqual(params["api_base"], backend_params["api_base"])
                self.assertEqual(params["model"], backend_params["model"])
                self.assertEqual(params.get("extra_body"), backend_params.get("extra_body"))
                self.assertEqual(params.get("temperature"), backend_params.get("temperature"))
                self.assertEqual(params.get("top_p"), backend_params.get("top_p"))
                self.assertEqual(
                    entry["model_info"],
                    {key: value for key, value in direct_by_name[backend_name]["model_info"].items()
                     if key != "id"},
                )

    def test_router_aliases_retries_and_fallbacks(self):
        router = self.values["proxy_config"]["router_settings"]
        self.assertEqual(router["routing_strategy"], "least-busy")
        self.assertTrue(router["enable_pre_call_checks"])
        self.assertEqual(router["num_retries"], 2)
        self.assertEqual(router["timeout"], 300)
        self.assertEqual(router["cooldown_time"], 15)
        self.assertEqual(
            router["model_group_alias"],
            {
                "fast": "ergoshear-fast-edit",
                "coder": "ergoshear-coder",
                "architect": "ergoshear-architect",
                "vision": "ergoshear-vision",
                "long-context": "ergoshear-long-context",
                "default": "ergoshear-coder",
                "gpt-4o": {"model": "ergoshear-coder", "hidden": True},
                "gpt-4o-mini": {"model": "ergoshear-fast-edit", "hidden": True},
                "o3-mini": {"model": "ergoshear-architect", "hidden": True},
                "claude-3-7-sonnet-latest": {
                    "model": "ergoshear-coder", "hidden": True,
                },
            },
        )
        self.assertEqual(
            router["fallbacks"],
            [
                {"ergoshear-fast-edit": ["ergoshear-coder"]},
                {"ergoshear-coder": ["ergoshear-fast-edit", "ergoshear-architect"]},
                {"ergoshear-vision": ["mlops-node-4/Gemma-4-31B-QAT-NoThink"]},
            ],
        )
        self.assertEqual(
            self.values["proxy_config"]["litellm_settings"],
            {
                "drop_params": True,
                "modify_params": True,
                "allowed_openai_params": [
                    "chat_template_kwargs", "reasoning_format", "reasoning_budget",
                ],
                "set_verbose": False,
                "request_timeout": 300,
            },
        )

    def test_database_and_migrations_run_before_litellm_resources(self):
        self.assertEqual(
            self.values["postgresql"]["commonAnnotations"][
                "argocd.argoproj.io/sync-wave"
            ],
            "-2",
        )
        migration_job = self.values["migrationJob"]
        self.assertFalse(migration_job["hooks"]["argocd"]["enabled"])
        self.assertEqual(
            migration_job["jobAnnotations"]["argocd.argoproj.io/sync-wave"],
            "-1",
        )
        self.assertEqual(
            migration_job["jobAnnotations"][
                "argocd.argoproj.io/sync-options"
            ],
            "Force=true,Replace=true",
        )
        self.assertIsNone(migration_job["ttlSecondsAfterFinished"])


if __name__ == "__main__":
    unittest.main()
