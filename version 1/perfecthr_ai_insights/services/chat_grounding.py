# -*- coding: utf-8 -*-
"""Our-side grounding for the HR Chatbot.

The hr_chatbot worker accepts only a `question` string, so to make it answer
with the organization's *own* data we gather relevant facts from the live Odoo
database and pack a compact context block in front of the user's question.

The model's context window is small, so we send a curated, budgeted slice -- the
asking employee's own record plus a non-sensitive org snapshot -- never the whole
database. Every section is guarded so a missing optional module (hr_holidays,
hr_contract, ...) just yields less context instead of an error. Extend the
_*_facts helpers to widen what the bot knows.
"""
import logging

_logger = logging.getLogger(__name__)

# Char budget for the grounding context (leaves room for the answer in a small
# model window). Tune here if you switch to a larger runtime model.
MAX_CONTEXT_CHARS = 6000


def _truncate(text, limit):
    text = text or ''
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + ' …[truncated]'


def _employee_for_partner(env, partner_id):
    """Best-effort map: the message author (a partner) -> their hr.employee."""
    if not partner_id or 'hr.employee' not in env:
        return env['hr.employee'].browse() if 'hr.employee' in env else None
    Employee = env['hr.employee'].sudo()
    user = env['res.users'].sudo().search([('partner_id', '=', partner_id)], limit=1)
    emp = Employee.browse()
    if user:
        emp = Employee.search([('user_id', '=', user.id)], limit=1)
    if not emp:
        emp = Employee.search([('work_contact_id', '=', partner_id)], limit=1)
    return emp


def _leave_balance_lines(env, emp):
    """Approved allocations minus approved leaves, per leave type. Defensive:
    only runs if hr_holidays is installed."""
    if 'hr.leave.allocation' not in env or not emp:
        return []
    try:
        allocated = {}
        allocs = env['hr.leave.allocation'].sudo().search([
            ('employee_id', '=', emp.id), ('state', '=', 'validate')])
        for a in allocs:
            name = a.holiday_status_id.name or '?'
            allocated[name] = allocated.get(name, 0.0) + (a.number_of_days or 0.0)
        used = {}
        if 'hr.leave' in env:
            for lv in env['hr.leave'].sudo().search([
                    ('employee_id', '=', emp.id), ('state', '=', 'validate')]):
                name = lv.holiday_status_id.name or '?'
                used[name] = used.get(name, 0.0) + (lv.number_of_days or 0.0)
        lines = []
        for name in sorted(allocated):
            remaining = allocated[name] - used.get(name, 0.0)
            lines.append("  - %s: %.1f remaining (allocated %.1f, taken %.1f)"
                         % (name, remaining, allocated[name], used.get(name, 0.0)))
        return lines
    except Exception as exc:  # noqa: BLE001 - grounding must never break the chat
        _logger.warning("Leave-balance grounding skipped: %s", exc)
        return []


def _employee_facts(env, emp):
    lines = [
        "Name: %s" % (emp.name or '-'),
        "Job title: %s" % (emp.job_title or (emp.job_id.name if emp.job_id else '') or '-'),
        "Department: %s" % (emp.department_id.name or '-'),
        "Manager: %s" % (emp.parent_id.name or '-'),
        "Work email: %s" % (emp.work_email or '-'),
    ]
    balances = _leave_balance_lines(env, emp)
    if balances:
        lines.append("Leave balances:")
        lines.extend(balances)
    return lines


def _org_facts(env, company):
    """Non-sensitive org snapshot. Deliberately excludes other employees'
    personal/pay data; only aggregate, structural facts."""
    lines = ["Company: %s" % (company.name or '-')]
    try:
        if 'hr.employee' in env:
            lines.append("Headcount: %s" % env['hr.employee'].sudo().search_count([]))
        if 'hr.department' in env:
            names = env['hr.department'].sudo().search([], limit=40).mapped('name')
            if names:
                lines.append("Departments: %s" % ", ".join(n for n in names if n))
        if 'hr.job' in env:
            names = env['hr.job'].sudo().search([], limit=40).mapped('name')
            if names:
                lines.append("Job positions: %s" % ", ".join(n for n in names if n))
        if 'hr.leave.type' in env:
            names = env['hr.leave.type'].sudo().search([], limit=40).mapped('name')
            if names:
                lines.append("Leave types: %s" % ", ".join(n for n in names if n))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Org-snapshot grounding partial: %s", exc)
    return lines


def build_grounded_question(channel, author_partner_id, question):
    """Return the grounded prompt string to send as the worker's `question`."""
    env = channel.env
    company = env.company
    lines = [
        'You are PerfectHR\'s internal HR assistant for the organization "%s".'
        % (company.name or 'the company'),
        "Answer the employee's question using ONLY the organizational data below. "
        "If the data does not contain the answer, say you don't have that "
        "information and suggest contacting HR. Be concise and specific.",
        "",
        "[ASKING EMPLOYEE]",
    ]
    emp = _employee_for_partner(env, author_partner_id)
    if emp:
        lines.extend(_employee_facts(env, emp))
    else:
        partner = env['res.partner'].sudo().browse(author_partner_id)
        lines.append("Name: %s (no linked employee record found)"
                     % (partner.name or '-'))

    lines.append("")
    lines.append("[ORGANIZATION SNAPSHOT]")
    lines.extend(_org_facts(env, company))

    context = _truncate("\n".join(str(l) for l in lines), MAX_CONTEXT_CHARS)
    return "%s\n\n[EMPLOYEE QUESTION]\n%s" % (context, question)
