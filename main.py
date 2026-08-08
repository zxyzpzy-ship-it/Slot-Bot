import os
import json
import asyncio
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks


# =========================================================
# CONFIG
# =========================================================

CONFIG_FILE = Path("config.json")
DATA_FILE = Path("slots.json")

IST = ZoneInfo("Asia/Kolkata")

STAFF_ROLE_ID = 1535194766004846693
PREMIUM_ROLE_ID = 1535194940982820904
STANDARD_ROLE_ID = 1535195023983910942

FOOTER = "Cupic Bot"

# Change these if you want different colors.
EMBED_COLOR = 0x5865F2
SUCCESS_COLOR = 0x57F287
WARNING_COLOR = 0xFEE75C
ERROR_COLOR = 0xED4245


# =========================================================
# CONFIG FILE
# =========================================================

def load_config():
    if not CONFIG_FILE.exists():
        raise RuntimeError(
            "config.json is missing. Create it with your guildid."
        )

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "guildid" not in config:
        raise RuntimeError("config.json must contain 'guildid'.")

    return config


CONFIG = load_config()
GUILD_ID = int(CONFIG["guildid"])


# =========================================================
# BOT
# =========================================================

intents = discord.Intents.all()


class CupicBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=",",
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        # Start background tasks only once.
        if not expiry_loop.is_running():
            expiry_loop.start()

        if not ping_reset_loop.is_running():
            ping_reset_loop.start()

        # Guild sync prevents the global-command delay.
        guild = discord.Object(id=GUILD_ID)

        try:
            synced = await self.tree.sync(guild=guild)
            print(f"Slash commands synced: {len(synced)}")
        except discord.Forbidden:
            print(
                "\n[ERROR] Slash command sync failed: Missing Access.\n"
                "Make sure the bot is in the configured guild and was "
                "installed with applications.commands.\n"
            )
        except Exception as e:
            print(f"[ERROR] Slash command sync failed: {e}")


bot = CupicBot()


# =========================================================
# STORAGE
# =========================================================

def load_slots():
    if not DATA_FILE.exists():
        return []

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            return []

        return data

    except (json.JSONDecodeError, OSError):
        return []


def save_slots(slots):
    temp_file = DATA_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(slots, f, indent=4)

    temp_file.replace(DATA_FILE)


slots = load_slots()


def get_slot(channel_id):
    for slot in slots:
        if int(slot["channel_id"]) == int(channel_id):
            return slot

    return None


def get_user_slot(user_id):
    for slot in slots:
        if int(slot["user_id"]) == int(user_id):
            return slot

    return None


# =========================================================
# TIME HELPERS
# =========================================================

def now_timestamp():
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def format_duration(seconds):
    seconds = max(0, int(seconds))

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if days:
        return f"{days} Days"

    if hours:
        return f"{hours} Hours"

    return f"{minutes} Minutes"


def calculate_expiry(amount, unit):
    now = datetime.datetime.now(datetime.timezone.utc)

    unit = unit.lower()

    if unit == "minutes":
        delta = datetime.timedelta(minutes=amount)

    elif unit == "hours":
        delta = datetime.timedelta(hours=amount)

    elif unit == "days":
        delta = datetime.timedelta(days=amount)

    elif unit == "months":
        # Slot systems commonly treat a month as 30 days.
        delta = datetime.timedelta(days=amount * 30)

    else:
        raise ValueError("Invalid time unit.")

    return now.timestamp() + delta.total_seconds()


def discord_timestamp(timestamp, style="R"):
    return f"<t:{int(timestamp)}:{style}>"


# =========================================================
# EMBEDS
# =========================================================

def base_embed(title=None, color=EMBED_COLOR):
    embed = discord.Embed(
        title=title,
        color=color
    )

    embed.set_footer(text=FOOTER)
    return embed


def error_embed(description):
    return base_embed(
        "Action Failed",
        ERROR_COLOR
    ).add_field(
        name="",
        value=description,
        inline=False
    )


