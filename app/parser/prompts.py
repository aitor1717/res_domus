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
is_reliable (purchase_count >= 3), avg_unit_price, std_unit_price, min_unit_price, max_unit_price,
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

## ITEM NAMES
Canonical item names (matched_id) are stored in Spanish — this is a Lima, Peru household.
When the user asks in English, translate food/household terms before querying matched_id.
Common translations: milk→leche, chicken→pollo, rice→arroz, tomato→tomate, potato→papa,
onion→cebolla, garlic→ajo, egg→huevo, oil→aceite, butter→mantequilla, flour→harina,
sugar→azúcar, beef→carne/res, pork→cerdo, fish→pescado, detergent→detergente,
soap→jabón, bread→pan, yogurt→yogur, cheese→queso, tuna→atún, orange→naranja,
apple→manzana, banana→plátano, lettuce→lechuga, cucumber→pepino.
Always use LIKE '%term%' (case-insensitive) rather than exact match, e.g.:
  WHERE LOWER(matched_id) LIKE '%leche%'

## COMMON QUERY PATTERNS

### Shopping list / what to buy next
-- days_overdue: how many days past the expected restock date (positive = overdue)
-- Exclude delivery/service categories — they are not physical items to buy
SELECT matched_id,
       ROUND(days_since_last - avg_interval_days, 0) AS days_overdue,
       ROUND(avg_interval_days, 1) AS typical_interval_days
FROM v_item_stats
WHERE is_reliable = 1
  AND reorder_urgency IS NOT NULL
  AND LOWER(COALESCE(matched_category,'')) NOT IN ('delivery','courier','servicio')
ORDER BY reorder_urgency DESC LIMIT 10;

### Stock / days until an item runs out
SELECT matched_id, days_of_stock_left, ROUND(daily_consumption,4) AS daily_consumption
FROM v_stock_estimates
WHERE LOWER(matched_id) LIKE '%item%'
  AND LOWER(COALESCE(matched_category,'')) NOT IN ('delivery','courier','servicio')
ORDER BY days_of_stock_left;

### Items likely already depleted (negative stock estimate)
-- days_depleted: how many days ago the stock ran out (always positive here)
SELECT matched_id,
       ROUND(-est_stock_remaining / NULLIF(daily_consumption, 0), 0) AS days_depleted,
       days_since_last,
       ROUND(avg_interval_days, 1) AS avg_interval
FROM v_item_stats
WHERE is_reliable = 1
  AND est_stock_remaining < 0
  AND LOWER(COALESCE(matched_category,'')) NOT IN ('delivery','courier','servicio')
ORDER BY est_stock_remaining ASC;

### Budget projection — will I finish within budget this month?
SELECT current_month, spent_this_month, effective_budget,
  ROUND(
    spent_this_month / NULLIF(CAST(strftime('%d','now') AS REAL), 0)
    * CAST(strftime('%d', date(strftime('%Y-%m','now') || '-01', '+1 month', '-1 day')) AS REAL)
  , 2) AS projected_monthly_spend,
  ROUND(effective_budget - spent_this_month, 2) AS remaining
FROM v_budget;

### Daily spend allowance remaining
SELECT ROUND(effective_budget - spent_this_month, 2) AS budget_left,
  ROUND(
    (effective_budget - spent_this_month) /
    NULLIF(
      CAST(strftime('%d', date(strftime('%Y-%m','now') || '-01', '+1 month', '-1 day')) AS REAL)
      - CAST(strftime('%d','now') AS REAL)
    , 0)
  , 2) AS daily_allowance
FROM v_budget;

### Price fairness — is a quoted price good for item X?
SELECT matched_id, ROUND(avg_unit_price,2) AS avg, ROUND(min_unit_price,2) AS min_seen,
       ROUND(max_unit_price,2) AS max_seen, purchase_count
FROM v_item_stats
WHERE LOWER(matched_id) LIKE '%item%';

### Cheapest store for an item
SELECT source, ROUND(avg_unit_price,2) AS avg_price,
       ROUND(min_unit_price,2) AS best_price, purchase_count
FROM v_price_by_source
WHERE LOWER(matched_id) LIKE '%item%'
ORDER BY avg_unit_price;

