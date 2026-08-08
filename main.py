import os
import re
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks


# ============================================================
# CUPIC SLOTS
# main.py
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing")

try:
    GUILD_ID = int(GUILD_ID_RAW) if GUILD_ID_RAW else 0
except ValueError:
    raise RuntimeError("GUILD_ID must be a valid Discord server ID")

# Fixed roles requested for this bot.
STAFF_ROLE_ID = 1535194766004846693
PREMIUM_ROLE_ID = 1535194940982820904
STANDARD_ROLE_ID = 1535195023983910942

DB_FILE = "cupic_slots.db"
IST = ZoneInfo("Asia/Kolkata")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA foreign_keys=ON")

db.execute(
    """
    CREATE TABLE IF NOT EXISTS slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL UNIQUE,
        owner_id INTEGER NOT NULL,
        slot_name TEXT NOT NULL,
        slot_type TEXT NOT NULL,
        category_id INTEGER NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        duration_value INTEGER NOT NULL,
        duration_unit TEXT NOT NULL,
        here_limit INTEGER NOT NULL DEFAULT 0,
        everyone_limit INTEGER NOT NULL DEFAULT 0,
        here_count INTEGER NOT NULL DEFAULT 0,
        everyone_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        last_reset_date TEXT NOT NULL,
        notified_24h INTEGER NOT NULL DEFAULT 0,
        notified_1h INTEGER NOT NULL DEFAULT 0,
        details_message_id INTEGER,
        rules_message_id INTEGER
    )
    """
)
db.commit()


def db_execute(query, params=(), commit=True):
    cur = db.execute(query, params)
    if commit:
        db.commit()
    return cur


def db_one(query, params=()):
    return db.execute(query, params).fetchone()


def db_all(query, params=()):
    return db.execute(query, params).fetchall()


def get_slot(channel_id: int):
    return db_one("SELECT * FROM slots WHERE channel_id = ?", (channel_id,))


def get_active_slots(guild_id: int):
    return db_all(
        "SELECT * FROM slots WHERE guild_id = ? AND status IN ('active', 'held')",
        (guild_id,),
    )


# ============================================================
# HELPERS
# ============================================================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_ts() -> int:
    return int(now_utc().timestamp())


def ist_now() -> datetime:
    return datetime.now(IST)


def discord_timestamp(timestamp: float) -> int:
    return int(timestamp)


def safe_channel_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9\- ]+", "", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return (name or "slot")[:90]


def duration_seconds(value: int, unit: str) -> int:
    if unit == "minutes":
        return value * 60
    if unit == "hours":
        return value * 60 * 60
    if unit == "days":
        return value * 24 * 60 * 60
    if unit == "months":
        return value * 30 * 24 * 60 * 60
    if unit == "years":
        return value * 365 * 24 * 60 * 60
    raise ValueError("Invalid time unit")


def duration_text(value: int, unit: str) -> str:
    label = unit[:-1] if value == 1 and unit.endswith("s") else unit
    return f"{value} {label.title()}"


def role_for_type(guild: discord.Guild, slot_type: str):
    role_id = PREMIUM_ROLE_ID if slot_type == "premium" else STANDARD_ROLE_ID
    return guild.get_role(role_id)


def bot_member(guild: discord.Guild):
    return guild.me


def is_staff(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator
        or any(role.id == STAFF_ROLE_ID for role in member.roles)
    )


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


def can_manage_slot(member: discord.Member, slot) -> bool:
    return is_staff(member) or member.id == int(slot["owner_id"])


def reset_marker_for_creation() -> str:
    """
    A slot created before 12:00 IST should reset at today's noon.
    A slot created after 12:00 IST should reset tomorrow.
    """
    current = ist_now()
    noon = current.replace(hour=12, minute=0, second=0, microsecond=0)

    if current >= noon:
        return current.strftime("%Y-%m-%d")

    return (current.date() - timedelta(days=1)).isoformat()


