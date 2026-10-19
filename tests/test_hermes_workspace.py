import subprocess
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SECRET_NAME = "hermes-workspace-secrets"


def render(path):
    output = subprocess.check_output(
        ["kubectl", "kustomize", str(ROOT / path)], text=True
    )
    return {
        (resource["kind"], resource["metadata"]["name"]): resource
        for resource in yaml.safe_load_all(output) if resource
    }


class HermesWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.applications = render("argocd/applications")
        cls.resources = render("apps/hermes-workspace")
        cls.application = cls.applications[("Application", "hermes-workspace")]
        cls.deployment = cls.resources[("Deployment", "hermes-workspace")]
        cls.containers = {
            container["name"]: container
            for container in cls.deployment["spec"]["template"]["spec"]["containers"]
        }

    def test_child_application_is_included_and_auto_syncs(self):
        parent = yaml.safe_load((ROOT / "argocd/kustomization.yaml").read_text())
        self.assertIn("applications", parent["resources"])
        spec = self.application["spec"]
        self.assertEqual(spec["source"]["repoURL"], "git@github.com:ergoshear/k3s.git")
        self.assertEqual(spec["source"]["targetRevision"], "HEAD")
        self.assertEqual(spec["source"]["path"], "apps/hermes-workspace")
        self.assertEqual(spec["destination"]["namespace"], "hermes-workspace")
        self.assertTrue(spec["syncPolicy"]["automated"]["enabled"])
        self.assertTrue(spec["syncPolicy"]["automated"]["prune"])
        self.assertTrue(spec["syncPolicy"]["automated"]["selfHeal"])
        self.assertIn("CreateNamespace=true", spec["syncPolicy"]["syncOptions"])
        self.assertNotIn("ServerSideApply=true", spec["syncPolicy"]["syncOptions"])

    def test_deployment_pins_both_images_and_shares_persistent_state(self):
        self.assertEqual(
            self.containers["hermes-agent"]["image"],
            "nousresearch/hermes-agent:v0.22.2",
        )
        self.assertEqual(
            self.containers["hermes-workspace"]["image"],
            "ghcr.io/outsourc-e/hermes-workspace:main-c631425",
        )
        self.assertEqual(self.deployment["spec"]["strategy"]["type"], "Recreate")
        pod_spec = self.deployment["spec"]["template"]["spec"]
        self.assertEqual(pod_spec["securityContext"]["fsGroup"], 10010)
        mounts = {
            name: {
                (mount["name"], mount["mountPath"])
                for mount in container["volumeMounts"]
            }
            for name, container in self.containers.items()
        }
        self.assertIn(("hermes-agent-data", "/opt/data"), mounts["hermes-agent"])
        self.assertIn(
            ("hermes-agent-data", "/home/workspace/.hermes"),
            mounts["hermes-workspace"],
        )
        self.assertIn(
            ("hermes-workspace-files", "/workspace"),
            mounts["hermes-workspace"],
        )

    def test_initial_agent_config_uses_the_mlop_endpoint_and_model(self):
        config_map = self.resources[("ConfigMap", "hermes-agent-config")]
        config = yaml.safe_load(config_map["data"]["config.yaml"])
        self.assertEqual(
            config["model"],
            {
                "default": "Qwen3-Coder-30B-A3B",
                "provider": "custom",
                "base_url": "http://mlops-node-7.tailb11f97.ts.net:8080/v1",
            },
        )
        init_container = self.deployment["spec"]["template"]["spec"][
            "initContainers"
        ][0]
        self.assertEqual(init_container["name"], "initialize-agent-config")
        self.assertIn("if [ ! -e /opt/data/config.yaml ]", init_container["args"][0])
        self.assertIn(
            {"name": "hermes-agent-config", "mountPath": "/config", "readOnly": True},
            init_container["volumeMounts"],
        )

    def test_public_ui_requires_secret_and_uses_internal_gateway(self):
        workspace_env = {
            item["name"]: item
            for item in self.containers["hermes-workspace"]["env"]
        }
        agent_env = {
            item["name"]: item
            for item in self.containers["hermes-agent"]["env"]
        }
        self.assertEqual(
            workspace_env["HERMES_API_URL"]["value"], "http://127.0.0.1:8642"
        )
        self.assertEqual(
            workspace_env["HERMES_DASHBOARD_URL"]["value"],
            "http://127.0.0.1:9119",
        )
        for name, key in (
            ("HERMES_PASSWORD", "HERMES_PASSWORD"),
            ("HERMES_API_TOKEN", "API_SERVER_KEY"),
        ):
            self.assertEqual(
                workspace_env[name]["valueFrom"]["secretKeyRef"],
                {"name": SECRET_NAME, "key": key},
            )
        self.assertEqual(
            agent_env["API_SERVER_KEY"]["valueFrom"]["secretKeyRef"],
            {"name": SECRET_NAME, "key": "API_SERVER_KEY"},
        )
        self.assertEqual(agent_env["API_SERVER_ENABLED"]["value"], "true")
        self.assertEqual(agent_env["HERMES_DASHBOARD_HOST"]["value"], "127.0.0.1")
        self.assertEqual(workspace_env["COOKIE_SECURE"]["value"], "1")
        self.assertNotIn(("Secret", SECRET_NAME), self.resources)

    def test_only_ui_is_ingressed_with_tls_and_cluster_dns(self):
        ingress = self.resources[("Ingress", "hermes-workspace")]
        spec = ingress["spec"]
        self.assertEqual(spec["ingressClassName"], "traefik")
        self.assertEqual(spec["rules"][0]["host"], "hermes.ergoshear.dev")
        self.assertEqual(spec["tls"][0]["secretName"], "hermes-workspace-tls")
        self.assertEqual(
            ingress["metadata"]["annotations"][
                "external-dns.kubernetes.io/target"
            ],
            "k3s.ergoshear.dev",
        )
        self.assertEqual(
            self.resources[("Certificate", "hermes-workspace-tls")]["spec"][
                "dnsNames"
            ],
            ["hermes.ergoshear.dev"],
        )
        service = self.resources[("Service", "hermes-workspace")]
        self.assertEqual(
            service["spec"]["ports"],
            [{"name": "http", "port": 80, "targetPort": "http", "protocol": "TCP"}],
        )

    def test_persistent_claims_use_local_path_storage(self):
        agent_data = self.resources[("PersistentVolumeClaim", "hermes-agent-data")]
        workspace_files = self.resources[
            ("PersistentVolumeClaim", "hermes-workspace-files")
        ]
        for pvc in (agent_data, workspace_files):
            self.assertEqual(pvc["spec"]["storageClassName"], "local-path")
            self.assertEqual(pvc["spec"]["accessModes"], ["ReadWriteOnce"])
            self.assertEqual(pvc["spec"]["resources"]["requests"]["storage"], "10Gi")


if __name__ == "__main__":
    unittest.main()
