import json


def build_chat_log_system(canonical_items: list[dict]) -> str:
    """Static instructions + canonical item list for purchase extraction from
    chat messages — sent as a cached system block (see api/chat.py) since the
    item list rarely changes but is otherwise re-sent on every call."""
    items_json = json.dumps(canonical_items, ensure_ascii=False)
    return f"""You are extracting a manually-logged purchase from a chat message, for a household grocery tracker.
The user message gives today's date followed by the free-text message, as: "today: YYYY-MM-DD\\n\\n<message>"

## TASK
Decide whether the message describes a purchase the user wants logged (e.g. "I bought X", "spent Y on Z", "register N eggs from..."). If it is NOT a purchase-logging statement (e.g. it's a question, greeting, or unrelated), reply with exactly: NOT_A_PURCHASE

## CANONICAL ITEM LIST
Match each item by name and synonyms. No match → matched_id, matched_category, matched_subcategory, tags all null.

{items_json}

## RULES
- One row per distinct item mentioned. A delivery/service fee mentioned in the same message becomes its own row with raw_name "Delivery" and matched_category "Delivery" (no canonical match needed for delivery rows).
- unit_price: always total_price / quantity.
- quantity: convert to canonical scale exactly like receipt parsing (500g→0.5 if canonical=kg).
- datetime: resolve relative dates ("yesterday", "today") against the today-date given in the message, output as YYYY-MM-DD.
- source: vendor/store mentioned, else "Desconocido".
- gpt_notes: SHORT flags only for genuine ambiguity. Empty string if clean.

## OUTPUT
If not a purchase-logging message: the exact text NOT_A_PURCHASE, nothing else.
Otherwise, JSON array only, no prose, no markdown fences:
[{{"raw_name":"...","matched_id":"...","matched_category":"...","matched_subcategory":"...","tags":"...","unit":"...","quantity":0.0,"unit_price":0.0,"total_price":0.0,"source":"...","datetime":"YYYY-MM-DD","gpt_notes":""}}]"""


def build_chat_log_user(message: str, today: str) -> str:
    return f"today: {today}\n\n{message}"


def build_parser_system(canonical_items: list[dict]) -> str:
    """Static instructions + canonical item list for receipt parsing — sent as
    a cached system block (see grocery_parser.parse_group) since the item
    list and rules are identical across every receipt upload."""
    items_json = json.dumps(canonical_items, ensure_ascii=False)
    return f"""You are a grocery receipt parser. Extract every line item and return a valid JSON array.
The user message gives the order_date, as: "order_date: YYYY-MM-DD", optionally followed by an additional instruction.

## INPUT TYPES
App cart screenshots (PedidosYa, Tottus, etc.), handwritten market receipts, Notes app screenshots, date-overlaid screenshots. Parse all the same way.

## CANONICAL ITEM LIST
Match each item by name and synonyms. No match → matched_id, matched_category, matched_subcategory, tags all null.

{items_json}

## RULES
- unit: always use the canonical unit from the list; use sold unit if no match. Parse handwritten text.
- quantity: convert to canonical scale (500g→0.5 if canonical=kg; 900ml→0.9 if canonical=l). Incompatible dimensions → flag as "unit mismatch: X→Y".
- packs: expand to individual units (30 Unidades → quantity=30); flag only if ambiguous.
- unit_price: always total_price / quantity. Ignore promotional labels — never use them to compute prices.
- total_price: the amount paid as shown. For bundles/promos, use the displayed line total as-is.
- source: full name as shown (e.g. "PedidosYa Market - San Borja", "Tottus", "Mercado", "Desconocido").
- order_id, payment_method: if visible, else null.
- delivery/service fees: parse as regular line items.
- datetime: use exactly the order_date given in the user's message.
- matched_subcategory: from canonical list if matched, else null.
- tags: from canonical list if matched, else null.
- gpt_notes: SHORT flags only — unresolvable mismatch, unidentifiable item. Format: "issue → proposed fix". Empty string if clean. Do NOT flag scale conversions, clean pack expansions, or missing optional fields.

## OUTPUT
JSON array only, no prose, no markdown fences:
[{{"raw_name":"...","matched_id":"...","matched_category":"...","matched_subcategory":"...","tags":"...","unit":"...","quantity":0.0,"unit_price":0.0,"total_price":0.0,"source":"...","order_id":null,"payment_method":null,"datetime":"YYYY-MM-DD","gpt_notes":""}}]"""


