# --- Imports ---
import json
import os
import sys
from pathlib import Path

from groq import Groq

# --- Settings ---
MODEL = "openai/gpt-oss-120b"
TEMPERATURE = 0.2
MAX_TOOL_ROUNDS = 4          # stops infinite tool loops
DISCOUNT_CAP_PERCENT = 10    # keep this equal to your Level 3 cap in guards.py
MAX_QTY_PER_LINE = 20
VALID_SIZES = ["S", "M", "L", "XL", "Free"]

HANDOFF_MESSAGE = "Theek hai, main aap ko hamari team ke member se connect kar raha hoon. Wo jaldi aap se baat karenge."
FALLBACK_MESSAGE = "Maaf kijiye, mujhe thori dikkat aa rahi hai. Main team member ko bula raha hoon."

SYSTEM_PROMPT = (
    "You are a friendly WhatsApp sales assistant for an online clothing store. "
    "Reply in the customer's language (Roman Urdu, Urdu or English), short and natural.\n"
    "Rules:\n"
    "- All prices are in PKR. Never use any other currency symbol.\n"
    "- Format every amount as PKR with thousands separators, for example PKR 1,500.\n"
    "- Reply in the same script the customer used. If they write Roman Urdu, reply in Roman Urdu, never in Urdu script.\n"
    "- Never mention tools, systems or caps. For a capped discount just say the maximum discount you can offer is that percent.\n"
    "- Never state stock or availability without calling check_stock.\n"
    "- Never do price math yourself. Use calculate_total for any total or discount.\n"
    "- For discounts, pass the percent the customer asked for. The system caps it. "
    "Tell the customer the applied discount from the tool result and never promise more.\n"
    "- Call handoff_to_human if the customer asks for a human, agent or manager, "
    "is angry, or wants a refund or to complain.\n"
    "- If the customer only asks whether you are a bot or AI, answer honestly that you are an AI "
    "assistant. Do not hand off for that.\n"
    "- If a tool says not found or out of stock, say so honestly. Never invent products."
)

# --- Read the API key from the environment; never print or hardcode it ---
api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    print("ERROR: GROQ_API_KEY is not set.")
    print('In Windows Command Prompt, run: setx GROQ_API_KEY "your_key_here"')
    print("Then close and reopen the terminal, and run: python tool_test.py")
    sys.exit(1)

# --- Load the mock inventory next to this script ---
base_dir = Path(__file__).resolve().parent
inventory_path = base_dir / "inventory.json"
if not inventory_path.exists():
    print(f"ERROR: {inventory_path} not found.")
    sys.exit(1)
with inventory_path.open(encoding="utf-8") as file:
    inventory = json.load(file)

client = Groq(api_key=api_key)

# --- Tool schemas: what the model is allowed to call ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Check mock stock for one product and one size.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string"},
                    "size": {"type": "string", "enum": VALID_SIZES},
                },
                "required": ["product", "size"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_total",
            "description": (
                "Calculate the order total for one or more items, with an optional discount. "
                "Always use this for any price or total. The discount is capped by the system."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product": {"type": "string"},
                                "size": {"type": "string", "enum": VALID_SIZES},
                                "quantity": {"type": "integer", "minimum": 1},
                            },
                            "required": ["product", "size", "quantity"],
                        },
                    },
                    "discount_percent": {
                        "type": "number",
                        "description": "Discount the customer asked for. 0 if none.",
                    },
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_human",
            "description": "Pass the chat to a human team member.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": ["customer_request", "angry_or_refund", "out_of_scope", "other"],
                    }
                },
                "required": ["reason"],
            },
        },
    },
]


# --- Helpers: product lookup, size cleanup, stock and price reads ---
def find_product(name):
    """Exact match first, then a single unambiguous partial match."""
    if not isinstance(name, str) or not name.strip():
        return None, None
    wanted = name.strip().lower()
    for product_name, data in inventory.items():
        if product_name.lower() == wanted:
            return product_name, data
    matches = [
        (product_name, data)
        for product_name, data in inventory.items()
        if wanted in product_name.lower() or product_name.lower() in wanted
    ]
    if len(matches) == 1:
        return matches[0]
    return None, None


def normalize_size(size):
    if not isinstance(size, str):
        return None
    cleaned = size.strip().upper()
    if cleaned == "FREE":
        return "Free"
    return cleaned if cleaned in VALID_SIZES else None


def get_qty(data, size):
    try:
        return int(data.get("sizes", {}).get(size, 0))
    except (TypeError, ValueError):
        return 0


