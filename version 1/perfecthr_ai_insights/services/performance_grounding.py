# -*- coding: utf-8 -*-
"""Ground the Performance Analysis in an employee's REAL performance data.

Pulls from EVERY installed source (defensively — a missing module/field just
yields less context):
  - hr.appraisal (oh_appraisal + hr_employee_appraisal): OKR / 9-box criteria,
    final_score, performance_rating, and the survey final_evaluation narrative;
  - oh.appraisal.result (oh_appraisal_ext): functional/role/common scores,
    final_percentage, rating_label, notes;
  - Skills (employee_skill_ids);
  - task.management (via user_id.employee_id).

Produces (payload, seed):
  - the Score/Rating SEED from the authoritative hr.appraisal.final_score /
    performance_rating (fallback: oh.appraisal.result.final_percentage);
  - a compact, comprehensive summary packed into `review_period` (the only
    free-text field the runtime worker reads) so the AI narrative is grounded;
  - the full detail in `performance_data` (best-effort extra field).
"""
import logging

_logger = logging.getLogger(__name__)

_PERIOD_CAP = 1300      # review_period is sent to the worker — keep compact
_CONTEXT_CAP = 8000


def _f(rec, name, default=''):
    try:
        return rec[name] if name in rec._fields else default
    except Exception:  # noqa: BLE001
        return default


def _clean(text):
    if not text:
        return ''
    import re
    text = re.sub(r'<[^>]+>', ' ', str(text))
    return re.sub(r'\s+', ' ', text).strip()


def _rating_label(appraisal):
    val = _f(appraisal, 'performance_rating')
    if not val:
        return ''
    try:
        return dict(appraisal._fields['performance_rating'].selection).get(val, val)
    except Exception:  # noqa: BLE001
        return val


def _period(appraisal):
    return _f(appraisal, 'app_period_from') or _f(appraisal, 'appraisal_deadline') or ''


def _lines(appraisal):
    def g(name):
        return appraisal[name] if name in appraisal._fields else []
    kind = _f(appraisal, 'appraisal_template_type')
    if kind == 'okr':
        return g('okr_line_ids')
    if kind == 'ninebox':
        return list(g('ninebox_performance_line_ids')) + list(g('ninebox_potential_line_ids'))
    return g('okr_line_ids') or g('ninebox_performance_line_ids')


def _category_pcts(appraisal):
    cats = {}
    for line in _lines(appraisal):
        lt = _f(line, 'line_type') or 'other'
        agg = cats.setdefault(lt, [0.0, 0.0])
        agg[0] += float(_f(line, 'actual_value', 0) or 0)
        agg[1] += float(_f(line, 'target_value', 0) or 0)
    return {k: (v[0] / v[1] * 100 if v[1] else 0.0) for k, v in cats.items()}


def _top_bottom(appraisal):
    items = [l for l in _lines(appraisal) if _f(l, 'objective_breakdown')]
    if not items:
        return [], []
    items = sorted(items, key=lambda l: float(_f(l, 'achievement_percentage', 0) or 0))
    fmt = lambda l: "%s (%.0f%%)" % (_f(l, 'objective_breakdown'),
                                     float(_f(l, 'achievement_percentage', 0) or 0))
    weak = [fmt(l) for l in items[:2]]
    strong = [fmt(l) for l in list(reversed(items))[:2]]
    return strong, weak


def _profile(employee):
    return ("Employee: %s | Job: %s | Department: %s | Manager: %s"
            % (employee.name or '-',
               employee.job_title or (employee.job_id.name if employee.job_id else '') or '-',
               employee.department_id.name if employee.department_id else '-',
               employee.parent_id.name if employee.parent_id else '-'))


# ── per-source gatherers: each returns {'compact': str, 'detail': [str], ...} ──

