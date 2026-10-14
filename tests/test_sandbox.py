"""The sandbox is the agent's world: these tests pin down its behaviour, including the chaos."""
import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from sandbox import app as sandbox
from sandbox.scenarios import SCENARIOS

ADMIN = {"x-admin-token": sandbox.ADMIN_TOKEN}
NEW_HIRE = {
    "full_name": "Priya Sharma", "personal_email": "priya.sharma@example.com",
    "title": "Site Reliability Engineer", "department": "Engineering", "manager": "E1023",
    "start_date": "02/11/2026", "location": "Bengaluru",
}


def client_for(scenario: str) -> TestClient:
    client = TestClient(sandbox.app)
    assert client.post(f"/admin/reset?scenario={scenario}", headers=ADMIN).status_code == 200
    r = client.post("/login", data={"email": sandbox.USER, "password": sandbox.PASSWORD, "next": "/"})
    assert r.status_code == 200 and "Acme Portal" in r.text
    return client


def state(client: TestClient) -> dict:
    return client.get("/admin/state", headers=ADMIN).json()


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_every_scenario_resets(scenario):
    client = client_for(scenario)
    assert state(client)["scenario"] == scenario


def test_signed_out_users_are_sent_to_login():
    client = TestClient(sandbox.app)
    r = client.get("/hr/employees", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")


def test_admin_needs_token():
    assert TestClient(sandbox.app).get("/admin/state").status_code == 403


def test_create_employee_happy_path():
    client = client_for("onboard_happy")
    r = client.post("/hr/employees", data=NEW_HIRE)
    assert "Employee E1062 created." in r.text
    priya = [e for e in state(client)["employees"] if e["full_name"] == "Priya Sharma"]
    assert len(priya) == 1 and priya[0]["start_date"] == "2026-11-02"


def test_iso_date_is_rejected_with_a_readable_error():
    client = client_for("onboard_happy")
    r = client.post("/hr/employees", data={**NEW_HIRE, "start_date": "2026-11-02"})
    assert "Start date must be in DD/MM/YYYY format." in r.text
    assert not [e for e in state(client)["employees"] if e["full_name"] == "Priya Sharma"]


def test_ghost_write_saves_then_returns_500():
    client = client_for("onboard_chaos")
    first = client.post("/hr/employees", data=NEW_HIRE, follow_redirects=False)
    assert first.status_code == 303 and "expired=1" in first.headers["location"]  # session-expiry chaos fires first
    client.post("/login", data={"email": sandbox.USER, "password": sandbox.PASSWORD, "next": "/"})
    second = client.post("/hr/employees", data=NEW_HIRE)
    assert second.status_code == 500
    assert len([e for e in state(client)["employees"] if e["full_name"] == "Priya Sharma"]) == 1


def test_silent_drop_claims_success_but_stores_nothing():
    client = client_for("verify_silent_drop")
    before = len(state(client)["tickets"])
    r = client.post("/it/tickets", data={"employee": "E1032", "type": "Access change", "access": ["Figma"]})
    assert r.status_code == 200 and "Ticket IT-1003 created." in r.text
    assert len(state(client)["tickets"]) == before
    assert client.get("/it/tickets/IT-1003").status_code == 404


def test_renamed_buttons_in_chaos_scenario():
    client = client_for("onboard_chaos")
    assert "Add team member" in client.get("/hr/employees/new").text


def test_offer_letter_text_is_extractable():
    client = client_for("onboard_happy")
    assert "Signed offer: Priya Sharma" in client.get("/mail").text
    offer_mail = next(m for m in sandbox.db["emails"] if m["attachment"])
    pdf = client.get(f"/mail/{offer_mail['id']}/attachment")
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Start date: 02 November 2026" in text and "/s/ Priya Sharma" in text


def test_deactivate_and_send_mail():
    client = client_for("offboard_happy")
    r = client.post("/hr/employees/E1051/deactivate", data={"last_day": "31/10/2026"})
    assert "deactivated" in r.text
    client.post("/mail/send", data={"to": "rohan.das@acme.example", "subject": "Offboarding", "body": "Done."})
    s = state(client)
    assert next(e for e in s["employees"] if e["code"] == "E1051")["status"] == "Inactive"
    assert s["sent"][0]["to"] == "rohan.das@acme.example"
