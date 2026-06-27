# -*- coding: utf-8 -*-
"""Employee Engagement & Retention adapter -> population-level (company / department).

Worker contract (AIHR_Response_to_Integration_Plan.md §3):
  in : table(str) — workforce data rows; contexts(dict) — aggregate metadata;
       optional model_name.
  out: overall_engagement_score(0-100), retention_risk(low|medium|high),
       key_drivers[], at_risk_segments[], recommendations[], summary.

The table + contexts are assembled by perfecthr_ai_insights.engagement_grounding
over a population of employees (whole company or one department). This adapter maps
the result and detects the runtime's canned fallback; the payload is always built
by the insights layer, so build_payload here is not used (population, not record).
"""
import json

from odoo.exceptions import UserError

from .base import AIModelAdapter, register_adapter


@register_adapter
class EngagementRetentionAdapter(AIModelAdapter):
    module_key = 'employee_engagement_retention'
    label = 'Engagement & Retention'
    target_model = ''   # population-level; the payload is built by the insights grounding
    required_inputs = ('table', 'contexts')

    def build_payload(self, record):
        raise UserError(
            "Engagement & Retention is launched from the company-wide menu or the "
            "department button (perfecthr_ai_insights), not from a single record.")

    def map_result(self, raw):
        raw = raw or {}
        try:
            score = float(raw.get('overall_engagement_score') or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        structured = {
            'key_drivers': raw.get('key_drivers') or [],
            'at_risk_segments': raw.get('at_risk_segments') or [],
            'recommendations': raw.get('recommendations') or [],
        }
        return {
            'score': score,
            'label': (raw.get('retention_risk') or '').lower(),
            'summary': raw.get('summary') or '',
            'structured_json': json.dumps(structured, indent=2, default=str),
        }

    def is_real_inference(self, raw):
        raw = raw or {}
        summary = str(raw.get('summary') or '').lower()
        if 'analysis unavailable' in summary or 'safe_mode' in summary:
            return False
        return bool(raw.get('key_drivers') or raw.get('at_risk_segments')
                    or raw.get('overall_engagement_score'))
