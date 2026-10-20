from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class SecurityBoundaryTests(unittest.TestCase):
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
        public = yaml.safe_load((ROOT / "apps/litellm/values.yaml").read_text())
        self.assertNotIn("traefik.ingress.kubernetes.io/router.middlewares",
                         public["ingress"]["annotations"])
        self.assertEqual(public["environmentSecrets"], ["inference-backend"])
        for model in public["proxy_config"]["model_list"]:
            self.assertEqual(model["litellm_params"]["api_key"],
                             "os.environ/INFERENCE_BACKEND_KEY")

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