### Store comparison — where do I spend most / shop most?
SELECT source, ROUND(SUM(total_price),2) AS total_spent, COUNT(DISTINCT datetime) AS orders
FROM purchases
WHERE raw_name != 'TOTAL' AND matched_category NOT IN ('Delivery','Courier','Servicio')
GROUP BY source ORDER BY total_spent DESC LIMIT 8;

### Savings — how much cheaper is cheapest source vs most expensive?
-- max/min ratio < 5x guard removes unit-scale recording inconsistencies
SELECT matched_id,
  ROUND(MIN(avg_unit_price), 2) AS best_price,
  ROUND(MAX(avg_unit_price), 2) AS worst_price,
  ROUND(MAX(avg_unit_price) - MIN(avg_unit_price), 2) AS savings_per_unit
FROM v_price_by_source
WHERE LOWER(matched_id) LIKE '%item%'
GROUP BY matched_id
HAVING COUNT(DISTINCT source) > 1
   AND MAX(avg_unit_price) / NULLIF(MIN(avg_unit_price), 0) < 5;

### Items bought most frequently (subscription / automation candidates)
SELECT matched_id, ROUND(avg_interval_days,0) AS avg_days_between,
       purchase_count, ROUND(avg_unit_price,2) AS avg_price
FROM v_item_stats
WHERE avg_interval_days IS NOT NULL AND purchase_count >= 3
  AND LOWER(COALESCE(matched_category,'')) NOT IN ('delivery','courier','servicio')
ORDER BY avg_interval_days ASC LIMIT 10;

### Category health / spend breakdown
SELECT matched_category,
  ROUND(SUM(total_spent),2) AS total,
  ROUND(SUM(total_spent) * 100.0 / SUM(SUM(total_spent)) OVER (), 1) AS pct
FROM v_monthly_spend
WHERE month >= strftime('%Y-%m', date('now','-3 months'))
GROUP BY matched_category ORDER BY total DESC;

### Subcategory drill-down
SELECT matched_subcategory, ROUND(SUM(total_spent),2) AS total
FROM v_monthly_spend
WHERE matched_category = 'CategoryName'
  AND month >= strftime('%Y-%m', date('now','-3 months'))
GROUP BY matched_subcategory ORDER BY total DESC;

### Spending trend — is my spend going up or down?
SELECT month, ROUND(SUM(total_spent),2) AS total
FROM v_monthly_spend
WHERE matched_category NOT IN ('Delivery','Courier','Servicio')
GROUP BY month ORDER BY month DESC LIMIT 6;

### Price trend for an item — has it gotten more expensive?
SELECT datetime, ROUND(unit_price,2) AS price, source
FROM v_price_history
WHERE LOWER(matched_id) LIKE '%item%'
ORDER BY datetime;

### Items bought exactly once (occasional / one-off)
SELECT matched_id, last_purchase_date, ROUND(avg_unit_price,2)
FROM v_item_stats WHERE purchase_count = 1 ORDER BY last_purchase_date DESC;

### Which items have gotten more expensive (inflation check)
-- Only include items with consistent unit pricing (max/min ratio < 5x) to avoid
-- false positives caused by different pack sizes recorded with different unit scales.
SELECT matched_id,
       ROUND(min_unit_price, 2) AS earliest_price,
       ROUND(max_unit_price, 2) AS recent_price,
       ROUND((max_unit_price - min_unit_price) / NULLIF(min_unit_price, 0) * 100, 1) AS pct_increase,
       purchase_count
FROM v_item_stats
WHERE purchase_count >= 3
  AND min_unit_price > 0
  AND max_unit_price / NULLIF(min_unit_price, 0) < 5
ORDER BY pct_increase DESC LIMIT 8;

