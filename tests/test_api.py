"""
API integration tests — hit every public endpoint via FastAPI TestClient.
No live AWS, Stripe, or GitHub OAuth needed: DynamoDB and auth are mocked.

Coverage:
  GET  /api/v1/health            — public
  GET  /api/v1/version           — public
  POST /api/v1/parse             — public, all 4 sample files
  POST /api/v1/convert/diagram   — optional auth, all 4 samples
  POST /api/v1/convert/clean     — optional auth, all 4 samples
  POST /api/v1/convert           — requires auth (mocked), all 4 samples
  POST /api/v1/convert/file      — requires auth (mocked), multipart upload
  POST /api/v1/convert           — no key → 401
  POST /api/v1/convert/diagram   — bad XML → 422
  POST /api/v1/parse             — bad XML → 422
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Environment stubs (must be set before app import) ─────────────────────────
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")
os.environ.setdefault("DYNAMODB_TABLE", "bpel2orkes-test")
os.environ.setdefault("BPEL2ORKES_ENV", "test")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SESSION_SECRET", "test-secret-key-minimum-32-chars!!")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_dummy")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── Mock DynamoDB before boto3 is imported by auth.py ─────────────────────────
_mock_table = MagicMock()
_mock_table.get_item.return_value = {"Item": None}
_mock_table.update_item.return_value = {}
_mock_table.put_item.return_value = {}

import boto3  # noqa: E402 — must be after env vars
with patch.object(boto3, "resource") as _mock_res:
    _mock_res.return_value.Table.return_value = _mock_table
    from api import app  # noqa: E402
    import auth as _auth_module  # noqa: E402

from fastapi import Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ── Fake authenticated user injected via dependency override ──────────────────
_FAKE_USER = {
    "userId": "github:999999",
    "email": "test@example.com",
    "name": "Test User",
    "apiKey": "bpel2_test_abc123",
    "creditBalanceCents": 5000,
    "conversionsRemaining": 50,
    "tier": "free",
}

app.dependency_overrides[_auth_module.require_api_key] = lambda: _FAKE_USER
app.dependency_overrides[_auth_module.optional_api_key] = lambda: _FAKE_USER

SAMPLES = Path(__file__).parent.parent / "samples"

SAMPLE_FILES = {
    "loan_approval":               SAMPLES / "loan_approval.bpel",
    "income_verification":         SAMPLES / "income_verification.bpel",
    "communications_orchestration": SAMPLES / "communications_orchestration.bpel",
    "credit_card_provisioning":    SAMPLES / "credit_card_provisioning.bpel",
}

# ── Client ────────────────────────────────────────────────────────────────────
# Patch deduct_credit so tests don't try DynamoDB writes
import api as _api_module  # noqa: E402

# Patch deduct_credit where api.py imported it (not in auth module)
with patch.object(_api_module, "deduct_credit", return_value=None):
    client = TestClient(app, raise_server_exceptions=True)


# ═════════════════════════════════════════════════════════════════════════════
# System endpoints
# ═════════════════════════════════════════════════════════════════════════════

def test_health():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_version():
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert "2.0" in body.get("supportedBpelVersions", [])


# ═════════════════════════════════════════════════════════════════════════════
# Parse — public, no auth
# ═════════════════════════════════════════════════════════════════════════════

class TestParse:
    def _parse(self, bpel_path: Path):
        return client.post(
            "/api/v1/parse",
            content=bpel_path.read_bytes(),
            headers={"Content-Type": "application/xml"},
        )

    def test_loan_approval_parses(self):
        r = self._parse(SAMPLE_FILES["loan_approval"])
        assert r.status_code == 200
        body = r.json()
        assert "ast" in body
        assert body["ast"]["process"]["name"] == "LoanApprovalProcess"

    def test_income_verification_parses(self):
        r = self._parse(SAMPLE_FILES["income_verification"])
        assert r.status_code == 200
        assert "ast" in r.json()

    def test_communications_orchestration_parses(self):
        r = self._parse(SAMPLE_FILES["communications_orchestration"])
        assert r.status_code == 200
        assert "ast" in r.json()

    def test_credit_card_provisioning_parses(self):
        r = self._parse(SAMPLE_FILES["credit_card_provisioning"])
        assert r.status_code == 200
        assert "ast" in r.json()

    def test_bad_xml_returns_422(self):
        r = client.post(
            "/api/v1/parse",
            content=b"this is not xml",
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 422

    def test_wrong_root_returns_422(self):
        r = client.post(
            "/api/v1/parse",
            content=b"<notaprocess/>",
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 422


# ═════════════════════════════════════════════════════════════════════════════
# Diagram — optional auth (mocked as authenticated)
# ═════════════════════════════════════════════════════════════════════════════

class TestDiagram:
    def _diagram(self, bpel_path: Path):
        return client.post(
            "/api/v1/convert/diagram",
            content=bpel_path.read_bytes(),
            headers={"Content-Type": "application/xml"},
        )

    def _assert_diagram_shape(self, r):
        assert r.status_code == 200
        body = r.json()
        assert "mermaid" in body
        assert "flowchart" in body["mermaid"]
        assert "summary" in body
        s = body["summary"]
        assert "total" in s
        assert "autoConverted" in s
        assert "needsWork" in s
        assert s["total"] >= 1

    def test_loan_approval_diagram(self):
        self._assert_diagram_shape(self._diagram(SAMPLE_FILES["loan_approval"]))

    def test_income_verification_diagram(self):
        self._assert_diagram_shape(self._diagram(SAMPLE_FILES["income_verification"]))

    def test_communications_orchestration_diagram(self):
        self._assert_diagram_shape(self._diagram(SAMPLE_FILES["communications_orchestration"]))

    def test_credit_card_provisioning_diagram(self):
        self._assert_diagram_shape(self._diagram(SAMPLE_FILES["credit_card_provisioning"]))

    def test_bad_xml_returns_422(self):
        r = client.post(
            "/api/v1/convert/diagram",
            content=b"<bad/>",
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 422


# ═════════════════════════════════════════════════════════════════════════════
# Convert/clean — optional auth (mocked as authenticated)
# ═════════════════════════════════════════════════════════════════════════════

class TestConvertClean:
    def _clean(self, bpel_path: Path):
        return client.post(
            "/api/v1/convert/clean",
            content=bpel_path.read_bytes(),
            headers={"Content-Type": "application/xml"},
        )

    def _assert_clean_shape(self, r):
        assert r.status_code == 200
        body = r.json()
        assert "mainWorkflow" in body
        wf = body["mainWorkflow"]
        assert "name" in wf
        assert "tasks" in wf
        assert isinstance(wf["tasks"], list)
        assert len(wf["tasks"]) >= 1
        assert "warnings" in body
        assert "workflowCount" in body
        assert body["workflowCount"] >= 1

    def test_loan_approval_clean(self):
        self._assert_clean_shape(self._clean(SAMPLE_FILES["loan_approval"]))

    def test_income_verification_clean(self):
        self._assert_clean_shape(self._clean(SAMPLE_FILES["income_verification"]))

    def test_communications_orchestration_clean(self):
        self._assert_clean_shape(self._clean(SAMPLE_FILES["communications_orchestration"]))

    def test_credit_card_provisioning_clean(self):
        self._assert_clean_shape(self._clean(SAMPLE_FILES["credit_card_provisioning"]))


# ═════════════════════════════════════════════════════════════════════════════
# Convert — requires auth (dependency overridden; deduct_credit patched)
# ═════════════════════════════════════════════════════════════════════════════

class TestConvert:
    def _convert(self, bpel_path: Path):
        with patch.object(_api_module, "deduct_credit", return_value=None):
            return client.post(
                "/api/v1/convert",
                content=bpel_path.read_bytes(),
                headers={"Content-Type": "application/xml"},
            )

    def _assert_bundle_shape(self, r, expected_name: str = None):
        assert r.status_code == 200
        body = r.json()
        assert "bundle" in body
        assert "durationMs" in body
        assert body["durationMs"] > 0
        assert "workflowCount" in body
        assert body["workflowCount"] >= 1
        bundle = body["bundle"]
        assert "mainWorkflow" in bundle
        wf = bundle["mainWorkflow"]
        assert "name" in wf
        assert "tasks" in wf
        assert len(wf["tasks"]) >= 1
        if expected_name:
            assert wf["name"] == expected_name
        assert "warnings" in bundle
        assert isinstance(bundle["warnings"], list)

    def test_loan_approval_converts(self):
        self._assert_bundle_shape(
            self._convert(SAMPLE_FILES["loan_approval"]),
            expected_name="LoanApprovalProcess",
        )

    def test_income_verification_converts(self):
        self._assert_bundle_shape(self._convert(SAMPLE_FILES["income_verification"]))

    def test_communications_orchestration_converts(self):
        self._assert_bundle_shape(self._convert(SAMPLE_FILES["communications_orchestration"]))

    def test_credit_card_provisioning_converts(self):
        self._assert_bundle_shape(self._convert(SAMPLE_FILES["credit_card_provisioning"]))

    def test_no_api_key_returns_401(self):
        # Temporarily remove override to test real auth rejection
        app.dependency_overrides.pop(_auth_module.require_api_key, None)
        try:
            r = client.post(
                "/api/v1/convert",
                content=SAMPLE_FILES["loan_approval"].read_bytes(),
                headers={"Content-Type": "application/xml"},
            )
            assert r.status_code == 401
        finally:
            app.dependency_overrides[_auth_module.require_api_key] = lambda: _FAKE_USER

    def test_bundle_tasks_have_required_fields(self):
        r = self._convert(SAMPLE_FILES["loan_approval"])
        tasks = r.json()["bundle"]["mainWorkflow"]["tasks"]
        for task in tasks:
            assert "name" in task, f"Task missing 'name': {task}"
            assert "type" in task, f"Task missing 'type': {task}"

    def test_sub_workflows_are_list(self):
        r = self._convert(SAMPLE_FILES["income_verification"])
        bundle = r.json()["bundle"]
        assert isinstance(bundle.get("subWorkflows", []), list)
        assert isinstance(bundle.get("compensationFlows", []), list)
        assert isinstance(bundle.get("faultHandlerFlows", []), list)


# ═════════════════════════════════════════════════════════════════════════════
# Convert/file — multipart upload
# ═════════════════════════════════════════════════════════════════════════════

class TestConvertFile:
    def test_file_upload_loan_approval(self):
        bpel = SAMPLE_FILES["loan_approval"].read_bytes()
        with patch.object(_api_module, "deduct_credit", return_value=None):
            r = client.post(
                "/api/v1/convert/file",
                files={"file": ("loan_approval.bpel", bpel, "application/xml")},
            )
        assert r.status_code == 200
        body = r.json()
        assert "bundle" in body
        assert body["bundle"]["mainWorkflow"]["name"] == "LoanApprovalProcess"

    def test_file_upload_wrong_mime_still_works(self):
        bpel = SAMPLE_FILES["income_verification"].read_bytes()
        with patch.object(_api_module, "deduct_credit", return_value=None):
            r = client.post(
                "/api/v1/convert/file",
                files={"file": ("process.xml", bpel, "text/xml")},
            )
        assert r.status_code == 200
