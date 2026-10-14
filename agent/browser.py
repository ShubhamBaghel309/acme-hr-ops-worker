"""The agent's eyes and hands: one Playwright browser per session.

- `observe()` turns the page into compact text with [eN] refs on interactive elements. It relies only
  on generic HTML meaning (labels, roles, headings, tables), so it works for any server-rendered app.
- A route handler on the context sends every request through the egress gate (`policy.decide`).
  Held or blocked requests are answered with 204, so the page and any filled form stay as they are.
- Login is automatic from .env, like a password manager. Credentials never reach the LLM.
- Playwright objects live here, outside the graph state, because they can't be checkpointed.
"""
import os
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from agent.policy import Company, Decision, decide

SNAPSHOT_JS = r"""
() => {
  document.querySelectorAll('[data-agent-ref]').forEach(el => el.removeAttribute('data-agent-ref'));
  let n = 0, out = [], line = [], inCell = false;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const q = s => JSON.stringify(clean(s).slice(0, 300));
  const flush = () => { const t = clean(line.join(' ')); if (t) out.push(t); line = []; };
  const hidden = el => {
    if (el.type === 'hidden') return true;
    const st = getComputedStyle(el);
    return st.display === 'none' || st.visibility === 'hidden';
  };
  const labelOf = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return clean(l.innerText); }
    const wrap = el.closest('label'); if (wrap) return clean(wrap.innerText);
    return clean(el.getAttribute('aria-label') || el.placeholder || el.name);
  };
  const ref = el => { const r = 'e' + (++n); el.setAttribute('data-agent-ref', r); return r; };
  const control = el => {
    const tag = el.tagName;
    if (tag === 'A' && el.getAttribute('href')) {
      const u = new URL(el.href, location.href);
      return `[${ref(el)}] link ${q(el.innerText)} -> ${u.pathname}${u.search}`;
    }
    if (tag === 'BUTTON' || (tag === 'INPUT' && ['submit', 'button'].includes(el.type)))
      return `[${ref(el)}] button ${q(el.innerText || el.value)}`;
    if (tag === 'INPUT' && ['checkbox', 'radio'].includes(el.type))
      return `[${ref(el)}] ${el.type} ${q(labelOf(el))}${el.checked ? ' (checked)' : ''}`;
    if (tag === 'INPUT' || tag === 'TEXTAREA') {
      const kind = tag === 'TEXTAREA' ? 'textarea' : (el.type === 'password' ? 'password' : 'textbox');
      const ph = el.placeholder ? ` placeholder=${q(el.placeholder)}` : '';
      return `[${ref(el)}] ${kind} ${q(labelOf(el))} value=${q(kind === 'password' ? '' : el.value)}${ph}`;
    }
    if (tag === 'SELECT') {
      const opts = [...el.options].map(o => clean(o.text));
      const sel = el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].text) : '';
      return `[${ref(el)}] select ${q(labelOf(el))} selected=${q(sel)} options=${JSON.stringify(opts)}`;
    }
    return null;
  };
  const BLOCK = new Set(['DIV', 'P', 'UL', 'OL', 'LI', 'TABLE', 'THEAD', 'TBODY', 'FORM', 'FIELDSET',
                         'LEGEND', 'SECTION', 'MAIN', 'HEADER', 'NAV', 'FOOTER', 'BR', 'HR']);
  const walk = node => {
    if (node.nodeType === Node.TEXT_NODE) { line.push(node.textContent); return; }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node, tag = el.tagName;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(tag) || hidden(el)) return;
    if (tag === 'LABEL' && (el.htmlFor || el.querySelector('input,select,textarea'))) {
      el.querySelectorAll('input,select,textarea').forEach(c => { if (!hidden(c)) { flush(); out.push(control(c)); } });
      return;  // the controls carry the label text
    }
    const role = el.getAttribute('role');
    if (role === 'alert' || role === 'status') { flush(); out.push(`[${role}] ${clean(el.innerText)}`); return; }
    if (/^H[1-6]$/.test(tag)) { flush(); out.push('#'.repeat(+tag[1]) + ' ' + clean(el.innerText)); return; }
    if (tag === 'TR') {
      flush();
      inCell = true;
      const cells = [...el.children].map(cell => { line = []; cell.childNodes.forEach(walk); return clean(line.join(' ')); });
      inCell = false; line = [];
      out.push('| ' + cells.join(' | ') + ' |');
      return;
    }
    if (tag === 'PRE') { flush(); el.innerText.split('\n').map(clean).filter(Boolean).forEach(l => out.push(l)); return; }
    const c = control(el);
    if (c) {
      if (tag === 'A' || inCell) line.push(c); else { flush(); out.push(c); }
      return;
    }
    const block = BLOCK.has(tag);
    if (block) flush();
    el.childNodes.forEach(walk);
    if (block) flush();
  };
  walk(document.body);
  flush();
  return out.join('\n');
}
"""


