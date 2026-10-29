# --- Imports: model client, JSON handling, logging, and plain-Python guardrails ---
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from groq import Groq

from guardrails import (
    MAX_DISCOUNT,
    approved_discount,
    build_fallback,
    build_verified_facts,
    compute_totals,
    detect_injection,
    detect_order_items,
    detect_stated_name,
    detect_stop,
    format_totals,
    handoff_reason,
    is_price_pushback,
    pre_send_check,
    totals_numbers,
    update_angry_streak,
)
from guards import FREE_SHIPPING_ABOVE, detect_human_request, free_shipping_gap

# --- Easy-to-change settings ---
MODEL = "openai/gpt-oss-120b"
TEMPERATURE = 0.3
HISTORY_LIMIT = 8
PROMPT_VERSION = "v4"

# --- API key comes only from the environment; it is never printed or logged ---
api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    print("ERROR: GROQ_API_KEY is not set.")
    print('In Windows Command Prompt, run: setx GROQ_API_KEY "your_key_here"')
    print("Then close and reopen the terminal, and run: python chat3.py")
    sys.exit(1)

base_dir = Path(__file__).resolve().parent

# --- Mock cart facts ---
with (base_dir / "cart.json").open(encoding="utf-8") as file:
    cart = json.load(file)

# The model never needs the cart owner's name after the first message.
public_cart = {key: value for key, value in cart.items() if key != "customer_name"}
public_cart["free_shipping_above"] = FREE_SHIPPING_ABOVE
public_cart["amount_needed_for_free_shipping"] = free_shipping_gap(cart)
public_cart_json = json.dumps(public_cart, ensure_ascii=False)
CURRENCY = str(cart.get("currency", "PKR"))

# --- Inventory: create the stopgap file only when it is missing ---
DEFAULT_INVENTORY = {
    "Black Hoodie": {"price": 3500, "sizes": {"S": 0, "M": 5, "L": 3, "XL": 0}},
    "Cotton Cap": {"price": 1200, "sizes": {"Free": 12}},
    "Grey Joggers": {"price": 2800, "sizes": {"S": 4, "M": 6, "L": 0}},
}
inventory_path = base_dir / "inventory.json"
if not inventory_path.exists():
    inventory_path.write_text(json.dumps(DEFAULT_INVENTORY, indent=2), encoding="utf-8")
with inventory_path.open(encoding="utf-8") as file:
    inventory = json.load(file)

client = Groq(api_key=api_key)

# --- Log files: create the folder if missing; secrets are never written ---
log_dir = base_dir / "logs"
log_dir.mkdir(exist_ok=True)
chat_log_path = log_dir / "chat_log.jsonl"
handoff_queue_path = log_dir / "handoff_queue.jsonl"

# --- Code-written customer messages ---
HANDOFF_MESSAGE = "Maine aapki baat team ke liye note kar di hai."
STOP_CONFIRMATION = "Theek hai, main aapko aur messages nahi bhejoongi."
INJECTION_REPLY = "Main apne rules nahi badal sakti, lekin cart mein madad kar sakti hoon."
DISCOUNT_MESSAGE = "Theek hai, aap ke liye {percent}% discount de sakti hoon."
SAFE_OPENING = "Assalam o alaikum! Aap ka cart abhi bhi reserve hai. Kya main kisi cheez mein madad kar sakti hoon?"


