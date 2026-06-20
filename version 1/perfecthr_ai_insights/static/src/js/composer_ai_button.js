/** @odoo-module **/

import { Composer } from "@mail/core/common/composer";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

/**
 * "AI Assistant" mode for the Discuss composer (see composer_ai_button.xml/.css).
 *
 * The toolbar button toggles an AI mode for the current conversation. While the
 * mode is ON, sending a message (Send button or Enter) posts it as usual AND asks
 * the PerfectHR HR chatbot; the answer is posted server-side and delivered live
 * over the bus. While OFF, the composer behaves completely normally.
 */
patch(Composer.prototype, {
    setup() {
        super.setup();
        this._perfecthrAiOrm = useService("orm");
        this._perfecthrAiNotif = useService("notification");
        this.perfecthrAi = useState({ active: false });
    },

    togglePerfecthrAi() {
        this.perfecthrAi.active = !this.perfecthrAi.active;
        this._perfecthrAiNotif.add(
            this.perfecthrAi.active
                ? _t("AI Assistant is ON — type your question and press Send.")
                : _t("AI Assistant is OFF."),
            { type: this.perfecthrAi.active ? "info" : "warning" }
        );
    },

    get placeholder() {
        if (this.perfecthrAi?.active && this.thread?.model === "discuss.channel") {
            return _t("Ask the AI Assistant…");
        }
        return super.placeholder;
    },

    async sendMessage() {
        const aiOn = this.perfecthrAi?.active && this.thread?.model === "discuss.channel";
        if (!aiOn) {
            return super.sendMessage();
        }
        const thread = this.thread;
        const question = (this.props.composer.text || "").trim();
        if (!question) {
            return super.sendMessage();
        }
        // 1) Post the question through the normal send (instant, optimistic).
        await super.sendMessage();
        // 2) Ask the AI in a separate request → the answer commits + is delivered
        //    live over the bus when ready (no reload). AI mode stays on.
        this._perfecthrAiNotif.add(_t("Asking the AI Assistant…"), { type: "info" });
        try {
            await this._perfecthrAiOrm.call("discuss.channel", "perfecthr_ai_run", [
                [thread.id],
                question,
            ]);
        } catch {
            this._perfecthrAiNotif.add(
                _t("The AI Assistant could not be reached. Please try again."),
                { type: "danger" }
            );
        }
    },
});