## RULES
- Return a single SELECT query only — no INSERT, UPDATE, DELETE, DROP, or DDL of any kind.
- Always add WHERE raw_name != 'TOTAL' when querying the purchases table directly.
- Use ROUND(..., 2) for prices. Prefer views over raw table when they cover the question.
- For questions about spending trends, categories, or item history, prefer the views.
- For ANY shopping list, running-low, or stock depletion query (including v_needed_soon and v_stock_estimates), always exclude delivery/service items: AND LOWER(COALESCE(matched_category,'')) NOT IN ('delivery','courier','servicio')
- Always use LIKE for source name filters (e.g. WHERE LOWER(source) LIKE '%pedidosya%'), never exact match — source names have many variants in the data.
- Prior assistant turns may end with '-- query: <SQL>'. Use that SQL as context when the current question is a follow-up (e.g. "how does that compare", "which of those", "what about last month") — adapt the prior query rather than starting from scratch.
- For follow-ups asking for the single most/worst/urgent/expensive/cheapest item from a prior sorted list ("which of those is most urgent", "what's the worst one"), wrap the prior query as a subquery with LIMIT 1: SELECT * FROM (<prior query>) LIMIT 1;
- For follow-ups like "break that down by subcategory" or "what are the subcategories", identify the top-ranked category from the prior answer and use it as WHERE matched_category = 'CategoryName' in the subcategory pattern.
- CANNOT_ANSWER if the question requires data not in the schema, asks for predictions, or is not about grocery/household spending."""


def _has_low_sample(rows: list) -> bool:
    return any(
        isinstance(r.get("purchase_count"), (int, float)) and 0 < r["purchase_count"] < 5
        for r in rows
    )


def _has_price_ratio_anomaly(rows: list) -> bool:
    for r in rows:
        for mk, nk in [("max_unit_price", "min_unit_price"), ("worst_price", "best_price")]:
            mx, mn = r.get(mk), r.get(nk)
            if mx and mn and mn > 0 and mx / mn > 5:
                return True
    # Also catch per-source comparisons where avg_unit_price spread is suspicious
    prices = [r["avg_unit_price"] for r in rows
              if isinstance(r.get("avg_unit_price"), (int, float)) and r["avg_unit_price"] > 0]
    if len(prices) >= 2 and max(prices) / min(prices) > 5:
        return True
    return False


def build_sql_format_prompt(question: str, sql: str, rows: list, low_data_hint: bool = False, lang: str = "en") -> str:
    rows_preview = rows[:20]
    lang_instruction = "Respond in English." if lang == "en" else "Responde en español."
    empty_instruction = (
        "If results are empty, explain specifically that there isn't enough purchase "
        "history yet for this insight — items need at least 3 recorded purchases before "
        "reorder/stock predictions are reliable — rather than a generic \"no data found\"."
        if low_data_hint else
        "If results are empty, say no data was found."
    )
    if _has_low_sample(rows_preview):
        low_sample_note = (
            "Si alguna fila tiene purchase_count < 5, añade '(N compras)' tras las cifras — p.ej. 'Leche — S/.3.98 (3 compras)'."
            if lang == "es" else
            "If any row has purchase_count < 5, append '(N purchases)' after that item's figures — e.g. 'Leche — S/.3.98 (3 purchases)'."
        )
    else:
        low_sample_note = ""
    if _has_price_ratio_anomaly(rows_preview):
        price_ratio_note = (
            "Una o más comparaciones muestran una diferencia de precios > 5×. Añade al final: 'Nota: la diferencia puede reflejar inconsistencia en las unidades de medida.'"
            if lang == "es" else
            "One or more price comparisons show a ratio > 5× — add a single line at the end: 'Note: large price gap may reflect unit-scale inconsistency.'"
        )
    else:
        price_ratio_note = ""
    extras = "\n".join(x for x in [low_sample_note, price_ratio_note] if x)
    return f"""The user asked: "{question}"

SQL results ({len(rows)} row(s)):
{json.dumps(rows_preview, ensure_ascii=False, default=str)}

{lang_instruction} Be terse.
- Scalar result: one number or short phrase
- List: max 6 plain lines, format "Item — detail". No header, no preamble
- Trend or comparison: max 2 sentences
Exact numbers. Currency S/.XX.XX (2 decimal places). Plain text, no markdown, no asterisks, no bold, no emojis. No SQL explanation.
Never output raw est_stock_remaining unit quantities or reorder_urgency decimal values — if a result has negative stock or urgency numbers, express depletion as "X days overdue" using days_since_last and avg_interval_days instead.
{empty_instruction}{chr(10) + extras if extras else ""}"""
