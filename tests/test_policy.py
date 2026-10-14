from urllib.parse import urlencode

from agent.policy import decide, load_company

COMPANY = load_company()
BASE = "http://127.0.0.1:8001"


def post(path, form, approved=frozenset(), mode="work"):
    return decide("POST", BASE + path, urlencode(form, doseq=True), COMPANY, mode, set(approved))


WELCOME = {"to": "priya.sharma@example.com", "subject": "Welcome to Acme", "body": "You start on 02/11/2026."}


def test_external_email_needs_approval_internal_does_not():
    assert post("/mail/send", WELCOME).action == "approve"
    assert post("/mail/send", {**WELCOME, "to": "rahul.verma@acme.example"}).action == "allow"


def test_approval_binds_to_the_exact_payload():
    held = post("/mail/send", WELCOME)
    assert held.payload == WELCOME
    assert post("/mail/send", WELCOME, approved={held.key_hash}).action == "allow"
    changed = post("/mail/send", {**WELCOME, "body": "You start on 03/11/2026."}, approved={held.key_hash})
    assert changed.action == "approve"


def test_money_in_an_email_is_blocked_even_when_approved():
    body = {**WELCOME, "body": "Your CTC is INR 28,00,000."}
    held = post("/mail/send", body)
    assert held.action == "block" and held.rule == "no_money_in_messages"
    assert post("/mail/send", {**body, "to": "x@acme.example"}).action == "block"


def test_privileged_access_needs_approval():
    ticket = {"employee": "E1062", "type": "Onboarding", "access": ["Email account", "Slack", "GitHub"]}
    assert post("/it/tickets", ticket).action == "allow"
    held = post("/it/tickets", {**ticket, "access": ticket["access"] + ["AWS Production Console"]})
    assert held.action == "approve" and held.rule == "privileged_access"
    assert held.payload["access"] == "Email account, Slack, GitHub, AWS Production Console"


def test_deactivation_needs_approval():
    held = post("/hr/employees/E1051/deactivate", {"last_day": "31/10/2026"})
    assert held.action == "approve" and held.payload == {"path": "/hr/employees/E1051/deactivate", "last_day": "31/10/2026"}


def test_admin_is_blocked_and_verify_mode_is_read_only():
    assert decide("GET", BASE + "/admin/state", None, COMPANY, "work", set()).action == "block"
    assert decide("GET", BASE + "/hr/employees?q=Priya", None, COMPANY, "verify", set()).action == "allow"
    assert post("/hr/employees", {"full_name": "X"}, mode="verify").rule == "verify_read_only"


def test_company_docs_are_citable():
    assert {"policy:onboarding", "policy:communication", "policy:access_matrix"} <= set(COMPANY.docs)
