# -*- coding: utf-8 -*-
"""perfecthr.ai.result — the normalized store every AIHR model writes into.

This is the seam between the engine (perfecthr_ai_core) and the surfacing layer
(perfecthr_ai_insights): the engine produces these rows, insights consumes them.
"""
import logging

from odoo import api, fields, models

from ..adapters.base import all_adapters

_logger = logging.getLogger(__name__)


def _ai_module_selection(self=None):
    options = [(a.module_key, a.label or a.module_key) for a in all_adapters()]
    return options or [('cv_matcher', 'CV Matcher')]


class PerfectHRAIResult(models.Model):
    _name = 'perfecthr.ai.result'
    _description = 'PerfectHR AI Result'
    _order = 'create_date desc'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=True)
    module_key = fields.Selection(selection=_ai_module_selection, string='AI Model',
                                  required=True, index=True)
    res_model = fields.Char(string='Source Model', index=True)
    res_id = fields.Integer(string='Source Record ID', index=True)
    job_id = fields.Char(string='AIHR Job ID', index=True, copy=False)
    state = fields.Selection([
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], default='queued', required=True, index=True)

    score = fields.Float(string='Score', digits=(6, 2))
    label = fields.Char(string='Verdict')
    summary = fields.Text()
    structured_json = fields.Text(string='Structured (JSON)')
    raw_json = fields.Text(string='Raw Result (JSON)')
    insight_json = fields.Text(string='Insights (JSON)')
    payload_json = fields.Text(string='Submitted Payload (JSON)')
    is_real_inference = fields.Boolean(
        string='Real Inference', default=False,
        help="False when the runtime returned its canned fallback "
             "(e.g. Ollama/GPU unavailable) rather than a real AI result.")
    error_message = fields.Text(readonly=True)
    analyzed_at = fields.Datetime(readonly=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)

    @api.depends('module_key', 'res_model', 'res_id', 'state')
    def _compute_display_name(self):
        labels = dict(_ai_module_selection())
        for rec in self:
            lbl = labels.get(rec.module_key, rec.module_key or 'AI')
            rec.display_name = "%s · %s#%s · %s" % (
                lbl, rec.res_model or '-', rec.res_id or 0, rec.state or '')

    def action_poll(self):
        from ..services.ai_orchestrator import AIOrchestrator
        orchestrator = AIOrchestrator(self.env)
        for rec in self:
            orchestrator.poll(rec)
        return True

    def action_open_source(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def _cron_poll_pending(self, limit=100):
        """Scheduled: advance queued/processing results by polling the Control
        Plane, so users never have to click 'Poll Result'. Each record polls in
        its own savepoint — one failure never aborts the batch."""
        pending = self.search([
            ('state', 'in', ('queued', 'processing')),
            ('job_id', '!=', False),
        ], limit=limit)
        if not pending:
            return
        from ..services.ai_orchestrator import AIOrchestrator
        orchestrator = AIOrchestrator(self.env)
        for rec in pending:
            try:
                with self.env.cr.savepoint():
                    orchestrator.poll(rec)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("AI auto-poll failed for result %s: %s", rec.id, exc)