# --- Rebuild the prompt on every call so name and totals are always current ---
def build_system_prompt(stated_name, verified_totals=None, first_message=False):
    if first_message:
        owner_name = str(cart.get("customer_name", "")).strip()
        owner_line = (
            "This is the first message. You may use the cart owner's name ("
            + owner_name
            + ") once, only in this message. Never use it again afterwards."
            if owner_name
            else "This is the first message. Do not use any customer name."
        )
        name_block = "The customer has not stated a name yet."
    else:
        owner_line = ""
        if stated_name:
            name_block = (
                "The customer says their name is " + stated_name
                + ". Address them as " + stated_name
                + ". Never use or reveal the cart owner's name."
            )
        else:
            name_block = "The customer has not stated a name. Do not use any customer name."

    totals_block = ""
    if verified_totals:
        totals_block = (
            "\nORDER TOTALS FOR THIS TURN (computed by code, state exactly):\n"
            + format_totals(verified_totals)
            + "\n"
        )

    return f"""You are a friendly WhatsApp assistant for a store, helping with an abandoned cart.

Cart facts (the cart owner's name is not included):
{public_cart_json}

VERIFIED FACTS (catalog and size availability):
{build_verified_facts(inventory)}
{totals_block}
{name_block}
{owner_line}

Rules:
- For size or stock questions, answer ONLY from VERIFIED FACTS.
- If a product is not in our catalog, say we do not have it in our catalog.
- Never say "I will check later" or promise to check and get back.
- If ORDER TOTALS are shown, state those totals exactly. Never do arithmetic yourself.
- Currency is {CURRENCY}. Use only that currency, never another currency word or symbol.
- Format every amount as PKR with thousands separators, for example PKR 1,500.
- Reply in the language of the customer's last message.
- Use one consistent feminine persona (Roman Urdu feminine verbs like "kar sakti hoon").
- Apologise at most once in the whole conversation; do not apologise again if you already did.
- Do not end every message with an offer of more help.
- Never claim to be human. If asked, say you are an AI assistant.
- Max 2 short lines. Do not invent facts or discount offers.
- Do not offer a discount in your message. The system adds any approved discount. You may only set discount_requested on a price objection.
- Never say you forwarded or escalated anything. The system handles handoffs.
- If the customer says you confirmed an order, calmly say you have not confirmed any order and restate the cart items.
- If asked "what is my name", answer with the stated name only. If asked whose name is on the cart, say you cannot share account details.

Output rules:
- Return only one JSON object. No markdown and no text outside it.
- Keys: message, objection, discount_requested.
- message is the WhatsApp reply.
- objection is one of: price, shipping, trust, size, later, none, angry.
- discount_requested is an integer percent, or 0 when you are not requesting a discount.
"""


FIRST_MESSAGE_PROMPT = (
    "Start the conversation. Write a short, friendly first WhatsApp message "
    "in natural Roman Urdu and English. Mention at most one item by name and "
    "offer help. Do not list totals, prices, or shipping. Do not offer a "
    "discount. Return only the JSON object."
)


# --- Time, JSONL, and one-turn logging; the API key is never included ---
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path, record):
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_turn(
    customer_text,
    raw_model_output,
    final_message,
    objection,
    requested,
    approved,
    guardrail,
    handoff=False,
    price_pushback_count=0,
    injection_detected=False,
    angry_streak=0,
    stated_name=None,
    verified_totals=None,
    block_reason="",
    blocked_message="",
    model_called=False,
):
    record = {
        "timestamp": now_iso(),
        "prompt_version": PROMPT_VERSION,
        "customer_text": customer_text,
        "raw_model_output": raw_model_output,
        "final_message": final_message,
        "objection": objection,
        "discount_requested": requested,
        "discount_approved": approved,
        "price_pushback_count": price_pushback_count,
        "injection_detected": injection_detected,
        "angry_streak": angry_streak,
        "stated_name": stated_name,
        "guardrail": guardrail,
        "handoff": handoff,
        "block_reason": block_reason,
        "blocked_message": blocked_message,
        "model_called": model_called,
    }
    if verified_totals is not None:
        record["verified_totals"] = verified_totals
    append_jsonl(chat_log_path, record)


# --- API helper: JSON mode, errors caught, key masked ---
def ask_agent(messages):
    try:
        response = client.chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
            messages=messages,
        )
        return response.choices[0].message.content.strip()
    except Exception as error:
        safe_error = str(error).replace(api_key, "[hidden]")
        print(f"\n[API error] {type(error).__name__}: {safe_error}")
        print("Please check your connection or key, then try again.")
        return None


# --- Structured output: validate fields and retry once on invalid JSON ---
VALID_OBJECTIONS = {"price", "shipping", "trust", "size", "later", "none", "angry"}