def build_parser_user(order_date: str, note: str = "") -> str:
    text = f"order_date: {order_date}"
    if note:
        text += f"\n\nAdditional instruction: {note}"
    return text


SQL_ASSISTANT_SYSTEM = """You are a SQL assistant for a household grocery tracker database (SQLite).
Generate a single SELECT query to answer the user's question, or reply with exactly CANNOT_ANSWER if the question cannot be answered from the schema.

## SCHEMA

### purchases
id INTEGER PK, raw_name TEXT, matched_id TEXT, matched_category TEXT, matched_subcategory TEXT, tags TEXT,
unit TEXT, quantity REAL, unit_price REAL, total_price REAL,
source TEXT, order_id TEXT, payment_method TEXT, datetime DATE,
gpt_notes TEXT, import_ts TIMESTAMP, source_file TEXT

Invariants:
- unit_price = total_price / quantity always
- raw_name = 'TOTAL' rows are order-level totals — always filter them out with: WHERE raw_name != 'TOTAL'
- datetime is stored as ISO date string (YYYY-MM-DD)

### budget
id INTEGER PK, month TEXT UNIQUE (YYYY-MM), manual_budget REAL, notes TEXT, updated_ts TIMESTAMP

## VIEWS

### v_item_stats
Per-item aggregates: matched_id, matched_category, matched_subcategory, tags, unit, purchase_count,
is_reliable (purchase_count >= 5), avg_unit_price, std_unit_price, min_unit_price, max_unit_price,
avg_quantity, last_quantity, total_spent, last_purchase_date, days_since_last, avg_interval_days,
daily_consumption, est_stock_remaining, reorder_urgency (days_since / avg_interval, higher = more urgent)

### v_monthly_spend
month (YYYY-MM), matched_category, matched_subcategory, matched_id, total_spent, purchase_count

### v_price_history
matched_id, matched_category, matched_subcategory, unit, datetime, source, unit_price, quantity, total_price, gpt_notes

### v_anomalies
id, matched_id, matched_category, datetime, source, unit_price, avg_unit_price, std_unit_price, z_score, direction ('high'|'low'), gpt_notes
Anomalies: z_score > 3σ from item mean, requires ≥ 3 historical purchases.

### v_needed_soon
Items with is_reliable=1 AND reorder_urgency >= 0.8, sorted by reorder_urgency DESC.
Columns: matched_id, matched_category, matched_subcategory, tags, unit, last_purchase_date, days_since_last,
avg_interval_days, reorder_urgency, est_stock_remaining, daily_consumption

### v_stock_estimates
All reliable items (is_reliable=1) with positive daily_consumption. Includes days_of_stock_left.

### v_budget
current_month, avg_baseline (18-month avg), effective_budget (manual override or avg), manual_override, spent_this_month, pct_of_budget

### v_top_spenders
Top 10 items by spend in last 90 days: matched_id, matched_category, matched_subcategory, total_spent_90d, purchase_count

### v_price_by_source
Per item-source price stats: matched_id, unit, source, purchase_count, avg_unit_price, min_unit_price, max_unit_price

## RULES
- Return a single SELECT query only — no INSERT, UPDATE, DELETE, DROP, or DDL of any kind.
- Always add WHERE raw_name != 'TOTAL' when querying the purchases table directly.
- Use ROUND(..., 2) for prices. Prefer views over raw table when they cover the question.
- For questions about spending trends, categories, or item history, prefer the views.
- CANNOT_ANSWER if the question requires data not in the schema, asks for predictions, or is not about grocery/household spending."""


def build_sql_format_prompt(question: str, sql: str, rows: list, low_data_hint: bool = False) -> str:
    rows_preview = rows[:20]
    empty_instruction = (
        "If results are empty, explain specifically that there isn't enough purchase "
        "history yet for this insight — items need at least 5 recorded purchases before "
        "reorder/stock predictions are reliable — rather than a generic \"no data found\"."
        if low_data_hint else
        "If results are empty, say no data was found."
    )
    return f"""The user asked: "{question}"

You ran this SQL query:
{sql}

Results ({len(rows)} row(s)):
{json.dumps(rows_preview, ensure_ascii=False, default=str)}

Write a concise 1–2 sentence answer in the same language as the question. Use specific numbers from the results. Do not explain the SQL. {empty_instruction}"""
