# Security checklist

Each rule in `CLAUDE.md` that must never be broken, how it is enforced, and the tests that would fail if it were broken. `apps/api/tests/test_rules.py` checks that every rule here names at least one test that exists.

Run all of them with `make test`.

## Rule 1: Money is integer minor units (paise)

**Enforced by:** every amount column is `BIGINT` and ends in `_minor`; the money helpers in `revlane/money.py` take and return integers; mappers convert provider strings with exact decimals and refuse floats; JSON payloads are parsed with decimals, never floats.

- `apps/api/tests/test_rules.py::test_every_money_column_is_whole_paise_and_no_column_is_a_float`
- `apps/api/tests/test_rules.py::test_a_float_is_never_accepted_as_an_amount`
- `apps/api/tests/test_money.py::test_parsing_refuses_bad_amounts`
- `apps/api/tests/test_ingest_synth.py::test_money_arrives_exact_to_the_paisa`
- `apps/api/tests/test_ingest_synth.py::test_stored_decimals_are_exact`

## Rule 2: The model never produces a number

**Enforced by:** model text refers to tool or query results as `{{t1.total_minor}}`; code resolves and formats every value; any digit, currency sign or amount word outside a reference fails validation, in agents and in chat.

- `apps/api/tests/test_grounding.py::test_text_carrying_its_own_figure_is_refused`
- `apps/api/tests/test_grounding.py::test_a_reference_must_lead_to_a_value_a_tool_returned`
- `apps/api/tests/test_harness.py::test_an_output_containing_a_bare_figure_is_rejected`
- `apps/api/tests/test_harness.py::test_no_stored_model_text_carries_a_figure_of_its_own`
- `apps/api/tests/test_chat.py::test_an_answer_with_a_figure_of_its_own_is_never_shown`

Limit: a count spelled out in words with no unit ("three payouts") is not caught.

## Rule 3: The model never writes SQL

**Enforced by:** chat produces a query spec whose schema has no field that could hold SQL; names are looked up in the semantic layer; values become bound parameters; queries run as a read-only database role that can see only the chat views.

- `apps/api/tests/test_chat.py::test_a_plan_with_anywhere_to_put_sql_is_not_a_plan`
- `apps/api/tests/test_chat.py::test_a_name_the_semantic_layer_does_not_define_is_refused_before_any_query`
- `apps/api/tests/test_chat.py::test_a_value_from_the_model_is_only_ever_a_bound_parameter`
- `apps/api/tests/test_chat.py::test_the_chat_code_builds_no_sql_from_text`
- `apps/api/tests/test_chat.py::test_the_chat_role_can_read_the_views_and_nothing_else`

## Rule 4: Every tenant table has `tenant_id` and row-level security

**Enforced by:** a policy on every table; sessions switch to a restricted role and pin the tenant; unrestricted sessions are allowed only in a short list of files.

- `apps/api/tests/test_tenant_isolation.py::test_table_has_a_tenant_column_and_row_level_security`
- `apps/api/tests/test_tenant_isolation.py::test_tenant_a_cannot_read_tenant_b`
- `apps/api/tests/test_tenant_isolation.py::test_tenant_a_cannot_write_a_row_for_tenant_b`
- `apps/api/tests/test_session_use.py::test_unrestricted_database_access_stays_in_the_allowed_files`
- `apps/api/tests/test_chat.py::test_a_cross_tenant_question_returns_nothing_from_another_tenant`

The isolation tests are written against the catalogue, so a table added later is covered without touching them.

## Rule 5: Nothing is sent to a provider or a customer

**Enforced by:** connectors expose no write action; every agent action only drafts a document; only three modules may open a network connection (sandbox reads and the model provider).

- `apps/api/tests/test_rules.py::test_only_three_modules_can_open_a_network_connection`
- `apps/api/tests/test_rules.py::test_no_connector_can_write_to_a_provider`
- `apps/api/tests/test_rules.py::test_every_action_only_drafts_a_document`
- `apps/api/tests/test_live_connectors.py::test_razorpay_backfill_pages_through_payments_with_get_requests_only`

## Rule 6: Every proposal needs approval

**Enforced by:** a database trigger refuses any `action` or `claim` row unless the proposal has the approvals the tenant requires and no rejection; the code checks the same before it writes.

- `apps/api/tests/test_approvals.py::test_the_database_refuses_an_action_or_a_claim_for_an_unapproved_proposal`
- `apps/api/tests/test_approvals.py::test_setting_a_proposal_to_approved_by_hand_does_not_get_past_the_gate`
- `apps/api/tests/test_approvals.py::test_a_rejected_proposal_can_never_be_carried_out`
- `apps/api/tests/test_approvals.py::test_with_two_person_approval_one_approval_is_not_enough`
- `apps/api/tests/test_harness.py::test_a_model_that_obeys_the_injection_still_gets_nothing_past_the_harness`