PARSE_RETRY_PROMPT = (
    "Your previous reply was not valid JSON with the required fields. "
    "Return only one JSON object with the keys message, objection, and "
    "discount_requested. Keep the same meaning. No markdown."
)


def to_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_model_json(raw_output):
    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    message = data.get("message")
    objection = data.get("objection")
    requested = to_int(data.get("discount_requested"))

    if not isinstance(message, str) or not message.strip():
        return None
    if objection not in VALID_OBJECTIONS:
        return None
    if requested is None:
        return None

    return {
        "message": message.strip(),
        "objection": objection,
        "discount_requested": max(0, requested),
    }


def get_structured_output(messages):
    raw_output = ask_agent(messages)
    if raw_output is None:
        return None, None, "api_error"

    data = parse_model_json(raw_output)
    if data is not None:
        return data, raw_output, "none"

    retry_messages = messages + [
        {"role": "assistant", "content": raw_output},
        {"role": "user", "content": PARSE_RETRY_PROMPT},
    ]
    retry_raw = ask_agent(retry_messages)
    if retry_raw is None:
        return None, raw_output, "api_error"

    retry_data = parse_model_json(retry_raw)
    if retry_data is None:
        return None, retry_raw, "json_parse"

    return retry_data, retry_raw, "json_parse"


# --- Allowed numbers for this turn: cart facts, verified totals, approved % ---
def allowed_numbers_for_turn(approved=0, verified_totals=None):
    numbers = set()
    for item in cart.get("items", []):
        if isinstance(item.get("price"), (int, float)):
            numbers.add(int(item["price"]))
    for key in (
        "cart_total",
        "shipping_cost",
    ):
        value = cart.get(key)
        if isinstance(value, (int, float)):
            numbers.add(int(value))
    numbers.add(FREE_SHIPPING_ABOVE)
    numbers.add(int(free_shipping_gap(cart)))
    for day_text in re.findall(r"\d+", str(cart.get("return_policy", ""))):
        numbers.add(int(day_text))
    if verified_totals:
        numbers |= totals_numbers(verified_totals)
    numbers.add(int(approved))
    return numbers


def outgoing_check(message, approved, allowed, first_message=False):
    return pre_send_check(
        message,
        allowed,
        approved,
        CURRENCY,
        owner_name=cart.get("customer_name"),
        first_message=first_message,
    )


# --- Regenerate once after a blocked message, then use a code fallback ---
def get_safe_message(
    messages,
    raw_output,
    data,
    approved,
    allowed,
    first_message=False,
    fallback_text=None,
):
    blocked_message = data["message"]
    is_safe, reason = outgoing_check(blocked_message, approved, allowed, first_message)
    if is_safe:
        return blocked_message, "none", raw_output, "", ""

    correction_prompt = (
        "Your previous reply was blocked by a pre-send rule: "
        + reason
        + ". Rewrite the message only. Keep the same objection and "
        "discount_requested values. Follow every rule and return only one "
        "JSON object."
    )
    retry_messages = messages + [
        {"role": "assistant", "content": raw_output},
        {"role": "user", "content": correction_prompt},
    ]
    retry_raw = ask_agent(retry_messages)

    if retry_raw is None:
        fallback = fallback_text or build_fallback(data["objection"], cart, inventory)
        return fallback, "pre_send_fallback", raw_output, reason + " (retry API error)", blocked_message

    retry_data = parse_model_json(retry_raw)
    if retry_data is None:
        fallback = fallback_text or build_fallback(data["objection"], cart, inventory)
        return fallback, "pre_send_fallback", retry_raw, reason + " (retry invalid JSON)", blocked_message

    retry_safe, retry_reason = outgoing_check(
        retry_data["message"], approved, allowed, first_message
    )
    if not retry_safe:
        fallback = fallback_text or build_fallback(data["objection"], cart, inventory)
        return fallback, "pre_send_fallback", retry_raw, retry_reason, retry_data["message"]

    return retry_data["message"], "pre_send", retry_raw, reason, blocked_message


