"""The fixed question set for Ask Revlane, each with its SQL ground truth.

The ground truth is written by hand against the tables, not the chat views,
and is run in a tenant-scoped session. `:start` and `:end` are the dates the
question's period means; a question about findings is dated today, because a
finding is dated by when it was found.

`expect` says how the answer is graded:
- "rows": the table's rows equal the SQL's rows (a label, then the value);
- "total": the table's total equals the SQL's single value (or values);
- "refuse": the question is refused.

Two questions are phrased in a way the stub's rules do not read. They are
here on purpose: the score is a measurement, not a formality.
"""

from dataclasses import dataclass

DAY = "(a.first_seen_at AT TIME ZONE 'Asia/Kolkata')::date"
ATTEMPTS = "payment_attempt a JOIN attempt_status s USING (tenant_id, provider, attempt_id)"
WON = "count(*) FILTER (WHERE s.status = 'succeeded')"
TRIED = "count(*) FILTER (WHERE s.status IN ('succeeded', 'failed'))"
RATE = f"(({WON} * 10000 + {TRIED} / 2) / NULLIF({TRIED}, 0))::bigint"
VOLUME = "COALESCE(sum(a.amount_minor) FILTER (WHERE s.status = 'succeeded'), 0)::bigint"
IN_PERIOD = f"{DAY} BETWEEN :start AND :end"
NOT_SETTLED = (
    "s.status = 'succeeded' AND NOT EXISTS (SELECT 1 FROM settlement_line l "
    "WHERE l.provider = a.provider AND l.attempt_id = a.attempt_id AND l.line_type = 'capture')"
)
LINES = (
    "settlement_line l JOIN settlement st USING (tenant_id, provider, settlement_id) "
    "LEFT JOIN payment_attempt a USING (tenant_id, provider, attempt_id)"
)
# Sums are exact decimals in Postgres, so this divides exactly and rounds once.
FEE_RATE = "round(sum(l.fee_minor + l.tax_minor) * 10000 / sum(l.gross_minor))::bigint"
CAPTURES = "l.line_type = 'capture' AND st.settled_on <= :end"
MATCHED = (
    "EXISTS (SELECT 1 FROM match_link k WHERE k.left_type = 'settlement' AND k.active "
    "AND k.right_type = 'bank_credit' AND k.left_id = st.provider || ':' || st.settlement_id)"
)
MATCH_RATE = f"((count(*) FILTER (WHERE {MATCHED}) * 10000 + count(*) / 2) / count(*))::bigint"
REFUND_DAY = "(r.occurred_at AT TIME ZONE 'Asia/Kolkata')::date"
OPEN = "f.state IN ('recoverable', 'claimed')"
FEES = "f.type IN ('fee_above_contract', 'tax_on_refunded_fee', 'fee_not_in_contract')"


@dataclass(frozen=True)
class Question:
    text: str
    expect: str
    sql: str = ""
    period: str = "all_time"
    # Findings are dated by when they were found: today, not the data's last day.
    about_findings: bool = False


