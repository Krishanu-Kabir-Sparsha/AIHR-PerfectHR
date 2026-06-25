# -*- coding: utf-8 -*-
"""Ground Learning & Development in the employee's REAL development needs.

Builds the `skill_gaps` the L&D worker turns into a personalized learning path,
from EVERY available source (defensively — a missing module/field just yields
fewer gaps):
  - Skills held below target proficiency (employee_skill_ids / level_progress);
  - The latest appraisal's weakest objectives (reuses performance_grounding);
  - A role/department fallback so the worker always has something to work with
    (the contract requires a non-empty skill_gaps list).

Two consumers: build_learning_request() shapes the dispatch payload (skill_gaps
sent inline), and employee_profile() answers the worker's data-callback
GET /modules/learning_development/api/employee-profile?employee_id=<id>.
"""
import json
import logging

from odoo import fields

_logger = logging.getLogger(__name__)

# Token budget: the L&D runtime worker bounds prompt+response together at
# num_ctx=1024 and caps output at num_predict=512 (hardcoded in the AIHR worker).
# The prompt already carries the skill_gaps + the [:1500]-char employee_profile,
# so a large requested output overflows the window and Ollama truncates mid-JSON
# (→ unparseable → "Unable to generate recommendations"). Keep both the gap list
# and the recommendation count small so the COMPLETE response fits in the window.
_MAX_GAPS = 8
_DEFAULT_MAX_RECOMMENDATIONS = 3
_MAX_PROFILE_SKILLS = 8         # cap the skills matrix sent in the profile
_MAX_PERF_SUMMARY_CHARS = 250   # cap the appraisal summary sent in the profile
_MAX_TRAINING = 5               # cap the training-history list sent in the profile
_PROFICIENCY_FLOOR = 75   # a held skill below this % is treated as a development area

# Map an appraisal rating to "does performance meet expectation?"
_RATING_MEETS = {'outstanding', 'exceptional', 'exceeds', 'exceeds_expectations',
                 'meets', 'meets_expectations'}
_RATING_BELOW = {'needs_improvement', 'below_expectations', 'unsatisfactory'}

# The L&D worker truncates the fetched profile around ~1500 chars; stay safely
# under so no field is silently cut from the model's context.
_PROFILE_CHAR_BUDGET = 1450


def _skill_gaps_from_skills(employee):
    """Skills the employee holds but at below-target proficiency."""
    gaps = []
    if 'employee_skill_ids' not in employee._fields:
        return gaps
    try:
        for line in employee.employee_skill_ids:
            skill = line.skill_id if 'skill_id' in line._fields else None
            if not skill:
                continue
            progress = line.level_progress if 'level_progress' in line._fields else 0
            if (progress or 0) < _PROFICIENCY_FLOOR:
                level = ''
                if 'skill_level_id' in line._fields and line.skill_level_id:
                    level = line.skill_level_id.name or ''
                gaps.append('%s (currently %s)' % (skill.name, level) if level else skill.name)
    except Exception:  # noqa: BLE001
        _logger.debug('learning_grounding: skills gather failed', exc_info=True)
    return gaps


def _skill_gaps_from_appraisal(employee):
    """The weakest objectives from the latest scored appraisal — concrete areas
    the employee under-delivered on, hence prime development targets."""
    gaps = []
    try:
        from .performance_grounding import _appraisals, _top_bottom
    except Exception:  # noqa: BLE001
        return gaps
    try:
        appr = _appraisals(employee)
        scored = appr.get('scored')
        if scored:
            _strong, weak = _top_bottom(scored[0])
            for w in weak:
                # weak items look like "Improve X (45%)" — drop the trailing %
                gaps.append(w.split(' (')[0].strip() if ' (' in w else w.strip())
    except Exception:  # noqa: BLE001
        _logger.debug('learning_grounding: appraisal gather failed', exc_info=True)
    return gaps


def _fallback_gaps(employee):
    """When nothing concrete is on file, give the worker the role context so it can
    still suggest foundational development (the contract needs a non-empty list)."""
    ctx = (employee.job_title
           or (employee.job_id.name if employee.job_id else '')
           or (employee.department_id.name if employee.department_id else '')
           or 'current role')
    return ['core competencies for the %s role' % ctx]


def _skill_gaps(employee):
    gaps = []
    seen = set()
    for g in _skill_gaps_from_skills(employee) + _skill_gaps_from_appraisal(employee):
        g = (g or '').strip()
        if g and g.lower() not in seen:
            seen.add(g.lower())
            gaps.append(g)
    return gaps or _fallback_gaps(employee)


def build_learning_request(employee, max_recommendations=_DEFAULT_MAX_RECOMMENDATIONS):
    """Return the dispatch payload {employee_id, skill_gaps, max_recommendations}."""
    return {
        'employee_id': employee.id,
        'skill_gaps': _skill_gaps(employee)[:_MAX_GAPS],
        'max_recommendations': max_recommendations,
    }


def _skills_detail(employee):
    """The employee's full skill matrix (name / level / progress) for the profile."""
    out = []
    if 'employee_skill_ids' not in employee._fields:
        return out
    try:
        for line in employee.employee_skill_ids:
            skill = line.skill_id if 'skill_id' in line._fields else None
            if not skill:
                continue
            out.append({
                'name': skill.name,
                'level': (line.skill_level_id.name
                          if 'skill_level_id' in line._fields and line.skill_level_id else ''),
                'progress': (line.level_progress if 'level_progress' in line._fields else 0) or 0,
            })
    except Exception:  # noqa: BLE001
        _logger.debug('learning_grounding: skills detail failed', exc_info=True)
    return out


