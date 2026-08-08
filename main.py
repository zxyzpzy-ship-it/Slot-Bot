import os
import json
import asyncio
import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing")


# Staff - can manage slots
STAFF_ROLE_ID = 1535194766004846693

# Slot owner roles
PREMIUM_ROLE_ID = 1535194940982820904
STANDARD_ROLE_ID = 1535195023983910942

# Your server
with open("config.json", "r") as file:
    CONFIG = json.load(file)

GUILD_ID = int(CONFIG["guildid"])


# ============================================================
# BOT
# ============================================================

intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix="/",
    intents=intents
)


# ============================================================
# FILES
# ============================================================

DATA_FILE = "data.json"
PING_FILE = "pingcount.json"

IST = ZoneInfo("Asia/Kolkata")


# ============================================================
# DATA
# ============================================================

def load_slots():
    try:
        with open(DATA_FILE, "r") as file:
            data = json.load(file)

        if not isinstance(data, list):
            return []

        return data

    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_slots(data):
    temp_file = DATA_FILE + ".tmp"

    with open(temp_file, "w") as file:
        json.dump(data, file, indent=4)

    os.replace(temp_file, DATA_FILE)


def load_ping_data():
    try:
        with open(PING_FILE, "r") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {}

        return data

    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ping_data(data):
    temp_file = PING_FILE + ".tmp"

    with open(temp_file, "w") as file:
        json.dump(data, file, indent=4)

    os.replace(temp_file, PING_FILE)


# ============================================================
# HELPERS
# ============================================================

def get_guild():
    return bot.get_guild(GUILD_ID)


def get_role(guild, role_id):
    return guild.get_role(role_id)


def is_staff(member):
    if member.guild_permissions.administrator:
        return True

    return any(role.id == STAFF_ROLE_ID for role in member.roles)


def get_slot_role(guild, slot_type):
    if slot_type.lower() == "premium":
        return guild.get_role(PREMIUM_ROLE_ID)

    if slot_type.lower() == "standard":
        return guild.get_role(STANDARD_ROLE_ID)

    return None


async def set_slot_role(member, slot_type):

    premium = member.guild.get_role(PREMIUM_ROLE_ID)
    standard = member.guild.get_role(STANDARD_ROLE_ID)

    remove_roles = []

    if premium and premium in member.roles:
        remove_roles.append(premium)

    if standard and standard in member.roles:
        remove_roles.append(standard)

    if remove_roles:
        await member.remove_roles(*remove_roles)

    role = get_slot_role(member.guild, slot_type)

    if role:
        await member.add_roles(role)

    return role


async def remove_slot_roles(member):

    premium = member.guild.get_role(PREMIUM_ROLE_ID)
    standard = member.guild.get_role(STANDARD_ROLE_ID)

    roles = []

    if premium and premium in member.roles:
        roles.append(premium)

    if standard and standard in member.roles:
        roles.append(standard)

    if roles:
        await member.remove_roles(*roles)


def calculate_time(amount, unit):

    amount = int(amount)
    unit = unit.lower()

    if unit == "d":
        seconds = amount * 24 * 60 * 60

    elif unit == "m":
        seconds = amount * 30 * 24 * 60 * 60

    else:
        return None

    return int(datetime.datetime.now().timestamp() + seconds)


def format_type(slot_type):
    return slot_type.capitalize()


def find_slot(channel_id, data=None):

    if data is None:
        data = load_slots()

    for slot in data:
        if int(slot["channelid"]) == int(channel_id):
            return slot

    return None


def find_user_slot(user_id, data=None):

    if data is None:
        data = load_slots()

    for slot in data:
        if int(slot["userid"]) == int(user_id):
            return slot

    return None


def today_key():
    return datetime.datetime.now(IST).strftime("%Y-%m-%d")


def current_ist():
    return datetime.datetime.now(IST)


# ============================================================
# EMBEDS
# ============================================================

def slot_details_embed(guild, member, slot_type, endtime, total_pings):

    embed = discord.Embed(
        description=(
            f"**Slot Owner:** {member.mention}\n"
            f"**Type:** {format_type(slot_type)}\n"
            f"**Pings:** {total_pings}\n"
            f"**End:** <t:{int(endtime)}:R>"
        ),
        color=0xFFFF00
    )

    if member.display_avatar:
        embed.set_author(
            name=member.display_name,
            icon_url=member.display_avatar.url
        )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(text="Cupic Bot")

    return embed


