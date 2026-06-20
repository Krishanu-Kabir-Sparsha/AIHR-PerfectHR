# -*- coding: utf-8 -*-
{
    'name': 'PerfectHR AI Core',
    'version': '18.0.1.0.0',
    'category': 'Human Resources/AI',
    'summary': 'Core engine that drives the AIHR models and stores normalized AI results',
    'description': """
PerfectHR AI Core (the brain)
=============================
Model-agnostic engine that consumes the AIHR AI models through the AIHR
connector and stores every result in one normalized store (perfecthr.ai.result).

- One adapter per AIHR model: build_payload (gather PerfectHR data inline) +
  map_result (normalize the runtime output) + fallback detection.
- Orchestrator reuses perfecthr_aihr_connector's AIHRRuntimeService for all
  HTTP (submit + poll). This module never talks to the Control Plane directly.
- No screens on business records — surfacing lives in perfecthr_ai_insights.

Adding a model = one adapter file. The connector and Control Plane are untouched.
    """,
    'author': 'Perfect HR',
    'website': 'https://perfecthr.net',
    'depends': ['perfecthr_aihr_connector'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron.xml',
        'views/ai_result_views.xml',
        'views/ai_run_wizard_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
