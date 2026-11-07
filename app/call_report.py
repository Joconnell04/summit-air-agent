"""The urgent-call email as a report the on-call person can act on from their inbox (Jackson, 6
November): who, where, what kind of customer, the problem in the caller's words, how to get in,
the visit if one was booked, the technician's summary when it has been written, and what the
caller was told. Built from the server's own records; a fact the call never settled says so
rather than being left out.

    subject, text, html = build(conn, tenant, flag, owner_view_url, fsm)

The plain text and the HTML carry the same facts; the HTML is inline-styled tables, which every
mail client renders.
"""

import html as html_lib
import json
import sqlite3

from . import lookup, records
from .db import parse_iso
from .fsm import FsmRejected, FsmUnavailable
from .tenant import Tenant
from .text import phone_display

NOT_SETTLED = "not settled on the call"
UNSAID = ("", "not asked yet", "not asked", "none", "unknown")


def build(
    conn: sqlite3.Connection,
    tenant: Tenant,
    flag: sqlite3.Row,
    owner_view_url: str = "",
    fsm=None,
) -> tuple[str, str, str]:
    call = records.get_call(conn, flag["call_id"])
    readback = records.readback(call) or {}
    booking = conn.execute(
        "SELECT * FROM bookings WHERE call_id = ? AND kind = 'book' AND status IN "
        "('booked', 'done', 'queued', 'sending') ORDER BY id DESC LIMIT 1",
        (call["id"],),
    ).fetchone()
    request = json.loads(booking["request_json"]) if booking else {}
    details = request.get("details") or {}

    at_a_glance = [
        ("Customer", _said(details.get("name"))),
        ("Phone", phone_display(readback.get("phone") or call["caller_id"])),
        ("Address", _address(readback)),
        ("Customer type", "Commercial" if call["commercial"] else _customer_type(conn, call)),
        ("Membership", _membership(conn, tenant, call, fsm)),
        ("Job", _job(tenant, booking)),
        ("Priority", _priority(booking)),
        ("Visit", _visit(conn, tenant, booking)),
    ]
    problem = [
        ("Issue", _said(details.get("issue")) if details else flag["reason"]),
        ("Since", _said(details.get("since"))),
        ("System", _said(details.get("system_type"))),
        ("Tried", _said(details.get("tried"))),
        ("Also noted", _said(details.get("also_noted"))),
    ]
    if details.get("inside_temp_f") is not None:
        problem.append(("Inside", f"{details['inside_temp_f']:g} F"))
    getting_in = [
        ("Who's home", _said(details.get("who_home"))),
        ("On arrival", _said(details.get("arrival_notes"))),
        ("Approves work", _said(details.get("approver"))),
    ]
    summary = _technician_summary(conn, call["id"])
    raised = tenant.local(parse_iso(flag["created_at"])).strftime("%-I:%M %p")
    told = _caller_told(conn, tenant, call)
    link = f"{owner_view_url.rstrip('/')}/owner/calls/{call['id']}" if owner_view_url else ""

    subject = " ".join(f"{tenant.name} urgent: {flag['reason']}".split())[:120]
    text = _text(flag, raised, at_a_glance, problem, getting_in, summary, told, link)
    page = _html(tenant, flag, raised, at_a_glance, problem, getting_in, summary, told, link)
    return subject, text, page


def _said(value) -> str:
    value = " ".join(str(value or "").split())
    return NOT_SETTLED if value.lower() in UNSAID else value


def _address(readback: dict) -> str:
    if not readback.get("street"):
        return "no address confirmed on the call"
    unit = f", unit {readback['unit']}" if readback.get("unit") else ""
    return f"{readback['street']}{unit}, {readback.get('zip') or ''}".rstrip(", ")


def _customer_type(conn: sqlite3.Connection, call: sqlite3.Row) -> str:
    row = conn.execute(
        "SELECT customer_type FROM call_customers WHERE call_id = ? LIMIT 1", (call["id"],)
    ).fetchone()
    return (row["customer_type"] if row else "Residential").capitalize()


def _membership(conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, fsm) -> str:
    if fsm is None:
        return NOT_SETTLED
    try:
        for customer_id in records.scoped_customers(conn, call["id"]):
            summary = lookup.membership_summary(tenant, fsm.memberships(customer_id))
            if summary:
                return f"{summary['status']} ({summary.get('plan') or 'membership'})"
    except (FsmUnavailable, FsmRejected):
        return NOT_SETTLED
    return "Not a member"


def _job(tenant: Tenant, booking: sqlite3.Row | None) -> str:
    if not booking:
        return "nothing booked on the call"
    job = tenant.job_types.get(booking["job_type"] or "", {})
    return job.get("name") or (booking["job_type"] or "").replace("_", " ")


def _priority(booking: sqlite3.Row | None) -> str:
    if not booking or not booking["priority"]:
        return NOT_SETTLED
    reasons = booking["priority_reasons"]
    return f"{booking['priority']} ({reasons})" if reasons else booking["priority"]


