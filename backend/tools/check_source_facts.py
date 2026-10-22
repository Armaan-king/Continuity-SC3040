"""Opt-in known-datasheet smoke: at most two Exa requests and one Groq request.

Uses the independently verified NXP link, not a live supplier search. Never writes
to a database. The second invocation inside this check must reuse both caches.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from continuity import env, llm
from continuity.designs.context import ExecutionContext, current
from continuity.engine.models import Board, PartSpec, Requirements, Slot
from continuity.engine.rules import interface_role_match
from continuity.parts import research


async def main():
    env.load()
    os.environ.update(CONTINUITY_LLM_PROVIDER='groq', CONTINUITY_RESEARCH_ENABLED='1', CONTINUITY_FIXTURES='0',
                      CONTINUITY_RESEARCH_MAX_CALLS='2', CONTINUITY_RESEARCH_BUDGET_USD='0.04',
                      CONTINUITY_EVIDENCE_MAX_PARTS='1', CONTINUITY_LLM_MAX_CALLS='1', CONTINUITY_LLM_TOKEN_BUDGET='12000')
    llm.MAX_ATTEMPTS = 1  # This smoke never retries a billable provider request.
    if not research.configured() or not llm.available():
        print(json.dumps({'passed': False, 'reason': 'Both Exa and Groq must be configured privately.'}))
        return 1
    part = PartSpec('MC9S08AW60CFGE', 'NXP Semicon', '', 'Microcontrollers', role='master',
                    datasheet='https://www.nxp.com/docs/en/data-sheet/MC9S08AW60.pdf')
    sensor = PartSpec('BH1750', 'ROHM', '', 'Sensors', role='peripheral', interfaces=('I2C',))
    def checks(controller):
        board = Board(Requirements(), {'mcu': Slot('mcu', 'Controller', 'core', part=controller),
                                      'light': Slot('light', 'Light sensor', 'peripherals', part=sensor)}, {})
        return interface_role_match(board)
    context = ExecutionContext('manual-source-fact-smoke')
    token = current.set(context)
    try:
        enriched = await research.enrich(part)
        counts = (context.research_calls, context.llm_calls)
        repeated = await research.enrich(part)
        cache_reused = (context.research_calls, context.llm_calls) == counts and repeated == enriched
        after = checks(enriched)
        passed = any(v.subject == 'light' and v.status == 'pass' for v in after) and cache_reused
        output = {'passed': passed, 'before_unknown': sum(v.unchecked for v in checks(part)),
                  'after': [{'subject': v.subject, 'status': v.status, 'unchecked': v.unchecked} for v in after],
                  'interfaces': enriched.interfaces, 'cache_reused': cache_reused,
                  'exa_requests': context.research_calls, 'groq_requests': context.llm_calls,
                  'accounted_tokens': context.llm_tokens,
                  'exa_reported_cost_usd': sum(r.get('cost_usd', 0) for r in context.research_cache.values()),
                  'sources': [{'url': d['url'], 'characters': len(d['text']), 'extraction_status': d.get('extraction_status'),
                               'claims': [{'group': c['group'], 'status': c['status'], 'reason': c['reason'],
                                           'quote': c['quote'], 'identity_basis': c.get('identity_basis')}
                                          for c in d.get('claims', [])]} for d in enriched.research]}
        print(json.dumps(output, indent=2, ensure_ascii=True))
        return 0 if passed else 1
    finally:
        current.reset(token)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
