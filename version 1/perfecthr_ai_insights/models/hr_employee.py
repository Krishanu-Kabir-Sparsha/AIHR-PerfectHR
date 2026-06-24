# -*- coding: utf-8 -*-
"""Performance surfacing: wire Performance Management onto hr.employee via the AI mixin."""
from odoo import models


class HrEmployee(models.Model):
    _name = 'hr.employee'
    _inherit = ['hr.employee', 'perfecthr.ai.mixin']

    def action_perfecthr_performance(self):
        """Submit a Performance Management analysis for this employee.

        The Score/Rating are seeded from the employee's REAL latest appraisal
        (hr.appraisal.final_score / performance_rating); the AI provides the
        grounded narrative (the auto-poll cron fills it into the result, shown in
        the 'Performance Analysis (AI)' tab)."""
        self.ensure_one()
        from ..services.performance_grounding import build_performance_request
        payload, seed = build_performance_request(self)
        result = self._ai_submit_payload('performance_management', payload)
        if seed:
            result.write(seed)   # real appraisal Score/Rating; AI fills the narrative
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Performance Analysis Submitted',
                'message': 'AI analysis queued — the result will appear shortly '
                           'in the Performance Analysis (AI) tab.',
                'type': 'success',
                'sticky': False,
            },
        }