def _appraisals(employee):
    env = employee.env
    out = {'compact': '', 'detail': [], 'scored': None}
    if 'hr.appraisal' not in env:
        return out
    recent = env['hr.appraisal'].sudo().search(
        [('employee_id', '=', employee.id)],
        order='appraisal_deadline desc, id desc', limit=4)
    if not recent:
        return out
    scored = recent.filtered(lambda a: (a.final_score or 0) > 0)
    out['scored'] = scored
    if scored:
        latest = scored[0]
        cats = _category_pcts(latest)
        cat_str = ", ".join("%s %.0f%%" % (k.capitalize(), v) for k, v in cats.items() if v)
        strong, weak = _top_bottom(latest)
        parts = ["OKR/9-box appraisal %.1f%% (%s)"
                 % (latest.final_score or 0.0, _rating_label(latest) or 'n/a')]
        if cat_str:
            parts.append("by area: " + cat_str)
        if strong:
            parts.append("strongest: " + "; ".join(strong))
        if weak:
            parts.append("weakest: " + "; ".join(weak))
        if len(scored) > 1:
            trend = " -> ".join("%.0f%%" % (a.final_score or 0) for a in list(reversed(scored)))
            parts.append("trend: " + trend)
        out['compact'] = ". ".join(parts)
    # detail (all recent appraisals incl. survey narrative)
    for idx, a in enumerate(recent):
        tag = "LATEST" if idx == 0 else "PREVIOUS"
        out['detail'].append(
            "[%s APPRAISAL] period %s, type %s, score %.1f%% (%s)"
            % (tag, _period(a) or 'n/a', _f(a, 'appraisal_template_type', '?'),
               a.final_score or 0.0, _rating_label(a) or 'n/a'))
        for line in _lines(a):
            out['detail'].append(
                "  - [%s] %s: actual %s / target %s = %.0f%% (weight %s%%)"
                % (_f(line, 'line_type', '?'), _f(line, 'objective_breakdown', '?'),
                   _f(line, 'actual_value', '?'), _f(line, 'target_value', '?'),
                   float(_f(line, 'achievement_percentage', 0) or 0),
                   _f(line, 'weightage', '?')))
        evaluation = _clean(_f(a, 'final_evaluation'))
        if evaluation:
            out['detail'].append("  manager/survey evaluation: " + evaluation)
    return out


def _ext_results(employee):
    env = employee.env
    out = {'compact': '', 'detail': [], 'records': None}
    if 'oh.appraisal.result' not in env:
        return out
    try:
        recs = env['oh.appraisal.result'].sudo().search(
            [('employee_id', '=', employee.id)], order='date desc', limit=3)
    except Exception:  # noqa: BLE001
        return out
    if not recs:
        return out
    out['records'] = recs
    latest = recs[0]
    out['compact'] = ("OpenHRMS appraisal result %.0f%% (%s)"
                      % (float(_f(latest, 'final_percentage', 0) or 0),
                         _f(latest, 'rating_label', '') or 'n/a'))
    for r in recs:
        out['detail'].append(
            "[APPRAISAL RESULT] %s: final %.1f%% (functional %.1f, role %.1f, common %.1f)"
            % (_f(r, 'rating_label', '') or 'result', float(_f(r, 'final_percentage', 0) or 0),
               float(_f(r, 'functional_score', 0) or 0), float(_f(r, 'role_score', 0) or 0),
               float(_f(r, 'common_score', 0) or 0)))
        notes = _clean(_f(r, 'notes'))
        if notes:
            out['detail'].append("  notes: " + notes)
    return out


def _skills(employee):
    out = {'compact': '', 'detail': []}
    if 'employee_skill_ids' not in employee._fields:
        return out
    try:
        skills = employee.employee_skill_ids
    except Exception:  # noqa: BLE001
        return out
    if not skills:
        return out
    names = []
    for s in skills:
        sk = _f(s, 'skill_id')
        lvl = _f(s, 'skill_level_id')
        label = (sk.name if sk else '')
        if lvl:
            label += " (%s)" % lvl.name
        if label.strip():
            names.append(label)
    if names:
        out['compact'] = "skills: " + ", ".join(names[:6])
        out['detail'] = ["[SKILLS] " + ", ".join(names)]
    return out


