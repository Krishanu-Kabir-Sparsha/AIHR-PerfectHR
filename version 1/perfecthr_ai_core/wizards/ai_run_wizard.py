# -*- coding: utf-8 -*-
"""A small tester to run any registered AIHR model from a menu, so the core is
verifiable on its own before the polished per-screen surfacing (insights) exists.
"""
from odoo import fields, models
from odoo.exceptions import UserError

from ..adapters.base import all_adapters, get_adapter


def _wizard_module_selection(self=None):
    options = [(a.module_key, a.label or a.module_key) for a in all_adapters()]
    return options or [('cv_matcher', 'CV Matcher')]


class PerfectHRAIRunWizard(models.TransientModel):
    _name = 'perfecthr.ai.run.wizard'
    _description = 'Run AIHR Analysis'

    module_key = fields.Selection(selection=_wizard_module_selection,
                                  string='AI Model', required=True)
    applicant_id = fields.Many2one('hr.applicant', string='Applicant')

    def action_run(self):
        self.ensure_one()
        adapter = get_adapter(self.module_key)
        if not adapter:
            raise UserError("No adapter registered for '%s'." % self.module_key)

        if adapter.target_model == 'hr.applicant':
            if not self.applicant_id:
                raise UserError("Select an applicant to run %s." % adapter.label)
            record = self.applicant_id
        else:
            raise UserError(
                "This tester currently supports applicant-based models only "
                "(selected model targets %s)." % adapter.target_model)

        from ..services.ai_orchestrator import AIOrchestrator
        result = AIOrchestrator(self.env).submit(self.module_key, record)
        return {
            'type': 'ir.actions.act_window',
            'name': 'AI Result',
            'res_model': 'perfecthr.ai.result',
            'res_id': result.id,
            'view_mode': 'form',
            'target': 'current',
        }
