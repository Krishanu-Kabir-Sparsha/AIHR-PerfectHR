# -*- coding: utf-8 -*-
"""Ground Employee Engagement & Retention in ONE employee's REAL HR metrics.

Assembles the worker's two inputs for an individual analysis:
  - `table` (str): the employee's HR metrics as readable `key: value` lines —
    income, hourly rate, years at company / in role / since last promotion,
    performance rating + score + trend AND the OKR / 9-box appraisal summary
    (same custom flows as Performance Analysis), overtime, absence, job
    satisfaction & involvement, age, etc.;
  - `contexts` (dict): identity + an instruction to analyse THIS employee.

The AI evaluates the metrics and returns the employee's engagement score,
retention risk, key drivers and recommendations.

Defensive: every metric is optional (model-in-env / field checks); a missing
source just drops that line. Several signals come from non-standard sources:
  - income / hourly rate -> hr.contract.wage;
  - overtime -> hr_attendance_gateway (attendance.daily.punch.overtime_hours);
  - years in role / since promotion -> the tracked hr.employee.job_id changes
    (chatter), falling back to tenure;
  - job satisfaction / involvement -> the rating fields on hr.employee.
"""
import logging

from odoo import fields

_logger = logging.getLogger(__name__)

_ABSENCE_WINDOW_DAYS = 180
_OVERTIME_WINDOW_DAYS = 180
_STD_MONTHLY_HOURS = 173.33


def _years_at_company(emp):
    jd = emp.joining_date if 'joining_date' in emp._fields else None
    if not jd:
        return None
    try:
        return round((fields.Date.today() - jd).days / 365.25, 1)
    except Exception:  # noqa: BLE001
        return None


def _income(emp):
    try:
        contract = emp.contract_id if 'contract_id' in emp._fields else None
        return round(contract.wage, 2) if contract and contract.wage else None
    except Exception:  # noqa: BLE001
        return None


def _hourly_rate(emp, monthly):
    if not monthly:
        return None
    try:
        cal = emp.resource_calendar_id if 'resource_calendar_id' in emp._fields else None
        hpw = (cal.hours_per_week if cal and 'hours_per_week' in cal._fields else 0) or 40
        monthly_hours = hpw * 52 / 12 or _STD_MONTHLY_HOURS
        return round(monthly / monthly_hours, 2)
    except Exception:  # noqa: BLE001
        return round(monthly / _STD_MONTHLY_HOURS, 2)


def _last_job_change_date(emp):
    """Date of the most recent job_id change, from the chatter tracking log.
    Used to approximate years-in-role / years-since-promotion."""
    env = emp.env
    try:
        field = env['ir.model.fields']._get('hr.employee', 'job_id')
        if not field:
            return None
        Track = env['mail.tracking.value'].sudo()
        field_domain = ([('field_id', '=', field.id)]
                        if 'field_id' in Track._fields else [('field', '=', 'job_id')])
        track = Track.search(field_domain + [
            ('mail_message_id.model', '=', 'hr.employee'),
            ('mail_message_id.res_id', '=', emp.id),
        ], order='create_date desc', limit=1)
        return track.create_date.date() if track and track.create_date else None
    except Exception:  # noqa: BLE001
        _logger.debug('engagement_grounding: job-change lookup failed', exc_info=True)
        return None


def _appraisal_signals(emp):
    """Performance signals from our CUSTOM appraisal flows — the SAME sources the
    Performance Analysis model uses, so Engagement & Retention is grounded in the
    real OKR / 9-box criteria (and the OpenHRMS appraisal-result ext), not just a
    headline rating. Returns a dict; keys are omitted when their source is absent:
      rating, score, trend, okr_9box (compact OKR/9-box summary), appraisal_result.
    """
    out = {}
    try:
        from .performance_grounding import _appraisals, _rating_label, _ext_results
    except Exception:  # noqa: BLE001
        return out
    try:
        appr = _appraisals(emp)          # hr.appraisal OKR / 9-box (oh_appraisal, oh_9_box, ...)
        if appr.get('compact'):
            out['okr_9box'] = appr['compact']
        scored = appr.get('scored')
        if scored:
            latest = scored[0]
            out['rating'] = _rating_label(latest) or None
            if latest.final_score or 0:
                out['score'] = round(latest.final_score or 0.0, 1)
            if len(scored) > 1:
                new, old = scored[0].final_score or 0, scored[-1].final_score or 0
                out['trend'] = ('improving' if new > old + 2 else
                                'declining' if new < old - 2 else 'stable')
    except Exception:  # noqa: BLE001
        _logger.debug('engagement_grounding: appraisal signals failed', exc_info=True)
    try:
        ext = _ext_results(emp).get('compact')   # oh.appraisal.result (functional/role/common)
        if ext:
            out['appraisal_result'] = ext
    except Exception:  # noqa: BLE001
        pass
    return out


