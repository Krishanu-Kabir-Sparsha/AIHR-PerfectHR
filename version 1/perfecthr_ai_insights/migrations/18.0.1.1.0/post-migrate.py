# -*- coding: utf-8 -*-
"""Retire the old 'PerfectHR AI' direct-message channels.

The chat-channel approach (a dedicated bot DM per user) was replaced by the
composer 'AI Assistant' button, so the leftover DMs are no longer wanted. The
bot now only *authors* answers in the conversation where you ask — it is never a
channel member — so any 'chat' channel that still has the bot as a member is an
old DM and is safe to remove.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    bot = env.ref('perfecthr_ai_insights.partner_perfecthr_ai_bot',
                  raise_if_not_found=False)
    if not bot:
        return
    members = env['discuss.channel.member'].search([('partner_id', '=', bot.id)])
    channels = members.mapped('channel_id').filtered(
        lambda c: c.channel_type == 'chat')
    if channels:
        channels.unlink()