def role_mention(role_id: int) -> str:
    return f"<@&{role_id}>"


async def get_text_channel(guild: discord.Guild, channel_id: int):
    channel = guild.get_channel(channel_id)

    if isinstance(channel, discord.TextChannel):
        return channel

    try:
        fetched = await bot.fetch_channel(channel_id)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    return None


async def get_member(guild: discord.Guild, user_id: int):
    member = guild.get_member(user_id)
    if member:
        return member

    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def details_embed(guild: discord.Guild, member: discord.Member, slot, *, title_override=None):
    embed = discord.Embed(
        title=title_override or "Cupic Slots",
        description=(
            "## Slot Information\n\n"
            f"**User**\n{member.mention}\n\n"
            f"**Slot Name**\n🌸 • {slot['slot_name']}\n\n"
            f"**Duration**\n{duration_text(int(slot['duration_value']), slot['duration_unit'])}\n\n"
            f"**Creation Date**\n<t:{int(slot['created_at'])}:R>\n\n"
            f"**Expiry Date**\n<t:{int(slot['expires_at'])}:R>\n\n"
            f"**Remaining**\n<t:{int(slot['expires_at'])}:R>\n\n"
            f"**Ping Allowed**\n"
            f"`@here : {int(slot['here_limit'])}`  │  "
            f"`@everyone : {int(slot['everyone_limit'])}`\n\n"
            "**Slot Rules**\n"
            "📌 • Check server rules\n"
            "📌 • Use only the allowed pings\n"
            "📌 • No spam or misuse"
        ),
        color=discord.Color.from_rgb(255, 92, 172),
    )

    if guild.icon:
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Cupic Bot", icon_url=guild.icon.url)
    else:
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Cupic Bot")

    return embed


def rules_embed(guild: discord.Guild):
    embed = discord.Embed(
        title="Slot Rules",
        description=(
            "📌 • Follow the server rules.\n"
            "📌 • Use only your allowed `@here` and `@everyone` pings.\n"
            "📌 • Do not spam or misuse the slot.\n"
            "📌 • Only the slot owner may advertise in the slot.\n"
            "📌 • Staff instructions must be followed.\n"
            "📌 • Excessive or unauthorized pings can revoke the slot."
        ),
        color=discord.Color.from_rgb(255, 92, 172),
    )
    embed.set_footer(text="Cupic Bot")
    return embed


