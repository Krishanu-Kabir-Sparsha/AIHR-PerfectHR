# -*- coding: utf-8 -*-
"""Learning & Development adapter -> hr.employee.

Worker contract (AIHR_Response_to_Integration_Plan.md §3):
  in : employee_id (int), skill_gaps [str]; optional max_recommendations (1-20, default 5).
       Data is passed INLINE — no PerfectHR data-callback needed (unlike performance).
  out: recommendations[{title, type(online_course|book|workshop|mentoring|certification),
       provider, duration_hours, skill_addressed, priority(high|med|low), description}],
       learning_path_summary, estimated_completion_weeks.

The rich skill_gaps are gathered by perfecthr_ai_insights (skills below proficiency +
appraisal weak areas + a role fallback); this adapter maps the result and detects the
runtime's canned fallback. build_payload here is a minimal record-driven fallback used
by the generic tester wizard.
"""
import json
import re

from .base import AIModelAdapter, register_adapter

_DEFAULT_MAX_RECOMMENDATIONS = 3
_PROFICIENCY_FLOOR = 75   # a held skill below this % is treated as a development area


def _salvage_recommendations(raw_str):
    """Recover recommendation objects from the runtime's raw_response.

    The runtime stores the original Ollama text in raw_response when its own JSON
    parse fails — typically because the output was markdown-fenced (```json…```)
    or truncated mid-array by the model's token limit. Recommendation objects are
    flat dicts, so we strip any fences and pull out each complete {...} block,
    keeping the ones that parse and carry a title. Returns (recommendations, summary)."""
    if not raw_str:
        return [], ''
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw_str.strip())
    cleaned = re.sub(r'\s*```$', '', cleaned.strip())
    # Fast path: the whole thing is valid JSON.
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj.get('recommendations') or [], obj.get('learning_path_summary') or ''
    except (json.JSONDecodeError, ValueError):
        pass
    # Salvage path: extract each complete flat {...} object (truncated tail ignored).
    recs = []
    for block in re.findall(r'\{[^{}]*\}', cleaned):
        try:
            item = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(item, dict) and item.get('title'):
            recs.append(item)
    return recs, ''


def basic_skill_gaps(employee):
    """Skills the employee holds below mastery — the minimal, skills-only gap list.
    perfecthr_ai_insights enriches this with appraisal weak areas + a role fallback."""
    gaps = []
    if 'employee_skill_ids' in employee._fields:
        try:
            for line in employee.employee_skill_ids:
                skill = line.skill_id if 'skill_id' in line._fields else None
                progress = line.level_progress if 'level_progress' in line._fields else 0
                if skill and (progress or 0) < _PROFICIENCY_FLOOR:
                    gaps.append(skill.name)
        except Exception:  # noqa: BLE001
            pass
    return gaps


@register_adapter
class LearningDevelopmentAdapter(AIModelAdapter):
    module_key = 'learning_and_development'
    label = 'Learning & Development'
    target_model = 'hr.employee'
    required_inputs = ('employee_id', 'skill_gaps')

    def build_payload(self, employee):
        # Minimal fallback; the grounded payload is built by perfecthr_ai_insights.
        return {
            'employee_id': employee.id,
            'skill_gaps': basic_skill_gaps(employee),
            'max_recommendations': _DEFAULT_MAX_RECOMMENDATIONS,
        }

    def map_result(self, raw):
        raw = raw or {}
        recommendations = raw.get('recommendations') or []
        summary = raw.get('learning_path_summary') or raw.get('summary') or ''
        weeks = raw.get('estimated_completion_weeks')

        # Safety net: the runtime couldn't parse Ollama's output (markdown-fenced or
        # truncated) so it returned no recommendations but kept the raw text. Recover
        # whatever complete recommendation objects we can from raw_response.
        if not recommendations and raw.get('raw_response'):
            salvaged, salvaged_summary = _salvage_recommendations(raw['raw_response'])
            if salvaged:
                recommendations = salvaged
                if 'unable to generate' in summary.lower() or not summary:
                    summary = salvaged_summary or (
                        'Recommended development: %s.' % '; '.join(
                            r.get('title', '') for r in salvaged[:3] if r.get('title')))

        structured = {
            'recommendations': recommendations,
            'estimated_completion_weeks': weeks,
        }
        # A compact verdict for the list view: "N items · ~M wks".
        label = ''
        if recommendations:
            label = '%d recommendation%s' % (
                len(recommendations), '' if len(recommendations) == 1 else 's')
            if weeks:
                label += ' · ~%s wks' % weeks
        return {
            'score': 0.0,   # L&D has no 0-100 score; the value is the path itself
            'label': label,
            'summary': summary,
            'structured_json': json.dumps(structured, indent=2, default=str),
        }

    def is_real_inference(self, raw):
        raw = raw or {}
        if raw.get('recommendations'):
            return True
        # The runtime's parse failed but real model output may be salvageable.
        if raw.get('raw_response'):
            salvaged, _ = _salvage_recommendations(raw['raw_response'])
            if salvaged:
                return True
        summary = str(raw.get('learning_path_summary') or raw.get('summary') or '').lower()
        if 'analysis unavailable' in summary or 'safe_mode' in summary \
                or 'unable to generate' in summary:
            return False
        return bool(summary)
