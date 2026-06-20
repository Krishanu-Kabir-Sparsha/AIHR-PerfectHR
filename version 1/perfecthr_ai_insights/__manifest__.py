# -*- coding: utf-8 -*-
{
    'name': 'PerfectHR AI Insights',
    'version': '18.0.1.0.0',
    'category': 'Human Resources/AI',
    'summary': 'Surfaces AIHR analysis results inside PerfectHR screens',
    'description': """
PerfectHR AI Insights (the face)
================================
Surfacing layer for the AI results produced by perfecthr_ai_core.

- A reusable mixin (perfecthr.ai.mixin) gives any model an AI results relation
  and a generic submit helper, so adding AI to a new screen is a thin change.
- Recruitment vertical: a 'Match CV (AI)' button + an 'AIHR Analysis' tab on
  hr.applicant, backed by the normalized perfecthr.ai.result store.
- Supersedes the AIHR connector's basic applicant buttons/tab via view
  inheritance (the connector's own code is never modified).
    """,
    'author': 'Perfect HR',
    'website': 'https://perfecthr.net',
    'depends': ['perfecthr_ai_core', 'hr_recruitment', 'hr', 'mail'],
    'data': [
        'data/ai_bot_data.xml',
        'views/ai_chat_views.xml',
        'views/hr_applicant_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