A decision is recorded against the person signed in; the request cannot name anyone else.

- `apps/api/tests/test_auth.py::test_a_decision_is_recorded_against_whoever_is_signed_in`
- `apps/api/tests/test_auth.py::test_each_role_can_do_what_it_should_and_no_more`

## Rule 7: Raw payloads are kept; card numbers and contact details are not

**Enforced by:** mappers scrub every payload before it is stored; shoppers are a keyed hash under a per-tenant key; uploaded files are not stored; erasure removes the hash on instruction.

- `apps/api/tests/test_ingest_synth.py::test_no_stored_payload_keeps_a_card_number_or_contact`
- `apps/api/tests/test_ingest_synth.py::test_the_generated_files_do_hold_what_ingestion_drops`
- `apps/api/tests/test_ingest_synth.py::test_shoppers_are_keyed_hash_tokens_only`
- `apps/api/tests/test_hardening.py::test_erasing_a_shopper_removes_their_token_and_nothing_else`

## Rule 8: No secrets in code, logs, prompts or fixtures

**Enforced by:** credentials live only in the vault; references to them reveal nothing; bound values are hidden from database errors; model errors never carry the provider's message; the repository is scanned for secret-shaped strings.

- `apps/api/tests/test_rules.py::test_no_file_in_the_repository_holds_something_shaped_like_a_secret`
- `apps/api/tests/test_rules.py::test_env_files_are_never_committed_and_the_example_holds_no_values`
- `apps/api/tests/test_rules.py::test_bound_values_never_appear_in_database_errors_or_logs`
- `apps/api/tests/test_vault.py::test_the_stored_value_is_not_the_secret`
- `apps/api/tests/test_vault.py::test_the_reference_and_the_vault_do_not_reveal_anything`
- `apps/api/tests/test_live_model.py::test_sdk_errors_become_model_errors_the_queue_can_retry`

Limit: the scan looks for known key shapes. It cannot recognise a secret with no recognisable shape.

## Rule 9: Status is derived from `attempt_event`

**Enforced by:** an attempt has no status column; the derived status is a cache that names its rule version and can be rebuilt from events at any time; the mapping table is pinned by contract tests.

- `apps/api/tests/test_rules.py::test_an_attempt_has_no_status_column_of_its_own`
- `apps/api/tests/test_ingest_synth.py::test_status_can_be_rebuilt_from_events_alone`
- `apps/api/tests/test_ingest_synth.py::test_trap_t4_changes_the_status_and_keeps_the_history`
- `apps/api/tests/test_status_contract.py::test_the_table_above_covers_every_mapped_status_and_nothing_else`
- `apps/api/tests/test_status_contract.py::test_the_rules_carry_a_version`

## Rule 10: Only sandbox or synthetic data

**Enforced by:** live calls are off unless `PROVIDER_LIVE_CALLS` is set; the transport refuses every host but two sandbox hosts; Razorpay keys must be test-mode keys; tests and evaluation run on the synthetic merchant with the stub model.

- `apps/api/tests/test_live_connectors.py::test_live_calls_are_off_by_default`
- `apps/api/tests/test_live_connectors.py::test_the_transport_refuses_every_host_that_is_not_a_sandbox`
- `apps/api/tests/test_live_connectors.py::test_no_other_provider_host_is_written_anywhere_in_the_connectors`
- `apps/api/tests/test_live_connectors.py::test_razorpay_refuses_anything_but_a_test_mode_key`
- `apps/api/tests/test_settings.py::test_stub_mode_is_the_default_without_a_key`

Limit: `api.razorpay.com` serves both test and live mode; what keeps calls in test mode is the key check, not the host. PayU's sandbox has its own host.

## Known gaps

These are outside the ten rules but matter before any real merchant uses this.

- **Sign-in is email and password only.** Sessions are signed cookies; every route but the health check, the sample data and sign-in itself needs one, and `apps/api/tests/test_auth.py::test_every_route_but_the_public_few_refuses_a_request_with_no_session` checks each route. Not built: single sign-on and a second factor (stubs only), password reset, account lockout beyond the sign-in rate limit, and a screen for managing people.
- **A session cannot be ended early for one device.** It ends at sign-out, at expiry, when the person's row is removed, or for everyone when `SESSION_SECRET` changes.
- **The demo users** can sign in whenever `DEMO_USER_PASSWORD` is set. Leave it empty outside development.
- **The API's own documentation pages** (`/docs`, `/openapi.json`) are public. They describe the routes and hold no data.
- **The console does not hide what a role cannot do.** A viewer sees the Approve button; the API refuses the request.
- **Rate limits are per process.** With more than one API instance they need a shared store.
- **The development vault** encrypts with a key from the environment. Production needs the KMS-backed backend behind the same interface; it is not built.
- **Uploaded files are parsed in memory**, up to 25 MB.
- **The model provider has never been called.** The live clients are tested against a stand-in for the SDK only.
