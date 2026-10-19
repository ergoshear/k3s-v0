import csv
import hashlib
from pathlib import Path
import ssl
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
URL = "https://argocd.ergoshear.dev"
IDP = (
    "https://portal.sso.us-east-2.amazonaws.com/saml/assertion/"
    "MjA0NDU5MDM4Njc2X2lucy02Njg0ZDFkYTU5MDBhYmNj"
)


class SSOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/argo-cd")], text=True
        )
        cls.resources = {
            (resource["kind"], resource["metadata"]["name"]): resource
            for resource in yaml.safe_load_all(rendered) if resource
        }
        cls.config = cls.resources[("ConfigMap", "argocd-cm")]["data"]
        cls.rbac = cls.resources[("ConfigMap", "argocd-rbac-cm")]["data"]
        cls.connector = yaml.safe_load(cls.config["dex.config"])["connectors"][0]

    def test_saml_endpoints_and_claims_match_identity_center(self):
        self.assertEqual(self.config["url"], URL)
        self.assertEqual(self.connector["type"], "saml")
        self.assertEqual(self.connector["id"], "aws")
        config = self.connector["config"]
        self.assertEqual(config["ssoURL"], IDP)
        self.assertEqual(config["ssoIssuer"], IDP)
        self.assertEqual(config["entityIssuer"], URL + "/api/dex/callback")
        self.assertEqual(config["redirectURI"], URL + "/api/dex/callback")
        self.assertEqual(config["nameIDPolicyFormat"], "emailAddress")
        self.assertEqual(config["usernameAttr"], "email")
        self.assertEqual(config["emailAttr"], "email")
        self.assertNotIn("groupsAttr", config)
        self.assertNotIn("insecureSkipSignatureValidation", config)

    def test_only_explicit_email_receives_sso_admin_access(self):
        self.assertEqual(self.rbac["policy.default"], "role:no-access")
        self.assertEqual(yaml.safe_load(self.rbac["scopes"]), ["email"])
        rules = list(csv.reader(self.rbac["policy.csv"].splitlines(), skipinitialspace=True))
        self.assertEqual(rules, [["g", "christopher@agilityqa.com", "role:admin"]])

    def test_local_admin_remains_enabled_and_anonymous_access_disabled(self):
        self.assertEqual(self.config["admin.enabled"], "true")
        self.assertEqual(self.config["users.anonymous.enabled"], "false")

    def test_generated_public_certificate_is_mounted_read_only(self):
        deployment = self.resources[("Deployment", "argocd-dex-server")]
        spec = deployment["spec"]["template"]["spec"]
        container = next(container for container in spec["containers"] if container["name"] == "dex")
        mount = next(mount for mount in container["volumeMounts"] if mount["name"] == "identity-center-signing")
        self.assertTrue(mount["readOnly"])
        self.assertEqual(self.connector["config"]["ca"], mount["mountPath"] + "/signing.pem")
        volume = next(volume for volume in spec["volumes"] if volume["name"] == mount["name"])
        name = volume["configMap"]["name"]
        self.assertTrue(name.startswith("argocd-identity-center-"))
        certificate = self.resources[("ConfigMap", name)]["data"]["signing.pem"]
        self.assertEqual(
            certificate, (ROOT / "argocd/argo-cd/identity-center-signing.pem").read_text()
        )
        digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
        self.assertEqual(digest, "a4eae0e1c7fd6847990ae58f0a25f84a0f99d50593aa026da81e27f8614a5f40")

    def test_certificate_is_valid_and_not_close_to_expiry(self):
        subprocess.run([
            "openssl", "x509", "-in", str(ROOT / "argocd/argo-cd/identity-center-signing.pem"),
            "-noout", "-checkend", str(30 * 24 * 60 * 60),
        ], check=True, capture_output=True)

    def test_existing_dex_mounts_and_control_plane_toleration_are_preserved(self):
        spec = self.resources[("Deployment", "argocd-dex-server")]["spec"]["template"]["spec"]
        container = next(container for container in spec["containers"] if container["name"] == "dex")
        self.assertTrue(
            {"static-files", "dexconfig", "argocd-dex-server-tls"}
            <= {mount["name"] for mount in container["volumeMounts"]}
        )
        self.assertIn({
            "key": "node-role.kubernetes.io/control-plane",
            "operator": "Exists", "effect": "NoSchedule",
        }, spec["tolerations"])
        self.assertEqual(container["securityContext"]["runAsUser"], 1001)

    def test_existing_ingress_tls_termination_setting_is_preserved(self):
        parameters = self.resources[("ConfigMap", "argocd-cmd-params-cm")]["data"]
        self.assertEqual(parameters["server.insecure"], "true")


if __name__ == "__main__":
    unittest.main()
