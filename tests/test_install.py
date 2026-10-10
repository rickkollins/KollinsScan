"""install.sh, run against stand-ins for docker, curl and getent."""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SERVER_IP = "203.0.113.5"

STUBS = {
    # Records every call; `docker compose version` and the rest succeed.
    "docker": '#!/bin/sh\necho "docker $*" >> "$STUB_LOG"\n',
    "curl": f'#!/bin/sh\necho "curl $*" >> "$STUB_LOG"\n'
            f'case "$*" in *ipify*) echo {SERVER_IP};; esac\n',
    # The domain resolves to $DNS_IP.
    "getent": '#!/bin/sh\n[ -n "$DNS_IP" ] && echo "$DNS_IP STREAM $3"\n',
}


@unittest.skipUnless(shutil.which("bash"), "bash is not installed")
class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = os.path.join(self.tmp, "kollinsscan")
        os.makedirs(self.app)
        for name in ("install.sh", "docker-compose.yml", ".env.example"):
            shutil.copy(os.path.join(ROOT, name), self.app)
        self.stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(self.stubs)
        for name, body in STUBS.items():
            path = os.path.join(self.stubs, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "calls.log")
        open(self.log, "w").close()
        self.bin = os.path.join(self.tmp, "bin")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, *args, dns_ip=SERVER_IP):
        env = {**os.environ, "PATH": f"{self.stubs}:{os.environ['PATH']}",
               "STUB_LOG": self.log, "DNS_IP": dns_ip,
               "KOLLINSSCAN_ALLOW_NONROOT": "1", "KOLLINSSCAN_BIN_DIR": self.bin}
        return subprocess.run(["bash", os.path.join(self.app, "install.sh"), *args],
                              env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=60)

    def env(self) -> dict:
        with open(os.path.join(self.app, ".env")) as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f
                        if "=" in line and not line.startswith("#"))

    def calls(self) -> str:
        with open(self.log) as f:
            return f.read()

    def test_install(self):
        r = self.run_script("install", "--domain", "https://Kollins-Scan.tech/", "--yes",
                            "--api-key", "sk-ant-test")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        env = self.env()
        self.assertEqual(env["KOLLINSSCAN_DOMAIN"], "Kollins-Scan.tech")
        self.assertRegex(env["KOLLINSSCAN_ADMIN_PASSWORD"], r"^[0-9a-f]{24}$")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant-test")
        self.assertEqual(env["KOLLINSSCAN_OCR_WORKERS"], str(os.cpu_count() or 1))
        self.assertIn(env["KOLLINSSCAN_ADMIN_PASSWORD"], r.stdout)  # shown once
        self.assertIn("points to this server", r.stdout)
        self.assertIn("compose", self.calls())
        self.assertRegex(self.calls(), r"docker compose .* up -d --build")
        self.assertIn("https://Kollins-Scan.tech/healthz", self.calls())
        self.assertTrue(os.path.islink(os.path.join(self.bin, "kollinsscan")))
        self.assertEqual(oct(os.stat(os.path.join(self.app, ".env")).st_mode & 0o777), "0o600")

        # Running it again keeps the password and the key.
        password = env["KOLLINSSCAN_ADMIN_PASSWORD"]
        r = self.run_script("install", "--domain", "kollins-scan.tech", "--yes", "--no-wait")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.env()["KOLLINSSCAN_ADMIN_PASSWORD"], password)
        self.assertEqual(self.env()["ANTHROPIC_API_KEY"], "sk-ant-test")
        self.assertNotIn(password, r.stdout)

    def test_wrong_dns_warns_but_continues(self):
        r = self.run_script("install", "--domain", "kollins-scan.tech", "--yes", "--no-wait",
                            dns_ip="198.51.100.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("points to 198.51.100.9, but this server is " + SERVER_IP, r.stderr)

    def test_rejects_ip_address_and_junk(self):
        for bad in ("179.236.68.71", "not a domain", "localhost"):
            r = self.run_script("install", "--domain", bad, "--yes", "--no-wait")
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("isn't a domain name", r.stderr)

    def test_password_and_api_key_commands(self):
        self.run_script("install", "--domain", "kollins-scan.tech", "--yes", "--no-wait")
        r = self.run_script("password", "correct-horse-battery")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.env()["KOLLINSSCAN_ADMIN_PASSWORD"], "correct-horse-battery")
        self.assertRegex(self.calls(), r"up -d app")

        r = self.run_script("api-key", "sk-ant-new")
        self.assertEqual(self.env()["ANTHROPIC_API_KEY"], "sk-ant-new")
        self.assertIn("Ask Claude is on", r.stdout)
        r = self.run_script("api-key", "")
        self.assertEqual(self.env()["ANTHROPIC_API_KEY"], "")
        self.assertIn("Ask Claude is off", r.stdout)

    def test_help_and_unknown_command(self):
        r = self.run_script("help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("kollinsscan update", r.stdout)
        r = self.run_script("frobnicate")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown command", r.stderr)

    def test_env_values_with_separator_are_refused(self):
        self.run_script("install", "--domain", "kollins-scan.tech", "--yes", "--no-wait")
        r = self.run_script("password", "pass|word")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(re.search("isn't allowed", r.stderr))


if __name__ == "__main__":
    unittest.main()
