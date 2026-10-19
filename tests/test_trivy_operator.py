import subprocess
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PLANE_TOLERATION = {
    "key": "node-role.kubernetes.io/control-plane",
    "operator": "Exists",
    "effect": "NoSchedule",
}


class TrivyOperatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/applications")], text=True
        )
        cls.applications = {
            (resource["kind"], resource["metadata"]["name"]): resource
            for resource in yaml.safe_load_all(rendered) if resource
        }
        cls.application = cls.applications[("Application", "trivy-operator")]
        cls.values = yaml.safe_load(
            (ROOT / "apps/trivy-operator/values.yaml").read_text()
        )

    def test_parent_application_includes_child_applications(self):
        parent = yaml.safe_load((ROOT / "argocd/kustomization.yaml").read_text())
        self.assertIn("applications", parent["resources"])
        self.assertIn(
            ("Application", "kube-prometheus-stack"), self.applications
        )

    def test_chart_is_pinned_and_reads_values_from_this_repository(self):
        chart, values = self.application["spec"]["sources"]
        self.assertEqual(chart["repoURL"], "https://aquasecurity.github.io/helm-charts/")
        self.assertEqual(chart["chart"], "trivy-operator")
        self.assertEqual(chart["targetRevision"], "0.37.0")
        self.assertEqual(
            chart["helm"]["valueFiles"],
            ["$values/apps/trivy-operator/values.yaml"],
        )
        self.assertEqual(values["repoURL"], "git@github.com:ergoshear/k3s.git")
        self.assertEqual(values["targetRevision"], "HEAD")
        self.assertEqual(values["ref"], "values")

    def test_application_syncs_into_a_dedicated_namespace(self):
        spec = self.application["spec"]
        self.assertEqual(spec["destination"]["namespace"], "trivy-system")
        self.assertTrue(spec["syncPolicy"]["automated"]["enabled"])
        self.assertTrue(spec["syncPolicy"]["automated"]["prune"])
        self.assertTrue(spec["syncPolicy"]["automated"]["selfHeal"])
        self.assertIn("CreateNamespace=true", spec["syncPolicy"]["syncOptions"])

    def test_scans_all_namespaces_with_control_plane_tolerations(self):
        self.assertEqual(self.values["targetNamespaces"], "")
        self.assertEqual(self.values["operator"]["scanJobsConcurrentLimit"], 2)
        self.assertEqual(self.values["tolerations"], [CONTROL_PLANE_TOLERATION])
        self.assertEqual(
            self.values["trivyOperator"]["scanJobTolerations"],
            [CONTROL_PLANE_TOLERATION],
        )
        self.assertEqual(
            self.values["nodeCollector"]["tolerations"],
            [CONTROL_PLANE_TOLERATION],
        )


if __name__ == "__main__":
    unittest.main()
