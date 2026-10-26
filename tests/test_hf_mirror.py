from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def render(path):
    output = subprocess.check_output(
        ["kubectl", "kustomize", str(ROOT / path)], text=True
    )
    return {
        (resource["kind"], resource["metadata"]["name"]): resource
        for resource in yaml.safe_load_all(output)
        if resource
    }


class HfMirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = render("apps/hf-mirror")
        cls.applications = render("argocd/applications")

    def test_application_is_registered_and_auto_syncs(self):
        application = self.applications[("Application", "hf-mirror")]
        self.assertEqual(application["spec"]["source"]["path"], "apps/hf-mirror")
        self.assertEqual(application["spec"]["destination"]["namespace"], "hf-mirror")
        self.assertTrue(application["spec"]["syncPolicy"]["automated"]["enabled"])
        self.assertIn("CreateNamespace=true", application["spec"]["syncPolicy"]["syncOptions"])

    def test_mirror_is_pinned_to_node_five_and_persistent_storage(self):
        deployment = self.resources[("Deployment", "hf-mirror")]
        pod_spec = deployment["spec"]["template"]["spec"]
        self.assertEqual(
            pod_spec["nodeSelector"], {"kubernetes.io/hostname": "mlops-node-5"}
        )
        self.assertEqual(
            pod_spec["volumes"][0]["persistentVolumeClaim"]["claimName"],
            "hf-mirror-cache",
        )
        volume = self.resources[("PersistentVolume", "hf-mirror-cache")]
        self.assertEqual(volume["spec"]["hostPath"]["path"], "/srv/storage/hf-mirror-cache")
        self.assertEqual(
            volume["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0][
                "matchExpressions"
            ][0],
            {
                "key": "kubernetes.io/hostname",
                "operator": "In",
                "values": ["mlops-node-5"],
            },
        )
        pvc = self.resources[("PersistentVolumeClaim", "hf-mirror-cache")]
        self.assertEqual(pvc["spec"]["volumeName"], "hf-mirror-cache")
        self.assertEqual(pvc["spec"]["storageClassName"], "hf-mirror-local")

    def test_ingress_uses_tls_for_hf_domain(self):
        ingress = self.resources[("Ingress", "hf-mirror")]
        self.assertEqual(ingress["spec"]["rules"][0]["host"], "hf.ergoshear.dev")
        self.assertEqual(ingress["spec"]["tls"][0]["secretName"], "hf-mirror-tls")
        certificate = self.resources[("Certificate", "hf-mirror-tls")]
        self.assertEqual(certificate["spec"]["dnsNames"], ["hf.ergoshear.dev"])


if __name__ == "__main__":
    unittest.main()
