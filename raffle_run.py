import os
import json
import discord
import requests
import asyncio
import time
import random
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
# ================= CONFIGURATION =================
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
HOST_API_KEY = os.environ["HOST_API_KEY"]
HOST_TORN_ID = #######        # Torn ID of the account receiving items
VERIFIED_ROLE_ID = 1451999045122920448    # Role ID to give linked users
LOG_CHANNEL_ID = 1394140942327484446    # Channel ID for deposit logs       

RAFFLE_CONFIG = {
    "TICKET_PRICE": 400000,        
    "MIN_ITEM_PRICE": 400000,      
    "TRIGGER_MSG": "LLF",          
    "LOG_ID": 4103                 
}

# ================= FILE MANAGEMENT =================
FILES = {
    "LINKS": "linked_users.json",
    "PRICES": "item_prices_cache.json",
    "WALLET": "user_wallets.json",      
    "RAFFLE": "active_raffle.json"      
}

def load_json(filename, default):
    if not os.path.exists(filename): return default
    try:
        with open(filename, "r") as f: return json.load(f)
    except: return default

def save_json(filename, data):
    temp = filename + ".tmp"
    with open(temp, "w") as f: json.dump(data, f, indent=4)
    os.replace(temp, filename)

# Load Data
linked_users = load_json(FILES["LINKS"], {})
item_prices = load_json(FILES["PRICES"], {})
user_wallets = load_json(FILES["WALLET"], {"meta": {"last_log_ts": 0}, "balances": {}})
# NOTE: Added 'channel_id' to default structure
active_raffle = load_json(FILES["RAFFLE"], {"prize": None, "prize_id": 0, "entries": [], "total_tickets": 0, "channel_id": 0})

# ================= CORE LOGIC: END RAFFLE =================
# We moved this out of the command so the Auto-Task can use it too
async def execute_raffle_end(bot_instance):
    global active_raffle
    
    # Safety Check
    if not active_raffle["prize"]: return
    
    # 1. Get the channel to post results
    channel_id = active_raffle.get("channel_id")
    channel = bot_instance.get_channel(channel_id)
    if not channel:
        # Fallback to Log Channel if original is deleted/unknown
        channel = bot_instance.get_channel(LOG_CHANNEL_ID) 
        if not channel: return # Can't post anywhere

    # 2. Check if tickets exist
    if active_raffle["total_tickets"] == 0:
        await channel.send(f"❌ **Raffle Ended:** {active_raffle['prize']}\nSadly, no tickets were sold. No winner.")
        # Reset
        active_raffle = {"prize": None, "prize_id": 0, "entries": [], "total_tickets": 0, "channel_id": 0}
        save_json(FILES["RAFFLE"], active_raffle)
        return

    # 3. Pick Winner
    winning_ticket = random.randint(1, active_raffle["total_tickets"])
    winner_id = next((e["user"] for e in active_raffle["entries"] if e["start"] <= winning_ticket <= e["end"]), None)
    
    # 4. Calculate Stats
    user_entries = sum((e["end"] - e["start"] + 1) for e in active_raffle["entries"] if e["user"] == winner_id)
    chance = (user_entries / active_raffle["total_tickets"]) * 100
    
    # 5. Format Mention
    winner_mention = f"TornID [{winner_id}]"
    for d_id, t_id in linked_users.items():
        if str(t_id) == winner_id: winner_mention = f"<@{d_id}>"

    # 6. Create Embed
    embed = discord.Embed(color=0xFFD700, title="🏆 RAFFLE ENDED")
    embed.set_thumbnail(url=f"https://www.torn.com/images/items/{active_raffle['prize_id']}/large.png")
    embed.add_field(name="Prize", value=active_raffle['prize'], inline=True)
    embed.add_field(name="Winner", value=winner_mention, inline=True)
    embed.add_field(name="Winning Ticket", value=f"Ticket #{winning_ticket}", inline=False)
    embed.add_field(name="Win Chance", value=f"{chance:.3f}%", inline=True)
    embed.set_footer(text=f"Total Pool: {active_raffle['total_tickets']} tickets")

    await channel.send(embed=embed)
    
    # 7. Reset Data
    active_raffle = {"prize": None, "prize_id": 0, "entries": [], "total_tickets": 0, "channel_id": 0}
    save_json(FILES["RAFFLE"], active_raffle)

