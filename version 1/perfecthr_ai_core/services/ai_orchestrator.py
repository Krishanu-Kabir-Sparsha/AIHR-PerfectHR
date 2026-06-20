# -*- coding: utf-8 -*-
"""Orchestrator — submit + poll, reusing the connector's AIHRRuntimeService.

This is the only place that bridges an adapter to the AIHR transport. It never
makes HTTP calls itself; it delegates to perfecthr_aihr_connector so the
AIHR-owned auth/token/refresh logic stays the single source of truth.

Local-runtime fallback: when the Control Plane rejects a task with HTTP 422
("Unknown or disabled task"), submit directly to the local AIHR runtime at
localhost:8009. This covers tasks that are registered in the runtime worker
registry but not yet enabled in the CP's per-tenant allowlist. Jobs submitted
this way get a "local:" prefix on their job_id so poll() routes them correctly.
"""
import json
import logging

from odoo import fields
from odoo.exceptions import UserError

from ..adapters.base import get_adapter

_logger = logging.getLogger(__name__)

_LOCAL_PREFIX = 'local:'


class AIOrchestrator:
    def __init__(self, env):
        self.env = env

    def _service(self):
        # Imported lazily so this module never hard-fails at load if the
        # connector is being upgraded.
        from odoo.addons.perfecthr_aihr_connector.services.runtime_service import AIHRRuntimeService
        return AIHRRuntimeService(self.env)

    # ── local runtime helpers (stdlib only — no extra deps) ───────────────────

    def _get_param(self, key, default=''):
        return self.env['ir.config_parameter'].sudo().get_param(key, default=default)

    def _local_runtime_url(self):
        return self._get_param('perfecthr_aihr.local_runtime_url', 'http://localhost:8009')

    def _runtime_api_key(self):
        return self._get_param('perfecthr_aihr.runtime_api_key', 'aihr-runtime-local-key')

    def _make_module_token(self):
        """Build an HS256 JWT for the local runtime's executions endpoint using stdlib."""
        import base64
        import hashlib
        import hmac
        import time

        secret = self._get_param('perfecthr_aihr.module_secret_key', 'your-module-secret').encode()
        tenant_id = int(self._get_param('perfecthr_aihr.tenant_id') or 53)

        def _b64url(b):
            return base64.urlsafe_b64encode(b).rstrip(b'=').decode()

        hdr = _b64url(b'{"alg":"HS256","typ":"JWT"}')
        claims = json.dumps(
            {'tenant_id': tenant_id, 'module': 'any', 'exp': int(time.time()) + 3600},
            separators=(',', ':'),
        ).encode()
        pld = _b64url(claims)
        signing_input = f'{hdr}.{pld}'.encode()
        sig = _b64url(hmac.new(secret, signing_input, hashlib.sha256).digest())
        return f'{hdr}.{pld}.{sig}'

    def _submit_local(self, module_key, payload):
        """POST directly to /api/v1/runtime/execute on the local runtime."""
        import urllib.request

        tenant_id = int(self._get_param('perfecthr_aihr.tenant_id') or 53)
        url = self._local_runtime_url() + '/api/v1/runtime/execute'
        body = json.dumps({
            'module_name': module_key,
            'tenant_id': tenant_id,
            'input_payload': payload or {},
        }).encode()
        req = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json',
            'X-Runtime-API-Key': self._runtime_api_key(),
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                exec_id = data.get('execution_id', '')
                if not exec_id:
                    raise RuntimeError('Local runtime returned no execution_id')
                return {'success': True, 'message': 'Submitted to local runtime',
                        'data': {'job_id': f'{_LOCAL_PREFIX}{exec_id}'}}
        except urllib.error.HTTPError as e:
            err = e.read().decode('utf-8', errors='replace')
            raise RuntimeError(
                f'Local runtime dispatch failed (HTTP {e.code}): {err[:300]}') from e

    def _get_local_status(self, job_id):
        """GET /api/v1/runtime/executions/{id} on the local runtime."""
        import urllib.request

        exec_id = job_id[len(_LOCAL_PREFIX):]
        tenant_id = str(self._get_param('perfecthr_aihr.tenant_id') or '53')
        url = self._local_runtime_url() + f'/api/v1/runtime/executions/{exec_id}'
        req = urllib.request.Request(url, headers={
            'X-Module-Token': self._make_module_token(),
            'X-Tenant-ID': tenant_id,
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                status = (data.get('status') or '').lower()
                if status == 'completed':
                    return {'success': True,
                            'data': {'status': 'completed',
                                     'result': data.get('output_payload') or {}}}
                if status in ('failed', 'cancelled'):
                    return {'success': True,
                            'data': {'status': 'failed',
                                     'error': data.get('error_message') or 'Runtime error'}}
                return {'success': True, 'data': {'status': 'processing'}}
        except urllib.error.HTTPError as e:
            err = e.read().decode('utf-8', errors='replace')
            raise RuntimeError(
                f'Local runtime poll failed (HTTP {e.code}): {err[:300]}') from e

    # ─────────────────────────────────────────────────────────────────────────

    def submit(self, module_key, record=None, payload=None, res_model=None, res_id=None):
        """Submit an AI job and create a perfecthr.ai.result tracker. Returns it.

        Two ways to provide the payload:
          - record-driven (the default): pass a `record`; the adapter's
            build_payload(record) shapes the input and res_model/res_id default
            to that record (used by the recruitment vertical, the wizard, ...).
          - explicit: pass a ready `payload` dict (+ optional res_model/res_id)
            for jobs not tied to a single business record, e.g. the Discuss
            chatbot whose question is grounded by the insights layer.
        """
        adapter = get_adapter(module_key)
        if not adapter:
            raise UserError("No AI adapter registered for '%s'." % module_key)

        if payload is None:
            if record is None:
                raise UserError("submit() needs a record or an explicit payload "
                                "for '%s'." % module_key)
            if adapter.target_model and record._name != adapter.target_model:
                raise UserError("Adapter '%s' expects %s but got %s."
                                % (module_key, adapter.target_model, record._name))
            payload = adapter.build_payload(record)

        missing = [k for k in adapter.required_inputs if not payload.get(k)]
        if missing:
            raise UserError("Missing required input(s) for %s: %s"
                            % (module_key, ", ".join(missing)))

        response = self._service().submit_ai_job(module_key, payload)
        if not response.get('success'):
            msg = response.get('message', '')
            # CP returned 422 "Unknown or disabled task" → the task is registered
            # in the local runtime but not yet in the CP's per-tenant allowlist.
            # Fall back to submitting directly to the local runtime.
            if 'HTTP 422' in msg and ('Unknown or disabled task' in msg
                                      or 'VALIDATION_FAILED' in msg):
                _logger.info(
                    'CP task not enabled for %s — submitting to local runtime', module_key)
                try:
                    response = self._submit_local(module_key, payload)
                except Exception as exc:
                    raise UserError("AI submission failed: %s" % exc) from exc
            if not response.get('success'):
                raise UserError("AI submission failed: %s" % response.get('message'))

        data = response.get('data') or {}
        job_id = data.get('job_id') or data.get('execution_id') or ''
        if res_model is None:
            res_model = record._name if record is not None else False
        if res_id is None:
            res_id = record.id if record is not None else False
        return self.env['perfecthr.ai.result'].create({
            'module_key': module_key,
            'res_model': res_model or False,
            'res_id': res_id or False,
            'job_id': job_id,
            'state': 'queued',
            'payload_json': json.dumps(payload, indent=2, default=str),
        })

    def wait(self, result, timeout=20.0, interval=1.5):
        """Synchronously poll until the result is terminal or `timeout` seconds
        elapse, then return it. For interactive flows (the Discuss chatbot) that
        want the answer in-line; the background cron stays the safety net for
        any job that runs longer than the timeout."""
        import time
        self.poll(result)
        deadline = time.monotonic() + max(0.0, timeout)
        while result.state in ('queued', 'processing') and time.monotonic() < deadline:
            time.sleep(max(0.2, interval))
            self.poll(result)
        return result

    def poll(self, result):
        """Poll one perfecthr.ai.result via the connector and ingest the outcome."""
        if not result.job_id:
            raise UserError("This result has no job id to poll.")

        if result.job_id.startswith(_LOCAL_PREFIX):
            try:
                response = self._get_local_status(result.job_id)
            except Exception as exc:
                raise UserError("Could not fetch job status: %s" % exc) from exc
        else:
            response = self._service().get_job_status(result.job_id)
        if not response.get('success'):
            raise UserError("Could not fetch job status: %s" % response.get('message'))

        data = response.get('data') or {}
        status = (data.get('status') or '').lower()

        if status in ('completed', 'done', 'success'):
            raw = data.get('result') or {}
            adapter = get_adapter(result.module_key)
            vals = adapter.map_result(raw) if adapter else {}
            vals.update({
                'state': 'done',
                'raw_json': json.dumps(raw, indent=2, default=str),
                'is_real_inference': adapter.is_real_inference(raw) if adapter else True,
                'analyzed_at': fields.Datetime.now(),
                'error_message': False,
            })
            result.write(vals)
        elif status in ('failed', 'error'):
            result.write({'state': 'failed',
                          'error_message': data.get('error') or 'Runtime reported failure'})
        else:
            result.write({'state': 'processing'})
        return result
