# -*- coding: utf-8 -*-
"""Employee AI surfacing: wire Performance Management and Learning & Development
onto hr.employee via the AI mixin, each in its own results tab."""
from odoo import fields, models


class HrEmployee(models.Model):
    _name = 'hr.employee'
    _inherit = ['hr.employee', 'perfecthr.ai.mixin']

    ai_performance_result_ids = fields.Many2many(
        'perfecthr.ai.result', compute='_compute_perfecthr_ai_split',
        string='Performance Results')
    ai_learning_result_ids = fields.Many2many(
        'perfecthr.ai.result', compute='_compute_perfecthr_ai_split',
        string='Learning & Development Results')

    def _compute_perfecthr_ai_split(self):
        """Split this employee's AI results per model so each surfaces in its own
        tab (Performance Analysis vs Learning & Development)."""
        Result = self.env['perfecthr.ai.result']
        for rec in self:
            perf = learn = Result.browse()
            if rec.id:  # skip unsaved (NewId) records
                base = [('res_model', '=', rec._name), ('res_id', '=', rec.id)]
                perf = Result.search(base + [('module_key', '=', 'performance_management')])
                learn = Result.search(base + [('module_key', '=', 'learning_and_development')])
            rec.ai_performance_result_ids = perf
            rec.ai_learning_result_ids = learn

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
        return self._perfecthr_ai_toast(
            'Performance Analysis Submitted',
            'AI analysis queued — the result will appear shortly in the '
            'Performance Analysis (AI) tab.')

    def action_perfecthr_learning(self):
        """Submit a Learning & Development analysis for this employee.

        The AI returns a personalized learning path (courses / books / workshops /
        mentoring / certifications) grounded in the employee's REAL skill gaps —
        skills held below target proficiency plus the weakest appraisal objectives.
        The path lands in the 'AI Analysis' tab (filled by the auto-poll cron)."""
        self.ensure_one()
        from ..services.learning_grounding import build_learning_request
        payload = build_learning_request(self)
        self._ai_submit_payload('learning_and_development', payload)
        return self._perfecthr_ai_toast(
            'Learning & Development Submitted',
            'AI is building a personalized learning path — the result will appear '
            'shortly in the Learning & Development (AI) tab.')

    def _perfecthr_ai_toast(self, title, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': 'success',
                'sticky': False,
            },
        }