def status_embed(slot, member: discord.Member, action: str):
    status_text = {
        "active": "Active",
        "held": "Held",
        "expired": "Expired",
        "revoked": "Revoked",
    }.get(action, action.title())

    embed = discord.Embed(
        title="Cupic Slots",
        description=(
            f"**Slot:** {slot['slot_name']}\n"
            f"**Status:** {status_text}\n"
            f"**Type:** {slot['slot_type'].title()}\n"
            f"**Expiry:** <t:{int(slot['expires_at'])}:R>"
        ),
        color=discord.Color.from_rgb(255, 92, 172),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Cupic Bot")
    return embed


def renewal_view(guild_id: int, channel_id: int):
    view = discord.ui.View(timeout=None)
    button = discord.ui.Button(
        label="Click here to renew",
        style=discord.ButtonStyle.link,
        url=f"https://discord.com/channels/{guild_id}/{channel_id}",
    )
    view.add_item(button)
    return view


async def send_owner_dm(
    guild: discord.Guild,
    owner: discord.Member,
    slot,
    action: str,
    reason: str,
):
    embed = status_embed(slot, owner, action)
    embed.add_field(name="Reason", value=reason, inline=False)

    try:
        await owner.send(embed=embed, view=renewal_view(int(slot["guild_id"]), int(slot["channel_id"])))
    except (discord.Forbidden, discord.HTTPException):
        pass


async def remove_slot_role_if_unused(
    guild: discord.Guild,
    owner: discord.Member,
    slot_type: str,
    current_channel_id: int | None = None,
):
    role = role_for_type(guild, slot_type)
    if not role:
        return

    if current_channel_id is None:
        current_channel_id = -1

    other = db_one(
        """
        SELECT 1 FROM slots
        WHERE guild_id = ?
          AND owner_id = ?
          AND slot_type = ?
          AND status IN ('active', 'held')
          AND channel_id != ?
        LIMIT 1
        """,
        (guild.id, owner.id, slot_type, current_channel_id),
    )

    if other:
        return

    try:
        await owner.remove_roles(role, reason="Cupic Slots: no active slot of this type")
    except (discord.Forbidden, discord.HTTPException):
        pass


async def add_slot_role(guild: discord.Guild, owner: discord.Member, slot_type: str):
    role = role_for_type(guild, slot_type)
    if not role:
        return False

    me = bot_member(guild)
    if me and role >= me.top_role and not me.guild_permissions.administrator:
        return False

    try:
        await owner.add_roles(role, reason="Cupic Slots: slot created/restored")
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def set_owner_access(channel: discord.TextChannel, owner: discord.Member, allowed: bool):
    try:
        await channel.set_permissions(
            owner,
            view_channel=True,
            send_messages=allowed,
            mention_everyone=allowed,
            reason="Cupic Slots owner access",
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


async def refresh_details_message(guild: discord.Guild, slot):
    channel = await get_text_channel(guild, int(slot["channel_id"]))
    owner = await get_member(guild, int(slot["owner_id"]))

    if not channel or not owner:
        return

    embed = details_embed(guild, owner, slot)

    message_id = slot["details_message_id"]

    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(embed=embed)
            return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    try:
        message = await channel.send(embed=embed)
        db_execute(
            "UPDATE slots SET details_message_id = ? WHERE channel_id = ?",
            (message.id, channel.id),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


async def revoke_slot(
    guild: discord.Guild,
    slot,
    *,
    action: str,
    reason: str,
    delete_offending_message: discord.Message | None = None,
):
    if slot["status"] in ("expired", "revoked") and action in ("expired", "revoked"):
        return

    channel = await get_text_channel(guild, int(slot["channel_id"]))
    owner = await get_member(guild, int(slot["owner_id"]))

    if delete_offending_message:
        try:
            await delete_offending_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    new_status = "expired" if action == "expired" else "revoked"

    db_execute(
        "UPDATE slots SET status = ? WHERE channel_id = ?",
        (new_status, int(slot["channel_id"])),
    )

    if owner:
        if channel:
            await set_owner_access(channel, owner, False)

        await remove_slot_role_if_unused(
            guild,
            owner,
            slot["slot_type"],
            int(slot["channel_id"]),
        )
        await send_owner_dm(guild, owner, slot, new_status, reason)

    if channel:
        try:
            embed = discord.Embed(
                title="Cupic Slots",
                description=(
                    f"**Slot:** {slot['slot_name']}\n"
                    f"**Status:** {new_status.title()}\n"
                    f"**Reason:** {reason}"
                ),
                color=discord.Color.from_rgb(255, 92, 172),
            )
            embed.set_footer(text="Cupic Bot")
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


# ============================================================
# BOT
# ============================================================

class CupicBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.sync_done = False

    async def setup_hook(self):
        if not self.expiry_loop.is_running():
            self.expiry_loop.start()

        if GUILD_ID:
            guild_object = discord.Object(id=GUILD_ID)

            try:
                synced = await self.tree.sync(guild=guild_object)
                print(f"[SYNC] Synced {len(synced)} slash commands to guild {GUILD_ID}")
                self.sync_done = True
            except discord.Forbidden:
                print("[ERROR] Slash command sync failed: Missing Access.")
                print("[FIX] Check GUILD_ID and make sure the bot is installed in that server.")
                print("[FIX] Reinstall the bot with both 'bot' and 'applications.commands' scopes.")
            except discord.HTTPException as exc:
                print(f"[ERROR] Slash command sync failed: {exc}")
        else:
            try:
                synced = await self.tree.sync()
                print(f"[SYNC] Synced {len(synced)} global slash commands.")
                self.sync_done = True
            except discord.HTTPException as exc:
                print(f"[ERROR] Global slash command sync failed: {exc}")

    async def on_ready(self):
        print("--------------------------------")
        print(f"{self.user} is Ready")
        print(f"Guild ID: {GUILD_ID if GUILD_ID else 'Global'}")
        print(f"Loaded slots: {db_one('SELECT COUNT(*) AS c FROM slots')['c']}")
        print("--------------------------------")

    @tasks.loop(seconds=30)
    async def expiry_loop(self):
        await self.process_slots()

    @expiry_loop.before_loop
    async def before_expiry_loop(self):
        await self.wait_until_ready()

    async def process_slots(self):
        current = now_ts()
        current_ist = ist_now()
        noon = current_ist.replace(hour=12, minute=0, second=0, microsecond=0)
        today = current_ist.strftime("%Y-%m-%d")

        guilds = self.guilds

        for guild in guilds:
            slots = get_active_slots(guild.id)

            for slot in slots:
                # Daily @here/@everyone reset at 12:00 PM IST.
                if current_ist >= noon and slot["last_reset_date"] != today:
                    db_execute(
                        """
                        UPDATE slots
                        SET here_count = 0,
                            everyone_count = 0,
                            last_reset_date = ?
                        WHERE channel_id = ?
                        """,
                        (today, slot["channel_id"]),
                    )

                    slot = get_slot(int(slot["channel_id"]))

                    channel = await get_text_channel(guild, int(slot["channel_id"]))
                    if channel:
                        role_id = (
                            PREMIUM_ROLE_ID
                            if slot["slot_type"] == "premium"
                            else STANDARD_ROLE_ID
                        )
                        try:
                            await channel.send(
                                f"{role_mention(role_id)} pings have been resetted."
                            )
                        except (discord.Forbidden, discord.HTTPException):
                            pass

                # Expiry.
                if slot["status"] == "active" and float(slot["expires_at"]) <= current:
                    await revoke_slot(
                        guild,
                        slot,
                        action="expired",
                        reason="Slot expired.",
                    )
                    continue

                # Time-to-time owner DMs.
                if slot["status"] == "active":
                    remaining = float(slot["expires_at"]) - current

                    if remaining <= 86400 and not slot["notified_24h"]:
                        owner = await get_member(guild, int(slot["owner_id"]))
                        if owner:
                            try:
                                embed = status_embed(slot, owner, "active")
                                embed.add_field(
                                    name="Notice",
                                    value="Your slot expires within 24 hours.",
                                    inline=False,
                                )
                                await owner.send(
                                    embed=embed,
                                    view=renewal_view(int(slot["guild_id"]), int(slot["channel_id"])),
                                )
                            except (discord.Forbidden, discord.HTTPException):
                                pass

                        db_execute(
                            "UPDATE slots SET notified_24h = 1 WHERE channel_id = ?",
                            (slot["channel_id"],),
                        )

                    if remaining <= 3600 and not slot["notified_1h"]:
                        owner = await get_member(guild, int(slot["owner_id"]))
                        if owner:
                            try:
                                embed = status_embed(slot, owner, "active")
                                embed.add_field(
                                    name="Notice",
                                    value="Your slot expires within 1 hour.",
                                    inline=False,
                                )
                                await owner.send(
                                    embed=embed,
                                    view=renewal_view(int(slot["guild_id"]), int(slot["channel_id"])),
                                )
                            except (discord.Forbidden, discord.HTTPException):
                                pass

                        db_execute(
                            "UPDATE slots SET notified_1h = 1 WHERE channel_id = ?",
                            (slot["channel_id"],),
                        )


bot = CupicBot()


# ============================================================
# MESSAGE / PING SYSTEM
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    slot = get_slot(message.channel.id)

    if not slot:
        return

    if slot["guild_id"] != message.guild.id:
        return

    # Only the actual slot owner consumes @here/@everyone allowance.
    if message.author.id != int(slot["owner_id"]):
        return

    if slot["status"] != "active":
        return

    content = message.content

    # Only @here and @everyone count.
    has_here = "@here" in content
    has_everyone = "@everyone" in content

    if not has_here and not has_everyone:
        return

    new_here = int(slot["here_count"]) + (1 if has_here else 0)
    new_everyone = int(slot["everyone_count"]) + (1 if has_everyone else 0)

    here_bad = has_here and new_here > int(slot["here_limit"])
    everyone_bad = has_everyone and new_everyone > int(slot["everyone_limit"])

    if here_bad or everyone_bad:
        reasons = []

        if here_bad:
            reasons.append(
                f"@here limit exceeded ({slot['here_limit']} allowed)"
            )

        if everyone_bad:
            reasons.append(
                f"@everyone limit exceeded ({slot['everyone_limit']} allowed)"
            )

        await revoke_slot(
            message.guild,
            slot,
            action="revoked",
            reason="; ".join(reasons),
            delete_offending_message=message,
        )
        return

    db_execute(
        """
        UPDATE slots
        SET here_count = ?, everyone_count = ?
        WHERE channel_id = ?
        """,
        (new_here, new_everyone, message.channel.id),
    )

    # Do not create another @here/@everyone mention in the confirmation.
    counters = []

    if has_here:
        counters.append(f"`@here` • {new_here}/{slot['here_limit']}")

    if has_everyone:
        counters.append(
            f"`@everyone` • {new_everyone}/{slot['everyone_limit']}"
        )

    try:
        await message.channel.send("  |  ".join(counters))
    except (discord.Forbidden, discord.HTTPException):
        pass


# ============================================================
# COMMAND HELPERS
# ============================================================

async def require_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return False

    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return False

    return True


async def resolve_slot_from_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None,
):
    target = channel or interaction.channel

    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message(
            "Use this command in a slot channel or select a slot channel.",
            ephemeral=True,
        )
        return None

    slot = get_slot(target.id)

    if not slot:
        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True,
        )
        return None

    return slot


async def require_slot_access(
    interaction: discord.Interaction,
    slot,
) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False

    if not can_manage_slot(interaction.user, slot):
        await interaction.response.send_message(
            "You do not have permission to use this command here.",
            ephemeral=True,
        )
        return False

    return True


# ============================================================
# SLASH COMMANDS
# ============================================================

@bot.tree.command(name="create", description="Create a new slot")
@app_commands.describe(
    user="Slot owner",
    time="How long the slot should last",
    unit_of_time="Time unit",
    slotname="Slot name",
    typeofslot="Premium or standard",
    category="Category where the slot channel will be created",
    numberofpings="Allowed @here pings",
    numberofeveryoneping="Allowed @everyone pings",
)
@app_commands.choices(
    unit_of_time=[
        app_commands.Choice(name="Minutes", value="minutes"),
        app_commands.Choice(name="Hours", value="hours"),
        app_commands.Choice(name="Days", value="days"),
        app_commands.Choice(name="Months", value="months"),
        app_commands.Choice(name="Years", value="years"),
    ],
    typeofslot=[
        app_commands.Choice(name="Premium", value="premium"),
        app_commands.Choice(name="Standard", value="standard"),
    ],
)
@app_commands.rename(
    unit_of_time="unit_of_time",
    typeofslot="typeofslot",
    numberofpings="numberofpings",
    numberofeveryoneping="numberofeveryoneping",
)
async def create(
    interaction: discord.Interaction,
    user: discord.Member,
    time: app_commands.Range[int, 1, 36500],
    unit_of_time: app_commands.Choice[str],
    slotname: str,
    typeofslot: app_commands.Choice[str],
    category: discord.CategoryChannel,
    numberofpings: app_commands.Range[int, 0, 1000],
    numberofeveryoneping: app_commands.Range[int, 0, 1000],
):
    if not await require_staff(interaction):
        return

    if category.guild.id != interaction.guild.id:
        await interaction.response.send_message(
            "The category must be in this server.",
            ephemeral=True,
        )
        return

    if len(slotname.strip()) < 1:
        await interaction.response.send_message(
            "Slot name cannot be empty.",
            ephemeral=True,
        )
        return

    slot_type = typeofslot.value
    role = role_for_type(interaction.guild, slot_type)

    if not role:
        await interaction.response.send_message(
            "The configured slot role was not found.",
            ephemeral=True,
        )
        return

    me = interaction.guild.me
    if me and role >= me.top_role and not me.guild_permissions.administrator:
        await interaction.response.send_message(
            "Move the Cupic Bot role above the slot owner roles.",
            ephemeral=True,
        )
        return

    if not me.guild_permissions.manage_channels:
        await interaction.response.send_message(
            "Cupic Bot needs Manage Channels.",
            ephemeral=True,
        )
        return

    if not me.guild_permissions.manage_roles and role not in user.roles:
        await interaction.response.send_message(
            "Cupic Bot needs Manage Roles.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    created_at = now_ts()
    expires_at = created_at + duration_seconds(time, unit_of_time.value)
    channel_name = safe_channel_name(slotname)

    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            mention_everyone=False,
        ),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            mention_everyone=True,
        ),
    }

    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            mention_everyone=True,
            manage_messages=True,
        )

    # Make sure the bot itself can always work inside the private slot.
    if me:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            mention_everyone=True,
            manage_messages=True,
            manage_channels=True,
        )

    try:
        channel = await interaction.guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Cupic Slots: create {slotname}",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I could not create the slot. Check Manage Channels and the category permissions.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as exc:
        await interaction.followup.send(
            f"I could not create the slot: {exc}",
            ephemeral=True,
        )
        return

    reset_marker = reset_marker_for_creation()

    try:
        db_execute(
            """
            INSERT INTO slots (
                guild_id, channel_id, owner_id, slot_name, slot_type,
                category_id, created_at, expires_at, duration_value,
                duration_unit, here_limit, everyone_limit,
                here_count, everyone_count, status, last_reset_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'active', ?)
            """,
            (
                interaction.guild.id,
                channel.id,
                user.id,
                slotname.strip(),
                slot_type,
                category.id,
                created_at,
                expires_at,
                time,
                unit_of_time.value,
                numberofpings,
                numberofeveryoneping,
                reset_marker,
            ),
        )
    except sqlite3.Error:
        try:
            await channel.delete(reason="Cupic Slots: database error")
        except (discord.Forbidden, discord.HTTPException):
            pass

        await interaction.followup.send(
            "The slot could not be saved. No slot was created.",
            ephemeral=True,
        )
        return

    slot = get_slot(channel.id)

    role_added = await add_slot_role(interaction.guild, user, slot_type)

    # Details first, then rules.
    try:
        detail_message = await channel.send(
            embed=details_embed(interaction.guild, user, slot)
        )
        rules_message = await channel.send(
            embed=rules_embed(interaction.guild)
        )

        db_execute(
            """
            UPDATE slots
            SET details_message_id = ?, rules_message_id = ?
            WHERE channel_id = ?
            """,
            (detail_message.id, rules_message.id, channel.id),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

    # Creation DM.
    try:
        embed = details_embed(interaction.guild, user, slot)
        embed.add_field(
            name="Notice",
            value="Your slot has been created.",
            inline=False,
        )
        await user.send(
            embed=embed,
            view=renewal_view(interaction.guild.id, channel.id),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

    if not role_added:
        role_note = "\nRole could not be added. Check role hierarchy."
    else:
        role_note = ""

    await interaction.followup.send(
        f"successfully Create Slot {channel.mention}{role_note}",
        ephemeral=True,
    )


@bot.tree.command(name="renew", description="Renew a slot from staff")
@app_commands.describe(
    channel="Slot channel",
    time="New duration",
    unit_of_time="Time unit",
)
@app_commands.choices(
    unit_of_time=[
        app_commands.Choice(name="Minutes", value="minutes"),
        app_commands.Choice(name="Hours", value="hours"),
        app_commands.Choice(name="Days", value="days"),
        app_commands.Choice(name="Months", value="months"),
        app_commands.Choice(name="Years", value="years"),
    ]
)
async def renew(
    interaction: discord.Interaction,
    time: app_commands.Range[int, 1, 36500],
    unit_of_time: app_commands.Choice[str],
    channel: discord.TextChannel | None = None,
):
    if not await require_staff(interaction):
        return

    slot = await resolve_slot_from_command(interaction, channel)
    if not slot:
        return

    new_expiry = now_ts() + duration_seconds(time, unit_of_time.value)

    db_execute(
        """
        UPDATE slots
        SET expires_at = ?,
            duration_value = ?,
            duration_unit = ?,
            here_count = 0,
            everyone_count = 0,
            status = 'active',
            notified_24h = 0,
            notified_1h = 0,
            last_reset_date = ?
        WHERE channel_id = ?
        """,
        (
            new_expiry,
            time,
            unit_of_time.value,
            reset_marker_for_creation(),
            slot["channel_id"],
        ),
    )

    slot = get_slot(int(slot["channel_id"]))
    guild = interaction.guild
    owner = await get_member(guild, int(slot["owner_id"]))
    target_channel = await get_text_channel(guild, int(slot["channel_id"]))

    if owner:
        await add_slot_role(guild, owner, slot["slot_type"])

    if owner and target_channel:
        await set_owner_access(target_channel, owner, True)

    await refresh_details_message(guild, slot)

    await interaction.response.send_message(
        f"successfully renew Slot {target_channel.mention if target_channel else ''}",
        ephemeral=True,
    )


@bot.tree.command(name="hold", description="Hold a slot")
@app_commands.describe(channel="Slot channel")
async def hold(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
):
    if not await require_staff(interaction):
        return

    slot = await resolve_slot_from_command(interaction, channel)
    if not slot:
        return

    if slot["status"] not in ("active", "held"):
        await interaction.response.send_message(
            "This slot is not active.",
            ephemeral=True,
        )
        return

    db_execute(
        "UPDATE slots SET status = 'held' WHERE channel_id = ?",
        (slot["channel_id"],),
    )

    slot = get_slot(int(slot["channel_id"]))
    target = await get_text_channel(interaction.guild, int(slot["channel_id"]))
    owner = await get_member(interaction.guild, int(slot["owner_id"]))

    if owner and target:
        await set_owner_access(target, owner, False)
        await remove_slot_role_if_unused(
            interaction.guild,
            owner,
            slot["slot_type"],
            int(slot["channel_id"]),
        )

    await interaction.response.send_message(
        "successfully hold Slot",
        ephemeral=True,
    )


@bot.tree.command(name="unhold", description="Unhold a slot")
@app_commands.describe(channel="Slot channel")
async def unhold(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
):
    if not await require_staff(interaction):
        return

    slot = await resolve_slot_from_command(interaction, channel)
    if not slot:
        return

    if slot["status"] != "held":
        await interaction.response.send_message(
            "This slot is not held.",
            ephemeral=True,
        )
        return

    if float(slot["expires_at"]) <= now_ts():
        await revoke_slot(
            interaction.guild,
            slot,
            action="expired",
            reason="Slot expired while it was held.",
        )
        await interaction.response.send_message(
            "This slot has already expired.",
            ephemeral=True,
        )
        return

    db_execute(
        """
        UPDATE slots
        SET status = 'active',
            here_count = 0,
            everyone_count = 0
        WHERE channel_id = ?
        """,
        (slot["channel_id"],),
    )

    slot = get_slot(int(slot["channel_id"]))
    owner = await get_member(interaction.guild, int(slot["owner_id"]))
    target = await get_text_channel(interaction.guild, int(slot["channel_id"]))

    if owner:
        await add_slot_role(interaction.guild, owner, slot["slot_type"])

    if owner and target:
        await set_owner_access(target, owner, True)

    await refresh_details_message(interaction.guild, slot)

    await interaction.response.send_message(
        "successfully unhold Slot",
        ephemeral=True,
    )


@bot.tree.command(name="revoke", description="Revoke a slot")
@app_commands.describe(channel="Slot channel")
async def revoke(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
):
    if not await require_staff(interaction):
        return

    slot = await resolve_slot_from_command(interaction, channel)
    if not slot:
        return

    await revoke_slot(
        interaction.guild,
        slot,
        action="revoked",
        reason="Slot revoked by staff.",
    )

    await interaction.response.send_message(
        "successfully revoked Slot",
        ephemeral=True,
    )


@bot.tree.command(name="srules", description="Send slot rules")
async def srules(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "Use this command inside a slot channel.",
            ephemeral=True,
        )
        return

    slot = get_slot(interaction.channel.id)

    if not slot:
        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True,
        )
        return

    if not await require_slot_access(interaction, slot):
        return

    await interaction.channel.send(embed=rules_embed(interaction.guild))
    await interaction.response.send_message(
        "Slot rules sent.",
        ephemeral=True,
    )


@bot.tree.command(name="slotinfo", description="Show slot information")
@app_commands.describe(channel="Slot channel")
async def slotinfo(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
):
    target = channel or interaction.channel

    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message(
            "Use this command in a slot channel.",
            ephemeral=True,
        )
        return

    slot = get_slot(target.id)

    if not slot:
        await interaction.response.send_message(
            "Slot Not In DataBase",
            ephemeral=True,
        )
        return

    if not await require_slot_access(interaction, slot):
        return

    owner = await get_member(interaction.guild, int(slot["owner_id"]))

    if not owner:
        await interaction.response.send_message(
            "Slot owner could not be found.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=details_embed(interaction.guild, owner, slot),
        ephemeral=True,
    )


@bot.tree.command(name="pings", description="Reset all slot pings")
async def pings(interaction: discord.Interaction):
    if not await require_staff(interaction):
        return

    today = ist_now().strftime("%Y-%m-%d")

    db_execute(
        """
        UPDATE slots
        SET here_count = 0,
            everyone_count = 0,
            last_reset_date = ?
        WHERE guild_id = ?
          AND status = 'active'
        """,
        (today, interaction.guild.id),
    )

    await interaction.response.send_message(
        f"{role_mention(STANDARD_ROLE_ID)} {role_mention(PREMIUM_ROLE_ID)} "
        "pings have been resetted."
    )


@bot.tree.command(name="help", description="Show Cupic Slots commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Cupic Slots",
        description=(
            "**Slot Commands**\n"
            "`/help` — Show this menu\n"
            "`/slotinfo` — View slot information\n"
            "`/srules` — Send slot rules\n\n"
            "**Staff Commands**\n"
            "`/create` — Create a slot\n"
            "`/renew` — Renew a slot\n"
            "`/hold` — Hold a slot\n"
            "`/unhold` — Unhold a slot\n"
            "`/revoke` — Revoke a slot\n"
            "`/pings` — Reset slot pings"
        ),
        color=discord.Color.from_rgb(255, 92, 172),
    )
    embed.set_footer(text="Cupic Bot")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    bot.run(TOKEN)