# --- Handoff record: reason, stated name, and recent messages ---
def record_handoff(reason, stated_name):
    append_jsonl(
        handoff_queue_path,
        {
            "customer_name": cart.get("customer_name", ""),
            "stated_name": stated_name,
            "reason": reason,
            "last_4_messages": history[-4:],
            "timestamp": now_iso(),
        },
    )


# --- Print, log, and remember one agent reply; record a handoff when needed ---
def finish_turn(
    customer_text,
    raw_output,
    objection,
    requested,
    approved,
    final_message,
    guardrail,
    handoff=False,
    handoff_cause="",
    price_pushback_count=0,
    injection_detected=False,
    angry_streak=0,
    stated_name=None,
    verified_totals=None,
    block_reason="",
    blocked_message="",
    model_called=True,
):
    print("\nAgent:", final_message)
    print(
        "[objection=" + objection
        + " requested=" + str(requested) + "%"
        + " approved=" + str(approved) + "%"
        + " guardrail=" + guardrail
        + " streak=" + str(angry_streak)
        + "]"
    )

    if handoff:
        record_handoff(handoff_cause, stated_name)

    log_turn(
        customer_text,
        raw_output,
        final_message,
        objection,
        requested,
        approved,
        guardrail,
        handoff=handoff,
        price_pushback_count=price_pushback_count,
        injection_detected=injection_detected,
        angry_streak=angry_streak,
        stated_name=stated_name,
        verified_totals=verified_totals,
        block_reason=block_reason,
        blocked_message=blocked_message,
        model_called=model_called,
    )
    history.append({"role": "assistant", "content": final_message})

    if handoff:
        print("[system] Handoff recorded")


# --- Opening message: model-generated, pre-send checked, code fallback ---
history = []
opening_messages = [
    {"role": "system", "content": build_system_prompt(None, None, first_message=True)},
    {"role": "user", "content": FIRST_MESSAGE_PROMPT},
]
opening_data, opening_raw, opening_guardrail = get_structured_output(opening_messages)

if opening_data is None:
    if opening_guardrail == "api_error":
        log_turn(
            "", opening_raw, "", "none", 0, 0, "api_error",
            block_reason="opening API error", model_called=True,
        )
        print("Could not start the chat. Please try again later.")
        sys.exit(1)
    finish_turn(
        "",
        opening_raw,
        "none",
        0,
        0,
        SAFE_OPENING,
        "json_parse_fallback",
        block_reason="Opening output was not valid JSON after one retry.",
    )
else:
    opening_allowed = allowed_numbers_for_turn(0)
    (
        opening_message,
        opening_send_guardrail,
        opening_used_raw,
        opening_block_reason,
        opening_blocked_message,
    ) = get_safe_message(
        opening_messages,
        opening_raw,
        opening_data,
        0,
        opening_allowed,
        first_message=True,
        fallback_text=SAFE_OPENING,
    )
    opening_final_guardrail = (
        opening_guardrail if opening_send_guardrail == "none" else opening_send_guardrail
    )
    finish_turn(
        "",
        opening_used_raw,
        opening_data["objection"],
        opening_data["discount_requested"],
        0,
        opening_message,
        opening_final_guardrail,
        block_reason=opening_block_reason,
        blocked_message=opening_blocked_message,
    )

print("Type 'quit' to exit.")

# --- Chat loop: code checks each customer turn before and after the model ---
price_pushback_count = 0
angry_streak = 0
session = {"stated_name": None}

