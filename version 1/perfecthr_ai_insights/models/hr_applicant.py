# -*- coding: utf-8 -*-
"""Recruitment surfacing: wire CV Matcher onto hr.applicant via the AI mixin."""
from odoo import models


class HrApplicant(models.Model):
    _name = 'hr.applicant'
    _inherit = ['hr.applicant', 'perfecthr.ai.mixin']

    def action_aihr_match_cv(self):
        """Submit a CV Matcher job for this applicant; the auto-poll cron fills
        in the result, which appears in the 'AIHR Analysis' tab."""
        self.ensure_one()
        self._ai_submit('cv_matcher')  # raises a clear UserError if Job/CV missing
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'CV Match Submitted',
                'message': 'AI analysis queued — the result will appear shortly '
                           'in the AIHR Analysis tab.',
                'type': 'success',
                'sticky': False,
            },
        }
