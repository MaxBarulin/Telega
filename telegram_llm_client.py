
import os
import asyncio
import sys
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import User, Chat, Channel

# Load environment variables
load_dotenv()

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "anon")

if not API_ID or not API_HASH:
    print("Error: TELEGRAM_API_ID or TELEGRAM_API_HASH not found in .env")
    sys.exit(1)

API_ID = int(API_ID)

async def get_target_chat(client: TelegramClient):
    """
    Interactively select a chat to monitor.
    """
    while True:
        print("\n--- Select Target Chat ---")
        print("1. List recent private chats")
        print("2. Enter username/ID manually")
        print("q. Quit")
        
        choice = input("Choice: ").strip().lower()
        
        if choice == 'q':
            sys.exit(0)
            
        if choice == '1':
            print("\nFetching recent dialogs...")
            dialogs = await client.get_dialogs(limit=15)
            users = []
            for d in dialogs:
                if isinstance(d.entity, User) and not d.entity.bot:
                    users.append(d)
            
            for i, d in enumerate(users):
                name = f"{d.entity.first_name} {d.entity.last_name or ''}".strip()
                username = f"(@{d.entity.username})" if d.entity.username else ""
                print(f"{i+1}. {name} {username} [ID: {d.entity.id}]")
            
            sel = input("\nSelect number (or 'b' to back): ")
            if sel.lower() == 'b':
                continue
                
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(users):
                    return users[idx].entity
                else:
                    print("Invalid selection.")
            except ValueError:
                print("Invalid input.")

        elif choice == '2':
            target = input("Enter username (e.g., @username) or ID: ").strip()
            if not target:
                continue
            
            try:
                # If it looks like an ID, convert to int
                if target.lstrip('-').isdigit():
                    target = int(target)
                
                print(f"Resolving {target}...")
                entity = await client.get_entity(target)
                return entity
            except Exception as e:
                print(f"Error resolving entity: {e}")
        
        else:
            print("Invalid choice.")

async def main():
    print("Initializing Telegram Client...")
    
    if SESSION_STRING:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    else:
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    
    await client.start()
    
    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username})")
    
    target_entity = await get_target_chat(client)
    
    name = getattr(target_entity, 'first_name', getattr(target_entity, 'title', 'Unknown'))
    print(f"\nSelected chat: {name} (ID: {target_entity.id})")
    
    # Auto-mode selection
    auto_input = input("Enable fully automatic mode? [y/N]: ").strip().lower()
    auto_mode = auto_input == 'y'
    if auto_mode:
        print("⚠️  WARNING: Automatic mode enabled. Messages will be sent without confirmation.")
    
    # Message loop
    print(f"\nListening for messages from {name}...")
    print("Press Ctrl+C to stop.")

    # API Endpoint
    KOBOLD_API_URL = "http://localhost:5001/api/v1/generate"
    # Config
    MAX_HISTORY = 20
    SYSTEM_PROMPT = (
        "You are a user chatting on Telegram. Your goal is to keep the conversation going. "
        "Reply naturally, be engaging, and ask follow-up questions if appropriate. "
        "Do not be overly brief, but don't write essays either."
    )

    import aiohttp
    from telethon import events

    # Context buffer (simple recent history)
    # List of dicts: {"sender": str, "text": str}
    context_buffer = []

    # Fetch last N messages to populate context
    print(f"Fetching last {MAX_HISTORY} messages...")
    initial_msgs = await client.get_messages(target_entity, limit=MAX_HISTORY)
    for msg in reversed(initial_msgs):
        if msg.text:
            sender = "Me" if msg.out else name
            # Simple clean up of text
            text = msg.text.replace("\n", " ")
            context_buffer.append({"sender": sender, "text": text})

    @client.on(events.NewMessage(chats=target_entity))
    async def handler(event):
        if event.out:
            # Add our own messages to context but don't trigger AI
            text = event.message.text.replace("\n", " ")
            context_buffer.append({"sender": "Me", "text": text})
            # Keep buffer size reasonable
            if len(context_buffer) > MAX_HISTORY:
                context_buffer.pop(0)
            return

        sender_name = name
        msg_text = event.message.text.replace("\n", " ")
        
        print(f"\n\n--- New Message from {sender_name} ---")
        print(f"Content: {msg_text}")
        
        context_buffer.append({"sender": sender_name, "text": msg_text})
        if len(context_buffer) > MAX_HISTORY:
            context_buffer.pop(0)

        # Generate Prompt
        prompt = f"{SYSTEM_PROMPT}\n\n"
        for item in context_buffer:
            prompt += f"{item['sender']}: {item['text']}\n"
        prompt += "Me:"
        
        print("\n🤔 Generating response...")
        
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "prompt": prompt,
                    "max_context_length": 1024,
                    "max_length": 1024,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "stop_sequence": [f"{name}:", "Me:", "\n\n"]
                }
                async with session.post(KOBOLD_API_URL, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        generated_text = data['results'][0]['text'].strip()
                        
                        print(f"\nSuggestion:\n{generated_text}")
                        print("-" * 20)
                        
                        if auto_mode:
                            print("✅ Auto-sending...")
                            await client.send_message(target_entity, generated_text)
                            # context_buffer update handled by event.out handler
                            print("Sent!")
                        else:
                            while True:
                                action = await asyncio.get_running_loop().run_in_executor(None, input, "Action [ (S)end / (E)dit / (I)gnore ]: ")
                                action = action.lower().strip()
                                
                                if action in ('s', 'send', ''): # Default to send
                                    print("Sending...")
                                    await client.send_message(target_entity, generated_text)
                                    # context_buffer update handled by event.out handler
                                    print("Sent!")
                                    break
                                elif action in ('e', 'edit'):
                                    new_text = await asyncio.get_running_loop().run_in_executor(None, input, "Enter new text: ")
                                    generated_text = new_text.strip()
                                    print(f"New text: {generated_text}")
                                    # Loop back to confirm
                                elif action in ('i', 'ignore', 'skip'):
                                    print("Skipped.")
                                    break
                    else:
                        print(f"Error from KoboldCPP: {resp.status}")
        except Exception as e:
            print(f"Error querying KoboldCPP: {e}")

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
