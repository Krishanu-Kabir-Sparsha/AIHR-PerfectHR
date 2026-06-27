# -*- coding: utf-8 -*-
"""Employee AI surfacing: wire Performance Management, Learning & Development, and
Engagement & Retention onto hr.employee via the AI mixin, each in its own tab."""
from odoo import fields, models

_SATISFACTION_LEVELS = [
    ('1', '1 - Very Low'), ('2', '2 - Low'), ('3', '3 - Moderate'),
    ('4', '4 - High'), ('5', '5 - Very High'),
]


class HrEmployee(models.Model):
    _name = 'hr.employee'
    _inherit = ['hr.employee', 'perfecthr.ai.mixin']

    # HR-entered signals (no native Odoo source) fed to the Engagement & Retention AI
    job_satisfaction = fields.Selection(
        _SATISFACTION_LEVELS, string='Job Satisfaction',
        help="Self/HR-rated job satisfaction — fed to the Engagement & Retention AI.")
    job_involvement = fields.Selection(
        _SATISFACTION_LEVELS, string='Job Involvement',
        help="Self/HR-rated job involvement — fed to the Engagement & Retention AI.")

    ai_performance_result_ids = fields.Many2many(
        'perfecthr.ai.result', compute='_compute_perfecthr_ai_split',
        string='Performance Results')
    ai_learning_result_ids = fields.Many2many(
        'perfecthr.ai.result', compute='_compute_perfecthr_ai_split',
        string='Learning & Development Results')
    ai_engagement_result_ids = fields.Many2many(
        'perfecthr.ai.result', compute='_compute_perfecthr_ai_split',
        string='Engagement & Retention Results')
    ai_engagement_panel_html = fields.Html(
        compute='_compute_perfecthr_ai_split', sanitize=False,
        string='Latest Engagement Panel')

    def _compute_perfecthr_ai_split(self):
        """Split this employee's AI results per model so each surfaces in its own
        tab, and expose the latest engagement panel for inline display."""
        Result = self.env['perfecthr.ai.result']
        for rec in self:
            perf = learn = eng = Result.browse()
            panel = False
            if rec.id:  # skip unsaved (NewId) records
                base = [('res_model', '=', rec._name), ('res_id', '=', rec.id)]
                perf = Result.search(base + [('module_key', '=', 'performance_management')])
                learn = Result.search(base + [('module_key', '=', 'learning_and_development')])
                eng = Result.search(base + [('module_key', '=', 'employee_engagement_retention')])
                done = eng.filtered(lambda r: r.state == 'done')[:1]
                panel = done.engagement_panel_html if done else False
            rec.ai_performance_result_ids = perf
            rec.ai_learning_result_ids = learn
            rec.ai_engagement_result_ids = eng
            rec.ai_engagement_panel_html = panel

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

    def action_perfecthr_engagement(self):
        """Submit an Engagement & Retention analysis for this employee.

        The AI evaluates the employee's HR metrics (income, tenure, years in role /
        since last promotion, performance + trend, overtime, absence, job
        satisfaction / involvement, ...) and flags engagement risks + retention
        concerns. The result (rich panel) lands in the 'Engagement & Retention
        (AI)' tab, filled by the auto-poll cron."""
        self.ensure_one()
        from ..services.engagement_grounding import build_employee_engagement_request
        payload = build_employee_engagement_request(self)
        self._ai_submit_payload('employee_engagement_retention', payload)
        return self._perfecthr_ai_toast(
            'Engagement & Retention Submitted',
            'AI is evaluating engagement & retention risk — the result will appear '
            'shortly in the Engagement & Retention (AI) tab.')

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