while True:
    try:
        user_text = input("\nYou: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nChat ended.")
        break

    if not user_text:
        print("Type a message, or 'quit' to exit.")
        continue

    if user_text.lower() == "quit":
        print("Chat ended.")
        break

    # STOP is handled by code, without calling the model.
    if detect_stop(user_text):
        finish_turn(
            user_text,
            None,
            "none",
            0,
            0,
            STOP_CONFIRMATION,
            "stop",
            price_pushback_count=price_pushback_count,
            angry_streak=angry_streak,
            stated_name=session["stated_name"],
            model_called=False,
        )
        print("Chat ended.")
        break

    # Injection is blocked before the model and never counts as price pushback.
    if detect_injection(user_text):
        finish_turn(
            user_text,
            None,
            "none",
            0,
            0,
            INJECTION_REPLY,
            "injection_detected",
            price_pushback_count=price_pushback_count,
            injection_detected=True,
            angry_streak=angry_streak,
            stated_name=session["stated_name"],
            block_reason="prompt injection detected",
            model_called=False,
        )
        continue

    history.append({"role": "user", "content": user_text})
    previous_name = session["stated_name"]
    previous_streak = angry_streak

    detected_name = detect_stated_name(user_text)
    if detected_name:
        session["stated_name"] = detected_name

    # Anger, human requests and handoff are decided here, never by the model's label.
    angry_streak = update_angry_streak(angry_streak, user_text)
    cause = handoff_reason(user_text, angry_streak)
    if cause is None and detect_human_request(user_text):
        cause = "human_request"
    if cause:
        finish_turn(
            user_text,
            None,
            "none" if cause == "human_request" else "angry",
            0,
            0,
            HANDOFF_MESSAGE,
            "handoff",
            handoff=True,
            handoff_cause=cause,
            price_pushback_count=price_pushback_count,
            angry_streak=angry_streak,
            stated_name=session["stated_name"],
            block_reason=cause,
            model_called=False,
        )
        print("Chat ended.")
        break

    # If the customer names items, totals are computed by code for this turn.
    items = detect_order_items(user_text, inventory)
    verified_totals = compute_totals(items, cart, inventory) if items else None

    messages = [
        {"role": "system", "content": build_system_prompt(session["stated_name"], verified_totals)},
    ] + history[-HISTORY_LIMIT:]

    data, raw_output, structured_guardrail = get_structured_output(messages)

    if data is None:
        if structured_guardrail == "api_error":
            session["stated_name"] = previous_name
            angry_streak = previous_streak
            history.pop()
            log_turn(
                user_text,
                raw_output,
                "",
                "none",
                0,
                0,
                "api_error",
                price_pushback_count=price_pushback_count,
                angry_streak=angry_streak,
                stated_name=session["stated_name"],
                block_reason="model API error",
                model_called=True,
            )
            continue

        finish_turn(
            user_text,
            raw_output,
            "none",
            0,
            0,
            build_fallback("default", cart, inventory),
            "json_parse_fallback",
            price_pushback_count=price_pushback_count,
            angry_streak=angry_streak,
            stated_name=session["stated_name"],
            verified_totals=verified_totals,
            block_reason="Model output was not valid JSON after one retry.",
            model_called=True,
        )
        continue

    # Only a price objection with a price word counts, and never an injection.
    if is_price_pushback(user_text, data["objection"], False):
        price_pushback_count += 1

    requested = data["discount_requested"]
    approved = approved_discount(requested, price_pushback_count, False, MAX_DISCOUNT)
    allowed = allowed_numbers_for_turn(approved, verified_totals)

    block_reason = ""
    blocked_message = ""
    if approved > 0:
        # The code states the percent, so it always matches the approval.
        final_message = DISCOUNT_MESSAGE.format(percent=approved)
        guardrail = "discount"
        used_raw = raw_output
    else:
        (
            final_message,
            send_guardrail,
            used_raw,
            block_reason,
            blocked_message,
        ) = get_safe_message(
            messages, raw_output, data, approved, allowed, first_message=False
        )
        guardrail = structured_guardrail if send_guardrail == "none" else send_guardrail

    finish_turn(
        user_text,
        used_raw,
        data["objection"],
        requested,
        approved,
        final_message,
        guardrail,
        price_pushback_count=price_pushback_count,
        angry_streak=angry_streak,
        stated_name=session["stated_name"],
        verified_totals=verified_totals,
        block_reason=block_reason,
        blocked_message=blocked_message,
        model_called=True,
    )