QUESTIONS = (
    # --- Payments ---
    Question(
        "What was our payment volume last week?",
        "total",
        f"SELECT {VOLUME} FROM {ATTEMPTS} WHERE {IN_PERIOD}",
        "last_seven_days",
    ),
    Question(
        "Payment volume by gateway last week",
        "rows",
        f"SELECT a.provider, {VOLUME} FROM {ATTEMPTS} WHERE {IN_PERIOD} GROUP BY 1",
        "last_seven_days",
    ),
    Question(
        "What is our success rate by gateway last week?",
        "rows",
        f"SELECT a.provider, {RATE} FROM {ATTEMPTS} WHERE {IN_PERIOD} GROUP BY 1",
        "last_seven_days",
    ),
    Question(
        "Which bank has the lowest success rate?",
        "rows",
        # An attempt whose bank the provider did not report is shown as "unknown".
        f"SELECT COALESCE(a.instrument_bank, 'unknown'), {RATE} FROM {ATTEMPTS} "
        f"WHERE {DAY} <= :end GROUP BY 1",
    ),
    Question(
        "Success rate by payment method last month",
        "rows",
        f"SELECT a.method, {RATE} FROM {ATTEMPTS} WHERE {IN_PERIOD} GROUP BY 1",
        "last_month",
    ),
    Question(
        "What is the success rate for UPI on Razorpay?",
        "total",
        f"SELECT {RATE} FROM {ATTEMPTS} "
        f"WHERE a.method = 'upi' AND a.provider = 'razorpay' AND {DAY} <= :end",
    ),
    Question(
        "Payment volume by card network for cards",
        "rows",
        f"SELECT a.card_network, {VOLUME} FROM {ATTEMPTS} "
        f"WHERE a.method = 'card' AND {DAY} <= :end GROUP BY 1",
    ),
    Question(
        "Success rate by UPI app",
        "rows",
        f"SELECT a.upi_app, {RATE} FROM {ATTEMPTS} "
        f"WHERE a.method = 'upi' AND {DAY} <= :end GROUP BY 1",
    ),
    Question(
        "How much did we process through PayU last month?",
        "total",
        f"SELECT {VOLUME} FROM {ATTEMPTS} WHERE a.provider = 'payu' AND {IN_PERIOD}",
        "last_month",
    ),
    Question(
        "Daily payment volume last week",
        "rows",
        f"SELECT to_char({DAY}, 'YYYY-MM-DD'), {VOLUME} FROM {ATTEMPTS} "
        f"WHERE {IN_PERIOD} GROUP BY 1",
        "last_seven_days",
    ),
    Question(
        "Success rate by hour last week",
        "rows",
        f"SELECT to_char(a.first_seen_at AT TIME ZONE 'Asia/Kolkata', 'HH24'), {RATE} "
        f"FROM {ATTEMPTS} WHERE {IN_PERIOD} GROUP BY 1",
        "last_seven_days",
    ),
    Question(
        "What is our payment success rate this month?",
        "total",
        f"SELECT {RATE} FROM {ATTEMPTS} WHERE {IN_PERIOD}",
        "this_month",
    ),
    Question(
        "How much is not yet settled?",
        "total",
        f"SELECT COALESCE(sum(a.amount_minor), 0)::bigint FROM {ATTEMPTS} "
        f"WHERE {NOT_SETTLED} AND {DAY} <= :end",
    ),
    Question(
        "Unsettled payments by gateway",
        "rows",
        f"SELECT a.provider, sum(a.amount_minor)::bigint FROM {ATTEMPTS} "
        f"WHERE {NOT_SETTLED} AND {DAY} <= :end GROUP BY 1",
    ),
    # --- Leaks ---
    Question(
        "Where did we lose the most last week?",
        "rows",
        f"SELECT CASE WHEN {FEES} THEN 'fee_audit' ELSE 'reconciliation' END, "
        "sum(f.amount_minor)::bigint FROM finding f GROUP BY 1",
        "last_seven_days",
        about_findings=True,
    ),
    Question(
        "How much money have we lost, by leak?",
        "rows",
        "SELECT f.type, sum(f.amount_minor)::bigint FROM finding f GROUP BY 1",
        about_findings=True,
    ),
    Question(
        "How much is recoverable?",
        "total",
        f"SELECT COALESCE(sum(f.amount_minor), 0)::bigint FROM finding f WHERE {OPEN}",
        about_findings=True,
    ),
    Question(
        "How much have we recovered?",
        "total",
        "SELECT COALESCE(sum(f.amount_minor), 0)::bigint FROM finding f "
        "WHERE f.state = 'recovered'",
        about_findings=True,
    ),
    Question(
        "Lost money by provider",
        "rows",
        "SELECT f.provider, sum(f.amount_minor)::bigint FROM finding f GROUP BY 1",
        about_findings=True,
    ),
    Question(
        "What did we lose and what is recoverable?",
        "total",
        "SELECT sum(f.amount_minor)::bigint, "
        f"(sum(f.amount_minor) FILTER (WHERE {OPEN}))::bigint FROM finding f",
        about_findings=True,
    ),
    # --- Fees ---
    Question(
        "How much were we overcharged in fees?",
        "total",
        f"SELECT COALESCE(sum(f.amount_minor), 0)::bigint FROM finding f WHERE {FEES}",
        about_findings=True,
    ),
    Question(
        "Fee overcharges by provider",
        "rows",
        f"SELECT f.provider, sum(f.amount_minor)::bigint FROM finding f WHERE {FEES} GROUP BY 1",
        about_findings=True,
    ),
    Question(
        "What is our effective fee rate by gateway last month?",
        "rows",
        f"SELECT l.provider, {FEE_RATE} FROM {LINES} "
        "WHERE l.line_type = 'capture' AND st.settled_on BETWEEN :start AND :end GROUP BY 1",
        "last_month",
    ),
    Question(
        "What is the effective rate on Amex?",
        "total",
        f"SELECT {FEE_RATE} FROM {LINES} WHERE {CAPTURES} AND a.card_network = 'amex'",
    ),
    Question(
        "Effective fee rate by method",
        "rows",
        f"SELECT COALESCE(a.method, l.fee_type), {FEE_RATE} FROM {LINES} "
        f"WHERE {CAPTURES} GROUP BY 1",
    ),
    # --- Settlements and refunds ---
    Question(
        "What share of settlements are matched to the bank?",
        "total",
        f"SELECT {MATCH_RATE} FROM settlement st WHERE st.settled_on <= :end",
    ),
    Question(
        "Match rate by provider",
        "rows",
        f"SELECT st.provider, {MATCH_RATE} FROM settlement st "
        "WHERE st.settled_on <= :end GROUP BY 1",
    ),
    Question(
        "How much did we refund last month?",
        "total",
        "SELECT COALESCE(sum(r.amount_minor), 0)::bigint FROM refund r "
        f"WHERE {REFUND_DAY} BETWEEN :start AND :end",
        "last_month",
    ),
    Question(
        "Refunds by gateway",
        "rows",
        "SELECT r.provider, sum(r.amount_minor)::bigint FROM refund r "
        f"WHERE {REFUND_DAY} <= :end GROUP BY 1",
    ),
    Question(
        "Refunds: merchant or automatic?",
        "rows",
        "SELECT r.type, sum(r.amount_minor)::bigint FROM refund r "
        f"WHERE {REFUND_DAY} <= :end GROUP BY 1",
    ),
    # --- Phrasings the stub's rules do not read ---
    Question(
        "Which gateway is weakest on cards?",
        "rows",
        f"SELECT a.provider, {RATE} FROM {ATTEMPTS} "
        f"WHERE a.method = 'card' AND {DAY} <= :end GROUP BY 1",
    ),
    Question(
        "How are our UPI payments doing this month?",
        "total",
        f"SELECT {RATE} FROM {ATTEMPTS} WHERE a.method = 'upi' AND {IN_PERIOD}",
        "this_month",
    ),
    # --- Metrics that are not defined: each must be refused ---
    Question("What is our customer churn?", "refuse"),
    Question("Average order value by gateway", "refuse"),
    Question("How many disputes are due this week?", "refuse"),
    Question("What is our profit margin?", "refuse"),
    Question("Show me refunds by bank", "refuse"),
    Question("What's the weather like in Mumbai?", "refuse"),
)
