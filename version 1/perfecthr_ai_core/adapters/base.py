# -*- coding: utf-8 -*-
"""Adapter framework — one adapter per AIHR model.

An adapter encapsulates everything model-specific:
  - target_model    : the PerfectHR/Odoo model it runs against
  - module_key      : the connector key used to submit (must match the AIHR
                      SDK module_registry key, e.g. 'cv_matcher')
  - required_inputs : payload keys that must be present/non-empty before submit
  - build_payload() : gather PerfectHR data and shape the runtime input (inline)
  - map_result()    : normalize the runtime output into ai.result write-values
  - is_real_inference(): tell a real LLM result from the runtime's canned fallback

Adapters self-register via @register_adapter, so the engine, store, wizard and
(future) insights layer stay model-agnostic.
"""
import logging

_logger = logging.getLogger(__name__)

_ADAPTERS = {}


def register_adapter(cls):
    """Class decorator: instantiate and register an adapter by its module_key."""
    inst = cls()
    if not inst.module_key:
        raise ValueError("Adapter %s declares no module_key" % cls.__name__)
    if inst.module_key in _ADAPTERS:
        _logger.warning("AI adapter '%s' already registered; overriding", inst.module_key)
    _ADAPTERS[inst.module_key] = inst
    return cls


def get_adapter(module_key):
    return _ADAPTERS.get(module_key)


def all_adapters():
    return list(_ADAPTERS.values())


class AIModelAdapter:
    """Base class for AIHR model adapters. Subclass + decorate with @register_adapter."""

    module_key = ""        # connector SDK key used to submit the job
    label = ""             # human-friendly name
    target_model = ""      # Odoo model this adapter runs against
    required_inputs = ()   # payload keys that must be present/non-empty

    # ---- input: PerfectHR record -> runtime payload (data passed inline) ----
    def build_payload(self, record):
        raise NotImplementedError("%s.build_payload is not implemented" % type(self).__name__)

    # ---- output: runtime result -> normalized ai.result write-values ----
    def map_result(self, raw):
        """Generic mapping; override per model for richer structure."""
        raw = raw or {}
        score = (raw.get('score') or raw.get('match_score') or raw.get('performance_score')
                 or raw.get('overall_engagement_score') or raw.get('engagement_score') or 0.0)
        try:
            score = float(score or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        return {
            'score': score,
            'label': str(raw.get('recommendation') or raw.get('rating')
                         or raw.get('retention_risk') or raw.get('category') or ''),
            'summary': raw.get('summary') or raw.get('answer') or raw.get('response') or '',
        }

    def is_real_inference(self, raw):
        """Default fallback detector. The runtime returns valid JSON with neutral
        values when Ollama/GPU is unavailable — treat that as 'not real'."""
        raw = raw or {}
        blob = " ".join(str(raw.get(k) or '') for k in
                        ('summary', 'answer', 'response', 'transcript')).lower()
        if 'analysis unavailable' in blob or 'safe_mode' in blob:
            return False
        debug = raw.get('debug') or {}
        if isinstance(debug, dict) and debug.get('safe_mode'):
            return False
        return True