def success_embed(description):
    return base_embed(
        "Done",
        SUCCESS_COLOR
    ).add_field(
        name="",
        value=description,
        inline=False
    )


def build_slot_embed(guild, member, slot):
    created = float(slot["created_at"])
    expiry = float(slot["end_time"])

    remaining = max(0, expiry - now_timestamp())

    embed = discord.Embed(
        title="Cupic Slots",
        color=EMBED_COLOR
    )

    embed.add_field(
        name="Slot Information",
        value="",
        inline=False
    )

    embed.add_field(
        name="User",
        value=member.mention,
        inline=False
    )

    embed.add_field(
        name="Slot Name",
        value=f"🌸 • {slot['slot_name']}",
        inline=False
    )

    embed.add_field(
        name="Type",
        value=slot["slot_type"].capitalize(),
        inline=False
    )

    embed.add_field(
        name="Duration",
        value=format_duration(expiry - created),
        inline=False
    )

    embed.add_field(
        name="Creation Date",
        value=discord_timestamp(created),
        inline=False
    )

    embed.add_field(
        name="Expiry Date",
        value=discord_timestamp(expiry),
        inline=False
    )

    embed.add_field(
        name="Remaining",
        value=format_duration(remaining),
        inline=False
    )

    embed.add_field(
        name="Ping Allowed",
        value=(
            f"`@here : {slot['here_limit']}`\n"
            f"`@everyone : {slot['everyone_limit']}`"
        ),
        inline=False
    )

    embed.add_field(
        name="Ping Used",
        value=(
            f"`@here : {slot['here_used']}/{slot['here_limit']}`\n"
            f"`@everyone : "
            f"{slot['everyone_used']}/{slot['everyone_limit']}`"
        ),
        inline=False
    )

    embed.add_field(
        name="Slot Rules",
        value=(
            "📌 • Follow the server rules\n"
            "📌 • Use only the allowed pings\n"
            "📌 • Do not abuse the slot"
        ),
        inline=False
    )

    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=FOOTER)

    return embed


def build_rules_embed():
    embed = discord.Embed(
        title="Slot Rules",
        color=EMBED_COLOR
    )

    embed.description = (
        "📌 • Follow the server rules\n"
        "📌 • Use only the allowed `@here` and `@everyone` pings\n"
        "📌 • User and role mentions do not count as slot pings\n"
        "📌 • Do not abuse or bypass the slot limits\n"
        "📌 • Slot access is personal and cannot be shared\n"
        "📌 • Excessive or unauthorized pings will revoke the slot"
    )

    embed.set_footer(text=FOOTER)

    return embed


# =========================================================
# PERMISSION HELPERS
# =========================================================

def is_staff_or_admin(member: discord.Member):
    if member.guild_permissions.administrator:
        return True

    return any(
        role.id == STAFF_ROLE_ID
        for role in member.roles
    )


def is_slot_owner(member, slot):
    return int(slot["user_id"]) == int(member.id)


# =========================================================
# ROLE HELPERS
# =========================================================

async def get_role(guild, role_id):
    role = guild.get_role(role_id)

    if role:
        return role

    try:
        return await guild.fetch_role(role_id)
    except discord.HTTPException:
        return None


async def assign_slot_role(guild, member, slot_type):
    premium = await get_role(guild, PREMIUM_ROLE_ID)
    standard = await get_role(guild, STANDARD_ROLE_ID)

    try:
        if slot_type.lower() == "premium":
            if premium:
                await member.add_roles(premium, reason="Cupic slot created")

            if standard and standard in member.roles:
                await member.remove_roles(
                    standard,
                    reason="Premium slot assigned"
                )

        else:
            if standard:
                await member.add_roles(standard, reason="Cupic slot created")

            if premium and premium in member.roles:
                await member.remove_roles(
                    premium,
                    reason="Standard slot assigned"
                )

    except discord.HTTPException as e:
        print(f"Role assignment error: {e}")


