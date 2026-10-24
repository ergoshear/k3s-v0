from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class GhcrRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policies = {
            item["metadata"]["name"]: item
            for item in yaml.safe_load_all(
                subprocess.check_output(
                    ["kubectl", "kustomize", str(ROOT / "apps/ghcr-registry")],
                    text=True,
                )
            )
            if item["kind"] == "ClusterPolicy"
        }

    def test_existing_and_future_namespaces_receive_synchronized_credentials(self):
        policy = self.policies["synchronize-ghcr-credentials"]
        rule = policy["spec"]["rules"][0]
        self.assertEqual(rule["match"]["any"][0]["resources"]["kinds"], ["Namespace"])
        self.assertEqual(
            rule["exclude"]["any"][0]["resources"]["names"], ["ghcr-registry"]
        )
        generate = rule["generate"]
        self.assertTrue(generate["generateExisting"])
        self.assertTrue(generate["synchronize"])
        self.assertTrue(generate["orphanDownstreamOnPolicyDelete"])
        self.assertEqual(generate["name"], "ghcr-registry-credentials")
        self.assertEqual(generate["namespace"], "{{ request.object.metadata.name }}")
        self.assertEqual(
            generate["clone"],
            {"namespace": "ghcr-registry", "name": "ghcr-registry-credentials"},
        )

    def test_pod_injection_is_additive_and_independent_of_service_account(self):
        policy = self.policies["inject-ghcr-credentials"]
        self.assertEqual(
            policy["metadata"]["annotations"]["pod-policies.kyverno.io/autogen-controllers"],
            "none",
        )
        self.assertFalse(policy["spec"]["background"])
        self.assertEqual(policy["spec"]["webhookConfiguration"]["failurePolicy"], "Ignore")
        rule = policy["spec"]["rules"][0]
        self.assertEqual(
            rule["match"]["any"][0]["resources"],
            {"kinds": ["Pod"], "operations": ["CREATE"]},
        )
        self.assertEqual(
            rule["mutate"]["patchStrategicMerge"],
            {"spec": {"imagePullSecrets": [{"name": "ghcr-registry-credentials"}]}},
        )

    def test_policies_declare_kyverno_defaulted_fields(self):
        for name, policy in self.policies.items():
            with self.subTest(policy=name):
                spec = policy["spec"]
                self.assertTrue(spec["admission"])
                self.assertFalse(spec["emitWarning"])
                self.assertEqual(spec["validationFailureAction"], "Audit")
                for rule in spec["rules"]:
                    self.assertTrue(rule["skipBackgroundRequests"])

    def test_system_namespaces_are_not_excluded(self):
        values = yaml.safe_load((ROOT / "apps/kyverno/values.yaml").read_text())
        self.assertFalse(values["config"]["excludeKyvernoNamespace"])
        self.assertEqual(
            values["config"]["webhooks"]["namespaceSelector"]["matchExpressions"], []
        )
        self.assertEqual(
            set(values["config"]["resourceFiltersExclude"]),
            {
                "[*/*,kube-system,*]",
                "[*/*,kube-public,*]",
                "[*/*,kube-node-lease,*]",
            },
        )

    def test_applications_are_registered_and_pinned(self):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/applications")], text=True
        )
        apps = {
            item["metadata"]["name"]: item for item in yaml.safe_load_all(rendered)
        }
        self.assertEqual(apps["kyverno"]["spec"]["sources"][0]["targetRevision"], "3.9.2")
        self.assertEqual(
            apps["ghcr-registry"]["spec"]["source"]["path"], "apps/ghcr-registry"
        )
        self.assertIn(
            "ServerSideDiff=true",
            apps["kyverno"]["spec"]["syncPolicy"]["syncOptions"],
        )

    def test_both_controllers_can_read_secrets_and_background_can_synchronize(self):
        values = yaml.safe_load((ROOT / "apps/kyverno/values.yaml").read_text())
        for controller in ["admissionController", "backgroundController"]:
            rule = values[controller]["rbac"]["clusterRole"]["extraResources"][0]
            self.assertEqual(rule["apiGroups"], [""])
            self.assertEqual(rule["resources"], ["secrets"])
            self.assertTrue({"get", "list", "watch"}.issubset(rule["verbs"]))
        background = values["backgroundController"]["rbac"]["clusterRole"]
        self.assertTrue(
            {"create", "update", "patch", "delete"}.issubset(
                background["extraResources"][0]["verbs"]
            )
        )


if __name__ == "__main__":
    unittest.main()
