# -*- coding: utf-8 -*-
"""Performance Management adapter -> hr.employee.

Worker contract (api_guidance §7): the runtime REQUIRES a UUID `dataset_id` and
reads ONLY employee_id(str)/employee_name/review_period/department/job_title;
"extra fields are ignored". So we cannot feed appraisal data as a separate field.

Design (decided with the user): the Score/Rating come from the REAL appraisal
(hr.appraisal.final_score / performance_rating), and the AI provides the grounded
narrative. perfecthr_ai_insights gathers the appraisal (OKR + 9-box, latest +
trend), packs a compact summary into `review_period` so the narrative is grounded,
submits via the explicit-payload path, and writes the real Score/Rating onto the
result. This adapter therefore maps ONLY the narrative and never overwrites
Score/Rating. build_payload here is a minimal record-driven fallback.
"""
import json
import re
import uuid

from odoo import fields

from .base import AIModelAdapter, register_adapter


def _rescue_raw_response(raw_str):
    """Strip markdown code fences and parse JSON from Ollama's raw_response.

    Ollama sometimes wraps its output in ```json...``` fences. The AIHR runtime
    fails to parse those and stores summary='Analysis unavailable'. We strip the
    fences here and re-parse so the structured data is not lost."""
    if not raw_str:
        return None
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', raw_str.strip())
    cleaned = re.sub(r'\n?```\s*$', '', cleaned.strip())
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None


def _summary_from_parsed(parsed):
    """Build a short narrative when the runtime couldn't generate one."""
    parts = []
    strengths = parsed.get('strengths') or []
    areas = parsed.get('improvement_areas') or []
    rating = (parsed.get('rating') or '').replace('_', ' ').title()
    if strengths:
        parts.append('Key strengths: %s.' % '; '.join(strengths[:2]))
    if areas:
        parts.append('Areas for development: %s.' % '; '.join(areas[:2]))
    if rating:
        parts.append('Overall rating: %s.' % rating)
    return ' '.join(parts)


def base_payload(employee):
    """The minimal runtime payload: the 5 fields the worker reads + a UUID id.
    Shared with perfecthr_ai_insights, which enriches review_period + extras."""
    today = fields.Date.today()
    quarter = (today.month - 1) // 3 + 1
    return {
        'dataset_id': str(uuid.uuid5(uuid.NAMESPACE_DNS, 'perfecthr-perf-%s' % employee.id)),
        'employee_id': str(employee.id),
        'employee_name': employee.name or '',
        'review_period': '%s-Q%s' % (today.year, quarter),
        'department': employee.department_id.name if employee.department_id else '',
        'job_title': employee.job_title or (employee.job_id.name if employee.job_id else ''),
    }


@register_adapter
class PerformanceManagementAdapter(AIModelAdapter):
    module_key = 'performance_management'
    label = 'Performance Management'
    target_model = 'hr.employee'
    required_inputs = ()

    def build_payload(self, employee):
        # Minimal fallback; the grounded payload is built by perfecthr_ai_insights.
        return base_payload(employee)

    def map_result(self, raw):
        """Map the AI narrative ONLY. Score/Rating are set from the real appraisal
        at submit time (perfecthr_ai_insights) and must NOT be overwritten here."""
        raw = raw or {}
        summary = raw.get('summary') or ''
        strengths = raw.get('strengths') or []
        improvement_areas = raw.get('improvement_areas') or []
        recommendations = raw.get('recommendations') or []
        goals_achieved_percent = raw.get('goals_achieved_percent')
        rating = raw.get('rating') or ''

        # When the AIHR runtime couldn't parse Ollama's markdown-fenced output it
        # stores summary='Analysis unavailable' and zeros all structured fields but
        # keeps the original raw_response. Rescue the data by stripping the fences.
        if 'analysis unavailable' in summary.lower() and raw.get('raw_response'):
            parsed = _rescue_raw_response(raw['raw_response'])
            if parsed:
                strengths = parsed.get('strengths') or strengths
                improvement_areas = parsed.get('improvement_areas') or improvement_areas
                recommendations = parsed.get('recommendations') or recommendations
                goals_achieved_percent = parsed.get('goals_achieved_percent') or goals_achieved_percent
                rating = parsed.get('rating') or rating
                summary = parsed.get('summary') or _summary_from_parsed(parsed)

        structured = {
            'rating': rating,
            'strengths': strengths,
            'improvement_areas': improvement_areas,
            'goals_achieved_percent': goals_achieved_percent,
            'recommendations': recommendations,
        }
        return {
            'summary': summary,
            'structured_json': json.dumps(structured, indent=2),
        }

    def is_real_inference(self, raw):
        raw = raw or {}
        summary = str(raw.get('summary') or '').lower()
        if 'analysis unavailable' in summary or 'safe_mode' in summary:
            # Still count as real if raw_response can be rescued
            if raw.get('raw_response') and _rescue_raw_response(raw['raw_response']):
                return True
            return False
        return bool(raw.get('strengths') or raw.get('recommendations') or summary)
