"""Opt-in, bounded live planning + retrieval regression; never logs credentials.

No database writes, catalogue sourcing, or whole-design runs. Existing account
quotas apply. Execute only when an explicit live check is intended.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from continuity import env, llm
from continuity.designs.context import ExecutionContext, current
from continuity.planner.plan import plan_board, structural_issues
from continuity.parts import research

BRIEF = ('Design a battery-powered bicycle anti-theft alarm. Use an accelerometer to detect movement, '
         'a buzzer, an arm/disarm button and a status LED. Power it from a single rechargeable '
         '3.7 V Li-ion battery with USB-C charging. Prioritize low standby power and compact size.')


async def main():
    env.load()
    os.environ.update(CONTINUITY_RESEARCH_MAX_CALLS='2', CONTINUITY_RESEARCH_BUDGET_USD='0.04',
                      CONTINUITY_LLM_MAX_CALLS='3', CONTINUITY_LLM_TOKEN_BUDGET='24000')
    context = ExecutionContext('review-provider-smoke')
    token = current.set(context)
    try:
        plan = await plan_board(BRIEF)
        planning_only = '--planning-only' in sys.argv
        found = await research.search_documents('Sensirion SHT40 manufacturer datasheet supply voltage') if not planning_only else {'documents': []}
        repeated = await research.search_documents('Sensirion SHT40 manufacturer datasheet supply voltage') if not planning_only else found
        urls = [d['url'] for d in found['documents']]
        contents = await research.read_documents(urls[:1]) if urls else {'documents': []}
        planning_ok = (plan is not None and not structural_issues(plan, BRIEF)
                       and plan.requirements.input_source == 'battery-3v7'
                       and plan.requirements.input_voltage == 3.7
                       and plan.requirements.supply_capacity_mah is None)
        research_ok = bool(contents['documents']) and found == repeated and context.research_calls <= 2
        print(json.dumps({'provider': llm.public_status(), 'planning_pass': planning_ok,
            'source': plan.requirements.input_source if plan else None,
            'input_voltage': plan.requirements.input_voltage if plan else None,
            'capacity_left_unknown': plan.requirements.supply_capacity_mah is None if plan else None,
            'slots': {key: slot.label for key, slot in plan.slots.items()} if plan else {},
            'planner_reply_slots': [[{'id': s.get('id'), 'tier': s.get('tier'), 'category': s.get('category')} for s in item.get('slots', []) if isinstance(s, dict)] for item in context.llm_cache.values()],
            'groq_requests': context.llm_calls, 'llm_accounted_tokens': context.llm_tokens,
            'research_pass': None if planning_only else research_ok, 'exa_requests': context.research_calls,
            'exa_reported_cost_usd': sum(item.get('cost_usd', 0) for item in (found, contents)),
            'research_documents': len(contents['documents']),
            'research_errors': [item.get('error') for item in (found, contents) if item.get('error')],
            'cached_search_reused': None if planning_only else found == repeated}, indent=2))
        return 0 if planning_ok and (research_ok or planning_only) else 1
    finally:
        current.reset(token)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
