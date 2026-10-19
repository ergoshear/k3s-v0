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

    def test_root_redirects_to_dashboard_and_keeps_api_docs(self):
        self.assertEqual(
            self.values["envVars"],
            {
                "DOCS_URL": "/docs",
                "ROOT_REDIRECT_URL": "/ui/",
                "STORE_MODEL_IN_DB": "True",
            },
        )

    def test_proxy_discovers_models_from_all_tailscale_backends(self):
        model_list = self.values["proxy_config"]["model_list"]
        nodes = ("mlops-node-4", "mlops-node-6", "mlops-node-7", "mlops-node-9")

        self.assertEqual(
            {entry["model_name"] for entry in model_list},
            {f"{node}/*" for node in nodes},
        )
        for entry in model_list:
            node = entry["model_name"].split("/")[0]
            self.assertEqual(entry["litellm_params"]["model"], "openai/*")
            self.assertEqual(
                entry["litellm_params"]["api_base"],
                f"http://{node}.hermes-workspace.svc.cluster.local:8080/v1",
            )
            self.assertEqual(entry["litellm_params"]["api_key"], "not-required")
            self.assertTrue(entry["litellm_settings"]["check_provider_endpoint"])
        self.assertEqual(
            self.values["proxy_config"]["general_settings"]["master_key"],
            "os.environ/PROXY_MASTER_KEY",
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