def _visit(conn: sqlite3.Connection, tenant: Tenant, booking: sqlite3.Row | None) -> str:
    if not booking or not booking["window_start"]:
        return "none booked"
    start = tenant.local(parse_iso(booking["window_start"]))
    end = tenant.local(parse_iso(booking["window_end"])) if booking["window_end"] else None
    when = start.strftime("%a %b %-d, %-I:%M %p")
    when += f" to {end.strftime('%-I:%M %p')}" if end else ""
    dispatched = conn.execute(
        "SELECT 1 FROM dispatches WHERE booking_id = ? AND replaced_at IS NULL", (booking["id"],)
    ).fetchone()
    return f"{when}{', technician dispatched' if dispatched else ''}"


def _technician_summary(conn: sqlite3.Connection, call_id: str) -> list[str]:
    row = conn.execute("SELECT lines_json FROM summaries WHERE call_id = ?", (call_id,)).fetchone()
    if not row or not row["lines_json"]:
        return []
    return [line for line in json.loads(row["lines_json"]) if str(line).strip()]


def _caller_told(conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row) -> str:
    callback = conn.execute(
        "SELECT due_at FROM callbacks WHERE call_id = ? AND status IN ('pending', 'overdue') "
        "ORDER BY due_at LIMIT 1",
        (call["id"],),
    ).fetchone()
    if not callback:
        return ""
    due = tenant.local(parse_iso(callback["due_at"])).strftime("%-I:%M %p")
    return f"Someone would do their best to call back by {due}."


def _text(flag, raised, glance, problem, getting_in, summary, told, link) -> str:
    out = [f"URGENT: {flag['reason']}", f"Raised {raised}", ""]
    for title, rows in (("AT A GLANCE", glance), ("THE PROBLEM", problem),
                        ("GETTING IN", getting_in)):
        out.append(title)
        out += [f"  {label}: {value}" for label, value in rows]
        out.append("")
    out.append("TECHNICIAN SUMMARY")
    out += [f"  - {line}" for line in summary] or ["  not written yet (it follows the call)"]
    if told:
        out += ["", f"Caller was told: {told}"]
    if link:
        out += ["", f"Call: {link}"]
    return "\n".join(out)


def _html(tenant, flag, raised, glance, problem, getting_in, summary, told, link) -> str:
    esc = html_lib.escape

    def table(rows):
        cells = "".join(
            f'<tr><td style="padding:6px 12px 6px 0;color:#5b6472;width:140px;'
            f'vertical-align:top;font-size:13px">{esc(label)}</td>'
            f'<td style="padding:6px 0;color:{"#9aa1ab" if value == NOT_SETTLED else "#1c2230"};'
            f'font-size:14px">{esc(value)}</td></tr>'
            for label, value in rows
        )
        return f'<table role="presentation" style="width:100%;border-collapse:collapse">{cells}</table>'

    def section(title, body):
        return (
            f'<div style="margin-top:20px"><div style="font-size:11px;letter-spacing:.08em;'
            f'text-transform:uppercase;color:#5b6472;border-bottom:1px solid #e3e6ea;'
            f'padding-bottom:6px;margin-bottom:6px">{esc(title)}</div>{body}</div>'
        )

    summary_html = (
        "<ul style=\"margin:4px 0 0 18px;padding:0;font-size:14px;color:#1c2230\">"
        + "".join(f'<li style="margin:3px 0">{esc(line)}</li>' for line in summary)
        + "</ul>"
        if summary
        else '<div style="font-size:14px;color:#9aa1ab">Not written yet; it follows the call.</div>'
    )
    told_html = (
        section("What the caller was told", f'<div style="font-size:14px">{esc(told)}</div>')
        if told
        else ""
    )
    button = (
        f'<div style="margin-top:24px"><a href="{esc(link)}" style="display:inline-block;'
        f'background:#1c2230;color:#ffffff;text-decoration:none;padding:10px 16px;'
        f'border-radius:6px;font-size:14px">Open the call</a></div>'
        if link
        else ""
    )
    return (
        '<div style="background:#f4f5f7;padding:24px 12px;font-family:-apple-system,'
        "'Segoe UI',Roboto,Helvetica,Arial,sans-serif\">"
        '<div style="max-width:620px;margin:0 auto;background:#ffffff;border:1px solid #e3e6ea;'
        'border-radius:8px;overflow:hidden">'
        '<div style="background:#b42318;color:#ffffff;padding:14px 20px">'
        f'<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.85">'
        f"{esc(tenant.name)} &middot; urgent call report</div>"
        f'<div style="font-size:18px;font-weight:600;margin-top:4px">{esc(flag["reason"])}</div>'
        f'<div style="font-size:13px;opacity:.85;margin-top:4px">Raised {esc(raised)}</div></div>'
        '<div style="padding:4px 20px 24px">'
        + section("At a glance", table(glance))
        + section("The problem", table(problem))
        + section("Getting in", table(getting_in))
        + section("Technician summary", summary_html)
        + told_html
        + button
        + "</div></div></div>"
    )
