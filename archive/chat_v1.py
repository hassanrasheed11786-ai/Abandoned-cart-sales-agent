# --- Imports: tools this chat needs ---
import json
import os
import sys
from pathlib import Path

from groq import Groq

# --- Easy-to-change settings ---
MODEL = "openai/gpt-oss-120b"
TEMPERATURE = 0.3
HISTORY_LIMIT = 8

# --- Read the API key from the environment; never print or hardcode it ---
api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    print("ERROR: GROQ_API_KEY is not set.")
    print('In Windows Command Prompt, run: setx GROQ_API_KEY "your_key_here"')
    print("Then close and reopen the terminal, and run: python chat.py")
    sys.exit(1)

# --- Load the mock cart from cart.json next to this script ---
cart_path = Path(__file__).with_name("cart.json")
with cart_path.open(encoding="utf-8") as file:
    cart = json.load(file)

# --- System prompt: role, facts, objection rules, and style ---
cart_facts = json.dumps(cart, ensure_ascii=False)
SYSTEM_PROMPT = f"""You are a friendly store assistant chatting on WhatsApp about an abandoned cart.

Cart facts (the only facts you may use):
{cart_facts}

Objection rules:
- Price: explain value first (quality and the return policy). No discount yet.
- Shipping: use shipping_cost and free_shipping_above from the cart facts.
- Trust: mention the return policy.
- Size: help, but never invent size details that are not in the cart facts.
- Later: politely ask when to follow up.
- Angry or confused: say a human will help.

Style rules:
- Max 2 short lines. Natural Roman Urdu + English mix. Use the customer's name when natural.
- No fake urgency, no invented facts, no promises. Never invent stock, sizes, discounts, delivery times, or currency units.
- Never claim to be human. If asked, say you are an AI assistant.
- Avoid robotic phrases like "How may I assist you".
- Reply only with the message, with no quotes or labels.

Tone examples (copy the style, not the facts):
- "Ayesha, aapke cart mein Black Hoodie (M) hai. Size ya shipping mein koi help chahiye?"
- "Bilkul, main AI assistant hoon. Cart ke baare mein jo poochna ho, bataiye."
"""

# --- Instruction for the first message (not a customer message) ---
FIRST_MESSAGE_PROMPT = (
    "Start the conversation. Write the first WhatsApp message for this abandoned cart. "
    "Use only the cart facts in the system prompt. No discount."
)

# --- Create the Groq client with the key from the environment ---
client = Groq(api_key=api_key)


# --- Ask the model once; catch API errors so the chat keeps running ---
def ask_agent(messages):
    try:
        response = client.chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            messages=messages,
        )
        return response.choices[0].message.content.strip()
    except Exception as error:
        safe_error = str(error).replace(api_key, "[hidden]")
        print(f"\n[API error] {type(error).__name__}: {safe_error}")
        print("Please check your connection or key, then try again.")
        return None


# --- Start the chat with the agent's opening message ---
history = []
opening = ask_agent(
    [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": FIRST_MESSAGE_PROMPT},
    ]
)

if not opening:
    print("Could not start the chat. Please try again later.")
    sys.exit(1)

history.append({"role": "assistant", "content": opening})
print("\nAgent:", opening)
print("Type 'quit' to exit.")

# --- Chat loop: you type as the customer ---
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

    # Add your message, then send only the newest 8 history items to save tokens.
    history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-HISTORY_LIMIT:]
    reply = ask_agent(messages)

    # On an API error, remove your unsent message so the history stays clean.
    if reply is None:
        history.pop()
        continue

    history.append({"role": "assistant", "content": reply})
    print("\nAgent:", reply)
