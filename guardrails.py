"""Plain-Python guardrails for the chat agent. No API calls, so easy to test."""

import re

from guards import FREE_SHIPPING_ABOVE, format_pkr, free_shipping_gap

MAX_QUANTITY = 10
MAX_DISCOUNT = 10

# ---------------------------------------------------------------- STOP
STOP_PHRASES = (
    "unsubscribe",
    "band karo",
    "mat bhejo",
    "mat bhejna",
    "no more messages",
)


def detect_stop(text):
    lower = text.lower()
    if any(phrase in lower for phrase in STOP_PHRASES):
        return True
    if re.search(r"\bstop\b", lower):
        # "do not stop the delivery" is not a request to stop messages.
        if not re.search(r"\b(?:do not|don't|dont|not)\s+stop\b", lower):
            return True
    return False


# ---------------------------------------------------------------- names
NAME_STOPWORDS = {
    "a", "an", "the", "not", "ok", "fine", "sure", "confused", "looking",
    "interested", "angry", "here", "back", "waiting", "new", "ready",
    "nahi", "bhi", "ek",
}

NAME_PATTERNS = (
    r"\bi(?:'m|\u2019m|\s+am)\s+([A-Za-z]{2,20})\b",
    r"\bim\s+([A-Za-z]{2,20})\b",
    r"\b(?:hi+|hey+|hello+)[,.!\s]+am\s+([A-Za-z]{2,20})\b",
    r"\b(?:my\s+name\s+is|name\s+is|mera\s+naam|mera\s+nam)\s+([A-Za-z]{2,20})\b",
    r"\bmain\s+([A-Za-z]{2,20})\s+hoon\b",
    r"\bname\s+([A-Za-z]{2,20})\s+(?:hai|ha|he)\b",
)


def detect_stated_name(text):
    """Return the customer's own name, or None."""
    for pattern in NAME_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            name = match.group(1)
            if name.lower() not in NAME_STOPWORDS:
                return name.capitalize()
    return None


# ---------------------------------------------------------------- injection
INJECTION_PATTERNS = (
    r"\bignore\s+(?:(?:your|the|previous|all|any|my)\s+){1,3}(?:rules|instructions)\b",
    r"\bprevious\s+instructions\b",
    r"\bsystem\s*:",
    r"\bdeveloper\s+mode\b",
    r"\bjailbreak\b",
    r"\boverride\b",
    r"\brules\s+ignore\b",
    r"\brules\s+k\s+ignore\b",
    r"\brules\s+ko\s+ignore\b",
    r"\brules\s+bhool\b",
    r"\bbhool\s+jao\s+rules\b",
    r"\bpretend\s+you\s+are\b",
    r"\byou\s+are\s+now\b",
)


def detect_injection(text):
    lower = text.lower()
    return any(re.search(pattern, lower) for pattern in INJECTION_PATTERNS)


# ---------------------------------------------------------------- price
PRICE_WORDS = (
    "mehnga", "mehngi", "zyada", "expensive", "costly", "price",
    "prize", "sasta", "kam karo", "discount", "off", "rate",
)

_PRICE_PATTERNS = tuple(
    re.compile(re.escape(word) if " " in word else r"\b" + re.escape(word) + r"\b")
    for word in PRICE_WORDS
)


def has_price_word(text):
    lower = text.lower()
    return any(pattern.search(lower) for pattern in _PRICE_PATTERNS)


def is_price_pushback(text, objection_label, injection):
    if str(objection_label).lower() != "price":
        return False
    return has_price_word(text) and not injection


def approved_discount(requested, pushback_count, injection, max_discount=MAX_DISCOUNT):
    if injection or pushback_count < 2:
        return 0
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        return 0
    return max(0, min(requested, max_discount))


# ---------------------------------------------------------------- anger / handoff
ANGER_PATTERNS = (
    r"\bannoying\b", r"\bfrustrated\b", r"\bworst\b", r"\buseless\b",
    r"\bwaste\b", r"\bridiculous\b", r"\bpareshan\b", r"\btang\b",
    r"\bbakwas\b", r"\bganda\b", r"\bbekar\b", r"\bfed\s+up\b",
    r"\bwaiting\s+forever\b", r"\bkitna\s+wait\b",
)

ESCALATION_PATTERNS = (
    r"\brefund\b", r"\bscam\b", r"\bfraud\b", r"\bcheat\b", r"\bfake\b",
    r"\bcomplaint\b", r"\blawyer\b", r"\blegal\b", r"\bpolice\b",
    r"\bconsumer\s+court\b",
)


def is_escalation(text):
    lower = text.lower()
    return any(re.search(pattern, lower) for pattern in ESCALATION_PATTERNS)


