# -*- coding: utf-8 -*-
"""Reusable AI surfacing mixin.

Inherit this on any business model (hr.applicant, hr.employee, hr.department, ...)
to get a relation to its AI results and a generic submit helper. This is what
keeps adding AI to a new screen a thin, repeatable change.
"""
from odoo import api, fields, models


class PerfectHRAIMixin(models.AbstractModel):
    _name = 'perfecthr.ai.mixin'
    _description = 'PerfectHR AI Surfacing Mixin'

    ai_result_ids = fields.Many2many(
        'perfecthr.ai.result', compute='_compute_ai_results',
        string='AI Results')
    ai_result_count = fields.Integer(compute='_compute_ai_results')

    def _compute_ai_results(self):
        Result = self.env['perfecthr.ai.result']
        for record in self:
            results = Result.browse()
            if record.id:  # skip unsaved (NewId) records
                results = Result.search([
                    ('res_model', '=', record._name),
                    ('res_id', '=', record.id),
                ])
            record.ai_result_ids = results
            record.ai_result_count = len(results)

    def _ai_submit(self, module_key):
        """Submit an AI job for this record via the core orchestrator."""
        self.ensure_one()
        from odoo.addons.perfecthr_ai_core.services.ai_orchestrator import AIOrchestrator
        return AIOrchestrator(self.env).submit(module_key, self)

    def action_view_ai_results(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'AI Results',
            'res_model': 'perfecthr.ai.result',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
            'context': {'create': False},
        }
