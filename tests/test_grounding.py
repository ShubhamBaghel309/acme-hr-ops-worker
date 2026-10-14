from agent.grounding import (check_claim, check_composed, check_fact, check_write, match_options, normalize,
                             to_iso_date)

OFFER = """ACME CORP - OFFER OF EMPLOYMENT
Candidate: Priya Sharma
Personal email: priya.sharma@example.com
Reporting manager: Rahul Verma
Start date: 02 November 2026
Annual compensation: INR 28,00,000 (fixed)
/s/ Priya Sharma        Date: 12 October 2026"""
SOURCES = {"doc:1": OFFER, "request": "Onboard Priya Sharma, her offer is signed.",
           "policy:communication": "Tell them to report to reception at 10:00 on their first day."}


def fact(value, type_, quote):
    normalized, msg = check_fact(value, type_, quote, "doc:1", SOURCES)
    assert msg == "", msg
    return {"value": normalized, "raw": value, "type": type_, "source": "doc:1", "quote": quote}


FACTS = {
    "start_date": fact("02/11/2026", "date", "Start date: 02 November 2026"),
    "manager_name": fact("Rahul Verma", "name", "Reporting manager: Rahul Verma"),
    "personal_email": fact("priya.sharma@example.com", "email", "Personal email: priya.sharma@example.com"),
    "work_email": {"value": "priya.sharma@acme.example", "raw": "priya.sharma@acme.example", "type": "email"},
    "it_ticket_id": {"value": "it-1003", "raw": "IT-1003", "type": "text"},
}


def test_normalize_ignores_ligatures_quotes_spacing_and_line_numbers():
    assert normalize("L12: The  ﬁrst “day”") == 'the first "day"'


def test_dates_follow_the_company_locale():
    assert to_iso_date("02/11/2026") == "2026-11-02"
    assert to_iso_date("02/11/2026", day_first=False) == "2026-02-11"
    assert to_iso_date("2 Nov 2026") == "2026-11-02"


def test_fact_value_must_be_readable_from_its_quote():
    assert FACTS["start_date"]["value"] == "2026-11-02"
    _, msg = check_fact("03/11/2026", "date", "Start date: 02 November 2026", "doc:1", SOURCES)
    assert "cannot be read from the quote" in msg


def test_fact_quote_must_exist_in_the_source():
    _, msg = check_fact("Priya Sharma", "name", "Candidate name: Priya Sharma", "doc:1", SOURCES)
    assert "not in doc:1" in msg
    _, msg = check_fact("Priya Sharma", "name", "Candidate: Priya Sharma", "doc:9", SOURCES)
    assert "Unknown source" in msg


def test_money_with_indian_grouping():
    assert fact("28,00,000", "money", "Annual compensation: INR 28,00,000 (fixed)")["value"] == "2800000"


def test_names_ignore_honorifics():
    _, msg = check_fact("Ms. Priya Sharma", "name", "Candidate: Priya Sharma", "doc:1", SOURCES)
    assert msg == ""


def test_writes_must_match_the_cited_fact():
    assert check_write("02/11/2026", "fact:start_date", FACTS, SOURCES) == ""
    assert check_write("2026-11-02", "fact:start_date", FACTS, SOURCES) == ""
    assert "does not match" in check_write("03/11/2026", "fact:start_date", FACTS, SOURCES)
    assert "no fact named" in check_write("x", "fact:salary", FACTS, SOURCES)
    assert check_write("Priya Sharma", "request", FACTS, SOURCES) == ""
    assert "does not appear" in check_write("Arjun", "request", FACTS, SOURCES)
    assert "Unknown source" in check_write("x", "page", FACTS, SOURCES)


def test_composed_text_must_trace_every_email_date_and_number():
    ok = ("Welcome Priya! You start on 02/11/2026. Your work email is priya.sharma@acme.example. "
          "Please report to reception at 10:00. Ticket IT-1003 covers your laptop.")
    assert check_composed(ok, FACTS, SOURCES) == ""
    bad = "You start on 09/11/2026 and your desk number is 42."
    msg = check_composed(bad, FACTS, SOURCES)
    assert "2026-11-09" in msg and "42" in msg


def test_select_detects_ambiguous_options():
    options = ["Select a manager", "Rahul Verma (E1023) - Engineering Manager, Platform",
               "Rahul Verma (E1088) - Engineering Manager, Payments", "Rohan Das (E1050) - Sales Manager"]
    assert len(match_options("Rahul Verma", options)) == 2
    assert match_options("E1088", options) == [options[2]]
    assert match_options("Engineering", ["Select a department", "Engineering", "Design"]) == ["Engineering"]


def test_pass_verdicts_must_quote_the_current_page():
    page = "Ticket IT-1003\nEmployee Priya Sharma (E1062)\nType Onboarding"
    assert check_claim("Priya Sharma (E1062)", page) == ""
    assert "not on the current page" in check_claim("Type Offboarding", page)
