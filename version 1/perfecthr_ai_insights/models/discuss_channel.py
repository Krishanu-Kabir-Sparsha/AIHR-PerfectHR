# -*- coding: utf-8 -*-
"""Discuss surfacing for the HR Chatbot.

A dedicated "PerfectHR AI" bot lives in a Discuss chat. When an employee posts a
message in that chat, we ground the question against the live database, submit an
hr_chatbot job, briefly wait for it, and the answer is posted back into the same
conversation by the bot. This mirrors OdooBot's supported pattern
(mail_bot -> discuss.channel._message_post_after_hook); no Odoo-core or
AIHR-owned code is patched.
"""
import logging
import re

from markupsafe import Markup, escape

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_BOT_XMLID = 'perfecthr_ai_insights.partner_perfecthr_ai_bot'

# Bound the in-line wait so a slow job never holds the request for long; the
# auto-poll cron in perfecthr_ai_core delivers anything that runs longer.
_CHAT_WAIT_TIMEOUT = 18.0
_CHAT_WAIT_INTERVAL = 1.5


def _html_to_text(html):
    if not html:
        return ''
    text = re.sub(r'<br\s*/?>', '\n', html or '')
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = (text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"'))
    return re.sub(r'[ \t]+', ' ', text).strip()


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    # ------------------------------------------------------------------
    # Bot identity / channel detection / launcher
    # ------------------------------------------------------------------
    def _perfecthr_ai_bot(self):
        return self.env.ref(_BOT_XMLID, raise_if_not_found=False)

    def _is_perfecthr_ai_channel(self):
        self.ensure_one()
        bot = self._perfecthr_ai_bot()
        if not bot or self.channel_type != 'chat':
            return False
        return bot in self.with_context(active_test=False).channel_partner_ids

    @api.model
    def perfecthr_ai_get_or_create_channel(self):
        """Return the current user's DM with the AI bot, creating + pinning it on
        first use. Called by the /perfecthr_ai/chat controller (a plain browser
        redirect — avoids the embedded SPA's server-action handling entirely)."""
        bot = self._perfecthr_ai_bot()
        if not bot:
            raise UserError("The PerfectHR AI assistant is not configured.")
        return self.channel_get([bot.id])

    # ------------------------------------------------------------------
    # Reply logic (hooked after each posted message, like OdooBot)
    # ------------------------------------------------------------------
    def _message_post_after_hook(self, message, msg_vals):
        res = super()._message_post_after_hook(message, msg_vals)
        try:
            self._perfecthr_ai_maybe_reply(message, msg_vals)
        except Exception as exc:  # noqa: BLE001 - never break message posting
            _logger.exception("PerfectHR AI chatbot reply failed: %s", exc)
        return res

    def _perfecthr_ai_maybe_reply(self, message, msg_vals):
        self.ensure_one()
        bot = self._perfecthr_ai_bot()
        if not bot or not self._is_perfecthr_ai_channel():
            return
        author_id = msg_vals.get('author_id') or (message.author_id.id if message else False)
        if not author_id or author_id == bot.id:
            return  # don't answer our own messages (prevents loops)
        message_type = msg_vals.get('message_type') or (message.message_type if message else '')
        if message_type != 'comment':
            return
        question = _html_to_text(msg_vals.get('body') or (message.body if message else '')).strip()
        if not question:
            return
        self._perfecthr_ai_answer(bot, author_id, question)

    def _perfecthr_ai_answer(self, bot, author_id, question):
        """Ground -> submit -> brief wait. Always leaves a visible message in the
        chat: the answer (posted by the result store when done/failed), or an
        explicit error/working note. Failures are surfaced, never swallowed."""
        from odoo.addons.perfecthr_ai_core.services.ai_orchestrator import AIOrchestrator
        from ..services.chat_grounding import build_grounded_question

        try:
            grounded = build_grounded_question(self, author_id, question)
        except Exception as exc:  # noqa: BLE001 - degrade to an ungrounded answer
            _logger.exception("PerfectHR AI grounding failed: %s", exc)
            grounded = question

        orchestrator = AIOrchestrator(self.env)
        try:
            result = orchestrator.submit(
                'hr_chatbot',
                payload={'question': grounded, 'session_id': str(self.id)},
                res_model='discuss.channel', res_id=self.id)
        except Exception as exc:  # noqa: BLE001 - show the real reason in the chat
            _logger.exception("PerfectHR AI submit failed: %s", exc)
            self._perfecthr_ai_post(
                bot, Markup("<p><i>⚠️ I couldn't reach the AI service: %s</i></p>")
                % escape(str(exc)))
            return

        try:
            orchestrator.wait(result, timeout=_CHAT_WAIT_TIMEOUT, interval=_CHAT_WAIT_INTERVAL)
        except Exception as exc:  # noqa: BLE001 - the cron remains a safety net
            _logger.exception("PerfectHR AI poll failed: %s", exc)

        # done/failed answers are posted by perfecthr.ai.result; cover the
        # still-running case so the user is never left without a response.
        if result.state not in ('done', 'failed'):
            self._perfecthr_ai_post(
                bot, Markup("<p><i>⏳ Working on your question — I'll reply here "
                            "shortly.</i></p>"))

    def _perfecthr_ai_post(self, bot, body):
        self.sudo().message_post(
            author_id=bot.id, body=body, message_type='comment',
            subtype_xmlid='mail.mt_comment', silent=True)
