import subprocess
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
REVISION = "e320cabdf0a67aa52cafe2ede1b4d10ad66dafad"


class LensTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/applications")], text=True
        )
        cls.application = next(
            resource for resource in yaml.safe_load_all(rendered)
            if resource and resource["metadata"]["name"] == "lens"
        )
        cls.values = yaml.safe_load((ROOT / "apps/lens/values.yaml").read_text())

    def test_chart_and_image_use_same_pinned_source(self):
        chart, values = self.application["spec"]["sources"]
        self.assertEqual(chart["repoURL"], "https://github.com/BerriAI/lens.git")
        self.assertEqual(chart["targetRevision"], REVISION)
        self.assertEqual(chart["path"], "helm/lens")
        self.assertEqual(
            chart["helm"]["valueFiles"], ["$values/apps/lens/values.yaml"]
        )
        self.assertEqual(values["ref"], "values")
        self.assertEqual(
            self.values["image"]["repository"], "ghcr.io/ergoshear/lens"
        )
        self.assertEqual(self.values["image"]["tag"], REVISION)
        self.assertRegex(self.values["image"]["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            self.values["imagePullSecrets"], [{"name": "lens-ghcr"}]
        )
        self.assertEqual(
            self.values["nodeSelector"]["kubernetes.io/arch"], "amd64"
        )

    def test_sync_preserves_generated_credentials(self):
        spec = self.application["spec"]
        self.assertEqual(spec["destination"]["namespace"], "lens")
        self.assertTrue(spec["syncPolicy"]["automated"]["enabled"])
        self.assertIn(
            "RespectIgnoreDifferences=true", spec["syncPolicy"]["syncOptions"]
        )
        self.assertEqual(
            spec["ignoreDifferences"],
            [
                {
                    "group": "",
                    "kind": "Secret",
                    "name": name,
                    "namespace": "lens",
                    "jsonPointers": ["/data"],
                }
                for name in ("lens-admin", "lens-clickhouse")
            ],
        )

    def test_tls_ingress_and_persistent_storage(self):
        self.assertEqual(self.values["publicUrl"], "https://lens.ergoshear.dev")
        ingress = self.values["ingress"]
        self.assertTrue(ingress["enabled"])
        self.assertEqual(ingress["className"], "traefik")
        self.assertEqual(ingress["host"], "lens.ergoshear.dev")
        self.assertEqual(
            ingress["annotations"]["cert-manager.io/cluster-issuer"],
            "letsencrypt-route53",
        )
        self.assertEqual(
            ingress["annotations"]["external-dns.kubernetes.io/target"],
            "k3s.ergoshear.dev",
        )
        self.assertEqual(
            ingress["tls"],
            [{"secretName": "lens-tls", "hosts": ["lens.ergoshear.dev"]}],
        )
        self.assertEqual(self.values["clickhouse"]["storage"], "20Gi")
        self.assertEqual(
            self.values["clickhouse"]["storageClassName"], "local-path"
        )


if __name__ == "__main__":
    unittest.main()