@dataclass
class Observation:
    url: str             # path + query of the current page
    snapshot: str        # compact text with [eN] refs, shown to the LLM
    page_text: str       # visible text, used to check quotes
    status: int | None   # HTTP status of the last page load caused by the action
    posted: bool         # whether the action submitted a form (POST)


class Browser:
    def __init__(self, company: Company, headed: bool = False):
        self.company = company
        self.headed = headed
        self.base = os.getenv("SANDBOX_URL") or company.profile["portal"]["base_url"]
        self.mode = "work"             # "verify" makes the gate refuse every non-GET request
        self.approved: set[str] = set()  # payload hashes a human approved
        self.held: Decision | None = None
        self._status: int | None = None
        self._posted = False

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=not self.headed, slow_mo=250 if self.headed else 0)
        self.context = await self._browser.new_context(viewport={"width": 1280, "height": 860})
        await self.context.route("**/*", self._gate)
        self.page = await self.context.new_page()
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        await self.login()

    async def close(self) -> None:
        await self._browser.close()
        await self._pw.stop()

    # ----- the egress gate -----

    async def _gate(self, route) -> None:
        req = route.request
        decision = decide(req.method, req.url, req.post_data, self.company, self.mode, self.approved)
        if decision.action == "allow":
            await route.continue_()
        else:
            self.held = decision
            await route.fulfill(status=204, body="")

    def _on_request(self, request) -> None:
        if request.is_navigation_request() and request.frame == self.page.main_frame and request.method == "POST":
            self._posted = True

    def _on_response(self, response) -> None:
        if response.request.is_navigation_request() and response.frame == self.page.main_frame:
            self._status = response.status

    # ----- actions; each returns a fresh observation -----

    async def _run(self, action) -> Observation:
        self.held, self._status, self._posted = None, None, False
        try:
            await action()
        except PlaywrightError:
            if not self.held:  # a held navigation aborts with an error; that is expected
                raise
        try:
            await self.page.wait_for_load_state("load", timeout=10_000)
        except PlaywrightError:
            pass
        return await self.observe()

    async def goto(self, path: str) -> Observation:
        return await self._run(lambda: self.page.goto(urljoin(self.base, path)))

    async def click(self, ref: str) -> Observation:
        return await self._run(lambda: self._el(ref).click(timeout=5_000))

    async def fill(self, ref: str, value: str) -> Observation:
        return await self._run(lambda: self._el(ref).fill(value, timeout=5_000))

    async def select(self, ref: str, option_text: str) -> Observation:
        return await self._run(lambda: self._el(ref).select_option(label=option_text, timeout=5_000))

    def _el(self, ref: str):
        return self.page.locator(f'[data-agent-ref="{ref}"]')

    async def observe(self) -> Observation:
        snapshot = await self.page.evaluate(SNAPSHOT_JS)
        text = await self.page.evaluate("() => document.body ? document.body.innerText : ''")
        u = urlparse(self.page.url)
        return Observation(u.path + (f"?{u.query}" if u.query else ""), snapshot, text, self._status, self._posted)

    async def login(self) -> None:
        """Sign in with the vault credentials. Keeps the login page's `next` so GET flows land where they were going."""
        login = self.company.profile["portal"]["login"]
        if urlparse(self.page.url).path != login["path"]:
            await self.page.goto(urljoin(self.base, login["path"]))
        await self.page.fill(login["user_field"], os.environ.get("SANDBOX_USER", ""))
        await self.page.fill(login["password_field"], os.environ.get("SANDBOX_PASSWORD", ""))
        await self.page.click(login["submit"])
        await self.page.wait_for_load_state("load")

    def on_login_page(self) -> bool:
        return urlparse(self.page.url).path == self.company.profile["portal"]["login"]["path"]

    async def fetch(self, href: str) -> tuple[bytes | None, Decision]:
        """Download a link's target with the session's cookies (gate-checked like any request)."""
        url = urljoin(self.page.url, href)
        decision = decide("GET", url, None, self.company, self.mode, self.approved)
        if decision.action != "allow":
            return None, decision
        resp = await self.context.request.get(url)
        return await resp.body(), decision

    async def screenshot(self, path: str) -> None:
        await self.page.screenshot(path=path)


_browsers: dict[str, Browser] = {}


async def get_browser(thread_id: str, company: Company, headed: bool = False) -> tuple[Browser, bool]:
    """The session's browser, and whether it was just (re)started."""
    if thread_id in _browsers:
        return _browsers[thread_id], False
    browser = Browser(company, headed)
    await browser.start()
    _browsers[thread_id] = browser
    return browser, True


async def close_browser(thread_id: str) -> None:
    if browser := _browsers.pop(thread_id, None):
        await browser.close()
