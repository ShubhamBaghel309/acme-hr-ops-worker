"""Acme Corp sandbox: a fake company portal (Mail, HR, IT) for the agent to operate.

Plain server-rendered HTML forms on purpose: the agent has to use the UI the way a
person would. `/admin/*` exists only for the eval harness (token-protected; the
agent's browser also blocks it).

Run: uvicorn sandbox.app:app --port 8001
"""
import os
import re
import secrets
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sandbox import scenarios
from sandbox.make_pdfs import FILES, ensure_pdfs

load_dotenv()
USER = os.getenv("SANDBOX_USER", "ops.bot@acme.example")
PASSWORD = os.getenv("SANDBOX_PASSWORD", "acme-sandbox-pass")
ADMIN_TOKEN = os.getenv("SANDBOX_ADMIN_TOKEN", "local-admin-token")
EMAIL_RE = re.compile(r"^[^@\s,]+@[^@\s,]+\.[a-z]{2,}$", re.I)
DMY_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

ensure_pdfs()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["dmy"] = lambda iso: datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y") if iso else ""

app = FastAPI(title="Acme sandbox")
db = scenarios.build(os.getenv("SANDBOX_SCENARIO", "onboard_happy"))
sessions: dict[str, list[str]] = {}  # session token -> flash messages waiting to be shown


# ---------- helpers ----------

def _session(request: Request) -> str | None:
    token = request.cookies.get("acme_session")
    return token if token in sessions else None


def _flash(request: Request, message: str) -> None:
    if token := _session(request):
        sessions[token].append(message)


def _render(request: Request, template: str, status_code: int = 200, **ctx):
    token = _session(request)
    flashes = list(sessions[token]) if token else []
    if token:
        sessions[token].clear()
    context = {"user": USER if token else None, "flashes": flashes, "labels": db["labels"], **ctx}
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def _not_found(request: Request, what: str):
    return _render(request, "error.html", 404, title="Not found", message=f"{what} was not found.")


def _referer_path(request: Request) -> str:
    return urlparse(request.headers.get("referer", "/")).path or "/"


def _fire(kind: str, path: str) -> bool:
    """True exactly once per chaos event, if the current scenario enables it."""
    flag = db["chaos"].get(kind)
    key = kind if flag is True else f"{kind}:{path}"
    if (flag is True or (isinstance(flag, list) and path in flag)) and key not in db["fired"]:
        db["fired"].append(key)
        return True
    return False


def _guard(request: Request):
    """Redirect to login when signed out. POSTs may also hit the session-expiry chaos."""
    if not _session(request):
        path = request.url.path if request.method == "GET" else _referer_path(request)
        return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
    if request.method == "POST" and _fire("expire_session_on_post", request.url.path):
        sessions.pop(_session(request), None)
        return RedirectResponse(f"/login?next={quote(_referer_path(request))}&expired=1", status_code=303)
    return None


def _parse_dmy(text: str) -> str | None:
    m = DMY_RE.match(text.strip())
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    except ValueError:
        return None


def _employee(code: str):
    return next((e for e in db["employees"] if e["code"] == code), None)


def _ticket(tid: str):
    return next((t for t in db["tickets"] if t["id"] == tid), None)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------- auth ----------

@app.get("/login")
def login_page(request: Request, next: str = "/", expired: int = 0):
    return _render(request, "login.html", next=next, expired=bool(expired))


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    nxt = str(form.get("next") or "/")
    if form.get("email") == USER and form.get("password") == PASSWORD:
        token = secrets.token_hex(16)
        sessions[token] = []
        resp = RedirectResponse(nxt if nxt.startswith("/") else "/", status_code=303)
        resp.set_cookie("acme_session", token, httponly=True)
        return resp
    return _render(request, "login.html", next=nxt, error="Incorrect email or password.")


@app.get("/logout")
def logout(request: Request):
    sessions.pop(_session(request), None)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("acme_session")
    return resp


@app.get("/")
def home(request: Request):
    return _guard(request) or _render(request, "home.html")


# ---------- mail ----------

@app.get("/mail")
def inbox(request: Request):
    if r := _guard(request):
        return r
    mails = sorted((m for m in db["emails"] if m["folder"] == "inbox"), key=lambda m: m["date"], reverse=True)
    return _render(request, "mail_list.html", folder="Inbox", mails=mails)


@app.get("/mail/sent")
def sent(request: Request):
    if r := _guard(request):
        return r
    mails = sorted((m for m in db["emails"] if m["folder"] == "sent"), key=lambda m: m["date"], reverse=True)
    return _render(request, "mail_list.html", folder="Sent", mails=mails)


@app.get("/mail/compose")
def compose(request: Request):
    return _guard(request) or _render(request, "mail_compose.html", form={}, errors=[])


