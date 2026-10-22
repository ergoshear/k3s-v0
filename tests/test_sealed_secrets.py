from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
PINNED_REVISION = "5703fa2f519fa0604d68ca6b003fd2a01cecdcb2"


class SealedSecretsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/applications")],
            text=True,
        )
        cls.applications = {
            resource["metadata"]["name"]: resource
            for resource in yaml.safe_load_all(rendered)
            if resource and resource.get("kind") == "Application"
        }

    def test_controller_is_managed_by_argocd_from_pinned_chart_source(self):
        application = self.applications["sealed-secrets"]
        spec = application["spec"]
        self.assertEqual(
            spec["source"]["repoURL"],
            "https://github.com/bitnami/sealed-secrets.git",
        )
        self.assertEqual(spec["source"]["targetRevision"], PINNED_REVISION)
        self.assertEqual(spec["source"]["path"], "helm/sealed-secrets")
        self.assertEqual(
            spec["source"]["helm"]["releaseName"],
            "sealed-secrets-controller",
        )
        self.assertEqual(
            spec["source"]["helm"]["parameters"],
            [
                {
                    "name": "fullnameOverride",
                    "value": "sealed-secrets-controller",
                }
            ],
        )
        self.assertEqual(spec["destination"]["namespace"], "kube-system")
        self.assertTrue(spec["syncPolicy"]["automated"]["enabled"])
        self.assertIn("ServerSideApply=true", spec["syncPolicy"]["syncOptions"])

    def test_frontend_deployment_uses_image_pull_secret(self):
        deployment = yaml.safe_load(
            (ROOT / "apps/frontend-website/deployment.yaml").read_text()
        )
        pod_spec = deployment["spec"]["template"]["spec"]
        self.assertEqual(
            pod_spec["imagePullSecrets"],
            [{"name": "ghcr-registry-credentials"}],
        )
        self.assertEqual(
            pod_spec["containers"][0]["image"].split(":")[0],
            "ghcr.io/ergoshear/frontend-website",
        )


if __name__ == "__main__":
    unittest.main()
