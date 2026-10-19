import subprocess
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def render(path):
    output = subprocess.check_output(
        ["kubectl", "kustomize", str(ROOT / path)], text=True
    )
    return {
        (resource["kind"], resource["metadata"]["name"]): resource
        for resource in yaml.safe_load_all(output) if resource
    }


class TailscaleOperatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.applications = render("argocd/applications")
        cls.egress = render("apps/tailscale-egress")

    def test_operator_application_pins_chart_and_creates_namespace(self):
        app = self.applications[("Application", "tailscale-operator")]["spec"]
        self.assertEqual(
            app["sources"][0]["repoURL"], "https://pkgs.tailscale.com/helmcharts"
        )
        self.assertEqual(app["sources"][0]["chart"], "tailscale-operator")
        self.assertEqual(app["sources"][0]["targetRevision"], "1.104.1")
        self.assertEqual(app["destination"]["namespace"], "tailscale")
        self.assertIn("CreateNamespace=true", app["syncPolicy"]["syncOptions"])
        self.assertIn("ServerSideApply=true", app["syncPolicy"]["syncOptions"])

    def test_egress_app_installs_proxy_group_and_model_service(self):
        app = self.applications[("Application", "tailscale-egress")]["spec"]
        self.assertEqual(app["source"]["path"], "apps/tailscale-egress")
        self.assertEqual(app["destination"]["namespace"], "hermes-workspace")
        self.assertEqual(
            app["ignoreDifferences"][0]["jsonPointers"], ["/spec/externalName"]
        )
        self.assertIn(
            "RespectIgnoreDifferences=true", app["syncPolicy"]["syncOptions"]
        )
        proxy_group = self.egress[("ProxyGroup", "egress-proxies")]
        self.assertEqual(proxy_group["spec"], {"type": "egress", "replicas": 2})
        service = self.egress[("Service", "mlops-node-7")]
        self.assertEqual(service["metadata"]["namespace"], "hermes-workspace")
        self.assertEqual(service["spec"]["type"], "ExternalName")
        self.assertEqual(
            service["metadata"]["annotations"],
            {
                "tailscale.com/tailnet-fqdn": "mlops-node-7.tailb11f97.ts.net",
                "tailscale.com/proxy-group": "egress-proxies",
            },
        )
        self.assertEqual(service["spec"]["ports"][0]["port"], 8080)


if __name__ == "__main__":
    unittest.main()