def _overtime_hours(emp):
    env = emp.env
    if 'attendance.daily.punch' not in env:
        return None
    Punch = env['attendance.daily.punch'].sudo()
    if 'employee_id' not in Punch._fields or 'overtime_hours' not in Punch._fields:
        return None
    try:
        domain = [('employee_id', '=', emp.id)]
        for df in ('date', 'punch_date', 'attendance_date', 'day'):
            if df in Punch._fields:
                since = fields.Date.subtract(fields.Date.today(), days=_OVERTIME_WINDOW_DAYS)
                domain.append((df, '>=', since))
                break
        recs = Punch.search(domain, limit=400)
        return round(sum(r.overtime_hours or 0 for r in recs), 1) or None
    except Exception:  # noqa: BLE001
        return None


def _absence_days(emp):
    env = emp.env
    if 'hr.leave' not in env:
        return None
    try:
        since = fields.Date.subtract(fields.Date.today(), days=_ABSENCE_WINDOW_DAYS)
        leaves = env['hr.leave'].sudo().search([
            ('employee_id', '=', emp.id),
            ('state', '=', 'validate'),
            ('date_from', '>=', fields.Datetime.to_datetime(since)),
        ])
        return round(sum(l.number_of_days or 0 for l in leaves), 1)
    except Exception:  # noqa: BLE001
        return None


def _age(emp):
    bd = emp.birthday if 'birthday' in emp._fields else None
    if not bd:
        return None
    try:
        return int((fields.Date.today() - bd).days // 365)
    except Exception:  # noqa: BLE001
        return None


def _rating_field(emp, fname):
    """Read a 1-5 Selection rating field as its human label (e.g. '4 - High')."""
    if fname not in emp._fields or not emp[fname]:
        return None
    try:
        return dict(emp._fields[fname].selection).get(emp[fname], emp[fname])
    except Exception:  # noqa: BLE001
        return emp[fname]


def _metrics(emp):
    sig = _appraisal_signals(emp)
    income = _income(emp)
    tenure = _years_at_company(emp)
    last_change = _last_job_change_date(emp)
    yrs_role = (round((fields.Date.today() - last_change).days / 365.25, 1)
                if last_change else tenure)

    m = {}

    def put(key, value):
        if value not in (None, '', False):
            m[key] = value

    put('monthly_income', income)
    put('hourly_rate', _hourly_rate(emp, income))
    put('years_at_company', tenure)
    put('years_in_current_role', yrs_role)
    put('years_since_last_promotion', yrs_role)
    put('performance_rating', sig.get('rating'))
    put('performance_score', sig.get('score'))
    put('performance_trend', sig.get('trend'))
    put('appraisal_okr_9box', sig.get('okr_9box'))
    put('appraisal_result', sig.get('appraisal_result'))
    put('overtime_hours_recent', _overtime_hours(emp))
    put('absence_days_recent', _absence_days(emp))
    put('job_satisfaction', _rating_field(emp, 'job_satisfaction'))
    put('job_involvement', _rating_field(emp, 'job_involvement'))
    put('age', _age(emp))
    if 'gender' in emp._fields:
        put('gender', emp.gender)
    if 'marital' in emp._fields:
        put('marital_status', emp.marital)
    put('department', emp.department_id.name if emp.department_id else None)
    put('job_title', emp.job_title or (emp.job_id.name if emp.job_id else None))
    put('manager', emp.parent_id.name if emp.parent_id else None)
    if 'employee_skill_ids' in emp._fields:
        try:
            put('skills_count', len(emp.employee_skill_ids) or None)
        except Exception:  # noqa: BLE001
            pass
    return m


def build_employee_engagement_request(employee):
    """Return the dispatch payload {table, contexts, model_name} for ONE employee."""
    metrics = _metrics(employee)
    table = '\n'.join('%s: %s' % (k, v) for k, v in metrics.items()) \
        or 'No HR metrics on file for this employee.'
    contexts = {
        'analysis_type': 'individual_employee',
        'employee_id': str(employee.id),
        'employee_name': employee.name or '',
        'department': employee.department_id.name if employee.department_id else '',
        'job_title': employee.job_title or (employee.job_id.name if employee.job_id else ''),
        'instruction': ("Evaluate THIS individual employee's engagement and retention "
                        "risk from the metrics in the table. Identify the key drivers "
                        "and concrete retention recommendations."),
    }
    return {'table': table, 'contexts': contexts, 'model_name': 'phi3:mini'}
