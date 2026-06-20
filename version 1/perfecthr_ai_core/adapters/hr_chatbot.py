# -*- coding: utf-8 -*-
"""HR Chatbot adapter.

Authoritative contract (AIHR_Response_to_Integration_Plan.md §3):
  in : question (str, required); optional session_id
  out: answer, category (leave_policy|payroll|benefits|onboarding|compliance|general),
       confidence (0.0-1.0), follow_up_suggestions[], session_id

Unlike the record-driven models (e.g. cv_matcher), the chatbot is invoked
ad-hoc from a Discuss conversation, not from a business record. The question is
built + grounded by the insights layer and submitted via the orchestrator's
explicit-payload path, so build_payload() is intentionally not used here.
"""
import json

from odoo.exceptions import UserError

from .base import AIModelAdapter, register_adapter


@register_adapter
class HRChatbotAdapter(AIModelAdapter):
    module_key = 'hr_chatbot'
    label = 'HR Chatbot'
    target_model = ''            # not tied to a single Odoo model
    required_inputs = ('question',)

    def build_payload(self, record):
        # The chatbot runs from its Discuss conversation (perfecthr_ai_insights),
        # which submits an explicit payload. Calling it record-driven is a misuse.
        raise UserError(
            "The HR Chatbot runs from its Discuss conversation, "
            "not from a record form.")

    def map_result(self, raw):
        raw = raw or {}
        try:
            confidence = float(raw.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        structured = {
            'category': raw.get('category') or '',
            'confidence': confidence,
            'follow_up_suggestions': raw.get('follow_up_suggestions') or [],
            'session_id': raw.get('session_id') or '',
        }
        return {
            # store confidence on the shared 0-100 score scale for consistency
            'score': round(confidence * 100.0, 2),
            'label': str(raw.get('category') or ''),
            'summary': raw.get('answer') or '',
            'structured_json': json.dumps(structured, indent=2),
        }

    def is_real_inference(self, raw):
        raw = raw or {}
        answer = str(raw.get('answer') or '').lower()
        if not answer or 'analysis unavailable' in answer or 'safe_mode' in answer:
            return False
        try:
            return float(raw.get('confidence') or 0.0) > 0.0
        except (TypeError, ValueError):
            return False
