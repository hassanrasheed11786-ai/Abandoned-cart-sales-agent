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
- Price: do not discount. For value, mention only the return policy and facts from the cart facts.
- Shipping: use shipping_cost and amount_needed_for_free_shipping from the cart facts to say how much more is needed for free shipping.
- Trust: give facts (the return policy), not claims like "we are trustworthy".
- Size or stock: you only know the sizes shown in the cart facts. For any other size or availability, say you are not sure and offer to check. Never say a size is available or unavailable.
- Later: politely ask when to follow up.
- Angry or confused: apologise briefly and say you will pass the issue to the team. Do not promise that a specific person will contact them.

Style rules:
- Max 2 short lines. Natural Roman Urdu + English mix.
- Use the customer's name in the first message only, and rarely after that. Do not start every message with the name.
- Never describe product qualities (fabric, durability, comfort, fit) that are not in the cart facts.
- No fake urgency, no invented facts, no promises. Never invent stock, sizes, discounts, delivery times, or currency units.
- Do not end every message with a question like "koi aur sawal?". Vary your endings.
- Never claim to be human. If asked, say you are an AI assistant.
- Avoid robotic phrases like "How may I assist you".
- Reply only with the message, with no quotes or labels.

Tone examples (copy the style, not the facts):
- "Hi Ayesha! Hoodie ke baare mein koi sawal tha? Size ya shipping mein help kar sakte hain."
- "Samajh aata hai. Pasand na aaye to 7 din mein return ya exchange ho jata hai."
- "Main AI assistant hoon. Cart ke baare mein jo poochna ho, bata dein."
"""

# --- Instruction for the first message (not a customer message) ---
FIRST_MESSAGE_PROMPT = (
    "Start the conversation. Write a short, friendly first WhatsApp message. "
    "Mention at most one item by name and offer help. "
    "Do not list totals, prices, or shipping. No discount."
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