async def remove_slot_roles(guild, member):
    premium = await get_role(guild, PREMIUM_ROLE_ID)
    standard = await get_role(guild, STANDARD_ROLE_ID)

    try:
        if premium and premium in member.roles:
            await member.remove_roles(
                premium,
                reason="Cupic slot ended"
            )

        if standard and standard in member.roles:
            await member.remove_roles(
                standard,
                reason="Cupic slot ended"
            )

    except discord.HTTPException as e:
        print(f"Role removal error: {e}")


# =========================================================
# CHANNEL PERMISSIONS
# =========================================================

async def apply_slot_permissions(channel, guild, owner):
    everyone = guild.default_role

    staff_role = guild.get_role(STAFF_ROLE_ID)

    overwrites = {
        everyone: discord.PermissionOverwrite(
            view_channel=False
        ),

        owner: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            mention_everyone=True
        )
    }

    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            mention_everyone=True
        )

    await channel.edit(overwrites=overwrites)


# =========================================================
# DM FUNCTIONS
# =========================================================

async def send_slot_dm(member, guild, slot, reason):
    channel_id = int(slot["channel_id"])

    view = discord.ui.View()

    button = discord.ui.Button(
        label="Click here to renew",
        style=discord.ButtonStyle.link,
        url=f"https://discord.com/channels/{guild.id}/{channel_id}"
    )

    view.add_item(button)

    embed = discord.Embed(
        title="Cupic Slots",
        color=ERROR_COLOR
    )

    embed.description = (
        f"Your **{slot['slot_name']}** slot in **{guild.name}** "
        f"has been {reason}.\n\n"
        f"**Reason:** {reason.capitalize()}."
    )

    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=FOOTER)

    try:
        await member.send(
            embed=embed,
            view=view
        )
    except discord.Forbidden:
        print(f"Could not DM {member}.")


# =========================================================
# REVOKE / EXPIRE
# =========================================================

async def revoke_slot(guild, channel, slot, reason):
    if slot not in slots:
        return

    try:
        member = guild.get_member(int(slot["user_id"]))

        # Delete the offending message is handled separately.
        await channel.set_permissions(
            member if member else guild.default_role,
            send_messages=False
        )

        if member:
            await remove_slot_roles(guild, member)

        embed = discord.Embed(
            title="Slot Revoked",
            description=(
                f"This slot has been revoked.\n\n"
                f"**Reason:** {reason}"
            ),
            color=ERROR_COLOR
        )

        embed.set_footer(text=FOOTER)

        await channel.send(embed=embed)

        if member:
            await send_slot_dm(
                member,
                guild,
                slot,
                "revoked"
            )

    except discord.HTTPException as e:
        print(f"Revoke error: {e}")

    try:
        slots.remove(slot)
        save_slots()
    except ValueError:
        pass


async def expire_slot(guild, channel, slot):
    if slot not in slots:
        return

    member = guild.get_member(int(slot["user_id"]))

    try:
        if member:
            await remove_slot_roles(guild, member)

        embed = discord.Embed(
            title="Slot Expired",
            description=(
                "This slot has expired.\n\n"
                f"**Expiry:** {discord_timestamp(slot['end_time'])}"
            ),
            color=WARNING_COLOR
        )

        embed.set_footer(text=FOOTER)

        await channel.send(embed=embed)

        if member:
            await send_slot_dm(
                member,
                guild,
                slot,
                "expired"
            )

    except discord.HTTPException as e:
        print(f"Expiry error: {e}")

    slots.remove(slot)
    save_slots()


# =========================================================
# BACKGROUND EXPIRY LOOP
# =========================================================

@tasks.loop(minutes=1)
async def expiry_loop():
    current = now_timestamp()

    for slot in slots.copy():
        if current >= float(slot["end_time"]):
            guild = bot.get_guild(GUILD_ID)

            if not guild:
                continue

            channel = guild.get_channel(
                int(slot["channel_id"])
            )

            if channel:
                await expire_slot(
                    guild,
                    channel,
                    slot
                )
            else:
                member = guild.get_member(
                    int(slot["user_id"])
                )

                if member:
                    await remove_slot_roles(
                        guild,
                        member
                    )

                slots.remove(slot)
                save_slots()


