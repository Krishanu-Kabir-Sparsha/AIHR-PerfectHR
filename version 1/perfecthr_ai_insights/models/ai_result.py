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

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

_BOT_XMLID = 'perfecthr_ai_insights.partner_perfecthr_ai_bot'


class PerfectHRAIResult(models.Model):
    _inherit = 'perfecthr.ai.result'

    chat_posted = fields.Boolean(
        string='Chat Answer Posted', default=False, copy=False,
        help="Set once a chatbot result has been delivered into its Discuss "
             "conversation, so the cron and the in-line wait never double-post.")

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
                message_type='comment', subtype_xmlid='mail.mt_comment', silent=True)
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