@app.post("/mail/send")
async def send_mail(request: Request):
    if r := _guard(request):
        return r
    form = dict(await request.form())
    recipients = [r.strip() for r in form.get("to", "").split(",") if r.strip()]
    errors = []
    if not recipients or not all(EMAIL_RE.match(r) for r in recipients):
        errors.append("Enter valid recipient email addresses, separated by commas.")
    if not form.get("subject", "").strip():
        errors.append("Subject is required.")
    if not form.get("body", "").strip():
        errors.append("Message body is required.")
    if errors:
        return _render(request, "mail_compose.html", form=form, errors=errors)
    db["emails"].append({
        "id": max(m["id"] for m in db["emails"]) + 1, "folder": "sent", "from": USER,
        "to": ", ".join(recipients), "subject": form["subject"].strip(), "body": form["body"].strip(),
        "date": _now(), "attachment": None,
    })
    _flash(request, f"Message sent to {', '.join(recipients)}.")
    return RedirectResponse("/mail/sent", status_code=303)


@app.get("/mail/{mail_id}")
def read_mail(request: Request, mail_id: int):
    if r := _guard(request):
        return r
    mail = next((m for m in db["emails"] if m["id"] == mail_id), None)
    return _render(request, "mail_message.html", mail=mail) if mail else _not_found(request, "Message")


@app.get("/mail/{mail_id}/attachment")
def attachment(request: Request, mail_id: int):
    if r := _guard(request):
        return r
    mail = next((m for m in db["emails"] if m["id"] == mail_id and m["attachment"]), None)
    if not mail:
        return _not_found(request, "Attachment")
    att = mail["attachment"]
    return FileResponse(FILES / att["file"], media_type="application/pdf", filename=att["filename"])


# ---------- HR ----------

def _hr_choices():
    return {
        "departments": scenarios.DEPARTMENTS,
        "locations": scenarios.LOCATIONS,
        "managers": [e for e in db["employees"] if e["status"] == "Active"],
    }


@app.get("/hr")
def hr_root():
    return RedirectResponse("/hr/employees", status_code=303)


@app.get("/hr/employees")
def employees(request: Request, q: str = ""):
    if r := _guard(request):
        return r
    rows = db["employees"]
    if q.strip():
        needle = q.strip().lower()
        rows = [e for e in rows if needle in " ".join(
            [e["full_name"], e["personal_email"], e["work_email"], e["code"]]).lower()]
    return _render(request, "hr_list.html", rows=rows, q=q, names={e["code"]: e["full_name"] for e in db["employees"]})


@app.get("/hr/employees/new")
def new_employee(request: Request):
    return _guard(request) or _render(request, "hr_new.html", form={}, errors=[], **_hr_choices())


@app.post("/hr/employees")
async def create_employee(request: Request):
    if r := _guard(request):
        return r
    form = {k: str(v).strip() for k, v in (await request.form()).items()}
    labels = {"full_name": "Full name", "personal_email": "Personal email", "title": "Job title",
              "department": "Department", "manager": "Manager", "start_date": "Start date", "location": "Location"}
    errors = [f"{label} is required." for field, label in labels.items() if not form.get(field)]
    start = _parse_dmy(form.get("start_date", ""))
    if form.get("personal_email") and not EMAIL_RE.match(form["personal_email"]):
        errors.append("Personal email is not a valid email address.")
    if form.get("start_date") and not start:
        errors.append("Start date must be in DD/MM/YYYY format.")
    if form.get("department") and form["department"] not in scenarios.DEPARTMENTS:
        errors.append("Choose a department from the list.")
    if form.get("location") and form["location"] not in scenarios.LOCATIONS:
        errors.append("Choose a location from the list.")
    if form.get("manager") and not _employee(form["manager"]):
        errors.append("Choose a manager from the list.")
    if errors:
        return _render(request, "hr_new.html", form=form, errors=errors, **_hr_choices())

    code = f"E{max(int(e['code'][1:]) for e in db['employees']) + 1}"
    handle = form["full_name"].lower().replace(" ", ".")
    taken = sum(1 for e in db["employees"] if e["work_email"].startswith(handle))
    emp = {
        "code": code, "full_name": form["full_name"], "personal_email": form["personal_email"],
        "work_email": f"{handle}{taken + 1 if taken else ''}@acme.example", "title": form["title"],
        "department": form["department"], "manager": form["manager"], "start_date": start,
        "location": form["location"], "status": "Active", "last_day": None,
    }
    if _fire("silent_drop", "/hr/employees"):
        return _render(request, "hr_detail.html", emp=emp, manager=_employee(emp["manager"]), tickets=[],
                       notice=f"Employee {code} created.", errors=[])
    db["employees"].append(emp)
    if _fire("ghost_write", "/hr/employees"):
        return _render(request, "error.html", 500, title="Internal Server Error",
                       message="Something went wrong while saving. Please try again later.")
    _flash(request, f"Employee {code} created.")
    return RedirectResponse(f"/hr/employees/{code}", status_code=303)


@app.get("/hr/employees/{code}")
def employee_detail(request: Request, code: str):
    if r := _guard(request):
        return r
    emp = _employee(code)
    if not emp:
        return _not_found(request, f"Employee {code}")
    tickets = [t for t in db["tickets"] if t["employee"] == code]
    return _render(request, "hr_detail.html", emp=emp, manager=_employee(emp["manager"] or ""), tickets=tickets, errors=[])