# =========================================================
# 12 PM IST PING RESET
# =========================================================

last_reset_date = None


@tasks.loop(minutes=1)
async def ping_reset_loop():
    global last_reset_date

    now = datetime.datetime.now(IST)

    # Exactly 12:00 PM IST.
    if now.hour != 12 or now.minute != 0:
        return

    today = now.date()

    if last_reset_date == today:
        return

    last_reset_date = today

    guild = bot.get_guild(GUILD_ID)

    if not guild:
        return

    for slot in slots.copy():

        # Don't reset expired slots.
        if now_timestamp() >= float(slot["end_time"]):
            continue

        slot["here_used"] = 0
        slot["everyone_used"] = 0

        channel = guild.get_channel(
            int(slot["channel_id"])
        )

        if not channel:
            continue

        role_id = (
            PREMIUM_ROLE_ID
            if slot["slot_type"].lower() == "premium"
            else STANDARD_ROLE_ID
        )

        role = guild.get_role(role_id)

        if role:
            try:
                await channel.send(
                    f"{role.mention} pings have been resetted."
                )
            except discord.HTTPException:
                pass

    save_slots()


# =========================================================
# MESSAGE HANDLER
# =========================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if not message.guild:
        await bot.process_commands(message)
        return

    slot = get_slot(message.channel.id)

    if slot:
        # Only @here and @everyone count.
        #
        # A normal user mention:
        # <@123>
        #
        # A role mention:
        # <@&123>
        #
        # does NOT count.
        #
        # Discord sets mention_everyone for @here/@everyone.

        if message.mention_everyone:
            content = message.content

            has_here = "@here" in content
            has_everyone = "@everyone" in content

            # Both can theoretically be present in one message.
            # Count each according to its own limit.
            if has_here:
                slot["here_used"] += 1

                if slot["here_limit"] == 0:
                    await message.delete()

                    await revoke_slot(
                        message.guild,
                        message.channel,
                        slot,
                        "Unauthorized @here ping"
                    )

                    return

                if slot["here_used"] > slot["here_limit"]:
                    await message.delete()

                    await revoke_slot(
                        message.guild,
                        message.channel,
                        slot,
                        "Excessive @here pings"
                    )

                    return

                try:
                    await message.channel.send(
                        f"{slot['here_used']}/{slot['here_limit']}"
                    )
                except discord.HTTPException:
                    pass

                save_slots()

            if has_everyone:
                slot["everyone_used"] += 1

                if slot["everyone_limit"] == 0:
                    await message.delete()

                    await revoke_slot(
                        message.guild,
                        message.channel,
                        slot,
                        "Unauthorized @everyone ping"
                    )

                    return

                if slot["everyone_used"] > slot["everyone_limit"]:
                    await message.delete()

                    await revoke_slot(
                        message.guild,
                        message.channel,
                        slot,
                        "Excessive @everyone pings"
                    )

                    return

                try:
                    await message.channel.send(
                        f"{slot['everyone_used']}/"
                        f"{slot['everyone_limit']}"
                    )
                except discord.HTTPException:
                    pass

                save_slots()

    await bot.process_commands(message)


# =========================================================
# SLASH COMMAND CHECK
# =========================================================

def staff_only():
    async def predicate(interaction: discord.Interaction):
        if not interaction.guild:
            return False

        member = interaction.user

        if not isinstance(member, discord.Member):
            return False

        return is_staff_or_admin(member)

    return app_commands.check(predicate)


# =========================================================
# /CREATE
# =========================================================

