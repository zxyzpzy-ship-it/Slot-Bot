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
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing")

try:
    GUILD_ID = int(GUILD_ID_RAW) if GUILD_ID_RAW else 0
except ValueError:
    raise RuntimeError("GUILD_ID must be a valid Discord server ID")


# ============================================================
# CONFIG
# ============================================================

STAFF_ROLE_ID = 1536652574994993252
PREMIUM_ROLE_ID = 1536652574994993251
STANDARD_ROLE_ID = 1536652574994993250

RENEW_CHANNEL_ID = 1536652576060215350
RENEW_GUILD_ID = 1536652574994993246

DB_FILE = "cupic_slots.db"

IST = ZoneInfo("Asia/Kolkata")

YELLOW = discord.Color.from_rgb(255, 193, 7)
DARK_YELLOW = discord.Color.from_rgb(245, 166, 35)


# ============================================================
# INTENTS
# ============================================================

intents = discord.Intents.default()

intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

# Required for custom status scanning.
intents.presences = True


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False,
)

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
        rules_message_id INTEGER,

        price TEXT DEFAULT 'Not set'
    )
    """
)


db.execute(
    """
    CREATE TABLE IF NOT EXISTS settings (
        guild_id INTEGER PRIMARY KEY,
        reset_channel_id INTEGER
    )
    """
)


db.execute(
    """
    CREATE TABLE IF NOT EXISTS scan_states (
        guild_id INTEGER PRIMARY KEY,
        message_id INTEGER,
        enforcement_stage INTEGER NOT NULL DEFAULT 0
    )
    """
)

db.commit()


def ensure_column(column_name: str, definition: str):

    columns = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(slots)"
        ).fetchall()
    }

    if column_name not in columns:

        db.execute(
            f"ALTER TABLE slots ADD COLUMN "
            f"{column_name} {definition}"
        )

        db.commit()


ensure_column(
    "price",
    "TEXT DEFAULT 'Not set'",
)


def db_execute(
    query,
    params=(),
    commit=True,
):

    cur = db.execute(
        query,
        params,
    )

    if commit:
        db.commit()

    return cur


def db_one(
    query,
    params=(),
):

    return db.execute(
        query,
        params,
    ).fetchone()


def db_all(
    query,
    params=(),
):

    return db.execute(
        query,
        params,
    ).fetchall()


def get_slot(channel_id: int):

    return db_one(
        """
        SELECT *
        FROM slots
        WHERE channel_id = ?
        """,
        (channel_id,),
    )


def get_active_slots(guild_id: int):

    return db_all(
        """
        SELECT *
        FROM slots
        WHERE guild_id = ?
        AND status IN ('active', 'held')
        """,
        (guild_id,),
    )


# ============================================================
# TIME
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def now_ts():

    return int(
        now_utc().timestamp()
    )


def ist_now():

    return datetime.now(
        IST
    )


def duration_seconds(
    value: int,
    unit: str,
):

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

    raise ValueError(
        "Invalid time unit"
    )


def discord_timestamp(timestamp):

    return f"<t:{int(timestamp)}:R>"


def absolute_timestamp(timestamp):

    return f"<t:{int(timestamp)}:f>"


def current_reset_date():

    current = ist_now()

    noon = current.replace(
        hour=12,
        minute=0,
        second=0,
        microsecond=0,
    )

    if current >= noon:
        return current.strftime("%Y-%m-%d")

    return (
        current.date()
        - timedelta(days=1)
    ).isoformat()


def reset_marker_for_creation():

    return current_reset_date()


# ============================================================
# GENERAL HELPERS
# ============================================================

def role_mention(role_id):

    return f"<@&{role_id}>"


def role_for_type(
    guild,
    slot_type,
):

    role_id = (
        PREMIUM_ROLE_ID
        if slot_type == "premium"
        else STANDARD_ROLE_ID
    )

    return guild.get_role(role_id)


def bot_member(guild):

    return guild.me


def is_staff(member):

    return (
        member.guild_permissions.administrator
        or any(
            role.id == STAFF_ROLE_ID
            for role in member.roles
        )
    )


def is_admin(member):

    return member.guild_permissions.administrator


def can_manage_slot(
    member,
    slot,
):

    return (
        is_staff(member)
        or member.id == int(
            slot["owner_id"]
        )
    )


# ============================================================
# SLOT NAME
# ============================================================

def slot_prefix(slot_type):

    if slot_type == "premium":
        return "🏅・"

    return "✨・"


def slot_display_name(
    name,
    slot_type="standard",
):

    name = name.strip()

    prefix = slot_prefix(
        slot_type
    )

    if name.startswith("🏅・"):
        name = name[2:]

    if name.startswith("✨・"):
        name = name[2:]

    return (
        f"{prefix}{name}"
    )[:100]


def safe_channel_name(
    name,
    slot_type,
):

    return slot_display_name(
        name,
        slot_type,
    )


# ============================================================
# CHANNEL / MEMBER
# ============================================================

async def get_text_channel(
    guild,
    channel_id,
):

    channel = guild.get_channel(
        channel_id
    )

    if isinstance(
        channel,
        discord.TextChannel,
    ):
        return channel

    try:

        fetched = await bot.fetch_channel(
            channel_id
        )

        if isinstance(
            fetched,
            discord.TextChannel,
        ):
            return fetched

    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass

    return None


async def get_member(
    guild,
    user_id,
):

    member = guild.get_member(
        user_id
    )

    if member:
        return member

    try:

        return await guild.fetch_member(
            user_id
        )

    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
    ):
        return None


# ============================================================
# EMBEDS
# ============================================================

def details_embed(
    guild,
    member,
    slot,
    title_override=None,
):

    status = slot["status"]

    status_map = {
        "active": "🟢 Active",
        "held": "🟠 Held",
        "expired": "🔴 Expired",
        "revoked": "🔴 Revoked",
    }

    embed = discord.Embed(
        title=(
            title_override
            or "🏅 Cupic Slot"
        ),
        description=(
            "### Slot Details\n\n"

            f"**Owner**\n"
            f"{member.mention}\n\n"

            f"**Slot**\n"
            f"`{slot_display_name(slot['slot_name'], slot['slot_type'])}`\n\n"

            f"**Type**\n"
            f"`{slot['slot_type'].title()}`\n\n"

            f"**Status**\n"
            f"{status_map.get(status, status.title())}\n\n"

            f"**Created**\n"
            f"{absolute_timestamp(slot['created_at'])} "
            f"({discord_timestamp(slot['created_at'])})\n\n"

            f"**Expires**\n"
            f"{absolute_timestamp(slot['expires_at'])} "
            f"({discord_timestamp(slot['expires_at'])})\n\n"

            f"**Price**\n"
            f"`{slot['price'] or 'Not set'}`\n\n"

            f"**Ping Allowance**\n"
            f"Here/User/Role: "
            f"`{int(slot['here_count'])}/"
            f"{int(slot['here_limit'])}`\n"
            f"Everyone: "
            f"`{int(slot['everyone_count'])}/"
            f"{int(slot['everyone_limit'])}`"
        ),
        color=YELLOW,
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    return embed


def rules_embed(guild):

    return discord.Embed(
        title="📜 Slot Rules",
        description=(
            "### Usage Rules\n\n"
            "• Follow all server rules.\n"
            "• Stay within your daily ping allowance.\n"
            "• User and role mentions are not allowed.\n"
            "• A user or role mention instantly revokes the slot.\n"
            "• `@here` counts toward the normal ping allowance.\n"
            "• `@everyone` is tracked separately.\n"
            "• Do not spam or bypass slot restrictions.\n"
            "• Staff may place slots on hold.\n"
            "• Repeated abuse may result in permanent revocation."
        ),
        color=YELLOW,
    )


def status_embed(
    slot,
    member,
    action,
):

    labels = {
        "active": (
            "🟢 Slot Active",
            YELLOW,
        ),
        "held": (
            "🟠 Slot Held",
            DARK_YELLOW,
        ),
        "expired": (
            "🔴 Slot Expired",
            discord.Color.red(),
        ),
        "revoked": (
            "🔴 Slot Revoked",
            discord.Color.red(),
        ),
    }

    title, color = labels.get(
        action,
        ("🏅 Slot Update", YELLOW),
    )

    return discord.Embed(
        title=title,
        description=(
            f"**Slot**\n"
            f"`{slot_display_name(slot['slot_name'], slot['slot_type'])}`\n\n"

            f"**Type**\n"
            f"`{slot['slot_type'].title()}`\n\n"

            f"**Expiry**\n"
            f"{discord_timestamp(slot['expires_at'])}"
        ),
        color=color,
    )


def ping_used_embed(
    username,
    used,
    total,
):

    return discord.Embed(
        description=(
            f"**{discord.utils.escape_markdown(username)}** "
            f"you used __**{used}/{total}**__ "
            f"pings today."
        ),
        color=YELLOW,
    )


def reset_embed():

    return discord.Embed(
        title="🔄 Daily Ping Reset",
        description=(
            "All slot ping allowances have been reset.\n\n"
            "You can now use your full daily allowance again."
        ),
        color=YELLOW,
    )


def hold_embed(
    slot,
    reason,
):

    return discord.Embed(
        title="⏸️ Slot Placed On Hold",
        description=(
            f"**Slot**\n"
            f"`{slot_display_name(slot['slot_name'], slot['slot_type'])}`\n\n"

            f"**Reason**\n"
            f"{reason}\n\n"

            "The slot is currently unavailable."
        ),
        color=DARK_YELLOW,
    )


def unhold_embed(
    slot,
    reason,
):

    return discord.Embed(
        title="▶️ Slot Released",
        description=(
            f"**Slot**\n"
            f"`{slot_display_name(slot['slot_name'], slot['slot_type'])}`\n\n"

            f"**Reason**\n"
            f"{reason}\n\n"

            "The slot is active again."
        ),
        color=YELLOW,
    )


# ============================================================
# RENEW BUTTON
# ============================================================

def renewal_view():

    view = discord.ui.View(
        timeout=None
    )

    view.add_item(
        discord.ui.Button(
            label="Click here to renew",
            style=discord.ButtonStyle.link,
            url=(
                f"https://discord.com/channels/"
                f"{RENEW_GUILD_ID}/"
                f"{RENEW_CHANNEL_ID}"
            ),
        )
    )

    return view


# ============================================================
# ROLES
# ============================================================

async def add_slot_role(
    guild,
    owner,
    slot_type,
):

    role = role_for_type(
        guild,
        slot_type,
    )

    if not role:
        return False

    me = bot_member(guild)

    if (
        me
        and role >= me.top_role
        and not me.guild_permissions.administrator
    ):
        return False

    try:

        await owner.add_roles(
            role,
            reason="Cupic Slots slot role",
        )

        return True

    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
        return False


async def remove_slot_role_if_unused(
    guild,
    owner,
    slot_type,
    current_channel_id=None,
):

    role = role_for_type(
        guild,
        slot_type,
    )

    if not role:
        return

    current_channel_id = (
        current_channel_id
        if current_channel_id is not None
        else -1
    )

    other = db_one(
        """
        SELECT 1
        FROM slots
        WHERE guild_id = ?
        AND owner_id = ?
        AND slot_type = ?
        AND status IN ('active', 'held')
        AND channel_id != ?
        LIMIT 1
        """,
        (
            guild.id,
            owner.id,
            slot_type,
            current_channel_id,
        ),
    )

    if other:
        return

    try:

        await owner.remove_roles(
            role,
            reason="No active slot of this type",
        )

    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass


# ============================================================
# CHANNEL ACCESS
# ============================================================

async def set_owner_access(
    channel,
    owner,
    allowed,
):

    try:

        await channel.set_permissions(
            owner,
            view_channel=True,
            send_messages=allowed,
            mention_everyone=allowed,
            reason="Cupic Slots owner access",
        )

    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass


# ============================================================
# DETAILS MESSAGE
# ============================================================

async def refresh_details_message(
    guild,
    slot,
):

    channel = await get_text_channel(
        guild,
        int(slot["channel_id"]),
    )

    owner = await get_member(
        guild,
        int(slot["owner_id"]),
    )

    if not channel or not owner:
        return

    embed = details_embed(
        guild,
        owner,
        slot,
    )

    message_id = slot[
        "details_message_id"
    ]

    if message_id:

        try:

            message = await channel.fetch_message(
                int(message_id)
            )

            await message.edit(
                embed=embed
            )

            return

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    try:

        message = await channel.send(
            embed=embed
        )

        db_execute(
            """
            UPDATE slots
            SET details_message_id = ?
            WHERE channel_id = ?
            """,
            (
                message.id,
                channel.id,
            ),
        )

    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass


# ============================================================
# REVOKE / EXPIRE
# ============================================================

async def revoke_slot(
    guild,
    slot,
    *,
    action,
    reason,
    delete_offending_message=None,
    delete_channel=False,
):

    channel = await get_text_channel(
        guild,
        int(slot["channel_id"]),
    )

    owner = await get_member(
        guild,
        int(slot["owner_id"]),
    )

    if delete_offending_message:

        try:
            await delete_offending_message.delete()

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    new_status = (
        "expired"
        if action == "expired"
        else "revoked"
    )

    db_execute(
        """
        UPDATE slots
        SET status = ?
        WHERE channel_id = ?
        """,
        (
            new_status,
            int(slot["channel_id"]),
        ),
    )

    if owner:

        if channel:

            await set_owner_access(
                channel,
                owner,
                False,
            )

        await remove_slot_role_if_unused(
            guild,
            owner,
            slot["slot_type"],
            int(slot["channel_id"]),
        )

        try:

            embed = status_embed(
                slot,
                owner,
                new_status,
            )

            embed.description += (
                f"\n\n**Reason**\n{reason}"
            )

            await owner.send(
                embed=embed,
                view=renewal_view(),
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    if channel and not delete_channel:

        try:

            await channel.send(
                embed=discord.Embed(
                    title=(
                        "🔴 Slot Expired"
                        if new_status == "expired"
                        else "🔴 Slot Revoked"
                    ),
                    description=(
                        f"**Slot**\n"
                        f"`{slot_display_name(slot['slot_name'], slot['slot_type'])}`\n\n"
                        f"**Reason**\n"
                        f"{reason}\n\n"
                        "This slot can no longer be used."
                    ),
                    color=discord.Color.red(),
                )
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    if delete_channel and channel:

        try:

            await channel.delete(
                reason=reason
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass


# ============================================================
# RESET ANNOUNCEMENT CHANNEL
# ============================================================

def get_reset_channel_id(
    guild_id,
):

    row = db_one(
        """
        SELECT reset_channel_id
        FROM settings
        WHERE guild_id = ?
        """,
        (guild_id,),
    )

    if not row:
        return None

    return row["reset_channel_id"]


def set_reset_channel(
    guild_id,
    channel_id,
):

    db_execute(
        """
        INSERT INTO settings (
            guild_id,
            reset_channel_id
        )
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            reset_channel_id = excluded.reset_channel_id
        """,
        (
            guild_id,
            channel_id,
        ),
    )


async def send_daily_reset_message(
    guild,
):

    channel_id = get_reset_channel_id(
        guild.id
    )

    if not channel_id:
        return

    channel = await get_text_channel(
        guild,
        int(channel_id),
    )

    if not channel:
        return

    allowed_mentions = discord.AllowedMentions(
        roles=True,
        users=False,
        everyone=False,
    )

    try:

        await channel.send(
            content=(
                f"{role_mention(STANDARD_ROLE_ID)} "
                f"{role_mention(PREMIUM_ROLE_ID)}"
            ),
            embed=reset_embed(),
            allowed_mentions=allowed_mentions,
        )

    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
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

    async def setup_hook(self):

        if not self.expiry_loop.is_running():
            self.expiry_loop.start()

        if GUILD_ID:

            guild_object = discord.Object(
                id=GUILD_ID
            )

            try:

                synced = await self.tree.sync(
                    guild=guild_object
                )

                print(
                    f"[SYNC] Synced {len(synced)} "
                    f"commands to {GUILD_ID}"
                )

            except Exception as exc:

                print(
                    f"[SYNC ERROR] {exc}"
                )

        else:

            try:

                synced = await self.tree.sync()

                print(
                    f"[SYNC] Synced {len(synced)} global commands"
                )

            except Exception as exc:

                print(
                    f"[SYNC ERROR] {exc}"
                )

    async def on_ready(self):

        print("--------------------------------")
        print(f"{self.user} is Ready")
        print(
            "Loaded slots:",
            db_one(
                "SELECT COUNT(*) AS c FROM slots"
            )["c"],
        )
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

        today = current_ist.strftime(
            "%Y-%m-%d"
        )

        noon_passed = (
            current_ist.hour >= 12
        )

        for guild in self.guilds:

            slots = get_active_slots(
                guild.id
            )

            # ====================================================
            # DAILY RESET
            # ====================================================

            if noon_passed:

                needs_reset = db_one(
                    """
                    SELECT 1
                    FROM slots
                    WHERE guild_id = ?
                    AND status IN ('active', 'held')
                    AND last_reset_date != ?
                    LIMIT 1
                    """,
                    (
                        guild.id,
                        today,
                    ),
                )

                if needs_reset:

                    db_execute(
                        """
                        UPDATE slots
                        SET
                            here_count = 0,
                            everyone_count = 0,
                            last_reset_date = ?
                        WHERE guild_id = ?
                        AND status IN ('active', 'held')
                        """,
                        (
                            today,
                            guild.id,
                        ),
                    )

                    # One reset message per configured channel.
                    await send_daily_reset_message(
                        guild
                    )

            # ====================================================
            # EXPIRY
            # ====================================================

            for slot in slots:

                if (
                    slot["status"] == "active"
                    and float(
                        slot["expires_at"]
                    ) <= current
                ):

                    await revoke_slot(
                        guild,
                        slot,
                        action="expired",
                        reason=(
                            "The slot reached its expiry time."
                        ),
                    )


bot = CupicBot()


# ============================================================
# MESSAGE TRACKING
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):

    if message.author.bot:
        return

    if not message.guild:
        return

    slot = get_slot(
        message.channel.id
    )

    if not slot:
        return

    if slot["guild_id"] != message.guild.id:
        return

    # Only owner activity counts.
    if message.author.id != int(
        slot["owner_id"]
    ):
        return

    # ========================================================
    # HELD / INACTIVE
    # ========================================================

    if slot["status"] != "active":
        return

    # ========================================================
    # USER / ROLE MENTION = INSTANT REVOKE
    # ========================================================

    if (
        len(message.mentions) > 0
        or len(message.role_mentions) > 0
    ):

        await revoke_slot(
            message.guild,
            slot,
            action="revoked",
            reason=(
                "A user or role was mentioned "
                "inside the slot."
            ),
            delete_offending_message=message,
        )

        return

    # ========================================================
    # @HERE
    # ========================================================

    has_here = (
        "@here" in message.content
    )

    # ========================================================
    # @EVERYONE
    # ========================================================

    has_everyone = (
        "@everyone" in message.content
    )

    if not has_here and not has_everyone:
        return

    new_here = int(
        slot["here_count"]
    )

    new_everyone = int(
        slot["everyone_count"]
    )

    if has_here:

        new_here += 1

    if has_everyone:

        new_everyone += 1

    # ========================================================
    # EXCEEDED = REVOKE
    #
    # IMPORTANT:
    # We DO NOT reset counters when /unhold is used.
    #
    # Therefore:
    #
    # 06:00 -> user uses 3 extra pings
    # 07:00 -> staff unholds
    # 08:00 -> user pings again
    #
    # If still before 12:00, the old counter remains.
    # The next excess ping revokes the slot again.
    #
    # 12:00 -> automatic reset
    # ========================================================

    if (
        has_here
        and new_here > int(
            slot["here_limit"]
        )
    ):

        await revoke_slot(
            message.guild,
            slot,
            action="revoked",
            reason=(
                "The slot exceeded its "
                "daily @here ping allowance."
            ),
            delete_offending_message=message,
        )

        return

    if (
        has_everyone
        and new_everyone > int(
            slot["everyone_limit"]
        )
    ):

        await revoke_slot(
            message.guild,
            slot,
            action="revoked",
            reason=(
                "The slot exceeded its "
                "daily @everyone ping allowance."
            ),
            delete_offending_message=message,
        )

        return

    # ========================================================
    # SAVE
    # ========================================================

    db_execute(
        """
        UPDATE slots
        SET
            here_count = ?,
            everyone_count = ?
        WHERE channel_id = ?
        """,
        (
            new_here,
            new_everyone,
            message.channel.id,
        ),
    )

    # ========================================================
    # SHORT PING CONFIRMATION
    # ========================================================

    try:

        username = message.author.name

        if has_here:

            await message.channel.send(
                embed=ping_used_embed(
                    username,
                    new_here,
                    int(slot["here_limit"]),
                )
            )

        if has_everyone:

            await message.channel.send(
                embed=ping_used_embed(
                    username,
                    new_everyone,
                    int(slot["everyone_limit"]),
                )
            )

    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass


# ============================================================
# COMMAND HELPERS
# ============================================================

async def require_staff(
    interaction,
):

    if (
        not interaction.guild
        or not isinstance(
            interaction.user,
            discord.Member,
        )
    ):

        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )

        return False

    if not is_staff(
        interaction.user
    ):

        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )

        return False

    return True


async def resolve_slot_from_command(
    interaction,
    channel,
):

    target = (
        channel
        or interaction.channel
    )

    if not isinstance(
        target,
        discord.TextChannel,
    ):

        await interaction.response.send_message(
            "Select a slot channel.",
            ephemeral=True,
        )

        return None

    slot = get_slot(
        target.id
    )

    if not slot:

        await interaction.response.send_message(
            "This channel is not registered as a slot.",
            ephemeral=True,
        )

        return None

    return slot


async def require_slot_access(
    interaction,
    slot,
):

    if not isinstance(
        interaction.user,
        discord.Member,
    ):
        return False

    if not can_manage_slot(
        interaction.user,
        slot,
    ):

        await interaction.response.send_message(
            "You do not have permission to use this slot.",
            ephemeral=True,
        )

        return False

    return True


# ============================================================
# /CREATE
# ============================================================

@bot.tree.command(
    name="create",
    description="Create a new slot",
)
@app_commands.describe(
    user="Slot owner",
    time="Slot duration",
    unit_of_time="Time unit",
    slotname="Slot name",
    typeofslot="Premium or Standard",
    category="Slot category",
    numberofpings="Allowed @here pings",
    numberofeveryoneping="Allowed @everyone pings",
)
@app_commands.choices(
    unit_of_time=[
        app_commands.Choice(
            name="Minutes",
            value="minutes",
        ),
        app_commands.Choice(
            name="Hours",
            value="hours",
        ),
        app_commands.Choice(
            name="Days",
            value="days",
        ),
        app_commands.Choice(
            name="Months",
            value="months",
        ),
        app_commands.Choice(
            name="Years",
            value="years",
        ),
    ],
    typeofslot=[
        app_commands.Choice(
            name="Premium",
            value="premium",
        ),
        app_commands.Choice(
            name="Standard",
            value="standard",
        ),
    ],
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

    if not await require_staff(
        interaction
    ):
        return

    if category.guild.id != interaction.guild.id:

        await interaction.response.send_message(
            "The category must be in this server.",
            ephemeral=True,
        )

        return

    slot_type = typeofslot.value

    role = role_for_type(
        interaction.guild,
        slot_type,
    )

    if not role:

        await interaction.response.send_message(
            "Configured slot role was not found.",
            ephemeral=True,
        )

        return

    me = interaction.guild.me

    if not me:

        await interaction.response.send_message(
            "Bot member could not be found.",
            ephemeral=True,
        )

        return

    if not me.guild_permissions.manage_channels:

        await interaction.response.send_message(
            "Cupic Bot needs Manage Channels.",
            ephemeral=True,
        )

        return

    if not me.guild_permissions.manage_roles:

        await interaction.response.send_message(
            "Cupic Bot needs Manage Roles.",
            ephemeral=True,
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    created_at = now_ts()

    expires_at = (
        created_at
        + duration_seconds(
            time,
            unit_of_time.value,
        )
    )

    display_name = slot_display_name(
        slotname,
        slot_type,
    )

    channel_name = safe_channel_name(
        slotname,
        slot_type,
    )

    staff_role = interaction.guild.get_role(
        STAFF_ROLE_ID
    )

    overwrites = {

        interaction.guild.default_role:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                mention_everyone=False,
            ),

        user:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                mention_everyone=True,
            ),
    }

    if staff_role:

        overwrites[staff_role] = (
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                mention_everyone=True,
                manage_messages=True,
            )
        )

    overwrites[me] = (
        discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            mention_everyone=True,
            manage_messages=True,
            manage_channels=True,
        )
    )

    try:

        channel = await interaction.guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            reason="Cupic Slots: slot creation",
        )

    except discord.Forbidden:

        await interaction.followup.send(
            "I could not create the slot. Check my channel permissions.",
            ephemeral=True,
        )

        return

    except discord.HTTPException as exc:

        await interaction.followup.send(
            f"Could not create slot: {exc}",
            ephemeral=True,
        )

        return

    reset_marker = reset_marker_for_creation()

    try:

        db_execute(
            """
            INSERT INTO slots (
                guild_id,
                channel_id,
                owner_id,
                slot_name,
                slot_type,
                category_id,
                created_at,
                expires_at,
                duration_value,
                duration_unit,
                here_limit,
                everyone_limit,
                here_count,
                everyone_count,
                status,
                last_reset_date,
                price
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, 0, 0, 'active', ?, 'Not set'
            )
            """,
            (
                interaction.guild.id,
                channel.id,
                user.id,
                display_name,
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
            await channel.delete()
        except Exception:
            pass

        await interaction.followup.send(
            "Database error. Slot creation cancelled.",
            ephemeral=True,
        )

        return

    slot = get_slot(
        channel.id
    )

    await add_slot_role(
        interaction.guild,
        user,
        slot_type,
    )

    try:

        detail_message = await channel.send(
            embed=details_embed(
                interaction.guild,
                user,
                slot,
            )
        )

        rules_message = await channel.send(
            embed=rules_embed(
                interaction.guild
            )
        )

        db_execute(
            """
            UPDATE slots
            SET
                details_message_id = ?,
                rules_message_id = ?
            WHERE channel_id = ?
            """,
            (
                detail_message.id,
                rules_message.id,
                channel.id,
            ),
        )

    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass

    await interaction.followup.send(
        f"### ✅ Slot Created\n"
        f"**Slot:** {channel.mention}\n"
        f"**Owner:** {user.mention}\n"
        f"**Type:** `{slot_type.title()}`",
        ephemeral=True,
    )


# ============================================================
# /HOLD
# ============================================================

@bot.tree.command(
    name="hold",
    description="Place a slot on hold",
)
@app_commands.describe(
    reason="Reason",
    channel="Slot channel",
)
async def hold(
    interaction,
    reason: str,
    channel: discord.TextChannel | None = None,
):

    if not await require_staff(
        interaction
    ):
        return

    slot = await resolve_slot_from_command(
        interaction,
        channel,
    )

    if not slot:
        return

    if slot["status"] not in (
        "active",
        "held",
    ):

        await interaction.response.send_message(
            "This slot is no longer active.",
            ephemeral=True,
        )

        return

    db_execute(
        """
        UPDATE slots
        SET status = 'held'
        WHERE channel_id = ?
        """,
        (
            slot["channel_id"],
        ),
    )

    slot = get_slot(
        int(slot["channel_id"])
    )

    owner = await get_member(
        interaction.guild,
        int(slot["owner_id"]),
    )

    target = await get_text_channel(
        interaction.guild,
        int(slot["channel_id"]),
    )

    if owner:

        await remove_slot_role_if_unused(
            interaction.guild,
            owner,
            slot["slot_type"],
            int(slot["channel_id"]),
        )

    if owner and target:

        await set_owner_access(
            target,
            owner,
            False,
        )

        try:

            await target.send(
                embed=hold_embed(
                    slot,
                    reason,
                )
            )

            # DM held notification
            await owner.send(
                embed=hold_embed(
                    slot,
                    reason,
                )
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    await interaction.response.send_message(
        "### ⏸️ Slot held\n"
        f"**Slot:** `{slot_display_name(slot['slot_name'], slot['slot_type'])}`",
        ephemeral=True,
    )


# ============================================================
# /UNHOLD
# ============================================================

@bot.tree.command(
    name="unhold",
    description="Release a held slot",
)
@app_commands.describe(
    reason="Reason",
    channel="Slot channel",
)
async def unhold(
    interaction,
    reason: str,
    channel: discord.TextChannel | None = None,
):

    if not await require_staff(
        interaction
    ):
        return

    slot = await resolve_slot_from_command(
        interaction,
        channel,
    )

    if not slot:
        return

    if slot["status"] != "held":

        await interaction.response.send_message(
            "This slot is not held.",
            ephemeral=True,
        )

        return

    # IMPORTANT:
    # DO NOT reset ping counters here.
    #
    # This is what makes:
    #
    # 06:00 -> exceeded
    # 07:00 -> unhold
    # 08:00 -> ping
    #
    # still use the same day's counters.
    #
    # The counters reset only at 12 PM IST.

    db_execute(
        """
        UPDATE slots
        SET status = 'active'
        WHERE channel_id = ?
        """,
        (
            slot["channel_id"],
        ),
    )

    slot = get_slot(
        int(slot["channel_id"])
    )

    owner = await get_member(
        interaction.guild,
        int(slot["owner_id"]),
    )

    target = await get_text_channel(
        interaction.guild,
        int(slot["channel_id"]),
    )

    if owner:

        await add_slot_role(
            interaction.guild,
            owner,
            slot["slot_type"],
        )

    if owner and target:

        await set_owner_access(
            target,
            owner,
            True,
        )

        try:

            await target.send(
                embed=unhold_embed(
                    slot,
                    reason,
                )
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    await refresh_details_message(
        interaction.guild,
        slot,
    )

    await interaction.response.send_message(
        "### ▶️ Slot released\n"
        f"**Slot:** `{slot_display_name(slot['slot_name'], slot['slot_type'])}`\n"
        "Daily ping counters were preserved.",
        ephemeral=True,
    )


# ============================================================
# /SLOTINFO
# ============================================================

@bot.tree.command(
    name="slotinfo",
    description="View slot information",
)
@app_commands.describe(
    channel="Slot channel",
)
async def slotinfo(
    interaction,
    channel: discord.TextChannel | None = None,
):

    target = (
        channel
        or interaction.channel
    )

    if not isinstance(
        target,
        discord.TextChannel,
    ):

        await interaction.response.send_message(
            "Use this command in a slot channel.",
            ephemeral=True,
        )

        return

    slot = get_slot(
        target.id
    )

    if not slot:

        await interaction.response.send_message(
            "This is not a registered slot.",
            ephemeral=True,
        )

        return

    if not await require_slot_access(
        interaction,
        slot,
    ):
        return

    owner = await get_member(
        interaction.guild,
        int(slot["owner_id"]),
    )

    if not owner:

        await interaction.response.send_message(
            "Owner not found.",
            ephemeral=True,
        )

        return

    await interaction.response.send_message(
        embed=details_embed(
            interaction.guild,
            owner,
            slot,
        ),
        ephemeral=True,
    )


# ============================================================
# /SRULES
# ============================================================

@bot.tree.command(
    name="srules",
    description="Send slot rules",
)
async def srules(
    interaction,
):

    if not interaction.guild:

        await interaction.response.send_message(
            "This can only be used in a server.",
            ephemeral=True,
        )

        return

    # Can now be used in ANY normal text channel.
    await interaction.channel.send(
        embed=rules_embed(
            interaction.guild
        )
    )

    await interaction.response.send_message(
        "Rules sent.",
        ephemeral=True,
    )


# ============================================================
# /SPRICE
# ============================================================

@bot.tree.command(
    name="sprice",
    description="Set slot price",
)
@app_commands.describe(
    price="Price",
    channel="Slot channel",
)
async def sprice(
    interaction,
    price: str,
    channel: discord.TextChannel | None = None,
):

    if not await require_staff(
        interaction
    ):
        return

    slot = await resolve_slot_from_command(
        interaction,
        channel,
    )

    if not slot:
        return

    db_execute(
        """
        UPDATE slots
        SET price = ?
        WHERE channel_id = ?
        """,
        (
            price.strip(),
            slot["channel_id"],
        ),
    )

    slot = get_slot(
        int(slot["channel_id"])
    )

    await refresh_details_message(
        interaction.guild,
        slot,
    )

    await interaction.response.send_message(
        f"Price updated to `{price.strip()}`.",
        ephemeral=True,
    )


# ============================================================
# /PINGS
#
# This resets ALL active + held slots.
# It also saves the channel where /pings was used.
#
# That channel becomes the automatic daily reset channel.
# ============================================================

@bot.tree.command(
    name="pings",
    description="Reset slot pings and set this channel for daily reset messages",
)
async def pings(
    interaction,
):

    if not await require_staff(
        interaction
    ):
        return

    today = ist_now().strftime(
        "%Y-%m-%d"
    )

    # Remember this channel for automatic daily messages.
    set_reset_channel(
        interaction.guild.id,
        interaction.channel.id,
    )

    # RESET ACTIVE + HELD.
    db_execute(
        """
        UPDATE slots
        SET
            here_count = 0,
            everyone_count = 0,
            last_reset_date = ?
        WHERE guild_id = ?
        AND status IN ('active', 'held')
        """,
        (
            today,
            interaction.guild.id,
        ),
    )

    allowed_mentions = discord.AllowedMentions(
        roles=True,
        users=False,
        everyone=False,
    )

    await interaction.channel.send(
        content=(
            f"{role_mention(STANDARD_ROLE_ID)} "
            f"{role_mention(PREMIUM_ROLE_ID)}"
        ),
        embed=reset_embed(),
        allowed_mentions=allowed_mentions,
    )

    await interaction.response.send_message(
        "Pings reset. This channel is now the automatic daily reset channel.",
        ephemeral=True,
    )


# ============================================================
# /FIND
#
# Searches messages from the last 7 days in every registered
# slot belonging to this guild.
# ============================================================

@bot.tree.command(
    name="find",
    description="Find text inside all slots from the last 7 days",
)
@app_commands.describe(
    text="Text to search for",
)
async def find(
    interaction,
    text: str,
):

    if not await require_staff(
        interaction
    ):
        return

    text = text.strip()

    if not text:

        await interaction.response.send_message(
            "Enter something to search for.",
            ephemeral=True,
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    cutoff = datetime.now(
        timezone.utc
    ) - timedelta(
        days=7
    )

    results = []

    slots = db_all(
        """
        SELECT *
        FROM slots
        WHERE guild_id = ?
        """,
        (
            interaction.guild.id,
        ),
    )

    for slot in slots:

        channel = await get_text_channel(
            interaction.guild,
            int(slot["channel_id"]),
        )

        if not channel:
            continue

        try:

            async for message in channel.history(
                limit=None,
                after=cutoff,
                oldest_first=False,
            ):

                if text.lower() in (
                    message.content.lower()
                ):

                    results.append(
                        (
                            slot,
                            channel,
                            message,
                        )
                    )

                    # Prevent gigantic output.
                    if len(results) >= 50:
                        break

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            continue

        if len(results) >= 50:
            break

    if not results:

        await interaction.followup.send(
            f"No messages containing `{text}` "
            "were found in the last 7 days.",
            ephemeral=True,
        )

        return

    embed = discord.Embed(
        title=f"🔎 Search: {text}",
        description=(
            f"Found **{len(results)}** matching message(s) "
            "from the last 7 days."
        ),
        color=YELLOW,
    )

    lines = []

    for slot, channel, message in results:

        slot_name = slot_display_name(
            slot["slot_name"],
            slot["slot_type"],
        )

        preview = (
            message.content
            .replace("\n", " ")
        )

        if len(preview) > 100:
            preview = preview[:97] + "..."

        lines.append(
            f"**{slot_name}**\n"
            f"{channel.mention} • "
            f"[Message]({message.jump_url})\n"
            f"> {discord.utils.escape_markdown(preview)}"
        )

    # Discord embed description max is 4096.
    text_output = ""

    for line in lines:

        if len(text_output) + len(line) + 2 > 3900:
            break

        text_output += (
            line + "\n\n"
        )

    embed.description += (
        "\n\n" + text_output
    )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
    )


# ============================================================
# VANITY / CUSTOM STATUS
# ============================================================

def has_cupic_vanity(member):

    target = ".gg/cupicslots"

    # Presence custom status.
    activities = member.activities or []

    for activity in activities:

        name = getattr(
            activity,
            "name",
            "",
        ) or ""

        state = getattr(
            activity,
            "state",
            "",
        ) or ""

        details = getattr(
            activity,
            "details",
            "",
        ) or ""

        combined = (
            f"{name} {state} {details}"
        ).lower()

        if target in combined:
            return True

    return False


# ============================================================
# SCAN RESULT
# ============================================================

def scan_embed(
    missing,
):

    embed = discord.Embed(
        title="🔎 Cupic Slots Vanity Scan",
        description=(
            f"Found **{len(missing)}** slot owner(s) "
            "without `.gg/cupicslots` in their custom status.\n\n"
            "⚠️ Discord bots cannot reliably read the "
            "profile About Me/bio field, so this scan checks "
            "available presence/custom-status data."
        ),
        color=YELLOW,
    )

    lines = []

    for item in missing:

        member = item["member"]
        channel = item["channel"]

        username_link = (
            f"[{discord.utils.escape_markdown(member.name)}]"
            f"(https://discord.com/users/{member.id})"
        )

        channel_link = (
            f"[{discord.utils.escape_markdown(channel.name)}]"
            f"({channel.jump_url if hasattr(channel, 'jump_url') else f'https://discord.com/channels/{channel.guild.id}/{channel.id}'})"
        )

        lines.append(
            f"• {username_link} — {channel_link}"
        )

    description = "\n".join(lines)

    if len(description) > 3900:
        description = (
            description[:3890]
            + "\n..."
        )

    embed.add_field(
        name="Missing Vanity",
        value=description
        if description
        else "Everyone has the required status.",
        inline=False,
    )

    return embed


# ============================================================
# ENFORCEMENT BUTTON
# ============================================================

class EnforcementView(
    discord.ui.View
):

    def __init__(
        self,
        guild_id,
    ):

        super().__init__(
            timeout=None
        )

        self.guild_id = guild_id

    @discord.ui.button(
        label="Enforcement Law??",
        style=discord.ButtonStyle.danger,
        custom_id="cupic_enforcement",
    )
    async def enforce(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        if not isinstance(
            interaction.user,
            discord.Member,
        ):
            return

        if not is_admin(
            interaction.user
        ):

            await interaction.response.send_message(
                "Only administrators can use enforcement.",
                ephemeral=True,
            )

            return

        state = db_one(
            """
            SELECT enforcement_stage
            FROM scan_states
            WHERE guild_id = ?
            """,
            (
                interaction.guild.id,
            ),
        )

        stage = (
            int(
                state["enforcement_stage"]
            )
            if state
            else 0
        )

        # ====================================================
        # FIRST CLICK = WARN
        # ====================================================

        if stage == 0:

            slots = db_all(
                """
                SELECT *
                FROM slots
                WHERE guild_id = ?
                AND status IN ('active', 'held')
                """,
                (
                    interaction.guild.id,
                ),
            )

            warned = 0

            for slot in slots:

                member = await get_member(
                    interaction.guild,
                    int(slot["owner_id"]),
                )

                if not member:
                    continue

                if has_cupic_vanity(member):
                    continue

                try:

                    await member.send(
                        embed=discord.Embed(
                            title="⚠️ Cupic Slots Warning",
                            description=(
                                "Your Cupic slot is missing "
                                "the required `.gg/cupicslots` "
                                "vanity in your custom status.\n\n"
                                "Please add it before the next "
                                "enforcement scan."
                            ),
                            color=YELLOW,
                        )
                    )

                    warned += 1

                except (
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    pass

            db_execute(
                """
                INSERT INTO scan_states (
                    guild_id,
                    message_id,
                    enforcement_stage
                )
                VALUES (?, ?, 1)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    message_id = excluded.message_id,
                    enforcement_stage = 1
                """,
                (
                    interaction.guild.id,
                    interaction.message.id,
                ),
            )

            await interaction.response.send_message(
                f"⚠️ Warning sent to **{warned}** seller(s).\n"
                "Press the button again to revoke their slots.",
                ephemeral=True,
            )

            return

        # ====================================================
        # SECOND CLICK = REVOKE + DELETE
        # ====================================================

        slots = db_all(
            """
            SELECT *
            FROM slots
            WHERE guild_id = ?
            AND status IN ('active', 'held')
            """,
            (
                interaction.guild.id,
            ),
        )

        revoked = 0

        for slot in slots:

            member = await get_member(
                interaction.guild,
                int(slot["owner_id"]),
            )

            if not member:
                continue

            if has_cupic_vanity(member):
                continue

            channel = await get_text_channel(
                interaction.guild,
                int(slot["channel_id"]),
            )

            # DM BEFORE deletion.
            try:

                await member.send(
                    embed=discord.Embed(
                        title="🔴 Slot Revoked",
                        description=(
                            "Your Cupic slot has been revoked "
                            "because the required "
                            "`.gg/cupicslots` vanity was not added.\n\n"
                            "You can renew your slot below."
                        ),
                        color=discord.Color.red(),
                    ),
                    view=renewal_view(),
                )

            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                pass

            db_execute(
                """
                UPDATE slots
                SET status = 'revoked'
                WHERE channel_id = ?
                """,
                (
                    slot["channel_id"],
                ),
            )

            if member:

                await remove_slot_role_if_unused(
                    interaction.guild,
                    member,
                    slot["slot_type"],
                    int(slot["channel_id"]),
                )

            if channel:

                try:

                    await channel.delete(
                        reason=(
                            "Cupic enforcement: "
                            "missing vanity"
                        )
                    )

                except (
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    pass

            revoked += 1

        # Reset state after enforcement.
        db_execute(
            """
            UPDATE scan_states
            SET
                enforcement_stage = 0,
                message_id = NULL
            WHERE guild_id = ?
            """,
            (
                interaction.guild.id,
            ),
        )

        await interaction.response.send_message(
            f"🔴 Revoked and deleted **{revoked}** slot(s).",
            ephemeral=True,
        )


# ============================================================
# /SCANALL
# ============================================================

@bot.tree.command(
    name="scanall",
    description="Scan all slot owners for the required vanity",
)
async def scanall(
    interaction,
):

    if not await require_staff(
        interaction
    ):
        return

    await interaction.response.defer(
        ephemeral=True
    )

    slots = db_all(
        """
        SELECT *
        FROM slots
        WHERE guild_id = ?
        AND status IN ('active', 'held')
        """,
        (
            interaction.guild.id,
        ),
    )

    missing = []

    for slot in slots:

        member = await get_member(
            interaction.guild,
            int(slot["owner_id"]),
        )

        channel = await get_text_channel(
            interaction.guild,
            int(slot["channel_id"]),
        )

        if not member or not channel:
            continue

        if not has_cupic_vanity(
            member
        ):

            missing.append(
                {
                    "member": member,
                    "channel": channel,
                }
            )

    db_execute(
        """
        INSERT INTO scan_states (
            guild_id,
            message_id,
            enforcement_stage
        )
        VALUES (?, NULL, 0)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            message_id = NULL,
            enforcement_stage = 0
        """,
        (
            interaction.guild.id,
        ),
    )

    embed = scan_embed(
        missing
    )

    await interaction.followup.send(
        embed=embed,
        view=EnforcementView(
            interaction.guild.id
        ),
        ephemeral=True,
    )


# ============================================================
# /HELP
# ============================================================

@bot.tree.command(
    name="help",
    description="Show Cupic commands",
)
async def help_command(
    interaction,
):

    embed = discord.Embed(
        title="🏅 Cupic Slots",
        description=(
            "### Slot\n"
            "`/slotinfo`\n"
            "`/srules`\n\n"

            "### Staff\n"
            "`/create`\n"
            "`/hold`\n"
            "`/unhold`\n"
            "`/sprice`\n"
            "`/pings`\n"
            "`/find`\n"
            "`/scanall`\n\n"

            "### Automatic\n"
            "• Standard slots use `✨・`\n"
            "• Premium slots use `🏅・`\n"
            "• User/role mentions instantly revoke.\n"
            "• Daily reset occurs at 12 PM IST.\n"
            "• Expired/revoked/held users receive DMs.\n"
            "• Ping counters are preserved when unholding.\n"
            "• `/pings` sets the daily reset announcement channel."
        ),
        color=YELLOW,
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    bot.run(TOKEN)