def get_price(data):
    # Adjust these key names if your inventory.json uses something else.
    for key in ("price", "price_pkr", "unit_price"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return value
    return None


# --- Tool 1: read-only stock lookup ---
def check_stock(product, size):
    name, data = find_product(product)
    clean_size = normalize_size(size)
    if name is None:
        return {"found": False, "error": "product_not_found"}
    if clean_size is None:
        return {"found": False, "error": "invalid_size"}
    qty = get_qty(data, clean_size)
    return {"found": True, "product": name, "size": clean_size, "in_stock": qty > 0, "qty": qty}


# --- Tool 2: all price math happens here, and the discount cap is enforced here ---
def calculate_total(items, discount_percent=0):
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "items_must_be_a_non_empty_list"}

    lines = []
    subtotal = 0
    for item in items:
        if not isinstance(item, dict):
            return {"ok": False, "error": "bad_item"}

        name, data = find_product(item.get("product"))
        if name is None:
            return {"ok": False, "error": "product_not_found", "product": item.get("product")}

        size = normalize_size(item.get("size"))
        if size is None:
            return {"ok": False, "error": "invalid_size", "product": name}

        quantity = item.get("quantity", 1)
        if (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity < 1
            or quantity > MAX_QTY_PER_LINE
        ):
            return {"ok": False, "error": "invalid_quantity", "product": name}

        available = get_qty(data, size)
        if available < quantity:
            return {
                "ok": False,
                "error": "insufficient_stock",
                "product": name,
                "size": size,
                "available": available,
            }

        price = get_price(data)
        if price is None:
            return {"ok": False, "error": "price_missing", "product": name}

        line_total = price * quantity
        subtotal += line_total
        lines.append(
            {"product": name, "size": size, "quantity": quantity,
             "unit_price": price, "line_total": line_total}
        )

    try:
        requested = float(discount_percent)
    except (TypeError, ValueError):
        requested = 0.0

    applied = max(0.0, min(requested, float(DISCOUNT_CAP_PERCENT)))
    discount_amount = round(subtotal * applied / 100, 2)
    total = round(subtotal - discount_amount, 2)

    return {
        "ok": True,
        "lines": lines,
        "subtotal": subtotal,
        "requested_discount_percent": requested,
        "applied_discount_percent": applied,
        "discount_capped": requested > DISCOUNT_CAP_PERCENT,
        "discount_amount": discount_amount,
        "total": total,
    }


# --- Tool 3: only marks the handoff. The loop below stops and sends a fixed message. ---
def handoff_to_human(reason):
    allowed = ["customer_request", "angry_or_refund", "out_of_scope", "other"]
    return {"handed_off": True, "reason": reason if reason in allowed else "other"}


TOOL_FUNCTIONS = {
    "check_stock": check_stock,
    "calculate_total": calculate_total,
    "handoff_to_human": handoff_to_human,
}


# --- Dispatcher: unknown tool, bad JSON or bad arguments never crash the agent ---
def run_tool(name, raw_arguments):
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return {"error": "unknown_tool"}
    try:
        arguments = json.loads(raw_arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return {"error": "invalid_json_arguments"}
    if not isinstance(arguments, dict):
        return {"error": "arguments_must_be_an_object"}
    try:
        return func(**arguments)
    except TypeError:
        return {"error": "bad_arguments"}


# --- API helper: mask the key in errors and never crash ---
def call_model(messages):
    try:
        return client.chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
    except Exception as error:
        safe_error = str(error).replace(api_key, "[hidden]")
        print(f"[API error] {type(error).__name__}: {safe_error}")
        return None


def assistant_to_dict(message):
    """Store the assistant tool-call message as plain data, not an SDK object."""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in message.tool_calls
        ],
    }


# --- The agent loop: model asks for tools, code runs them, repeat until a final answer ---
def run_agent(user_text, verbose=True):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    tools_called = []

    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        response = call_model(messages)
        if response is None:
            return FALLBACK_MESSAGE, tools_called

        message = response.choices[0].message
        if not message.tool_calls:
            return (message.content or "").strip(), tools_called

        messages.append(assistant_to_dict(message))
        handoff = False

        for call in message.tool_calls:
            name = call.function.name
            result = run_tool(name, call.function.arguments)
            tools_called.append(name)

            if verbose:
                print(f"  [round {round_no}] tool: {name}")
                print(f"    args:   {call.function.arguments}")
                print(f"    result: {json.dumps(result, ensure_ascii=False)}")

            if name == "handoff_to_human" and result.get("handed_off"):
                handoff = True

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        if handoff:
            return HANDOFF_MESSAGE, tools_called

    print(f"  [warning] stopped after {MAX_TOOL_ROUNDS} tool rounds")
    return FALLBACK_MESSAGE, tools_called


# --- Mini test suite: (customer message, set of tools we expect) ---
SCENARIOS = [
    ("Black Hoodie L size mein available hai?", {"check_stock"}, {"calculate_total", "handoff_to_human"}),
    ("2 Black Hoodie M ka total kitna hoga?", {"calculate_total"}, {"handoff_to_human"}),
    ("20% discount de do, 1 Black Hoodie M ka total batao", {"calculate_total"}, {"handoff_to_human"}),
    ("Mujhe kisi insaan se baat karni hai", {"handoff_to_human"}, set()),
    ("i want to talk a human", {"handoff_to_human"}, set()),
    ("Mera order kharab aaya, refund chahiye!", {"handoff_to_human"}, set()),
    ("Are you a bot?", set(), {"handoff_to_human", "check_stock", "calculate_total"}),
    ("Salam, kaise ho?", set(), {"handoff_to_human", "check_stock", "calculate_total"}),
    ("Red Jacket XL hai?", {"check_stock"}, {"handoff_to_human"}),
]

def run_suite():
    passed = 0
    for text, required, forbidden in SCENARIOS:
        print(f"\nCustomer: {text}")
        answer, called = run_agent(text)
        got = set(called)
        ok = required <= got and not (got & forbidden)
        passed += ok
        print(f"  Agent: {answer}")
        print(f"  Required: {sorted(required) or '-'} | Forbidden hit: {sorted(got & forbidden) or '-'} -> {'PASS' if ok else 'FAIL'}")
    print(f"\n=== {passed}/{len(SCENARIOS)} scenarios passed ===")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Single message mode: python tool_test.py "Black Hoodie M hai?"
        text = " ".join(sys.argv[1:])
        print(f"Customer: {text}")
        answer, _ = run_agent(text)
        print(f"Agent: {answer}")
    else:
        run_suite()