def is_angry(text):
    lower = text.lower()
    if any(re.search(pattern, lower) for pattern in ANGER_PATTERNS):
        return True
    if text.count("!") >= 3:
        return True
    letters = [char for char in text if char.isalpha()]
    return len(letters) >= 4 and all(char.isupper() for char in letters)


def update_angry_streak(streak, text):
    return streak + 1 if is_angry(text) else 0


def handoff_reason(text, streak):
    """Code owns handoff. The model's angry label never triggers a handoff."""
    if is_escalation(text):
        return "escalation_word"
    if streak >= 2:
        return "angry_streak_2"
    return None


# ---------------------------------------------------------------- order items
ALIAS_TO_NAME = {
    "hoodie": "Black Hoodie",
    "hodie": "Black Hoodie",
    "hoody": "Black Hoodie",
    "hoodies": "Black Hoodie",
    "hodies": "Black Hoodie",
    "cap": "Cotton Cap",
    "caps": "Cotton Cap",
    "jogger": "Grey Joggers",
    "joggers": "Grey Joggers",
}

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "ek": 1, "do": 2, "teen": 3}

_QTY = r"\d{1,3}|one|two|three|ek|do|teen"
_ITEM_PATTERN = re.compile(
    r"(?:(?P<qty>" + _QTY + r")\s+(?:(?:black|cotton|grey|gray)\s+)?)?"
    r"(?P<alias>hodies|hoodies|hoodie|hodie|hoody|joggers|jogger|caps|cap)\b",
    re.I,
)
_QTY_AFTER_PATTERN = re.compile(
    r"^\s*(?:x|\u00d7)?\s*[\(\-:,]?\s*(" + _QTY + r")\b",
    re.I,
)


def _to_quantity(token):
    if token is None:
        return 1
    token = token.lower()
    if token in NUMBER_WORDS:
        return NUMBER_WORDS[token]
    try:
        return int(token)
    except ValueError:
        return 1


def _catalog_names(catalog):
    if isinstance(catalog, dict):
        return set(catalog.keys())
    return set(catalog)


def detect_order_items(text, catalog):
    quantities = {}
    names = _catalog_names(catalog)
    for match in _ITEM_PATTERN.finditer(text):
        product = ALIAS_TO_NAME.get(match.group("alias").lower())
        if product not in names:
            continue
        quantity = _to_quantity(match.group("qty"))
        if match.group("qty") is None:
            after = _QTY_AFTER_PATTERN.match(text[match.end():])
            if after:
                quantity = _to_quantity(after.group(1))
        quantity = max(1, min(quantity, MAX_QUANTITY))
        quantities[product] = min(MAX_QUANTITY, quantities.get(product, 0) + quantity)
    return [{"name": name, "quantity": qty} for name, qty in quantities.items()]