@app.post("/hr/employees/{code}/deactivate")
async def deactivate(request: Request, code: str):
    if r := _guard(request):
        return r
    emp = _employee(code)
    if not emp:
        return _not_found(request, f"Employee {code}")
    form = await request.form()
    last_day = _parse_dmy(str(form.get("last_day", "")))
    errors = []
    if not last_day:
        errors.append("Last working day must be in DD/MM/YYYY format.")
    if emp["status"] == "Inactive":
        errors.append("This employee is already inactive.")
    if errors:
        tickets = [t for t in db["tickets"] if t["employee"] == code]
        return _render(request, "hr_detail.html", emp=emp, manager=_employee(emp["manager"] or ""), tickets=tickets, errors=errors)
    emp["status"], emp["last_day"] = "Inactive", last_day
    _flash(request, f"Employee {code} deactivated. Last working day: {datetime.fromisoformat(last_day):%d/%m/%Y}.")
    return RedirectResponse(f"/hr/employees/{code}", status_code=303)


# ---------- IT helpdesk ----------

@app.get("/it")
def it_root():
    return RedirectResponse("/it/tickets", status_code=303)


@app.get("/it/tickets")
def tickets(request: Request, q: str = ""):
    if r := _guard(request):
        return r
    names = {e["code"]: e["full_name"] for e in db["employees"]}
    rows = db["tickets"]
    if q.strip():
        needle = q.strip().lower()
        rows = [t for t in rows if needle in f"{t['id']} {names.get(t['employee'], '')} {t['employee']}".lower()]
    return _render(request, "it_list.html", rows=rows, q=q, names=names)


@app.get("/it/tickets/new")
def new_ticket(request: Request):
    return _guard(request) or _render(request, "it_new.html", form={}, chosen=[], errors=[], employees=db["employees"],
                                      types=scenarios.TICKET_TYPES, catalog=scenarios.ACCESS_CATALOG)


@app.post("/it/tickets")
async def create_ticket(request: Request):
    if r := _guard(request):
        return r
    raw = await request.form()
    form = {k: str(v).strip() for k, v in raw.items() if k != "access"}
    chosen = [str(a) for a in raw.getlist("access")]
    errors = []
    if not _employee(form.get("employee", "")):
        errors.append("Choose the employee this ticket is for.")
    if form.get("type") not in scenarios.TICKET_TYPES:
        errors.append("Choose a ticket type.")
    if not chosen:
        errors.append("Select at least one item.")
    if any(a not in scenarios.ACCESS_CATALOG for a in chosen):
        errors.append("Unknown item selected.")
    if errors:
        return _render(request, "it_new.html", form=form, chosen=chosen, errors=errors, employees=db["employees"],
                       types=scenarios.TICKET_TYPES, catalog=scenarios.ACCESS_CATALOG)

    tid = f"IT-{max(int(t['id'][3:]) for t in db['tickets']) + 1}"
    ticket = {"id": tid, "employee": form["employee"], "type": form["type"], "status": "Open",
              "created": date.today().isoformat(), "notes": form.get("notes", ""),
              "items": [{"name": a, "status": "Pending"} for a in chosen]}
    if _fire("silent_drop", "/it/tickets"):
        return _render(request, "it_detail.html", t=ticket, emp=_employee(ticket["employee"]), notice=f"Ticket {tid} created.")
    db["tickets"].append(ticket)
    if _fire("ghost_write", "/it/tickets"):
        return _render(request, "error.html", 500, title="Internal Server Error",
                       message="Something went wrong while saving. Please try again later.")
    _flash(request, f"Ticket {tid} created.")
    return RedirectResponse(f"/it/tickets/{tid}", status_code=303)


@app.get("/it/tickets/{tid}")
def ticket_detail(request: Request, tid: str):
    if r := _guard(request):
        return r
    t = _ticket(tid)
    return _render(request, "it_detail.html", t=t, emp=_employee(t["employee"])) if t else _not_found(request, f"Ticket {tid}")


# ---------- admin (eval harness only) ----------

def _admin_denied(request: Request):
    if request.headers.get("x-admin-token") != ADMIN_TOKEN:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return None


@app.post("/admin/reset")
def admin_reset(request: Request, scenario: str):
    global db
    if r := _admin_denied(request):
        return r
    if scenario not in scenarios.SCENARIOS:
        return JSONResponse({"error": f"unknown scenario {scenario}"}, status_code=404)
    db = scenarios.build(scenario)
    sessions.clear()
    return {"ok": True, "scenario": scenario}


@app.get("/admin/state")
def admin_state(request: Request):
    if r := _admin_denied(request):
        return r
    return {
        "scenario": db["scenario"],
        "employees": db["employees"],
        "tickets": db["tickets"],
        "sent": [m for m in db["emails"] if m["folder"] == "sent"],
        "fired": db["fired"],
    }


@app.get("/admin/scenarios")
def admin_scenarios(request: Request):
    return _admin_denied(request) or {"scenarios": list(scenarios.SCENARIOS)}
