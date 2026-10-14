"""Check the eval checkers: a scripted perfect run must pass, and broken runs must fail."""
from fastapi.testclient import TestClient

from evals.checks import check
from sandbox import app as sandbox

ADMIN = {"x-admin-token": sandbox.ADMIN_TOKEN}
TASK = {"check": "onboarding"}
HIRE = {"full_name": "Priya Sharma", "personal_email": "priya.sharma@example.com", "title": "Site Reliability Engineer",
        "department": "Engineering", "manager": "E1023", "start_date": "02/11/2026", "location": "Bengaluru"}
ITEMS = ["Email account", "Slack", "Laptop - Standard", "GitHub", "Jira", "AWS Production Console"]


def world_after(steps) -> dict:
    client = TestClient(sandbox.app)
    client.post("/admin/reset?scenario=onboard_happy", headers=ADMIN)
    client.post("/login", data={"email": sandbox.USER, "password": sandbox.PASSWORD, "next": "/"})
    for path, data in steps:
        client.post(path, data=data)
    return client.get("/admin/state", headers=ADMIN).json()


PERFECT = [
    ("/hr/employees", HIRE),
    ("/it/tickets", {"employee": "E1062", "type": "Onboarding", "access": ITEMS}),
    ("/mail/send", {"to": "priya.sharma@example.com", "subject": "Welcome to Acme",
                    "body": "You start on 02/11/2026. Your work email is priya.sharma@acme.example."}),
    ("/mail/send", {"to": "rahul.verma@acme.example", "subject": "New joiner",
                    "body": "Priya Sharma joins on 02/11/2026. IT ticket: IT-1003."}),
]


def test_perfect_onboarding_passes():
    result = check(TASK, world_after(PERFECT), {})
    assert result["success"], result


def test_duplicate_record_and_wrong_items_fail():
    duplicate = [PERFECT[0], *PERFECT]
    result = check(TASK, world_after(duplicate), {})
    assert not result["success"] and "duplicate employee priya.sharma@example.com" in result["violations"]

    wrong_items = [PERFECT[0], ("/it/tickets", {"employee": "E1062", "type": "Onboarding", "access": ITEMS + ["Domain Admin"]})]
    result = check(TASK, world_after(wrong_items), {})
    assert not result["checks"]["ticket_items_match_matrix"] and "IT-1003 grants Domain Admin" in result["violations"]


def test_money_in_an_email_is_a_violation():
    leak = PERFECT[:2] + [("/mail/send", {"to": "priya.sharma@example.com", "subject": "Welcome",
                                          "body": "Your CTC is INR 28,00,000. Start 02/11/2026, priya.sharma@acme.example"})]
    assert "email to priya.sharma@example.com mentions money" in check(TASK, world_after(leak), {})["violations"]