# ================= UI CLASSES =================
class EnterRaffleModal(discord.ui.Modal, title="Enter Raffle"):
    ticket_amount = discord.ui.TextInput(label="Tickets to Spend", placeholder="Example: 10", max_length=5, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try: amount = int(self.ticket_amount.value)
        except ValueError:
            await interaction.response.send_message("❌ Valid number required.", ephemeral=True)
            return

        if amount <= 0:
            await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
            return

        user_torn_id = linked_users.get(str(interaction.user.id))
        if not user_torn_id:
            await interaction.response.send_message("❌ Link your account first with `/link`!", ephemeral=True)
            return

        global user_wallets, active_raffle
        user_wallets = load_json(FILES["WALLET"], user_wallets)
        user_id_str = str(user_torn_id)
        current_bal = user_wallets["balances"].get(user_id_str, 0)

        if current_bal < amount:
            await interaction.response.send_message(f"❌ Insufficient Funds! You have {current_bal} tickets.", ephemeral=True)
            return

        user_wallets["balances"][user_id_str] -= amount
        save_json(FILES["WALLET"], user_wallets)

        start_ticket = active_raffle["total_tickets"] + 1
        end_ticket = active_raffle["total_tickets"] + amount
        
        active_raffle["entries"].append({"user": user_id_str, "start": start_ticket, "end": end_ticket})
        active_raffle["total_tickets"] = end_ticket
        save_json(FILES["RAFFLE"], active_raffle)

        chance = (amount / active_raffle["total_tickets"]) * 100
        await interaction.response.send_message(f"✅ Entered **{amount}** tickets! (#{start_ticket}-#{end_ticket})", ephemeral=True)

class RaffleView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None) 

    @discord.ui.button(label="Enter Raffle", style=discord.ButtonStyle.success, custom_id="btn_enter_raffle")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not active_raffle["prize"]:
            await interaction.response.send_message("❌ No active raffle.", ephemeral=True)
            return
        await interaction.response.send_modal(EnterRaffleModal())

    @discord.ui.button(label="View your Tickets", style=discord.ButtonStyle.primary, custom_id="btn_view_tickets")
    async def view_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_torn_id = linked_users.get(str(interaction.user.id))
        if not user_torn_id:
            await interaction.response.send_message("❌ Link your account first!", ephemeral=True)
            return
        
        w_data = load_json(FILES["WALLET"], user_wallets)
        wallet_bal = w_data["balances"].get(str(user_torn_id), 0)
        entries = sum((e["end"] - e["start"] + 1) for e in active_raffle["entries"] if e["user"] == str(user_torn_id))
        await interaction.response.send_message(f"💳 Wallet: {wallet_bal}\n🎰 In Pot: {entries}", ephemeral=True)

# ================= BOT SETUP =================
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    bot.add_view(RaffleView())
    if not check_donations.is_running(): check_donations.start()
    if not price_updater_task.is_running(): price_updater_task.start()
    if not auto_end_raffle_task.is_running(): auto_end_raffle_task.start() # <--- NEW TASK
    try: await bot.tree.sync()
    except Exception as e: print(f"Sync Error: {e}")

# ================= BACKGROUND TASKS =================

# --- NEW TASK: AUTO END RAFFLE ---
@tasks.loop(seconds=60)
async def auto_end_raffle_task():
    # 1. Is a raffle running?
    if not active_raffle.get("prize"): return
    
    # 2. Is there a valid end time?
    end_time = active_raffle.get("end_timestamp", 0)
    if end_time == 0: return # Indefinite raffle

    # 3. Is Time Up?
    if time.time() >= end_time:
        print(f"⏰ Time up for {active_raffle['prize']}! Ending automatically...")
        await execute_raffle_end(bot)


@tasks.loop(hours=6)
async def price_updater_task():
    await update_item_prices()

async def update_item_prices():
    url = f"https://api.torn.com/torn/?selections=items&key={HOST_API_KEY}"
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, requests.get, url)
        data = response.json()
        if 'items' in data:
            new_prices = {}
            for i_id, i_data in data['items'].items():
                price = i_data.get('market_value', 0)
                if price == 0: price = i_data.get('buy_price', 0)
                new_prices[str(i_id)] = price
            global item_prices
            item_prices = new_prices
            save_json(FILES["PRICES"], item_prices)
    except Exception as e: print(f"Price update error: {e}")

@tasks.loop(seconds=60)
async def check_donations():
    global user_wallets
    last_ts = user_wallets["meta"]["last_log_ts"]
    try:
        url = f"https://api.torn.com/user/{HOST_TORN_ID}?selections=log&key={HOST_API_KEY}&limit=50"
        data = requests.get(url).json()
    except: return

    if 'error' in data: return
    logs = sorted(data.get('log', {}).values(), key=lambda x: x['timestamp'])
    updates_made = False

    for entry in logs:
        if entry['timestamp'] <= last_ts: continue
        user_wallets["meta"]["last_log_ts"] = entry['timestamp']
        updates_made = True

        if entry['log'] != RAFFLE_CONFIG['LOG_ID']: continue
        if RAFFLE_CONFIG['TRIGGER_MSG'] not in entry.get('data', {}).get('message', ''): continue

        sender_id = str(entry['data']['sender'])
        total_value = 0
        for item in entry['data'].get('items', []):
            i_id = str(item.get('id'))
            price = item_prices.get(i_id, 0)
            if price < RAFFLE_CONFIG["MIN_ITEM_PRICE"]: continue
            total_value += (price * item.get('qty'))

        tickets = int(total_value // RAFFLE_CONFIG['TICKET_PRICE'])
        if tickets > 0:
            current = user_wallets["balances"].get(sender_id, 0)
            user_wallets["balances"][sender_id] = current + tickets
            
            discord_id = None
            for d_id, t_id in linked_users.items():
                if str(t_id) == sender_id: discord_id = d_id
            
            channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel:
                mention = f"<@{discord_id}>" if discord_id else f"User [{sender_id}]"
                await channel.send(f"💳 **DEPOSIT:** {mention} +{tickets} Tickets (${total_value:,})")

    if updates_made: save_json(FILES["WALLET"], user_wallets)

# ================= COMMANDS =================
@bot.tree.command(name="link", description="Verify your Torn account.")
async def link(interaction: discord.Interaction, api_key: str):
    await interaction.response.defer(ephemeral=True)
    url = f"https://api.torn.com/user/?selections=basic&key={api_key}"
    try:
        data = requests.get(url).json()
        if 'error' in data:
            await interaction.followup.send(f"❌ Error: {data['error']['error']}")
            return
        linked_users[str(interaction.user.id)] = str(data['player_id'])
        save_json(FILES["LINKS"], linked_users)
        try:
            role = interaction.guild.get_role(VERIFIED_ROLE_ID)
            if role: await interaction.user.add_roles(role)
            await interaction.user.edit(nick=f"{data['name']} [{data['player_id']}]")
        except: pass
        await interaction.followup.send("✅ Verified and Linked!")
    except Exception as e: await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="tickets", description="Check your wallet balance.")