@bot.tree.command(
    name="create",
    description="Create a new slot."
)
@app_commands.describe(
    user="Slot owner",
    time="Slot duration",
    unit="Duration unit",
    slotname="Slot name",
    typeofslot="Premium or standard",
    category="Category for the slot",
    numberofpings="Allowed @here pings",
    numberofeveryoneping="Allowed @everyone pings"
)
@app_commands.choices(
    unit=[
        app_commands.Choice(name="Minutes", value="minutes"),
        app_commands.Choice(name="Hours", value="hours"),
        app_commands.Choice(name="Days", value="days"),
        app_commands.Choice(name="Months", value="months"),
    ],
    typeofslot=[
        app_commands.Choice(name="Premium", value="premium"),
        app_commands.Choice(name="Standard", value="standard"),
    ]
)
@staff_only()
async def create(
    interaction: discord.Interaction,
    user: discord.Member,
    time: app_commands.Range[int, 1, 3650],
    unit: app_commands.Choice[str],
    slotname: str,
    typeofslot: app_commands.Choice[str],
    category: discord.CategoryChannel,
    numberofpings: app_commands.Range[int, 0, 100],
    numberofeveryoneping: app_commands.Range[int, 0, 100]
):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild

    # Prevent duplicate active slots.
    existing = get_user_slot(user.id)

    if existing:
        await interaction.followup.send(
            embed=error_embed(
                f"{user.mention} already has an active slot."
            ),
            ephemeral=True
        )
        return

    end_time = calculate_expiry(
        time,
        unit.value
    )

    slot_type = typeofslot.value

    # Create basic permissions.
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        ),

        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            mention_everyone=True
        )
    }

    staff_role = guild.get_role(STAFF_ROLE_ID)

    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            mention_everyone=True
        )

    # Channel name.
    safe_name = slotname.lower().replace(" ", "-")

    channel = await guild.create_text_channel(
        name=safe_name[:100],
        category=category,
        overwrites=overwrites,
        reason=f"Cupic slot created by {interaction.user}"
    )

    slot = {
        "channel_id": channel.id,
        "user_id": user.id,
        "slot_name": slotname,
        "slot_type": slot_type,
        "category_id": category.id,
        "created_at": now_timestamp(),
        "end_time": end_time,
        "here_limit": numberofpings,
        "everyone_limit": numberofeveryoneping,
        "here_used": 0,
        "everyone_used": 0
    }

    slots.append(slot)
    save_slots()

    # Assign standard/premium role.
    await assign_slot_role(
        guild,
        user,
        slot_type
    )

    # Main slot information.
    await channel.send(
        embed=build_slot_embed(
            guild,
            user,
            slot
        )
    )

    # Rules automatically follow details.
    await channel.send(
        embed=build_rules_embed()
    )

    await interaction.followup.send(
        embed=success_embed(
            f"Slot created successfully.\n"
            f"**Channel:** {channel.mention}\n"
            f"**Owner:** {user.mention}\n"
            f"**Type:** {slot_type.capitalize()}"
        ),
        ephemeral=True
    )


# =========================================================
# /SRULES
# =========================================================

@bot.tree.command(
    name="srules",
    description="Send slot rules in this channel."
)
@staff_only()
async def srules(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=build_rules_embed()
    )


# =========================================================
# /SLOTINFO
# =========================================================

@bot.tree.command(
    name="slotinfo",
    description="Show information about this slot."
)
async def slotinfo(interaction: discord.Interaction):
    slot = get_slot(interaction.channel_id)

    if not slot:
        await interaction.response.send_message(
            embed=error_embed(
                "This channel is not a registered slot."
            ),
            ephemeral=True
        )
        return

    member = interaction.guild.get_member(
        int(slot["user_id"])
    )

    if not member:
        await interaction.response.send_message(
            embed=error_embed(
                "The slot owner could not be found."
            ),
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        embed=build_slot_embed(
            interaction.guild,
            member,
            slot
        )
    )


# =========================================================
# /HOLD
# =========================================================

