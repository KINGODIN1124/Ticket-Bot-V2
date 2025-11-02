import discord
from discord.ext import commands
import asyncio, datetime, json, os
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# ----------------- Load environment -----------------
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
PORT = int(os.getenv("PORT", 8080))

# ----------------- Load config -----------------
with open("config.json") as f:
    config = json.load(f)

COOLDOWN_HOURS = config["cooldown_hours"]
AUTO_CLOSE_MINUTES = config["auto_close_minutes"]
APP_LINKS = config["apps"]

# ----------------- Flask keep-alive -----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ RASH TECH Ticket Bot is running!"

def run_web():
    app.run(host="0.0.0.0", port=PORT)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# ----------------- Discord setup -----------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)

cooldowns = {}          # user_id -> datetime
active_tickets = {}     # channel_id -> {"user": user_id, "timer": asyncio.Task}


# Helper: format time nicely
def format_time_left(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}h {m}m {s}s"


# ----------------- Ticket Command -----------------
@bot.command()
async def ticket(ctx, action=None):
    if action != "create":
        return

    user = ctx.author
    now = datetime.datetime.utcnow()
    expiry = cooldowns.get(user.id)

    # Cooldown check
    if expiry and expiry > now:
        remaining = int((expiry - now).total_seconds())
        embed = discord.Embed(
            title="⏳ Cooldown Active",
            description=f"You can create a new ticket in **{format_time_left(remaining)}**.",
            color=discord.Color.yellow()
        )
        msg = await ctx.send(embed=embed)

        async def countdown():
            while True:
                await asyncio.sleep(60)
                remaining = int((expiry - datetime.datetime.utcnow()).total_seconds())
                if remaining <= 0:
                    await msg.edit(embed=discord.Embed(
                        title="✅ Cooldown Expired",
                        description="You can now create a new ticket!",
                        color=discord.Color.green()
                    ))
                    break
                await msg.edit(embed=discord.Embed(
                    title="⏳ Cooldown Active",
                    description=f"You can create a new ticket in **{format_time_left(remaining)}**.",
                    color=discord.Color.yellow()
                ))
        bot.loop.create_task(countdown())
        return

    # Start cooldown
    cooldowns[user.id] = now + datetime.timedelta(hours=COOLDOWN_HOURS)

    # Create ticket channel
    guild = ctx.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True)
    }
    channel = await guild.create_text_channel(f"ticket-{user.name}", overwrites=overwrites)

    welcome = discord.Embed(
        title="🎫 Ticket Created Successfully!",
        description=(
            f"Hello {user.mention}, welcome to **RASH TECH Support!**\n"
            f"Please describe your issue or choose a premium app below.\n\n"
            f"⏱️ *This ticket will auto-close after {AUTO_CLOSE_MINUTES} minutes of inactivity.*"
        ),
        color=discord.Color.blue()
    )
    await channel.send(embed=welcome)

    # Premium app list
    app_list = "\n".join([f"- 🔹 **{name.title()}**" for name in APP_LINKS.keys()])
    apps_embed = discord.Embed(
        title="💎 Premium Apps List",
        description=(
            f"Here are the premium apps we currently offer:\n\n{app_list}\n\n"
            f"🆕 New apps will come soon!\n\n💖 Thank you for being a part of **RASH TECH**."
        ),
        color=discord.Color.teal()
    )
    await channel.send(embed=apps_embed)

    # Auto-close after inactivity
    async def auto_close():
        await asyncio.sleep(AUTO_CLOSE_MINUTES * 60)
        if channel.id in active_tickets:
            await channel.send(embed=discord.Embed(
                title="⚠️ Ticket Auto-Closed",
                description="This ticket has been closed due to 15 minutes of inactivity.",
                color=discord.Color.red()
            ))
            await channel.delete()

    task = bot.loop.create_task(auto_close())
    active_tickets[channel.id] = {"user": user.id, "timer": task}


