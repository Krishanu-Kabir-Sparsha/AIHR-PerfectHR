# -*- coding: utf-8 -*-
"""Make chatbot results self-deliver into their Discuss conversation.

When an hr_chatbot result tied to a discuss.channel reaches a terminal state, the
answer is posted into that channel by the bot -- whether it was completed by the
in-line wait (interactive) or by the background auto-poll cron (slow job). A
chat_posted flag guarantees the message is posted exactly once.
"""
import json
import logging

from markupsafe import Markup, escape

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_BOT_XMLID = 'perfecthr_ai_insights.partner_perfecthr_ai_bot'


def _seg_text(item):
    """Render a driver / at-risk-segment / recommendation item (str or dict)."""
    if isinstance(item, dict):
        name = item.get('segment') or item.get('name') or item.get('title') or ''
        level = item.get('risk') or item.get('risk_level') or item.get('priority') or ''
        if name and level:
            return '%s — %s' % (name, level)
        return name or item.get('description') or item.get('reason') or json.dumps(item, default=str)
    return str(item)


class PerfectHRAIResult(models.Model):
    _inherit = 'perfecthr.ai.result'

    chat_posted = fields.Boolean(
        string='Chat Answer Posted', default=False, copy=False,
        help="Set once a chatbot result has been delivered into its Discuss "
             "conversation, so the cron and the in-line wait never double-post.")

    engagement_panel_html = fields.Html(
        string='Engagement Panel', sanitize=False,
        compute='_compute_engagement_panel',
        help="Formatted visual panel for Engagement & Retention results "
             "(score bar + risk badge + drivers / segments / recommendations).")

    def write(self, vals):
        res = super().write(vals)
        if vals.get('state') in ('done', 'failed'):
            pending = self.filtered(
                lambda r: r.module_key == 'hr_chatbot'
                and r.res_model == 'discuss.channel' and r.res_id
                and not r.chat_posted)
            if pending:
                pending._post_chat_answer()
        return res

    def _post_chat_answer(self):
        bot = self.env.ref(_BOT_XMLID, raise_if_not_found=False)
        if not bot:
            return
        Channel = self.env['discuss.channel'].sudo()
        for rec in self:
            if (rec.chat_posted or rec.module_key != 'hr_chatbot'
                    or rec.res_model != 'discuss.channel' or not rec.res_id):
                continue
            channel = Channel.browse(rec.res_id).exists()
            if not channel:
                continue
            channel.message_post(
                author_id=bot.id, body=rec._format_chat_body(),
                message_type='comment', subtype_xmlid='mail.mt_comment')
            rec.chat_posted = True

    def _format_chat_body(self):
        self.ensure_one()
        if self.state == 'failed':
            return Markup("<p><i>⚠️ Sorry, I couldn't process that right now. "
                          "Please try again, or contact HR.</i></p>")
        answer = (self.summary or '').strip()
        if not answer:
            return Markup("<p><i>I couldn't find an answer to that. Please "
                          "rephrase, or contact HR.</i></p>")
        body = Markup("<p>%s</p>") % escape(answer).replace('\n', Markup('<br/>'))

        try:
            structured = json.loads(self.structured_json or '{}')
        except (ValueError, TypeError):
            structured = {}
        suggestions = [s for s in (structured.get('follow_up_suggestions') or []) if s][:3]
        if suggestions:
            items = Markup('').join(Markup("<li>%s</li>") % escape(s) for s in suggestions)
            body += Markup("<p><b>You might also ask:</b></p><ul>%s</ul>") % items
        if not self.is_real_inference:
            body += Markup("<p><i>⚠️ This looks like a fallback response (the AI "
                           "engine may be temporarily unavailable).</i></p>")
        return body

    @api.depends('module_key', 'score', 'label', 'structured_json', 'state')
    def _compute_engagement_panel(self):
        for rec in self:
            if rec.module_key == 'employee_engagement_retention' and rec.state == 'done':
                rec.engagement_panel_html = rec._build_engagement_panel()
            else:
                rec.engagement_panel_html = False

    def _build_engagement_panel(self):
        """Render score bar + color-coded risk badge + drivers / segments /
        recommendations as a self-contained HTML panel (inline styles)."""
        self.ensure_one()
        try:
            data = json.loads(self.structured_json or '{}')
        except (ValueError, TypeError):
            data = {}
        risk = (self.label or '').lower()
        color = {'low': '#28a745', 'medium': '#f0ad4e',
                 'high': '#dc3545'}.get(risk, '#6c757d')
        width = max(0, min(100, int(round(self.score or 0))))

        badge = Markup('<span style="background:%s;color:#fff;padding:2px 10px;'
                       'border-radius:12px;font-weight:600;text-transform:uppercase;">%s</span>'
                       ) % (color, escape(risk or 'n/a'))
        bar = (Markup('<div style="background:#e9ecef;border-radius:6px;height:18px;'
                      'max-width:340px;margin:6px 0 4px;">')
               + Markup('<div style="background:%s;height:18px;border-radius:6px;'
                        'width:%s%%;"></div>') % (color, width)
               + Markup('</div>'))

        def _section(title, items):
            items = [i for i in (items or []) if i]
            if not items:
                return Markup('')
            lis = Markup('').join(
                Markup('<li style="margin:2px 0;">%s</li>') % _seg_text(i)
                for i in items[:8])
            return (Markup('<div style="margin-top:10px;"><b>%s</b>') % title
                    + Markup('<ul style="margin:4px 0 0;padding-left:20px;">%s</ul></div>') % lis)

        return (Markup('<div style="padding:6px 2px;max-width:660px;">')
                + Markup('<div style="display:flex;align-items:center;gap:12px;">'
                         '<span style="font-size:22px;font-weight:700;">%s/100</span>%s</div>'
                         ) % (round(self.score or 0, 1), badge)
                + bar
                + _section('Key drivers', data.get('key_drivers'))
                + _section('At-risk segments', data.get('at_risk_segments'))
                + _section('Recommended actions', data.get('recommendations'))
                + Markup('</div>'))