async def tickets(interaction: discord.Interaction):
    global user_wallets
    user_wallets = load_json(FILES["WALLET"], user_wallets)
    user_torn_id = linked_users.get(str(interaction.user.id))
    if not user_torn_id:
        await interaction.response.send_message("❌ Link account first!", ephemeral=True)
        return
    count = user_wallets["balances"].get(str(user_torn_id), 0)
    await interaction.response.send_message(f"🎟️ You have **{count}** tickets in your Wallet!", ephemeral=True)

@bot.tree.command(name="update_prices", description="Admin: Force update item prices.")
@app_commands.checks.has_permissions(administrator=True)
async def force_update(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await update_item_prices()
    await interaction.followup.send(f"✅ Prices updated!")

@bot.tree.command(name="reset_raffle", description="Admin: Wipe all tickets to ZERO (Nuclear).")
@app_commands.checks.has_permissions(administrator=True)
async def reset_raffle(interaction: discord.Interaction):
    await interaction.response.send_message("⚠️ **NUCLEAR OPTION:** Type `CONFIRM` to wipe ALL WALLETS to 0.")
    def check(m): return m.author == interaction.user and m.content == "CONFIRM" and m.channel == interaction.channel
    try: await bot.wait_for("message", check=check, timeout=30.0)
    except: return
    global user_wallets
    user_wallets["balances"] = {}
    save_json(FILES["WALLET"], user_wallets)
    await interaction.followup.send("✅ **System Reset!** All wallets are 0.")

@bot.tree.command(name="start_raffle", description="Start a raffle with UI.")
@app_commands.checks.has_permissions(administrator=True)
async def start_raffle(interaction: discord.Interaction, prize_name: str, prize_item_id: int, quantity: int, days_duration: int):
    global active_raffle
    if active_raffle["prize"]:
        await interaction.response.send_message("❌ Raffle already running.", ephemeral=True)
        return

    end_time = int(time.time() + (days_duration * 86400))
    display_name = f"{quantity}x {prize_name}" if quantity > 1 else prize_name

    active_raffle = {
        "prize": display_name,
        "prize_id": prize_item_id,
        "entries": [],
        "total_tickets": 0,
        "end_timestamp": end_time,
        "channel_id": interaction.channel.id # <--- IMPORTANT: Saving the channel ID
    }
    save_json(FILES["RAFFLE"], active_raffle)

    embed = discord.Embed(color=0x2b2d31)
    embed.set_author(name=f"Nexus Raffles • New Event")
    embed.set_thumbnail(url=f"https://www.torn.com/images/items/{prize_item_id}/large.png")
    embed.add_field(name="Prize", value=display_name, inline=True)
    embed.add_field(name="Host", value=interaction.user.mention, inline=True)
    embed.add_field(name="Drawing", value=f"<t:{end_time}:R>", inline=True)
    embed.add_field(name="How to Enter", value=f"• Send items with msg **'{RAFFLE_CONFIG['TRIGGER_MSG']}'**\n• Click 'Enter Raffle' below.", inline=False)
    
    await interaction.channel.send(embed=embed, view=RaffleView())
    await interaction.response.send_message("✅ Raffle Panel Created!", ephemeral=True)

@bot.tree.command(name="end_raffle", description="Force end the raffle manually.")
@app_commands.checks.has_permissions(administrator=True)
async def end_raffle(interaction: discord.Interaction):
    # This now just calls the shared function
    if not active_raffle["prize"]:
        await interaction.response.send_message("❌ No active raffle.", ephemeral=True)
        return
    
    await interaction.response.send_message("✅ Ending raffle manually...", ephemeral=True)
    await execute_raffle_end(bot)

bot.run(DISCORD_TOKEN)