def _tenure_years(employee):
    """Years since joining (hr.employee.joining_date, computed from contracts)."""
    jd = employee.joining_date if 'joining_date' in employee._fields else None
    if not jd:
        return None
    try:
        days = (fields.Date.today() - jd).days
        return round(days / 365.25, 1) if days > 0 else 0.0
    except Exception:  # noqa: BLE001
        return None


def _performance_expectation(employee):
    """(rating_label, meets_expectation|None) from the latest scored appraisal.
    meets = True for meets/exceeds/outstanding, False for needs-improvement/
    unsatisfactory, None when there is no rating to judge."""
    try:
        from .performance_grounding import _appraisals, _rating_label, _f
    except Exception:  # noqa: BLE001
        return '', None
    try:
        scored = _appraisals(employee).get('scored')
        if not scored:
            return '', None
        latest = scored[0]
        raw = str(_f(latest, 'performance_rating') or '').lower()
        meets = True if raw in _RATING_MEETS else False if raw in _RATING_BELOW else None
        return _rating_label(latest), meets
    except Exception:  # noqa: BLE001
        return '', None


def _skills_below_target(employee):
    """The actionable subset of the skill matrix: skills held below target
    proficiency — i.e. where the employee does NOT yet meet skill expectation."""
    return [s for s in _skills_detail(employee) if (s.get('progress') or 0) < _PROFICIENCY_FLOOR]


def _training_history(employee):
    """Course names the employee already enrolled in / completed, so the worker
    doesn't re-recommend them. Defensive: reads Odoo eLearning
    (slide.channel.partner) via the employee's user->partner; [] if not used."""
    env = employee.env
    if 'slide.channel.partner' not in env:
        return []
    partner = employee.user_id.partner_id if employee.user_id else None
    if not partner:
        return []
    try:
        enrolls = env['slide.channel.partner'].sudo().search(
            [('partner_id', '=', partner.id)], limit=20)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in enrolls:
        ch = e.channel_id if 'channel_id' in e._fields else None
        if ch and ch.name and ch.name not in out:
            out.append(ch.name)
    return out


def _fit_profile(profile):
    """Guarantee the profile fits the worker's ~1500-char window so no field is
    silently truncated from the model's context. Trims ONLY the lowest-priority
    lists (training first, then the full skills-matrix tail) — never the core
    signals (identity / tenure / rating / meets-expectation / below-target gaps /
    recent performance). Logs when it has to trim, so nothing is dropped silently."""
    def size():
        return len(json.dumps(profile, default=str))
    trimmed = []
    while size() > _PROFILE_CHAR_BUDGET and profile.get('completed_training'):
        profile['completed_training'].pop()
        trimmed.append('training')
    while size() > _PROFILE_CHAR_BUDGET and len(profile.get('skills') or []) > 3:
        profile['skills'].pop()
        trimmed.append('skill')
    if trimmed:
        _logger.info('learning_grounding: employee-profile trimmed to fit the worker '
                     'window (dropped %d trailing items, now %d chars)', len(trimmed), size())
    return profile


def employee_profile(employee):
    """The profile the L&D worker fetches mid-execution via the data-callback
    GET /modules/learning_development/api/employee-profile?employee_id=<id>.

    Best-effort shape (no published AIHR schema). Comprehensive — gives the worker
    BOTH the full picture and the explicit judgement signals so it can assess "do
    skills & performance meet expectations?" and recommend a grounded, non-redundant
    path. Fields are ordered high-value-first and the whole thing is kept under the
    worker's ~1500-char window by _fit_profile():
      - identity + tenure;
      - performance: appraisal rating + an explicit meets-expectation flag + a
        compact recent-performance summary (strengths / weak objectives / trend);
      - skills: the FULL skill matrix (name/level/progress) AND the actionable
        below-target subset (expected vs actual);
      - completed training, so it does not recommend courses already taken.
    The skill_gaps list also rides the dispatch payload.
    """
    perf_summary = ''
    try:
        from .performance_grounding import _appraisals
        perf_summary = _appraisals(employee).get('compact') or ''
    except Exception:  # noqa: BLE001
        _logger.debug('learning_grounding: perf summary failed', exc_info=True)
    rating_label, meets = _performance_expectation(employee)
    below = [s['name'] for s in _skills_below_target(employee)][:_MAX_PROFILE_SKILLS]
    return _fit_profile({
        'employee_id': str(employee.id),
        'name': employee.name or '',
        'job_title': employee.job_title or (employee.job_id.name if employee.job_id else ''),
        'department': employee.department_id.name if employee.department_id else '',
        'manager': employee.parent_id.name if employee.parent_id else '',
        'tenure_years': _tenure_years(employee),
        'performance_rating': rating_label,
        'meets_performance_expectation': meets,
        'skills_below_target': below,
        'recent_performance': perf_summary[:_MAX_PERF_SUMMARY_CHARS],
        'skills': _skills_detail(employee)[:_MAX_PROFILE_SKILLS],
        'completed_training': _training_history(employee)[:_MAX_TRAINING],
    })
