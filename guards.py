"""Level 3 guardrails: plain code, no LLM. Prompts are requests, code is a guarantee."""
import re

FREE_SHIPPING_ABOVE = 5000
CURRENCY = "PKR"


def format_pkr(amount):
    return CURRENCY + " " + f"{amount:,.0f}"


def free_shipping_gap(cart):
    subtotal = cart.get("cart_total")
    if not isinstance(subtotal, (int, float)):
        subtotal = sum(
            item.get("price", 0)
            for item in cart.get("items", [])
            if isinstance(item.get("price"), (int, float))
        )
    return max(0, FREE_SHIPPING_ABOVE - subtotal)


STOP_WORDS = {"stop", "unsubscribe", "cancel", "band karo", "bas karo", "mat bhejo"}

INJECTION_PATTERNS = [
    r"ignore (all |any |your )?(previous|prior|above) (instructions|rules|prompt)",
    r"(reveal|show|print|repeat).{0,30}(system prompt|instructions|rules)",
    r"you are now", r"developer mode", r"act as (an? )?(admin|developer|owner)",
    r"(set|make|give).{0,25}(discount|price).{0,15}(to )?\d+\s*%",
    r"i am (the )?(owner|admin|manager)",
]

PRICE_PATTERNS = [
    r"\b(expensive|costly|too much|pricey|high price|mehng\w*|zyada)\b",
    r"\b(cheaper|discount|sasta|kam price|kam karo|best price)\b",
]

NAME_PATTERNS = [
    r"\b(?:i am|i'm|im|this is|my name is|mera naam|main)\s+([A-Za-z]{2,20})\b",
]
NOT_NAMES = {"not", "just", "still", "interested", "looking", "ready", "sure", "fine", "good", "here",
             "thinking", "confused", "worried", "ok", "okay", "the", "a", "an"}


def detect_stop(msg: str) -> bool:
    m = msg.strip().lower()
    return m in STOP_WORDS or any(m.startswith(w + " ") for w in ("stop", "unsubscribe"))


def detect_injection(msg: str) -> bool:
    m = msg.lower()
    return any(re.search(p, m) for p in INJECTION_PATTERNS)


HUMAN = r"\b(human|insaan|insan|aadmi|admi|banda|person|agent|representative|manager|owner|staff|team member)\b"
ACTION = (r"\b(talk|speak|baat+|ba+t+|connect|chat|contact|call|want|wana|wanna|chahiye|chaiye|chahie|chahye|chahta|chahti|"
          r"k(a|e)?r(n[ia]|ni)|transfer|need)\b")
BOT_Q = r"\b(are|r)\s+(you|u)\s+(a\s+|an\s+)?(real\s+)?(human|bot|robot|ai|person)\b|\b(aap|tum)\s+(insaan|human|bot)\s+ho\b"

def detect_human_request(text):
    t = re.sub(r"\s+", " ", text.lower())
    if not re.search(HUMAN, t):
        return False
    if re.search(BOT_Q, t) and not re.search(ACTION, t):
        return False
    return bool(re.search(ACTION, t))


def detect_price_objection(msg: str) -> bool:
    """Injection is checked FIRST by the caller, so an attack is never counted as price pushback."""
    m = msg.lower()
    return any(re.search(p, m) for p in PRICE_PATTERNS)


def extract_name(msg: str):
    for p in NAME_PATTERNS:
        m = re.search(p, msg, re.I)
        if m and m.group(1).lower() not in NOT_NAMES:
            return m.group(1).capitalize()
    return None


def cap_discount(requested_pct: float, max_pct: float = 10, allowed: bool = False) -> float:
    """Model only REQUESTS. Code decides. (matches the log line requested=10% approved=0%)"""
    approved = min(requested_pct, max_pct) if allowed else 0
    print(f"[guard] requested={requested_pct}% approved={approved}%")
    return approved


def scrub_private_name(reply: str, cart_owner: str, stated_name):
    """Never reveal the cart owner's name unless the user said that name themselves."""
    if cart_owner and cart_owner.lower() != (stated_name or "").lower():
        reply = re.sub(re.escape(cart_owner), "there", reply, flags=re.I)
    return reply


class ConversationState:
    """Memory lives in code, not in the 8-message window."""
    def __init__(self, cart_owner=""):
        self.cart_owner = cart_owner
        self.customer_name = None
        self.price_objections = 0
        self.stopped = False

    def update(self, msg: str):
        n = extract_name(msg)
        if n:
            self.customer_name = n
        if detect_price_objection(msg) and not detect_injection(msg):
            self.price_objections += 1

    def pinned_facts(self) -> str:
        """Put this in the system prompt EVERY turn, so 'I'm Hassan' can't fall out of history[-8:]."""
        name = self.customer_name or "unknown (do not use the cart owner's name)"
        return f"Customer's own stated name: {name}. Price objections so far: {self.price_objections}."

    def should_handoff(self, msg: str) -> bool:
        asked = bool(re.search(r"\b(human|agent|person|manager|insaan|call me)\b", msg.lower()))
        return asked or self.price_objections >= 3   # NOT on the first objection