@bot.tree.command(
    name="hold",
    description="Hold a slot."
)
@app_commands.describe(
    channel="Slot channel"
)
@staff_only()
async def hold(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    slot = get_slot(channel.id)

    if not slot:
        await interaction.response.send_message(
            embed=error_embed(
                "That channel is not a registered slot."
            ),
            ephemeral=True
        )
        return

    owner = interaction.guild.get_member(
        int(slot["user_id"])
    )

    if owner:
        await channel.set_permissions(
            owner,
            send_messages=False
        )

    await interaction.response.send_message(
        embed=success_embed(
            f"Slot {channel.mention} has been put on hold."
        ),
        ephemeral=True
    )


# =========================================================
# /UNHOLD
# =========================================================

@bot.tree.command(
    name="unhold",
    description="Remove the hold from a slot."
)
@app_commands.describe(
    channel="Slot channel"
)
@staff_only()
async def unhold(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    slot = get_slot(channel.id)

    if not slot:
        await interaction.response.send_message(
            embed=error_embed(
                "That channel is not a registered slot."
            ),
            ephemeral=True
        )
        return

    owner = interaction.guild.get_member(
        int(slot["user_id"])
    )

    if owner:
        await channel.set_permissions(
            owner,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            mention_everyone=True
        )

    await interaction.response.send_message(
        embed=success_embed(
            f"Slot {channel.mention} is active again."
        ),
        ephemeral=True
    )


# =========================================================
# /REVOKE
# =========================================================

@bot.tree.command(
    name="revoke",
    description="Revoke a slot."
)
@app_commands.describe(
    channel="Slot channel",
    reason="Reason for revoking"
)
@staff_only()
async def revoke(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    reason: str = "Staff action"
):
    slot = get_slot(channel.id)

    if not slot:
        await interaction.response.send_message(
            embed=error_embed(
                "That channel is not a registered slot."
            ),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    await revoke_slot(
        interaction.guild,
        channel,
        slot,
        reason
    )

    await interaction.followup.send(
        embed=success_embed(
            f"Slot {channel.mention} has been revoked."
        ),
        ephemeral=True
    )


# =========================================================
# /RENEW
# =========================================================

@bot.tree.command(
    name="renew",
    description="Renew an existing slot."
)
@app_commands.describe(
    channel="Slot channel",
    time="Additional duration",
    unit="Duration unit"
)
@app_commands.choices(
    unit=[
        app_commands.Choice(name="Minutes", value="minutes"),
        app_commands.Choice(name="Hours", value="hours"),
        app_commands.Choice(name="Days", value="days"),
        app_commands.Choice(name="Months", value="months"),
    ]
)
@staff_only()
async def renew(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    time: app_commands.Range[int, 1, 3650],
    unit: app_commands.Choice[str]
):
    slot = get_slot(channel.id)

    if not slot:
        await interaction.response.send_message(
            embed=error_embed(
                "That channel is not a registered slot."
            ),
            ephemeral=True
        )
        return

    additional = calculate_expiry(
        time,
        unit.value
    ) - now_timestamp()

    current_expiry = float(slot["end_time"])

    # If already expired, renew from now.
    if current_expiry < now_timestamp():
        current_expiry = now_timestamp()

    slot["end_time"] = current_expiry + additional

    save_slots()

    owner = interaction.guild.get_member(
        int(slot["user_id"])
    )

    if owner:
        await channel.set_permissions(
            owner,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            mention_everyone=True
        )

        await assign_slot_role(
            interaction.guild,
            owner,
            slot["slot_type"]
        )

    await channel.send(
        embed=build_slot_embed(
            interaction.guild,
            owner,
            slot
        )
    )

    await interaction.response.send_message(
        embed=success_embed(
            f"Slot {channel.mention} has been renewed."
        ),
        ephemeral=True
    )


# =========================================================
# /ADD
# =========================================================

@bot.tree.command(
    name="add",
    description="Give a member access to a slot."
)
@app_commands.describe(
    channel="Slot channel",
    user="Member to add"
)
@staff_only()
async def add(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    user: discord.Member
):
    slot = get_slot(channel.id)

    if not slot:
        await interaction.response.send_message(
            embed=error_embed(
                "That channel is not a registered slot."
            ),
            ephemeral=True
        )
        return

    await channel.set_permissions(
        user,
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        mention_everyone=True
    )

    await interaction.response.send_message(
        embed=success_embed(
            f"{user.mention} was added to {channel.mention}."
        ),
        ephemeral=True
    )


# =========================================================
# /REMOVE
# =========================================================

@bot.tree.command(
    name="remove",
    description="Remove a member from a slot."
)
@app_commands.describe(
    channel="Slot channel",
    user="Member to remove"
)
@staff_only()
async def remove(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    user: discord.Member
):
    slot = get_slot(channel.id)

    if not slot:
        await interaction.response.send_message(
            embed=error_embed(
                "That channel is not a registered slot."
            ),
            ephemeral=True
        )
        return

    # Never accidentally remove the actual owner.
    if int(slot["user_id"]) == user.id:
        await interaction.response.send_message(
            embed=error_embed(
                "You cannot remove the slot owner."
            ),
            ephemeral=True
        )
        return

    await channel.set_permissions(
        user,
        overwrite=None
    )

    await interaction.response.send_message(
        embed=success_embed(
            f"{user.mention} was removed from {channel.mention}."
        ),
        ephemeral=True
    )


# =========================================================
# /PINGS
# =========================================================

@bot.tree.command(
    name="pings",
    description="Reset all active slot ping counters."
)
@staff_only()
async def pings(interaction: discord.Interaction):
    guild = interaction.guild
    count = 0

    for slot in slots:
        if now_timestamp() >= float(slot["end_time"]):
            continue

        slot["here_used"] = 0
        slot["everyone_used"] = 0

        channel = guild.get_channel(
            int(slot["channel_id"])
        )

        if channel:
            role_id = (
                PREMIUM_ROLE_ID
                if slot["slot_type"].lower() == "premium"
                else STANDARD_ROLE_ID
            )

            role = guild.get_role(role_id)

            if role:
                try:
                    await channel.send(
                        f"{role.mention} pings have been resetted."
                    )
                except discord.HTTPException:
                    pass

            count += 1

    save_slots()

    await interaction.response.send_message(
        embed=success_embed(
            f"Ping counters have been reset for **{count}** active slots."
        ),
        ephemeral=True
    )


# =========================================================
# /HELP
# =========================================================

@bot.tree.command(
    name="help",
    description="Show available Cupic commands."
)
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Cupic Bot",
        description="Slot management commands.",
        color=EMBED_COLOR
    )

    embed.add_field(
        name="General",
        value=(
            "`/help` — Show this menu\n"
            "`/slotinfo` — View slot information\n"
            "`/srules` — View slot rules"
        ),
        inline=False
    )

    if isinstance(interaction.user, discord.Member):
        if is_staff_or_admin(interaction.user):
            embed.add_field(
                name="Staff",
                value=(
                    "`/create` — Create a slot\n"
                    "`/renew` — Renew a slot\n"
                    "`/hold` — Hold a slot\n"
                    "`/unhold` — Unhold a slot\n"
                    "`/revoke` — Revoke a slot\n"
                    "`/add` — Add slot access\n"
                    "`/remove` — Remove slot access\n"
                    "`/pings` — Reset ping counters"
                ),
                inline=False
            )

    embed.set_footer(text=FOOTER)

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# ERROR HANDLER
# =========================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.CheckFailure):
        message = "You do not have permission to use this command."

    elif isinstance(error, app_commands.CommandOnCooldown):
        message = "Please wait before using this command again."

    elif isinstance(error, app_commands.TransformerError):
        message = "One of the provided values is invalid."

    else:
        print(
            f"[COMMAND ERROR] "
            f"{type(error).__name__}: {error}"
        )

        message = "Something went wrong while running this command."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=error_embed(message),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed(message),
                ephemeral=True
            )

    except discord.HTTPException:
        pass


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():
    print("--------------------------------")
    print(f"{bot.user} is Ready")
    print(f"Guild ID: {GUILD_ID}")
    print(f"Loaded slots: {len(slots)}")
    print("--------------------------------")


# =========================================================
# START
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing."
    )

bot.run(TOKEN)
