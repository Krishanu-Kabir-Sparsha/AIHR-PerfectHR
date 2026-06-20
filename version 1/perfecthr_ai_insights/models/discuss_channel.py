# -*- coding: utf-8 -*-
"""Discuss surfacing for the HR Chatbot — composer-button approach.

An "AI Assistant" button is injected into the Discuss composer toolbar (see
static/src/) and is available in every conversation. When clicked, the frontend
posts the typed text as a normal message (instant), then calls `perfecthr_ai_run`
in a separate request: we ground the question against the live database, submit
an hr_chatbot job, and the answer is posted back by perfecthr.ai.result and
delivered live over the bus when ready. No Odoo-core or AIHR-owned code is patched.
"""
import logging

from markupsafe import Markup, escape

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_BOT_XMLID = 'perfecthr_ai_insights.partner_perfecthr_ai_bot'

# Bound the in-line wait so a slow job never holds the request for long; the
# auto-poll cron in perfecthr_ai_core delivers anything that runs longer.
_CHAT_WAIT_TIMEOUT = 18.0
_CHAT_WAIT_INTERVAL = 1.5


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    def _perfecthr_ai_bot(self):
        return self.env.ref(_BOT_XMLID, raise_if_not_found=False)

    def perfecthr_ai_run(self, question):
        """Entry point for the composer 'AI Assistant' button (called via ORM).

        The question is posted by the composer itself (normal send), so this only
        runs the HR chatbot and posts the answer. Running in its own request means
        the answer commits and is delivered live over the bus as soon as it's
        ready, instead of being held by the question's transaction."""
        self.ensure_one()
        question = (question or '').strip()
        if not question:
            return False
        bot = self._perfecthr_ai_bot()
        if not bot:
            raise UserError(_("The PerfectHR AI assistant is not configured."))
        self._perfecthr_ai_answer(bot, self.env.user.partner_id.id, question)
        return True

    def _perfecthr_ai_answer(self, bot, author_id, question):
        """Ground -> submit -> brief wait. Always leaves a visible message in the
        conversation: the answer (posted by the result store when done/failed),
        or an explicit error note. Failures are surfaced, never swallowed."""
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
            subtype_xmlid='mail.mt_comment')
