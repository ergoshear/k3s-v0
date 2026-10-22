from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/ergoshear/frontend-website"


class FrontendImageUpdateTests(unittest.TestCase):
    def test_frontend_tracks_latest_and_checks_registry_on_pod_start(self):
        deployment = yaml.safe_load(
            (ROOT / "apps/frontend-website/deployment.yaml").read_text()
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], f"{IMAGE}:latest")
        self.assertEqual(container["imagePullPolicy"], "Always")

    def test_only_frontend_latest_is_tracked_by_digest(self):
        updater = yaml.safe_load(
            (ROOT / "apps/argocd-image-updater/image-updater.yaml").read_text()
        )
        self.assertEqual(updater["spec"]["writeBackConfig"], {"method": "argocd"})
        applications = updater["spec"]["applicationRefs"]
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]["namePattern"], "frontend-website")
        image = applications[0]["images"][0]
        self.assertEqual(image["imageName"], f"{IMAGE}:latest")
        self.assertEqual(image["commonUpdateSettings"]["updateStrategy"], "digest")
        self.assertEqual(image["manifestTargets"], {"kustomize": {"name": IMAGE}})

    def test_parent_preserves_only_updater_owned_image_overrides(self):
        parent = yaml.safe_load((ROOT / "argocd/application.yaml").read_text())
        self.assertIn("RespectIgnoreDifferences=true", parent["spec"]["syncPolicy"]["syncOptions"])
        self.assertEqual(parent["spec"]["ignoreDifferences"], [{
            "group": "argoproj.io",
            "kind": "Application",
            "name": "frontend-website",
            "namespace": "argocd",
            "jsonPointers": ["/spec/source/kustomize/images"],
        }])
        application = yaml.safe_load(
            (ROOT / "argocd/applications/frontend-website.yaml").read_text()
        )
        self.assertEqual(application["spec"]["source"]["kustomize"], {})

    def test_updater_is_namespace_scoped_and_excludes_control_plane(self):
        values = yaml.safe_load(
            (ROOT / "apps/argocd-image-updater/values.yaml").read_text()
        )
        self.assertFalse(values["createClusterRoles"])
        self.assertEqual(values["config"]["watch.namespaces"], "argocd")
        self.assertEqual(values["config"]["interval"], "1m")
        self.assertEqual(
            values["config"]["registries"][0]["credentials"],
            "pullsecret:argocd/ghcr-registry-credentials",
        )
        expressions = values["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"][0]["matchExpressions"]
        self.assertIn(
            {"key": "node-role.kubernetes.io/control-plane", "operator": "DoesNotExist"},
            expressions,
        )

    def test_updater_application_is_registered_and_pinned(self):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/applications")], text=True
        )
        applications = {
            item["metadata"]["name"]: item for item in yaml.safe_load_all(rendered)
        }
        app = applications["argocd-image-updater"]
        self.assertEqual(app["spec"]["sources"][0]["targetRevision"], "1.3.2")
        self.assertEqual(
            app["spec"]["sources"][1]["path"], "apps/argocd-image-updater"
        )


if __name__ == "__main__":
    unittest.main()
