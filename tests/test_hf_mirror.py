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

    def test_runtime_configuration_survives_gitops_and_read_only_root(self):
        spec = self.resources[("Deployment", "hf-mirror")]["spec"]["template"]["spec"]
        container = spec["containers"][0]
        self.assertEqual(container["command"], ["nginx"])
        self.assertEqual(container["args"], ["-g", "daemon off;"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        volumes = {volume["name"]: volume for volume in spec["volumes"]}
        mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
        self.assertEqual(volumes["tmp"]["emptyDir"], {})
        self.assertEqual(mounts["tmp"]["mountPath"], "/tmp")
        config = self.resources[("ConfigMap", volumes["nginx-config"]["configMap"]["name"])]
        self.assertEqual(mounts["nginx-config"]["mountPath"], "/etc/nginx/nginx.conf")
        self.assertIn("pid /tmp/nginx.pid;", config["data"]["nginx.conf"])
        init = spec["initContainers"][0]
        self.assertEqual(init["name"], "prepare-cache")
        self.assertEqual(
            init["command"][-1],
            "mkdir -p /cache/hf && chown 101:101 /cache /cache/hf",
        )
        self.assertEqual(init["volumeMounts"][0]["name"], "cache")

    def test_clients_can_reach_mirror_without_changing_egress(self):
        ingress = self.resources[("NetworkPolicy", "mirror-ingress")]["spec"]
        self.assertIn(
            {
                "from": [{"namespaceSelector": {}}],
                "ports": [{"protocol": "TCP", "port": 8080}],
            },
            ingress["ingress"],
        )
        for (kind, _), resource in self.resources.items():
            if kind == "NetworkPolicy":
                self.assertEqual(resource["spec"]["policyTypes"], ["Ingress"])
                self.assertNotIn("egress", resource["spec"])

    def test_client_policy_only_mutates_new_application_pods(self):
        policy = self.resources[("ClusterPolicy", "inject-hf-mirror")]
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
        excluded = rule["exclude"]["any"][0]["resources"]["namespaces"]
        self.assertTrue(
            {"hf-mirror", "kube-system", "argocd", "kyverno", "monitoring",
             "tailscale", "cert-manager", "trivy-system"}.issubset(excluded)
        )
        self.assertTrue({"default", "hermes-workspace", "litellm"}.isdisjoint(excluded))
        mutations = rule["mutate"]["foreach"]
        self.assertEqual(len(mutations), 2)
        for mutation, field in zip(mutations, ("containers", "initContainers")):
            self.assertIn(f"request.object.spec.{field}", mutation["list"])
            patch = mutation["patchesJson6902"]
            expression = "{{ element.env[?name != 'HF_ENDPOINT' && name != 'HF_HUB_DISABLE_XET'] || `[]` }}"
            self.assertIn(expression, patch)
            operations = yaml.safe_load(patch.replace(expression, "[]"))
            self.assertEqual(operations[0]["op"], "add")
            self.assertEqual(
                operations[0]["path"], f"/spec/{field}/{{{{ elementIndex }}}}/env"
            )
            env = {operation["value"]["name"]: operation["value"]
                   for operation in operations[1:]}
            self.assertEqual(
                env["HF_ENDPOINT"]["value"],
                "http://hf-mirror.hf-mirror.svc.cluster.local:8080",
            )
            self.assertEqual(env["HF_HUB_DISABLE_XET"]["value"], "1")
            for variable in env.values():
                self.assertNotIn("valueFrom", variable)

    def test_cache_preserves_metadata_and_does_not_share_authenticated_responses(self):
        config = (ROOT / "apps/hf-mirror/nginx.conf").read_text()
        self.assertIn("set $download_url $redirect_target;", config)
        self.assertIn("~^/ https://huggingface.co$upstream_http_location;", config)
        self.assertIn("proxy_set_header Host $proxy_host;", config)
        self.assertIn("proxy_hide_header X-Repo-Commit;", config)
        self.assertIn("add_header X-Repo-Commit $response_repo_commit always;", config)
        self.assertIn(
            "proxy_no_cache $authenticated_request $has_cookie $metadata_request;",
            config,
        )
        self.assertIn(
            "proxy_cache_bypass $authenticated_request $has_cookie $metadata_request;",
            config,
        )
        self.assertNotIn("proxy_cache_valid 200 206", config)
        self.assertIn('proxy_set_header Range "";', config)


if __name__ == "__main__":
    unittest.main()
