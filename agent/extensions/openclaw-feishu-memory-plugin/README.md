# OpenClaw Feishu Memory Plugin

Local MVP companion plugin for Feishu project decision memory.

## Implemented Tools

```text
feishu_memory_ping
feishu_memory_create
feishu_memory_confirm
feishu_memory_search
feishu_memory_onboarding_pack
mars_memory_ingest_file
mars_memory_ingest_feishu_messages
mars_memory_ingest_text
mars_memory_digest
mars_memory_search
mars_memory_retrieval_logs
mars_memory_reconcile
mars_memory_decision_card
mars_memory_doc_decision_card
mars_memory_similar_decisions
feishu_memory_lifecycle_probe
feishu_memory_decision_command
feishu_memory_active_push_probe
feishu_memory_render_interactive_card
feishu_memory_bitable_governance_schema
feishu_memory_bitable_record_payload
feishu_memory_governance_action
```

## OpenClaw-Led Decision Flow

For user-facing Feishu document or chat decision cards, OpenClaw should make the final semantic judgment:

```text
1. Read the full Feishu document or chat context.
2. Produce a full-coverage structured summary across periods/topics.
3. Call feishu_memory_lifecycle_probe(projectId, query, summaryText).
4. Inspect similar_decisions and MARS heuristic_status.
5. Decide lifecycle: new / update / conflict / duplicate.
6. Call feishu_memory_decision_command with:
   - contextText: original text
   - agentSummary: full-coverage summary
   - agentLifecycleDecision: final lifecycle judgment
   - agentStructuredCard: decisions, reasons, objections, conclusions, phase, dates, evidence_items
   - autoCommit=false by default
```

For Feishu group history, OpenClaw should call the official Feishu history tool first:

```text
1. feishu_im_user_get_messages(chat_id, relative_time/page_size/sort_rule)
2. mars_memory_ingest_feishu_messages(projectId, chatId, messages, autoDigest=true)
3. feishu_memory_lifecycle_probe(...)
4. feishu_memory_decision_command(...)
```

This preserves Feishu `message_id` values as MARS raw ledger evidence IDs.

`mars_memory_doc_decision_card` is a low-level shortcut. Do not use it as the final user-facing flow when duplicate/update/conflict judgment matters.

## Evidence Chain

`feishu_memory_decision_command` now returns `decision_card.evidence_chain`.

The evidence chain contains:

```text
source_id
source_url
source_title
source_scope
evidence_message_ids
evidence_items[]:
  evidence_id
  source_type
  source_id
  source_url
  quote
  reason
coverage:
  has_decisions
  has_reasons
  has_objections
  has_conclusions
  has_time_points
  has_source_quotes
```

OpenClaw should pass explicit `agentStructuredCard.evidence_items` when it can quote exact Feishu source paragraphs. If it does not, the plugin infers short evidence snippets from `contextText` or `agentSummary`.

## Feishu Interactive Card

`feishu_memory_decision_command` returns `feishu_interactive_card`, and `feishu_memory_render_interactive_card` can render an existing `decision_card`.

The returned card is a Feishu interactive message card JSON object. Send it through the official OpenClaw Feishu message channel by passing it as the `card` parameter.

The card includes:

```text
Decision
Reason
Objection / Risk
Conclusion
Lifecycle
Recommended action
Confidence
Evidence
Buttons:
  Confirm
  Update Existing
  Ignore
```

Button payloads include stable action values such as `confirm_decision`, `update_decision`, and `ignore_decision`. A callback handler is still required to turn button clicks into governance actions.

When OpenClaw receives a button click or equivalent user instruction, call `feishu_memory_governance_action`. It returns the Bitable update fields and, when a local `memoryId` is provided, updates local memory status.

## Bitable Governance

`feishu_memory_bitable_governance_schema` returns the recommended Bitable field definitions.

`feishu_memory_bitable_record_payload` returns a `record.fields` payload for:

```text
feishu_bitable_app_table_record.create
feishu_bitable_app_table_record.update
```

Recommended governance statuses:

```text
pending_review
active
deprecated
ignored
conflict_review
```

Recommended Bitable flow:

```text
1. Create or reuse a Bitable app/table.
2. Use feishu_memory_bitable_governance_schema to create fields.
3. Generate a decision card with feishu_memory_decision_command.
4. Write bitable_record_fields with feishu_bitable_app_table_record.create.
5. When a human confirms/updates/ignores a card, call feishu_memory_governance_action.
6. Use the returned bitable_update_fields to update the same Bitable record.
```

## MARS Adapter

The `mars_memory_*` tools call the local Python MARS engine through:

```powershell
py -m mars_memory_engine ...
```

Recommended plugin config:

```json
{
  "enabled": true,
  "marsEnginePath": "D:/BaiduNetdiskWorkspace/项目/飞书openclaw/mars-memory-engine",
  "pythonCommand": "py"
}
```