def slot_rules_embed(guild):

    embed = discord.Embed(
        description="Your Slot Rules *",
        color=0xFFFF00
    )

    embed.set_author(name="Slot Rules")

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(text="Cupic Bot")

    return embed


def revoked_embed(guild, reason):

    embed = discord.Embed(
        title="Slot Revoked",
        description=(
            f"Your slot has been revoked.\n\n"
            f"**Reason:** {reason}\n\n"
            f"Please contact staff if you believe this was a mistake."
        ),
        color=discord.Color.red()
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(text="Cupic Bot")

    return embed


def expired_embed(guild):

    embed = discord.Embed(
        title="Slot Expired",
        description=(
            "Your slot has expired.\n\n"
            "Click here to renew your slot."
        ),
        color=0xFFFF00
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(text="Cupic Bot")

    return embed


# ============================================================
# RENEW BUTTON
# ============================================================

class RenewView(discord.ui.View):

    def __init__(self, channel_id):
        super().__init__(timeout=None)

        self.add_item(
            discord.ui.Button(
                label="Click here to renew",
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/channels/{GUILD_ID}/{channel_id}"
            )
        )


# ============================================================
# SLOT PERMISSIONS
# ============================================================

async def configure_slot_permissions(channel, member):

    guild = channel.guild

    # Everyone can see the slot but cannot write
    await channel.set_permissions(
        guild.default_role,
        view_channel=True,
        send_messages=False,
        send_messages_in_threads=False,
        create_public_threads=False,
        create_private_threads=False,
        mention_everyone=False
    )

    # Slot owner
    await channel.set_permissions(
        member,
        view_channel=True,
        send_messages=True,
        send_messages_in_threads=True,
        create_public_threads=True,
        create_private_threads=True,
        mention_everyone=True
    )

    # Staff
    staff_role = guild.get_role(STAFF_ROLE_ID)

    if staff_role:

        await channel.set_permissions(
            staff_role,
            view_channel=True,
            send_messages=True,
            send_messages_in_threads=True,
            manage_messages=True,
            manage_channels=True,
            mention_everyone=True
        )

    # Administrators keep access
    for role in guild.roles:

        if role.permissions.administrator:

            await channel.set_permissions(
                role,
                view_channel=True,
                send_messages=True,
                send_messages_in_threads=True,
                manage_messages=True,
                manage_channels=True,
                mention_everyone=True
            )


async def lock_slot(channel, member):

    await channel.set_permissions(
        member,
        view_channel=False,
        send_messages=False,
        send_messages_in_threads=False
    )


# ============================================================
# SLOT CREATE
# ============================================================

@bot.tree.command(
    name="create",
    description="Create a slot"
)
@app_commands.describe(
    user="Slot owner",
    time="Slot duration",
    unit="d = days, m = months",
    slotname="Slot channel name",
    typeofslot="Premium or Standard",
    category="Category ID",
    numberofpings="Total allowed pings",
    numberofeveryoneping="Allowed @here pings"
)
@app_commands.choices(
    unit=[
        app_commands.Choice(name="Days", value="d"),
        app_commands.Choice(name="Months", value="m")
    ],
    typeofslot=[
        app_commands.Choice(name="Premium", value="premium"),
        app_commands.Choice(name="Standard", value="standard")
    ]
)
async def create(
    interaction: discord.Interaction,
    user: discord.Member,
    time: int,
    unit: app_commands.Choice[str],
    slotname: str,
    typeofslot: app_commands.Choice[str],
    category: str,
    numberofpings: int,
    numberofeveryoneping: int
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    if time <= 0:
        await interaction.response.send_message(
            "Time must be greater than 0.",
            ephemeral=True
        )
        return

    if numberofpings < 0 or numberofeveryoneping < 0:

        await interaction.response.send_message(
            "Ping numbers cannot be negative.",
            ephemeral=True
        )
        return

    if numberofeveryoneping > numberofpings:

        await interaction.response.send_message(
            "Everyone pings cannot be greater than total pings.",
            ephemeral=True
        )
        return

    try:
        category_id = int(category)

    except ValueError:

        await interaction.response.send_message(
            "Invalid category ID.",
            ephemeral=True
        )
        return

    guild = interaction.guild

    if guild is None:
        return

    category_channel = guild.get_channel(category_id)

    if not isinstance(category_channel, discord.CategoryChannel):

        await interaction.response.send_message(
            "Category not found.",
            ephemeral=True
        )
        return

    data = load_slots()

    # Prevent duplicate active slot for same user
    existing = find_user_slot(user.id, data)

    if existing:

        await interaction.response.send_message(
            "This user already has an active slot.",
            ephemeral=True
        )
        return

    endtime = calculate_time(
        time,
        unit.value
    )

    # Create channel
    channel = await guild.create_text_channel(
        name=slotname,
        category=category_channel
    )

    await configure_slot_permissions(
        channel,
        user
    )

    # Give correct role
    role = await set_slot_role(
        user,
        typeofslot.value
    )

    # Database
    slot = {
        "channelid": channel.id,
        "userid": user.id,
        "endtime": endtime,
        "type": typeofslot.value,
        "total_pings": numberofpings,
        "everyone_pings": numberofeveryoneping,
        "used_pings": 0,
        "used_everyone_pings": 0,
        "created_at": int(datetime.datetime.now().timestamp()),
        "last_reminder": 0,
        "held": False
    }

    data.append(slot)

    save_slots(data)

    # Initial slot details
    await channel.send(
        embed=slot_details_embed(
            guild,
            user,
            typeofslot.value,
            endtime,
            numberofpings
        )
    )

    # Rules automatically
    await channel.send(
        embed=slot_rules_embed(guild)
    )

    await interaction.response.send_message(
        f"successfully Create Slot {channel.mention}",
        ephemeral=True
    )


# ============================================================
# ADD
# ============================================================

@bot.tree.command(
    name="add",
    description="Add a user to a slot"
)
@app_commands.describe(
    user="User to add",
    channel="Slot channel"
)
async def add(
    interaction: discord.Interaction,
    user: discord.Member,
    channel: discord.TextChannel
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    data = load_slots()

    slot = find_slot(channel.id, data)

    if not slot:

        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True
        )
        return

    await channel.set_permissions(
        user,
        view_channel=True,
        send_messages=True,
        mention_everyone=True
    )

    await interaction.response.send_message(
        "successfully Added",
        ephemeral=True
    )


# ============================================================
# REMOVE
# ============================================================

@bot.tree.command(
    name="remove",
    description="Remove a user from a slot"
)
@app_commands.describe(
    user="User",
    channel="Slot channel"
)
async def remove(
    interaction: discord.Interaction,
    user: discord.Member,
    channel: discord.TextChannel
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    data = load_slots()

    slot = find_slot(channel.id, data)

    if not slot:

        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True
        )
        return

    await channel.set_permissions(
        user,
        send_messages=False,
        mention_everyone=False
    )

    await interaction.response.send_message(
        "successfully removed",
        ephemeral=True
    )


# ============================================================
# HOLD
# ============================================================

@bot.tree.command(
    name="hold",
    description="Hold a slot"
)
@app_commands.describe(
    channel="Slot channel"
)
async def hold(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    data = load_slots()

    slot = find_slot(channel.id, data)

    if not slot:

        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True
        )
        return

    member = interaction.guild.get_member(
        int(slot["userid"])
    )

    if member:
        await lock_slot(channel, member)

    slot["held"] = True

    save_slots(data)

    await interaction.response.send_message(
        f"Slot held {channel.mention}",
        ephemeral=True
    )


# ============================================================
# UNHOLD
# ============================================================

@bot.tree.command(
    name="unhold",
    description="Unhold a slot"
)
@app_commands.describe(
    channel="Slot channel"
)
async def unhold(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    data = load_slots()

    slot = find_slot(channel.id, data)

    if not slot:

        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True
        )
        return

    member = interaction.guild.get_member(
        int(slot["userid"])
    )

    if not member:

        await interaction.response.send_message(
            "Member not found.",
            ephemeral=True
        )
        return

    await configure_slot_permissions(
        channel,
        member
    )

    slot["held"] = False

    save_slots(data)

    await interaction.response.send_message(
        f"Slot unheld {channel.mention}",
        ephemeral=True
    )


# ============================================================
# REVOKE
# ============================================================

async def revoke_slot(
    guild,
    slot,
    reason,
    delete_last_message=False
):

    channel = guild.get_channel(
        int(slot["channelid"])
    )

    member = guild.get_member(
        int(slot["userid"])
    )

    if channel and delete_last_message:

        try:
            messages = [
                message async for message
                in channel.history(limit=1)
            ]

            for message in messages:
                await message.delete()

        except:
            pass

    if member:

        await remove_slot_roles(member)

    if channel and member:

        await lock_slot(
            channel,
            member
        )

        try:

            embed = revoked_embed(
                guild,
                reason
            )

            await channel.send(
                embed=embed
            )

        except:
            pass

    # DM owner
    if member:

        try:

            embed = revoked_embed(
                guild,
                reason
            )

            await member.send(
                embed=embed,
                view=RenewView(
                    int(slot["channelid"])
                )
            )

        except discord.Forbidden:
            pass

    return channel, member


@bot.tree.command(
    name="revoke",
    description="Revoke a slot"
)
@app_commands.describe(
    channel="Slot channel",
    reason="Reason for revoking"
)
async def revoke(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    reason: str = "Staff action"
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    data = load_slots()

    slot = find_slot(channel.id, data)

    if not slot:

        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True
        )
        return

    await revoke_slot(
        interaction.guild,
        slot,
        reason
    )

    data.remove(slot)

    save_slots(data)

    await interaction.response.send_message(
        f"Slot revoked {channel.mention}",
        ephemeral=True
    )


# ============================================================
# RENEW
# ============================================================

@bot.tree.command(
    name="renew",
    description="Renew a slot"
)
@app_commands.describe(
    user="Slot owner",
    channel="Slot channel",
    time="Duration",
    unit="Days or months",
    typeofslot="Premium or Standard"
)
@app_commands.choices(
    unit=[
        app_commands.Choice(name="Days", value="d"),
        app_commands.Choice(name="Months", value="m")
    ],
    typeofslot=[
        app_commands.Choice(name="Premium", value="premium"),
        app_commands.Choice(name="Standard", value="standard")
    ]
)
async def renew(
    interaction: discord.Interaction,
    user: discord.Member,
    channel: discord.TextChannel,
    time: int,
    unit: app_commands.Choice[str],
    typeofslot: app_commands.Choice[str]
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    if time <= 0:

        await interaction.response.send_message(
            "Time must be greater than 0.",
            ephemeral=True
        )
        return

    data = load_slots()

    slot = find_slot(
        channel.id,
        data
    )

    if not slot:

        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True
        )
        return

    endtime = calculate_time(
        time,
        unit.value
    )

    slot["endtime"] = endtime
    slot["userid"] = user.id
    slot["type"] = typeofslot.value
    slot["used_pings"] = 0
    slot["used_everyone_pings"] = 0
    slot["held"] = False

    save_slots(data)

    await set_slot_role(
        user,
        typeofslot.value
    )

    await configure_slot_permissions(
        channel,
        user
    )

    # Delete old messages
    try:
        await channel.purge(limit=1000)

    except:
        pass

    # Details
    await channel.send(
        embed=slot_details_embed(
            interaction.guild,
            user,
            typeofslot.value,
            endtime,
            slot["total_pings"]
        )
    )

    # Rules
    await channel.send(
        embed=slot_rules_embed(
            interaction.guild
        )
    )

    await interaction.response.send_message(
        f"successfully renew Slot {channel.mention}",
        ephemeral=True
    )


# ============================================================
# SLOT RULES
# ============================================================

@bot.tree.command(
    name="srules",
    description="Send slot rules"
)
async def srules(
    interaction: discord.Interaction
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    await interaction.channel.send(
        embed=slot_rules_embed(
            interaction.guild
        )
    )

    await interaction.response.send_message(
        "Slot rules sent.",
        ephemeral=True
    )


# ============================================================
# PINGS RESET
# ============================================================

@bot.tree.command(
    name="pings",
    description="Reset slot pings"
)
async def pings(
    interaction: discord.Interaction
):

    if not is_staff(interaction.user):

        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    data = load_slots()

    for slot in data:

        slot["used_pings"] = 0
        slot["used_everyone_pings"] = 0

    save_slots(data)

    guild = interaction.guild

    premium = guild.get_role(
        PREMIUM_ROLE_ID
    )

    standard = guild.get_role(
        STANDARD_ROLE_ID
    )

    mentions = []

    if standard:
        mentions.append(standard.mention)

    if premium:
        mentions.append(premium.mention)

    role_text = " ".join(mentions)

    await interaction.channel.send(
        f"{role_text} pings have been resetted."
    )

    await interaction.response.send_message(
        "Pings reset.",
        ephemeral=True
    )


# ============================================================
# HELP
# ============================================================

@bot.tree.command(
    name="help",
    description="Show slot bot commands"
)
async def help_command(
    interaction: discord.Interaction
):

    embed = discord.Embed(
        description=(
            "**/create** - Use To Create Slot\n"
            "**/add** - Use To Add User In Slot\n"
            "**/remove** - Use To Remove User In SLot\n"
            "**/renew** - Use To Renew Slot\n"
            "**/hold** - Hold a slot\n"
            "**/unhold** - Unhold a slot\n"
            "**/revoke** - Revoke a slot\n"
            "**/srules** - Send slot rules\n"
            "**/pings** - Reset slot pings"
        ),
        color=0xFFFF00
    )

    if interaction.guild.icon:

        embed.set_thumbnail(
            url=interaction.guild.icon.url
        )

    embed.set_author(
        name="Slot Bot Help Menu"
    )

    embed.set_footer(
        text="Cupic Bot"
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# AUTOMATIC @HERE DETECTION
# ============================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    if not message.guild:
        return

    # Only monitor registered slot channels
    data = load_slots()

    slot = find_slot(
        message.channel.id,
        data
    )

    if not slot:
        return

    # Only slot owner can use the slot
    if message.author.id != int(slot["userid"]):

        # Staff/admin are allowed
        if not is_staff(message.author):

            try:
                await message.delete()

            except:
                pass

            return

    # Detect @here
    if message.mention_everyone:

        # Discord's mention_everyone also catches @everyone.
        # Check actual content to specifically count @here.
        if "@here" not in message.content.lower():
            return

        total_allowed = int(
            slot["everyone_pings"]
        )

        used = int(
            slot["used_everyone_pings"]
        )

        # No @here allowed
        if total_allowed <= 0:

            try:
                await message.delete()

            except:
                pass

            await revoke_slot(
                message.guild,
                slot,
                "Excessive @here ping.",
                delete_last_message=False
            )

            data.remove(slot)
            save_slots(data)

            return

        # Already used maximum
        if used >= total_allowed:

            try:
                await message.delete()

            except:
                pass

            await revoke_slot(
                message.guild,
                slot,
                "Excessive @here ping.",
                delete_last_message=False
            )

            data.remove(slot)
            save_slots(data)

            return

        # Count ping
        slot["used_everyone_pings"] = used + 1

        # Count total ping too
        slot["used_pings"] = int(
            slot["used_pings"]
        ) + 1

        save_slots(data)

        await message.channel.send(
            f"{slot['used_everyone_pings']}/{total_allowed}"
        )

        # Total ping limit
        if slot["used_pings"] >= int(slot["total_pings"]):

            # Don't revoke immediately if this was the exact
            # allowed ping.
            pass


# ============================================================
# AUTOMATIC EXPIRY
# ============================================================

@tasks.loop(minutes=1)
async def slot_expiry_loop():

    data = load_slots()

    now = int(
        datetime.datetime.now().timestamp()
    )

    changed = False

    for slot in data.copy():

        if slot.get("held", False):
            continue

        if now >= int(slot["endtime"]):

            guild = get_guild()

            if not guild:
                continue

            channel = guild.get_channel(
                int(slot["channelid"])
            )

            member = guild.get_member(
                int(slot["userid"])
            )

            # Remove role
            if member:

                await remove_slot_roles(
                    member
                )

                try:

                    await member.send(
                        embed=expired_embed(
                            guild
                        ),
                        view=RenewView(
                            int(slot["channelid"])
                        )
                    )

                except discord.Forbidden:
                    pass

            # Lock channel
            if channel and member:

                await lock_slot(
                    channel,
                    member
                )

                try:

                    await channel.send(
                        embed=expired_embed(
                            guild
                        ),
                        view=RenewView(
                            int(slot["channelid"])
                        )
                    )

                except:
                    pass

            data.remove(slot)

            changed = True

    if changed:
        save_slots(data)


# ============================================================
# DAILY 12 PM IST PING RESET
# ============================================================

@tasks.loop(minutes=1)
async def daily_ping_reset():

    now = current_ist()

    # Exactly 12:00 PM IST
    if now.hour != 12 or now.minute != 0:
        return

    data = load_slots()

    if not data:
        return

    # Prevent running multiple times during the same minute
    marker_file = "last_reset.json"

    try:

        with open(marker_file, "r") as file:
            marker = json.load(file)

    except:

        marker = {}

    today = now.strftime("%Y-%m-%d")

    if marker.get("date") == today:
        return

    for slot in data:

        slot["used_pings"] = 0
        slot["used_everyone_pings"] = 0

    save_slots(data)

    with open(marker_file, "w") as file:

        json.dump(
            {"date": today},
            file,
            indent=4
        )

    guild = get_guild()

    if not guild:
        return

    premium = guild.get_role(
        PREMIUM_ROLE_ID
    )

    standard = guild.get_role(
        STANDARD_ROLE_ID
    )

    mentions = []

    if standard:
        mentions.append(
            standard.mention
        )

    if premium:
        mentions.append(
            premium.mention
        )

    text = " ".join(mentions)

    # Tell active slot channels
    active_channels = set()

    for slot in data:

        channel = guild.get_channel(
            int(slot["channelid"])
        )

        if channel:
            active_channels.add(channel.id)

    for channel_id in active_channels:

        channel = guild.get_channel(
            channel_id
        )

        if channel:

            try:

                await channel.send(
                    f"{text} pings have been resetted."
                )

            except:
                pass


# ============================================================
# PERIODIC SLOT OWNER DETAILS
# ============================================================

@tasks.loop(hours=12)
async def slot_reminder_loop():

    data = load_slots()

    guild = get_guild()

    if not guild:
        return

    now = int(
        datetime.datetime.now().timestamp()
    )

    for slot in data:

        member = guild.get_member(
            int(slot["userid"])
        )

        if not member:
            continue

        # Don't remind held slots
        if slot.get("held", False):
            continue

        try:

            embed = discord.Embed(
                title="Slot Details",
                description=(
                    f"**Type:** "
                    f"{format_type(slot['type'])}\n"
                    f"**Pings:** "
                    f"{slot['used_pings']}/"
                    f"{slot['total_pings']}\n"
                    f"**@here:** "
                    f"{slot['used_everyone_pings']}/"
                    f"{slot['everyone_pings']}\n"
                    f"**End:** "
                    f"<t:{int(slot['endtime'])}:R>"
                ),
                color=0xFFFF00
            )

            if guild.icon:
                embed.set_thumbnail(
                    url=guild.icon.url
                )

            embed.set_footer(
                text="Cupic Bot"
            )

            await member.send(
                embed=embed
            )

        except discord.Forbidden:
            pass

        except Exception as error:
            print(
                f"Reminder error: {error}"
            )


# ============================================================
# COMMAND SYNC
# ============================================================

@bot.event
async def on_ready():

    print(
        f"{bot.user} is Ready"
    )

    print(
        f"Staff Role: {STAFF_ROLE_ID}"
    )

    print(
        f"Premium Role: {PREMIUM_ROLE_ID}"
    )

    print(
        f"Standard Role: {STANDARD_ROLE_ID}"
    )

    # Sync slash commands
    try:

        guild = discord.Object(
            id=GUILD_ID
        )

        bot.tree.copy_global_to(
            guild=guild
        )

        synced = await bot.tree.sync(
            guild=guild
        )

        print(
            f"Synced {len(synced)} slash commands."
        )

    except Exception as error:

        print(
            f"Slash command sync error: {error}"
        )

    # Start loops
    if not slot_expiry_loop.is_running():
        slot_expiry_loop.start()

    if not daily_ping_reset.is_running():
        daily_ping_reset.start()

    if not slot_reminder_loop.is_running():
        slot_reminder_loop.start()


# ============================================================
# RUN
# ============================================================

bot.run(TOKEN)
