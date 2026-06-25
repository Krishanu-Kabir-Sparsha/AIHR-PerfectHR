# -*- coding: utf-8 -*-
"""Data-callback endpoints the AIHR runtime workers fetch during execution.

When a data-fed model runs, the runtime worker calls back to PerfectHR for the
employee's data — observed in the logs as:
  performance_management      GET /api/performance-management/dataset-summary?dataset_id=<uuid>&employee_id=<id>
  learning_and_development    GET /modules/learning_development/api/employee-profile?employee_id=<id>
We answer with the employee's REAL data so the analysis is grounded instead of
falling back ("Analysis unavailable" / "Unable to generate recommendations").

Security: these expose HR data, so only the co-located local runtime (127.0.0.1)
may call them; any other source gets 403.
"""
import json

from odoo import http
from odoo.http import request

_LOCAL = ('127.0.0.1', '::1', 'localhost')


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[('Content-Type', 'application/json')], status=status)


class PerfectHRDataFeed(http.Controller):

    @http.route('/api/performance-management/dataset-summary',
                type='http', auth='public', methods=['GET'], csrf=False, save_session=False)
    def performance_dataset_summary(self, dataset_id=None, employee_id=None, **kw):
        if request.httprequest.remote_addr not in _LOCAL:
            return _json({'error': 'forbidden'}, 403)
        if not employee_id:
            return _json({'error': 'employee_id required'}, 400)
        try:
            employee = request.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        except (ValueError, TypeError):
            employee = None
        if not employee:
            return _json({'error': 'employee not found'}, 404)
        from ..services.performance_grounding import dataset_summary
        return _json(dataset_summary(employee, dataset_id))

    @http.route('/modules/learning_development/api/employee-profile',
                type='http', auth='public', methods=['GET'], csrf=False, save_session=False)
    def learning_employee_profile(self, employee_id=None, **kw):
        if request.httprequest.remote_addr not in _LOCAL:
            return _json({'error': 'forbidden'}, 403)
        if not employee_id:
            return _json({'error': 'employee_id required'}, 400)
        try:
            employee = request.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        except (ValueError, TypeError):
            employee = None
        if not employee:
            return _json({'error': 'employee not found'}, 404)
        from ..services.learning_grounding import employee_profile
        return _json(employee_profile(employee))