def _tasks(employee):
    env = employee.env
    out = {'compact': '', 'detail': []}
    if 'task.management' not in env:
        return out
    try:
        tasks = env['task.management'].sudo().search(
            [('user_id.employee_id', '=', employee.id)], limit=80)
    except Exception:  # noqa: BLE001
        return out
    if not tasks:
        return out
    done = sum(1 for t in tasks if _f(t, 'kanban_state') == 'done')
    planned = sum(float(_f(t, 'planned_hours', 0) or 0) for t in tasks)
    effective = sum(float(_f(t, 'effective_hours', 0) or 0) for t in tasks)
    out['compact'] = "tasks: %s/%s done" % (done, len(tasks))
    out['detail'] = ["[TASKS] %s assigned, %s done; planned %.1fh vs logged %.1fh"
                     % (len(tasks), done, planned, effective)]
    return out


def dataset_summary(employee, dataset_id=None):
    """Structured performance data returned to the runtime worker's data-callback
    (GET /api/performance-management/dataset-summary). The worker fetches this
    mid-execution to ground its analysis."""
    appr = _appraisals(employee)
    ext = _ext_results(employee)
    skills = _skills(employee)
    tasks = _tasks(employee)

    score, rating = 0.0, ''
    if appr['scored']:
        latest = appr['scored'][0]
        score = round(latest.final_score or 0.0, 2)
        rating = _rating_label(latest)
    elif ext['records']:
        r = ext['records'][0]
        score = round(float(_f(r, 'final_percentage', 0) or 0), 2)
        rating = _f(r, 'rating_label', '')

    appraisals = []
    for a in (appr['scored'] or []):
        appraisals.append({
            'period': str(_period(a)),
            'type': _f(a, 'appraisal_template_type', ''),
            'score': round(a.final_score or 0.0, 2),
            'rating': _rating_label(a),
            'evaluation': _clean(_f(a, 'final_evaluation')),
            'criteria': [{
                'category': _f(l, 'line_type', ''),
                'objective': _f(l, 'objective_breakdown', ''),
                'target': float(_f(l, 'target_value', 0) or 0),
                'actual': float(_f(l, 'actual_value', 0) or 0),
                'achievement_pct': round(float(_f(l, 'achievement_percentage', 0) or 0), 1),
                'weightage': float(_f(l, 'weightage', 0) or 0),
            } for l in _lines(a)],
        })

    summary = " | ".join(s['compact'] for s in (appr, ext, skills, tasks) if s['compact'])
    detail = "\n".join([_profile(employee)] + appr['detail'] + ext['detail']
                       + skills['detail'] + tasks['detail'])
    return {
        'dataset_id': dataset_id or '',
        'employee_id': str(employee.id),
        'employee_name': employee.name or '',
        'department': employee.department_id.name if employee.department_id else '',
        'job_title': employee.job_title or (employee.job_id.name if employee.job_id else ''),
        'performance_score': score,
        'rating': rating,
        'summary': summary or 'No structured appraisal records on file.',
        'detail': detail,
        'appraisals': appraisals,
    }


def build_performance_request(employee):
    """Return (payload, result_seed) — see module docstring."""
    from odoo.addons.perfecthr_ai_core.adapters.performance_management import base_payload
    payload = base_payload(employee)

    appr = _appraisals(employee)
    ext = _ext_results(employee)
    skills = _skills(employee)
    tasks = _tasks(employee)

    # Score/Rating seed — authoritative hr.appraisal, else the OpenHRMS result.
    seed = {}
    if appr['scored']:
        latest = appr['scored'][0]
        seed = {'score': round(latest.final_score or 0.0, 2), 'label': _rating_label(latest)}
    elif ext['records']:
        r = ext['records'][0]
        seed = {'score': round(float(_f(r, 'final_percentage', 0) or 0), 2),
                'label': _f(r, 'rating_label', '')}

    # review_period — compact comprehensive summary the worker actually reads.
    compact = [s['compact'] for s in (appr, ext, skills, tasks) if s['compact']]
    if compact:
        payload['review_period'] = (" | ".join(compact))[:_PERIOD_CAP]

    # performance_data — full detail (best-effort extra field).
    detail = [_profile(employee), ""]
    for s in (appr, ext, skills, tasks):
        detail.extend(s['detail'])
    if len(detail) <= 2:
        detail.append("No structured appraisal/performance records on file.")
    payload['performance_data'] = ("\n".join(str(x) for x in detail))[:_CONTEXT_CAP]

    return payload, seed
