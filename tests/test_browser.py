"""Real Chromium against the sandbox: snapshot quality and the egress gate."""
import re

import pytest

from agent.browser import Browser
from agent.policy import load_company
from tests.conftest import reset

COMPANY = load_company()
MAX_SNAPSHOT_CHARS = 6000  # roughly 1.5k tokens


@pytest.fixture
async def browser(sandbox_url):
    reset("onboard_happy")
    b = Browser(COMPANY)
    await b.start()
    yield b
    await b.close()


def ref_of(snapshot: str, pattern: str) -> str:
    line = next(line for line in snapshot.splitlines() if re.search(pattern, line))
    return re.search(r"\[(e\d+)\]", line).group(1)


async def test_form_snapshot_shows_labels_values_and_options(browser):
    obs = await browser.goto("/hr/employees/new")
    s = obs.snapshot
    assert 'textbox "Full name" value=""' in s
    assert 'placeholder="DD/MM/YYYY"' in s
    assert re.search(r'select "Manager" selected="Select a manager" options=\[.*Rahul Verma \(E1023\)', s)
    assert 'button "Create employee"' in s
    assert len(s) < MAX_SNAPSHOT_CHARS


async def test_list_and_message_snapshots(browser):
    inbox = (await browser.goto("/mail")).snapshot
    assert re.search(r'\| Neha Kapoor .* \| \[e\d+\] link "Signed offer: Priya Sharma" -> /mail/4 \|', inbox)
    message = (await browser.goto("/mail/4")).snapshot
    assert 'link "Offer_Letter_Priya_Sharma.pdf" -> /mail/4/attachment' in message
    ticket = (await browser.goto("/it/tickets/IT-1001")).snapshot
    assert "| Laptop - Standard | Delivered on 03/10/2026 |" in ticket
    for page in (inbox, message, ticket):
        assert len(page) < MAX_SNAPSHOT_CHARS


async def test_checkboxes_are_listed_by_label(browser):
    s = (await browser.goto("/it/tickets/new")).snapshot
    assert re.search(r'\[e\d+\] checkbox "GitHub"', s) and re.search(r'\[e\d+\] checkbox "Domain Admin"', s)


async def test_external_email_is_held_with_the_form_intact_then_replayed(browser):
    s = (await browser.goto("/mail/compose")).snapshot
    await browser.fill(ref_of(s, r'textbox "To"'), "priya.sharma@example.com")
    await browser.fill(ref_of(s, r'textbox "Subject"'), "Welcome to Acme")
    await browser.fill(ref_of(s, r'textarea "Message"'), "Welcome!")
    obs = await browser.click(ref_of(s, r'button "Send"'))
    assert browser.held.action == "approve" and browser.held.rule == "external_email"
    assert obs.url == "/mail/compose" and 'value="priya.sharma@example.com"' in obs.snapshot

    browser.approved.add(browser.held.key_hash)
    obs = await browser.click(ref_of(obs.snapshot, r'button "Send"'))
    assert browser.held is None and obs.url == "/mail/sent" and obs.posted
    assert "Message sent to priya.sharma@example.com." in obs.page_text


async def test_admin_is_blocked_and_verify_mode_cannot_write(browser):
    await browser.goto("/admin/state")
    assert browser.held.rule == "blocked_path"

    browser.mode = "verify"
    s = (await browser.goto("/hr/employees/E1051")).snapshot
    await browser.fill(ref_of(s, r'textbox "Last working day"'), "31/10/2026")
    await browser.click(ref_of(s, r'button "Deactivate employee"'))
    assert browser.held.rule == "verify_read_only"


async def test_session_expiry_lands_on_login_and_login_returns(browser, sandbox_url):
    reset("onboard_chaos")  # clears sessions and expires the next POST
    await browser.goto("/hr/employees/new")
    assert browser.on_login_page()
    await browser.login()
    assert not browser.on_login_page() and browser.page.url.endswith("/hr/employees/new")
