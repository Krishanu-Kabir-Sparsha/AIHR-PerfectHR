# -*- coding: utf-8 -*-
"""CV Matcher adapter -> hr.applicant.

Authoritative contract (AIHR_Response_to_Integration_Plan.md §3):
  in : cv_job_id (int, required), cv_id (int, required);
       optional cv_text, job_description -> passed INLINE so the runtime does not
       need to call back to this Odoo (essential when the runtime is remote).
  out: match_score (0-100), matched_skills[], missing_skills[],
       recommendation (hire|consider|reject), summary.
"""
import base64
import json
import re

from odoo.exceptions import UserError

from .base import AIModelAdapter, register_adapter


def _strip_html(html):
    if not html:
        return ''
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return (text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"'))


@register_adapter
class CVMatcherAdapter(AIModelAdapter):
    module_key = 'cv_matcher'
    label = 'CV Matcher'
    target_model = 'hr.applicant'
    required_inputs = ('cv_job_id', 'cv_id')

    def build_payload(self, applicant):
        if not applicant.job_id:
            raise UserError("A Job Position is required on the applicant before CV analysis.")
        attachment = applicant.env['ir.attachment'].search([
            ('res_model', '=', 'hr.applicant'),
            ('res_id', '=', applicant.id),
        ], limit=1, order='id desc')
        if not attachment:
            raise UserError("Attach a CV document to this applicant before running CV Match.")

        # Prefer Odoo's indexed attachment text; fall back to best-effort decode.
        cv_text = attachment.index_content or ''
        if not cv_text and attachment.datas:
            try:
                cv_text = base64.b64decode(attachment.datas).decode('utf-8', errors='replace')
            except Exception:
                cv_text = ''

        job = applicant.job_id
        parts = [job.name or '']
        if job.description:
            parts.append(_strip_html(job.description))
        job_description = '\n\n'.join(p for p in parts if p)

        return {
            'cv_job_id': job.id,
            'cv_id': attachment.id,
            'cv_text': cv_text,
            'job_description': job_description,
            'candidate_name': applicant.partner_name or '',
            'applicant_id': applicant.id,
        }

    def map_result(self, raw):
        raw = raw or {}
        try:
            score = float(raw.get('match_score', raw.get('score', 0.0)) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        recommendation = raw.get('recommendation') or ''
        structured = {
            'matched_skills': raw.get('matched_skills') or [],
            'missing_skills': raw.get('missing_skills') or [],
            'recommendation': recommendation,
        }
        return {
            'score': score,
            'label': str(recommendation),
            'summary': raw.get('summary') or '',
            'structured_json': json.dumps(structured, indent=2),
        }

    def is_real_inference(self, raw):
        raw = raw or {}
        if 'analysis unavailable' in str(raw.get('summary') or '').lower():
            return False
        try:
            score = float(raw.get('match_score', raw.get('score', 0)) or 0)
        except (TypeError, ValueError):
            score = 0
        return score > 0 or bool(raw.get('matched_skills'))
