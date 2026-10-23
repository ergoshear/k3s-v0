from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class SecurityBoundaryTests(unittest.TestCase):
    def policies(self, app):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "apps" / app)], text=True
        )
        return {x["metadata"]["name"]: x for x in yaml.safe_load_all(rendered)
                if x and x["kind"] == "NetworkPolicy"}

    def test_private_namespaces_default_deny_ingress_without_blocking_egress(self):
        for app, namespace in (("hermes-workspace", "hermes-workspace"),
                               ("lens", "lens"),
                               ("kube-prometheus-stack", "monitoring")):
            with self.subTest(app=app):
                policies = self.policies(app)
                deny = policies["default-deny-ingress"]["spec"]
                self.assertEqual(deny["podSelector"], {})
                self.assertNotIn("ingress", deny)
                for policy in policies.values():
                    self.assertEqual(policy["metadata"]["namespace"], namespace)
                    self.assertEqual(policy["spec"]["policyTypes"], ["Ingress"])
                    self.assertNotIn("egress", policy["spec"])
        for app in ("lens", "kube-prometheus-stack"):
            application = yaml.safe_load(
                (ROOT / "argocd/applications" / (app + ".yaml")).read_text()
            )
            source = application["spec"]["sources"][1]
            self.assertEqual(source["ref"], "values")
            self.assertEqual(source["path"], "apps/" + app)

    def test_hermes_exposes_only_workspace_port_to_ingress_host(self):
        policies = self.policies("hermes-workspace")
        self.assertEqual(set(policies), {"default-deny-ingress", "workspace-ingress"})
        spec = policies["workspace-ingress"]["spec"]
        self.assertEqual(spec["podSelector"]["matchLabels"],
                         {"app.kubernetes.io/name": "hermes-workspace"})
        self.assertEqual(spec["ingress"], [{
            "from": [{"ipBlock": {"cidr": "100.126.105.69/32"}},
                     {"ipBlock": {"cidr": "10.42.0.0/32"}}],
            "ports": [{"protocol": "TCP", "port": 3000}],
        }])

    def test_lens_isolates_clickhouse_and_limits_telemetry_clients(self):
        policies = self.policies("lens")
        self.assertEqual(set(policies),
                         {"default-deny-ingress", "lens-ingress", "clickhouse-ingress"})
        web = policies["lens-ingress"]["spec"]
        self.assertEqual(web["podSelector"]["matchLabels"],
                         {"app.kubernetes.io/component": "lens",
                          "app.kubernetes.io/instance": "lens"})
        rule = web["ingress"][0]
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 4318}])
        self.assertEqual(rule["from"][0], {"ipBlock": {"cidr": "100.126.105.69/32"}})
        self.assertEqual(rule["from"][1], {"ipBlock": {"cidr": "10.42.0.0/32"}})
        self.assertEqual(rule["from"][2:], [
            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": app}},
             "podSelector": {"matchLabels": {"app.kubernetes.io/name": app}}}
            for app in ("litellm", "hermes-workspace")
        ])
        database = policies["clickhouse-ingress"]["spec"]
        self.assertEqual(database["podSelector"]["matchLabels"],
                         {"app.kubernetes.io/component": "lens-clickhouse",
                          "app.kubernetes.io/instance": "lens"})
        self.assertEqual(database["ingress"], [{
            "from": [{"podSelector": web["podSelector"]}],
            "ports": [{"protocol": "TCP", "port": 8123}],
        }])

    def test_monitoring_preserves_internal_traffic_and_api_admission(self):
        policies = self.policies("kube-prometheus-stack")
        self.assertEqual(set(policies),
                         {"default-deny-ingress", "monitoring-internal",
                          "operator-admission"})
        internal = policies["monitoring-internal"]["spec"]
        self.assertEqual(internal["podSelector"], {})
        self.assertEqual(internal["ingress"], [{"from": [{"podSelector": {}}]}])
        admission = policies["operator-admission"]["spec"]
        self.assertEqual(admission["podSelector"]["matchLabels"], {
            "app.kubernetes.io/name": "kube-prometheus-stack-prometheus-operator",
        })
        self.assertEqual(admission["ingress"], [{
            "from": [{"ipBlock": {"cidr": "100.126.105.69/32"}},
                     {"ipBlock": {"cidr": "10.42.0.0/32"}}],
            "ports": [{"protocol": "TCP", "port": 10250}],
        }])

    def test_management_is_tailnet_only_but_litellm_is_public(self):
        middleware = list(yaml.safe_load_all(
            (ROOT / "argocd/platform/management-access.yaml").read_text()
        ))
        self.assertEqual({x["metadata"]["namespace"] for x in middleware},
                         {"argocd", "lens", "hermes-workspace"})
        for resource in middleware:
            if resource["metadata"]["name"] != "tailscale-only":
                continue
            self.assertEqual(resource["spec"]["ipAllowList"]["sourceRange"],
                             ["100.64.0.0/10", "fd7a:115c:a1e0::/48"])
        config = yaml.safe_load((ROOT / "argocd/platform/traefik-config.yaml").read_text())
        values = yaml.safe_load(config["spec"]["valuesContent"])
        self.assertTrue(values["hostNetwork"])
        self.assertEqual(values["service"]["spec"]["type"], "ClusterIP")
        self.assertEqual(values["ports"]["websecure"]["port"], 443)
        provider = values["providers"]["kubernetesIngress"]
        self.assertFalse(provider["publishedService"]["enabled"])
        self.assertEqual(provider["ingressEndpoint"]["ip"], "100.126.105.69")
        public = yaml.safe_load((ROOT / "apps/litellm/values.yaml").read_text())
        self.assertNotIn("traefik.ingress.kubernetes.io/router.middlewares",
                         public["ingress"]["annotations"])
        self.assertEqual(public["environmentSecrets"], ["inference-backend"])
        backend_models = [
            model for model in public["proxy_config"]["model_list"]
            if "api_base" in model["litellm_params"]
        ]
        self.assertTrue(backend_models)
        for model in backend_models:
            expected_key = (
                "os.environ/STRATA_API_KEY"
                if model["litellm_params"]["model"]
                == "openai/qwen3.8-flash-next-iq3_s"
                else "os.environ/INFERENCE_BACKEND_KEY"
            )
            self.assertEqual(model["litellm_params"]["api_key"], expected_key)

    def test_only_named_clients_can_enter_shared_egress_proxies(self):
        policy = yaml.safe_load((ROOT / "apps/tailscale-egress/network-policy.yaml").read_text())
        self.assertEqual(policy["spec"]["podSelector"]["matchLabels"]
                         ["tailscale.com/parent-resource"], "egress-proxies")
        clients = policy["spec"]["ingress"][0]["from"]
        self.assertEqual({x["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
                          for x in clients}, {"litellm", "hermes-workspace"})
        self.assertTrue(all(x["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
                            in {"litellm", "hermes-workspace"} for x in clients))
        # Operator-allocated targetPorts differ from the public service port 8080.
        self.assertNotIn("ports", policy["spec"]["ingress"][0])

    def test_dex_keys_allow_cluster_verification_without_opening_management(self):
        middleware = list(yaml.safe_load_all(
            (ROOT / "argocd/platform/management-access.yaml").read_text()
        ))
        keys = next(x for x in middleware
                    if x["metadata"]["name"] == "dex-keys-internal")
        self.assertEqual(keys["metadata"]["namespace"], "argocd")
        self.assertEqual(keys["spec"]["ipAllowList"]["sourceRange"],
                         ["100.64.0.0/10", "fd7a:115c:a1e0::/48", "10.42.0.0/16"])
        ingresses = list(yaml.safe_load_all(
            (ROOT / "argocd/platform/ingress.yaml").read_text()
        ))
        self.assertEqual(len(ingresses), 2)
        for ingress in ingresses:
            name = ingress["metadata"]["name"]
            annotation = ingress["metadata"]["annotations"][
                "traefik.ingress.kubernetes.io/router.middlewares"]
            paths = ingress["spec"]["rules"][0]["http"]["paths"]
            if name == "argocd-dex-keys":
                self.assertEqual(annotation, "argocd-dex-keys-internal@kubernetescrd")
                self.assertEqual(len(paths), 1)
                self.assertEqual(paths[0]["path"], "/api/dex/keys")
                self.assertEqual(paths[0]["pathType"], "Exact")
            else:
                self.assertEqual(name, "argocd-server")
                self.assertEqual(annotation, "argocd-tailscale-only@kubernetescrd")


if __name__ == "__main__":
    unittest.main()