# ----------------- App Verification -----------------
@bot.event
async def on_message(message):
    await bot.process_commands(message)
    if message.author.bot:
        return

    if message.channel.id in active_tickets:
        # Reset inactivity timer
        data = active_tickets[message.channel.id]
        data["timer"].cancel()
        new_task = bot.loop.create_task(asyncio.sleep(AUTO_CLOSE_MINUTES * 60))
        active_tickets[message.channel.id]["timer"] = new_task

        content = message.content.lower().strip()
        if content in APP_LINKS:
            await verify_app(message, content)


async def verify_app(message, app_name):
    user = message.author
    channel = message.channel

    embed = discord.Embed(
        title="🔍 Verification Required",
        description=(
            f"Before we can give you **{app_name.title()}**, please make sure you meet the following:\n\n"
            "1️⃣ Subscribe to our [YouTube channel](https://youtube.com/@rashtech)\n"
            "2️⃣ Post a screenshot showing you’re subscribed\n"
            "3️⃣ You’ve been in this server for **at least 24 hours**\n\n"
            "Once you’ve met these, our staff will verify and approve your request."
        ),
        color=discord.Color.orange()
    )
    await channel.send(embed=embed)

    def check(m):
        return m.author == user and (m.attachments or m.content.lower() in ["done", "submitted"])

    try:
        msg = await bot.wait_for("message", check=check, timeout=300)
        member = message.guild.get_member(user.id)
        joined = datetime.datetime.utcnow() - member.joined_at

        if joined.total_seconds() < 86400:
            await channel.send(embed=discord.Embed(
                title="❌ Verification Failed",
                description="You must be in this server for at least 24 hours.",
                color=discord.Color.red()
            ))
            return
        if not msg.attachments:
            await channel.send(embed=discord.Embed(
                title="❌ Verification Failed",
                description="You must upload a screenshot showing your subscription.",
                color=discord.Color.red()
            ))
            return

        # Passed verification
        await channel.send(embed=discord.Embed(
            title="✅ Verification Successful",
            description=f"Awesome {user.mention}! Verification for **{app_name.title()}** complete.\nPlease wait...",
            color=discord.Color.green()
        ))
        await asyncio.sleep(2)

        # Download link
        link = APP_LINKS[app_name]
        download = discord.Embed(
            title="📥 Here’s your download link!",
            description=(
                f"Here is your download link for **{app_name.title()}** — enjoy!\n\n"
                f"🔗 [**Click here to download {app_name.title()}**]({link})\n\n"
                f"Thank you for being a valued member of **RASH TECH** ❤️"
            ),
            color=discord.Color.blurple()
        )
        await channel.send(embed=download)

        # Log
        log = bot.get_channel(LOG_CHANNEL_ID)
        if log:
            log_embed = discord.Embed(
                title="🗃️ Ticket Verified",
                color=discord.Color.green()
            )
            log_embed.add_field(name="User", value=user.mention)
            log_embed.add_field(name="App", value=app_name.title())
            log_embed.timestamp = datetime.datetime.utcnow()
            await log.send(embed=log_embed)

    except asyncio.TimeoutError:
        await channel.send(embed=discord.Embed(
            title="⏰ Verification Timeout",
            description="You took too long to respond. Please try again later.",
            color=discord.Color.red()
        ))


# ----------------- Admin Command -----------------
@bot.command()
@commands.has_permissions(administrator=True)
async def cooldown(ctx, action=None, member: discord.Member = None):
    if action == "remove" and member:
        cooldowns.pop(member.id, None)
        await ctx.send(embed=discord.Embed(
            title="🕓 Cooldown Removed",
            description=f"Removed cooldown for {member.mention}.",
            color=discord.Color.green()
        ))


# ----------------- Ready Event -----------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

# ----------------- Start Flask + Bot -----------------
keep_alive()
bot.run(TOKEN)
