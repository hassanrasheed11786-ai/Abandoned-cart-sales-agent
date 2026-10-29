# --- Imports: tools this script needs ---
import json
import os
import sys
from pathlib import Path

from groq import Groq

# --- Easy-to-change settings ---
MODEL = "openai/gpt-oss-120b"
TEMPERATURE = 0.2

# --- System prompt: the agent's role, tone, and rules ---
SYSTEM_PROMPT = """You are a friendly store assistant writing the FIRST WhatsApp message about an abandoned cart.
Rules:
v2 rules:
- Do not start every message with the customer's name. Use it in the first
  message, and only occasionally after that.
- First message: do NOT list items or totals, and do NOT mention prices or
  shipping. Mention at most one item and offer help.
- Never describe product qualities (fabric, durability, comfort, fit) that
  are not in the cart data. For value, use only the return policy and
  facts from the cart.
- You do NOT know stock levels or sizes beyond what the cart shows. If asked
  about another size or availability, say you are not sure and offer to check.
  Never say a size is available or unavailable.
- If shipping is asked, use amount_needed_for_free_shipping to tell how much
  more is needed for free shipping.
- For trust questions, give facts (the return policy), do not just say
  "we are trustworthy".
- Do not end every message with "koi aur sawal?". Vary your endings.
- Do not promise that a human will contact the customer. Say you will pass
  the issue to the team.

Tone examples (copy the style, not the facts):
- "Ayesha, aapke cart mein Black Hoodie (M) hai. Size ya shipping mein koi help chahiye?"
- "Ayesha, koi question ho toh bataiye — help ke liye yahan hoon."
"""

# --- Read the API key from the environment; never print or hardcode it ---
api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    print("ERROR: GROQ_API_KEY is not set.")
    print('In Windows Command Prompt, run: setx GROQ_API_KEY "your_key_here"')
    print("Then close and reopen the terminal, and run: python level1.py")
    sys.exit(1)

# --- Load the mock cart from cart.json next to this script ---
cart_path = Path(__file__).with_name("cart.json")
with cart_path.open(encoding="utf-8") as file:
    cart = json.load(file)

# --- Short user prompt containing only the mock cart facts ---
user_prompt = (
    "Write the first WhatsApp message for this cart. "
    "Use only these facts:\n"
    + json.dumps(cart, ensure_ascii=False)
)

# --- Create the Groq client with the key from the environment ---
client = Groq(api_key=api_key)

# --- Ask the model for the first message ---
response = client.chat.completions.create(
    model=MODEL,
    temperature=TEMPERATURE,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ],
)

# --- Print only the generated message ---
print(response.choices[0].message.content.strip())
