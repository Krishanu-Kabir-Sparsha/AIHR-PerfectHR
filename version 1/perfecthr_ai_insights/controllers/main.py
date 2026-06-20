# -*- coding: utf-8 -*-
"""Launcher route for the PerfectHR AI Discuss assistant.

The 'Ask PerfectHR AI' menu points at this route via a plain ir.actions.act_url
(target=self). The debranded build runs a custom embedded SPA that loops on an
act_url *returned from a server action* but follows a menu act_url with a normal
window.location navigation -- so we create/get the user's bot DM here and
redirect straight into Discuss.
"""
from odoo import http
from odoo.http import request


class PerfectHRAIChatController(http.Controller):

    @http.route('/perfecthr_ai/chat', type='http', auth='user', methods=['GET'])
    def open_perfecthr_ai_chat(self, **kwargs):
        channel = request.env['discuss.channel'].perfecthr_ai_get_or_create_channel()
        if channel:
            return request.redirect(
                '/odoo/action-mail.action_discuss?active_id=discuss.channel_%s'
                % channel.id)
        return request.redirect('/odoo/discuss')