# ---------------------------------------------------------------- totals
def compute_totals(items, cart, inventory):
    currency = cart.get("currency", "PKR")
    lines = []
    subtotal = 0
    for item in items:
        product = item["name"]
        if product not in inventory:
            continue
        quantity = item["quantity"]
        unit_price = inventory[product]["price"]
        line_total = unit_price * quantity
        subtotal += line_total
        lines.append({
            "name": product,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    free_above = FREE_SHIPPING_ABOVE
    shipping = 0 if subtotal >= free_above else cart.get("shipping_cost", 0)
    return {
        "currency": currency,
        "items": lines,
        "subtotal": subtotal,
        "shipping": shipping,
        "grand_total": subtotal + shipping,
        "amount_needed_for_free_shipping": free_shipping_gap(cart),
    }


def totals_numbers(totals):
    numbers = {
        totals["subtotal"],
        totals["shipping"],
        totals["grand_total"],
        totals["amount_needed_for_free_shipping"],
    }
    for item in totals["items"]:
        numbers.add(item["quantity"])
        numbers.add(item["unit_price"])
        numbers.add(item["line_total"])
    return numbers


def format_totals(totals):
    lines = []
    for item in totals["items"]:
        lines.append(
            str(item["quantity"])
            + " x " + item["name"]
            + " @ " + format_pkr(item["unit_price"])
            + " = " + format_pkr(item["line_total"])
        )
    return (
        "; ".join(lines)
        + ". Subtotal " + format_pkr(totals["subtotal"])
        + ". Shipping " + format_pkr(totals["shipping"])
        + ". Grand total " + format_pkr(totals["grand_total"])
        + ". Cart mein free shipping ke liye "
        + format_pkr(totals["amount_needed_for_free_shipping"])
        + " aur chahiye."
    )


def build_verified_facts(inventory):
    lines = []
    for name, info in inventory.items():
        sizes = info.get("sizes", {})
        in_stock = [size for size, qty in sizes.items() if qty > 0]
        out_stock = [size for size, qty in sizes.items() if qty <= 0]
        parts = []
        if in_stock:
            parts.append("in stock: " + ", ".join(in_stock))
        if out_stock:
            parts.append("out of stock: " + ", ".join(out_stock))
        if not parts:
            parts.append("no sizes listed")
        lines.append("- " + name + ": " + "; ".join(parts))
    return "\n".join(lines)


def build_size_fallback(inventory):
    parts = []
    for name, info in inventory.items():
        sizes = info.get("sizes", {})
        in_stock = [size for size, qty in sizes.items() if qty > 0]
        out_stock = [size for size, qty in sizes.items() if qty <= 0]
        text = name + ": "
        text += ("available " + ", ".join(in_stock)) if in_stock else "koi size available nahi"
        if out_stock:
            text += " (" + ", ".join(out_stock) + " out of stock)"
        parts.append(text)
    return "Size ke liye: " + "; ".join(parts) + "."


# ---------------------------------------------------------------- pre-send
DISCOUNT_OFFER_PATTERNS = (
    re.compile(r"\d+\s*(?:%|percent\b)"),
    re.compile(r"\b\d[\d,]*\s*(?:pkr|rs\.?|rupees?)?\s*off\b"),
)
BANNED_PHRASES = (
    "last chance",
    "only 1 left",
    "hurry",
    "limited time",
    "our records show",
)
ALL_CURRENCY_TOKENS = (
    "rupees", "rupaye", "rs", "usd", "dollars", "dollar",
    "pkr", "inr", "aed", "dirham", "dirhams",
)
CURRENCY_TOKENS = {
    "pkr": {"pkr"},
    "inr": {"inr", "rs", "rupees", "rupaye"},
    "usd": {"usd", "$", "dollar", "dollars"},
    "aed": {"aed", "dirham", "dirhams"},
}


def extract_numbers(text):
    numbers = set()
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        numbers.add(int(value) if value.is_integer() else value)
    return numbers


def _normalise_numbers(values):
    normalised = set()
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        normalised.add(int(number) if number.is_integer() else number)
    return normalised


def currency_problem(message, cart_currency):
    currency = str(cart_currency).lower()
    allowed = CURRENCY_TOKENS.get(currency, {currency})
    lower = message.lower()
    for token in ALL_CURRENCY_TOKENS:
        if token == "rs":
            present = re.search(r"\brs\.?(?=\W|$)", lower)
        else:
            present = re.search(r"\b" + re.escape(token) + r"\b", lower)
        if present and token not in allowed:
            return "currency word '" + token + "' does not match cart currency " + str(cart_currency)
    if "$" in message and "$" not in allowed:
        return "$ does not match cart currency " + str(cart_currency)
    return ""


def pre_send_check(message, allowed_numbers, approved, cart_currency,
                   owner_name=None, first_message=False):
    lower = message.lower()

    # Only a real offer is blocked while no discount is approved.
    if approved == 0:
        for pattern in DISCOUNT_OFFER_PATTERNS:
            if pattern.search(lower):
                return False, "a discount offer is not allowed before an approved discount"

    for phrase in BANNED_PHRASES:
        if phrase in lower:
            return False, 'the banned phrase "' + phrase + '" is not allowed'

    problem = currency_problem(message, cart_currency)
    if problem:
        return False, problem

    allowed = _normalise_numbers(allowed_numbers)
    for number in extract_numbers(message):
        if number not in allowed:
            return False, "the number " + str(number) + " is not in the allowed facts"

    if owner_name and not first_message:
        if re.search(r"\b" + re.escape(str(owner_name)) + r"\b", message, re.I):
            return False, "the cart owner's name is not allowed after the first message"

    return True, ""


# ---------------------------------------------------------------- fallbacks
def build_fallback(objection, cart, inventory):
    policy = cart.get("return_policy", "return policy")

    if objection == "price":
        gap = free_shipping_gap(cart)
        if gap > 0:
            progress = "Cart mein free shipping ke liye " + format_pkr(gap) + " aur chahiye. "
        else:
            progress = "Is cart par free shipping lag rahi hai. "
        return (
            "Price ki fikar samajh sakti hoon. " + progress
            + "Discount abhi approve nahi hua. "
            + "Chahein to main aapko hamari team se connect kar doon?"
        )
    if objection == "shipping":
        gap = free_shipping_gap(cart)
        if gap <= 0:
            return "Is order par free shipping lag rahi hai."
        return (
            "Shipping " + format_pkr(cart.get("shipping_cost", 0))
            + " hai. Cart mein free shipping ke liye " + format_pkr(gap) + " aur chahiye."
        )
    if objection == "size":
        return build_size_fallback(inventory)
    if objection == "trust":
        return "Fikar na karein, " + policy + " hota hai."
    return "Main cart, size ya shipping mein madad kar sakti hoon."
