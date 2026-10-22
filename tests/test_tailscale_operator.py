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

    def test_egress_app_installs_proxy_group_and_model_services(self):
        app = self.applications[("Application", "tailscale-egress")]["spec"]
        self.assertEqual(app["source"]["path"], "apps/tailscale-egress")
        self.assertEqual(app["destination"]["namespace"], "hermes-workspace")
        self.assertIn(
            "RespectIgnoreDifferences=true", app["syncPolicy"]["syncOptions"]
        )
        proxy_group = self.egress[("ProxyGroup", "egress-proxies")]
        self.assertEqual(proxy_group["spec"], {"type": "egress", "replicas": 2})
        nodes = ("mlops-node-4", "mlops-node-6", "mlops-node-7", "mlops-node-9")
        for node in nodes:
            service = self.egress[("Service", node)]
            self.assertEqual(service["metadata"]["namespace"], "hermes-workspace")
            self.assertEqual(service["spec"]["type"], "ExternalName")
            self.assertEqual(
                service["metadata"]["annotations"],
                {
                    "tailscale.com/tailnet-fqdn": f"{node}.tailb11f97.ts.net",
                    "tailscale.com/proxy-group": "egress-proxies",
                },
            )
            expected_ports = {443, 8080} if node in ("mlops-node-7", "mlops-node-9") else {8080}
            self.assertEqual(
                {port["port"] for port in service["spec"]["ports"]}, expected_ports
            )
        self.assertEqual(
            {item["name"] for item in app["ignoreDifferences"]},
            set(nodes),
        )
        self.assertTrue(
            all(
                item["jsonPointers"] == ["/spec/externalName"]
                for item in app["ignoreDifferences"]
            )
        )

    def test_magicdns_forwards_tailnet_queries_to_pinned_nameserver(self):
        dns = self.egress[("DNSConfig", "ts-dns")]["spec"]["nameserver"]
        self.assertEqual(
            dns["image"], {"repo": "tailscale/k8s-nameserver", "tag": "v1.104.1"}
        )
        config = self.egress[("ConfigMap", "coredns-custom")]
        self.assertEqual(config["metadata"]["namespace"], "kube-system")
        server = config["data"]["tailnet.server"]
        self.assertIn("tailb11f97.ts.net:53", server)
        self.assertIn(f'forward . {dns["service"]["clusterIP"]}', server)


if __name__ == "__main__":
    unittest.main()
