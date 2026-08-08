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


# ============================================================
# CONFIG
# ============================================================

STAFF_ROLE_ID = 1535194766004846693
PREMIUM_ROLE_ID = 1535194940982820904
STANDARD_ROLE_ID = 1535195023983910942

DB_FILE = "cupic_slots.db"

IST = ZoneInfo("Asia/Kolkata")

YELLOW = discord.Color.from_rgb(255, 193, 7)
DARK_YELLOW = discord.Color.from_rgb(245, 166, 35)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True


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

db.commit()


def ensure_column(column_name: str, definition: str):
    columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(slots)").fetchall()
    }

    if column_name not in columns:
        db.execute(
            f"ALTER TABLE slots ADD COLUMN {column_name} {definition}"
        )
        db.commit()


ensure_column("price", "TEXT DEFAULT 'Not set'")


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
    return db_one(
        "SELECT * FROM slots WHERE channel_id = ?",
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

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_ts() -> int:
    return int(now_utc().timestamp())


def ist_now() -> datetime:
    return datetime.now(IST)


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


def discord_timestamp(timestamp: float) -> str:
    return f"<t:{int(timestamp)}:R>"


def absolute_timestamp(timestamp: float) -> str:
    return f"<t:{int(timestamp)}:f>"


def reset_marker_for_creation() -> str:

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
        current.date() - timedelta(days=1)
    ).isoformat()


# ============================================================
# GENERAL HELPERS
# ============================================================

def role_mention(role_id: int) -> str:
    return f"<@&{role_id}>"


def role_for_type(
    guild: discord.Guild,
    slot_type: str,
):
    role_id = (
        PREMIUM_ROLE_ID
        if slot_type == "premium"
        else STANDARD_ROLE_ID
    )

    return guild.get_role(role_id)


def bot_member(guild: discord.Guild):
    return guild.me


def is_staff(member: discord.Member) -> bool:

    return (
        member.guild_permissions.administrator
        or any(
            role.id == STAFF_ROLE_ID
            for role in member.roles
        )
    )


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


def can_manage_slot(member: discord.Member, slot) -> bool:

    return (
        is_staff(member)
        or member.id == int(slot["owner_id"])
    )


def slot_display_name(name: str) -> str:
    """
    User enters:
        venela

    Bot creates:
        🏅・venela
    """

    name = name.strip()

    if name.startswith("🏅・"):
        return name[:100]

    return f"🏅・{name}"[:100]


def safe_channel_name(name: str) -> str:
    """
    Discord channel name.

    The requested Cupic format is:
        🏅・venela
    """

    name = name.strip()

    if not name:
        name = "slot"

    if name.startswith("🏅・"):
        return name[:100]

    return f"🏅・{name}"[:100]


async def get_text_channel(
    guild: discord.Guild,
    channel_id: int,
):

    channel = guild.get_channel(channel_id)

    if isinstance(channel, discord.TextChannel):
        return channel

    try:
        fetched = await bot.fetch_channel(channel_id)

        if isinstance(fetched, discord.TextChannel):
            return fetched

    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass

    return None


async def get_member(
    guild: discord.Guild,
    user_id: int,
):

    member = guild.get_member(user_id)

    if member:
        return member

    try:
        return await guild.fetch_member(user_id)

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
    guild: discord.Guild,
    member: discord.Member,
    slot,
    title_override=None,
):

    status = slot["status"].title()

    if status == "Active":
        status_line = "🟢 Active"

    elif status == "Held":
        status_line = "🟠 Held"

    elif status == "Expired":
        status_line = "🔴 Expired"

    else:
        status_line = "🔴 Revoked"

    embed = discord.Embed(
        title=title_override or "🏅 Cupic Slot",
        description=(
            "### Slot Details\n\n"

            f"**Owner**\n"
            f"{member.mention}\n\n"

            f"**Slot**\n"
            f"`{slot_display_name(slot['slot_name'])}`\n\n"

            f"**Type**\n"
            f"`{slot['slot_type'].title()}`\n\n"

            f"**Status**\n"
            f"{status_line}\n\n"

            f"**Created**\n"
            f"{absolute_timestamp(slot['created_at'])} "
            f"({discord_timestamp(slot['created_at'])})\n\n"

            f"**Expires**\n"
            f"{absolute_timestamp(slot['expires_at'])} "
            f"({discord_timestamp(slot['expires_at'])})\n\n"

            f"**Price**\n"
            f"`{slot['price'] or 'Not set'}`\n\n"

            f"**Ping Allowance**\n"
            f"@here / user / role mentions: "
            f"`{int(slot['here_count'])}/{int(slot['here_limit'])}`\n"
            f"@everyone: "
            f"`{int(slot['everyone_count'])}/{int(slot['everyone_limit'])}`\n\n"

            "### Slot Guidelines\n"
            "• Use the slot only for its intended purpose.\n"
            "• Stay within the assigned ping allowance.\n"
            "• Spam or unauthorized usage may result in revocation.\n"
            "• Follow staff instructions and server rules."
        ),
        color=YELLOW,
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    if guild.icon:
        embed.set_footer(
            text="Cupic Slots • Slot Management",
            icon_url=guild.icon.url,
        )
    else:
        embed.set_footer(
            text="Cupic Slots • Slot Management"
        )

    return embed


def rules_embed(guild: discord.Guild):

    embed = discord.Embed(
        title="📜 Slot Rules",
        description=(
            "### Usage Rules\n\n"

            "• Follow all server rules while using your slot.\n"
            "• Only use the ping allowance assigned to your slot.\n"
            "• `@here`, user mentions and role mentions count toward the "
            "`@here` allowance.\n"
            "• `@everyone` is counted separately.\n"
            "• Do not spam, abuse or intentionally bypass the ping limit.\n"
            "• Only the slot owner should advertise through the slot.\n"
            "• Staff may place a slot on hold when necessary.\n"
            "• Repeated abuse may result in the slot being revoked.\n"
            "• Staff decisions regarding slot usage must be followed."
        ),
        color=YELLOW,
    )

    embed.set_footer(
        text="Cupic Slots • Please use your slot responsibly"
    )

    return embed


def status_embed(
    slot,
    member: discord.Member,
    action: str,
):

    labels = {
        "active": ("🟢 Slot Active", YELLOW),
        "held": ("🟠 Slot Held", DARK_YELLOW),
        "expired": ("🔴 Slot Expired", discord.Color.red()),
        "revoked": ("🔴 Slot Revoked", discord.Color.red()),
    }

    title, color = labels.get(
        action,
        ("🏅 Slot Update", YELLOW),
    )

    embed = discord.Embed(
        title=title,
        description=(
            f"**Slot**\n"
            f"`{slot_display_name(slot['slot_name'])}`\n\n"

            f"**Type**\n"
            f"`{slot['slot_type'].title()}`\n\n"

            f"**Expiry**\n"
            f"{discord_timestamp(slot['expires_at'])}"
        ),
        color=color,
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.set_footer(
        text="Cupic Slots"
    )

    return embed


def ping_used_embed(
    slot,
    mention_type: str,
    used: int,
    limit: int,
):

    if mention_type == "here":
        title = "📣 Slot Ping Used"

        description = (
            "**Your slot ping has been recorded.**\n\n"
            f"You have used **{used}/{limit}** "
            "of your allowed slot pings.\n\n"
            "User, role and `@here` mentions count toward "
            "this allowance."
        )

    else:
        title = "📣 Everyone Ping Used"

        description = (
            "**Your slot ping has been recorded.**\n\n"
            f"You have used **{used}/{limit}** "
            "of your allowed `@everyone` pings."
        )

    embed = discord.Embed(
        title=title,
        description=description,
        color=YELLOW,
    )

    embed.set_footer(
        text="Cupic Slots • Ping tracking"
    )

    return embed


def reset_embed():

    embed = discord.Embed(
        title="🔄 Daily Ping Reset",
        description=(
            "### Your slot allowances have been reset.\n\n"
            "All active slots now have their full daily "
            "ping allowance available again.\n\n"
            "The reset occurs automatically every day at "
            "**12:00 PM IST**."
        ),
        color=YELLOW,
    )

    embed.set_footer(
        text="Cupic Slots • Daily reset"
    )

    return embed


def hold_embed(
    slot,
    reason: str,
):

    embed = discord.Embed(
        title="⏸️ Slot Placed On Hold",
        description=(
            f"**Slot**\n"
            f"`{slot_display_name(slot['slot_name'])}`\n\n"

            f"**Status**\n"
            "`Held`\n\n"

            f"**Reason**\n"
            f"{reason}\n\n"

            "The slot owner cannot use the slot while it "
            "is on hold.\n\n"
            "A staff member can restore it with `/unhold`."
        ),
        color=DARK_YELLOW,
    )

    embed.set_footer(
        text="Cupic Slots • Staff action"
    )

    return embed


def unhold_embed(
    slot,
    reason: str,
):

    embed = discord.Embed(
        title="▶️ Slot Released",
        description=(
            f"**Slot**\n"
            f"`{slot_display_name(slot['slot_name'])}`\n\n"

            f"**Status**\n"
            "`Active`\n\n"

            f"**Reason**\n"
            f"{reason}\n\n"

            "The slot is active again and the owner can "
            "use it normally."
        ),
        color=YELLOW,
    )

    embed.set_footer(
        text="Cupic Slots • Staff action"
    )

    return embed


# ============================================================
# RENEWAL BUTTON
# ONLY USED FOR EXPIRED / REVOKED
# ============================================================

def renewal_view(
    guild_id: int,
    channel_id: int,
):

    view = discord.ui.View(
        timeout=None
    )

    button = discord.ui.Button(
        label="Click here to renew",
        style=discord.ButtonStyle.link,
        url=(
            f"https://discord.com/channels/"
            f"{guild_id}/{channel_id}"
        ),
    )

    view.add_item(button)

    return view


# ============================================================
# ROLE MANAGEMENT
# ============================================================

async def add_slot_role(
    guild: discord.Guild,
    owner: discord.Member,
    slot_type: str,
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
            reason=(
                "Cupic Slots: "
                f"{slot_type} slot created/restored"
            ),
        )

        return True

    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
        return False


async def remove_slot_role_if_unused(
    guild: discord.Guild,
    owner: discord.Member,
    slot_type: str,
    current_channel_id: int | None = None,
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
            reason=(
                "Cupic Slots: "
                "no active/held slot of this type"
            ),
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
    channel: discord.TextChannel,
    owner: discord.Member,
    allowed: bool,
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
    guild: discord.Guild,
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

    message_id = slot["details_message_id"]

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
# EXPIRY / REVOKE
# ============================================================

async def revoke_slot(
    guild: discord.Guild,
    slot,
    *,
    action: str,
    reason: str,
    delete_offending_message: discord.Message | None = None,
):

    if (
        slot["status"] in ("expired", "revoked")
        and action in ("expired", "revoked")
    ):
        return

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

        # IMPORTANT:
        # No routine DM.
        # Only expired/revoked gets the renewal button.
        try:

            embed = status_embed(
                slot,
                owner,
                new_status,
            )

            embed.description += (
                "\n\n"
                f"**Reason**\n"
                f"{reason}\n\n"
                "Use the button below only if you want "
                "to continue with renewal."
            )

            await owner.send(
                embed=embed,
                view=renewal_view(
                    int(slot["guild_id"]),
                    int(slot["channel_id"]),
                ),
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    if channel:

        try:

            embed = discord.Embed(
                title=(
                    "🔴 Slot Expired"
                    if new_status == "expired"
                    else "🔴 Slot Revoked"
                ),
                description=(
                    f"**Slot**\n"
                    f"`{slot_display_name(slot['slot_name'])}`\n\n"

                    f"**Reason**\n"
                    f"{reason}\n\n"

                    "The slot is no longer available for use."
                ),
                color=discord.Color.red(),
            )

            embed.set_footer(
                text="Cupic Slots • Slot Management"
            )

            await channel.send(
                embed=embed
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
                    f"commands to guild {GUILD_ID}"
                )

            except discord.Forbidden:

                print(
                    "[ERROR] Slash command sync failed."
                )

                print(
                    "[FIX] Reinstall bot with "
                    "'bot' and 'applications.commands'."
                )

            except discord.HTTPException as exc:

                print(
                    f"[ERROR] Command sync failed: {exc}"
                )

        else:

            try:

                synced = await self.tree.sync()

                print(
                    f"[SYNC] Synced {len(synced)} global commands."
                )

            except discord.HTTPException as exc:

                print(
                    f"[ERROR] Global sync failed: {exc}"
                )

    async def on_ready(self):

        print("--------------------------------")
        print(f"{self.user} is Ready")
        print(
            f"Guild ID: "
            f"{GUILD_ID if GUILD_ID else 'Global'}"
        )
        print(
            "Loaded slots: "
            f"{db_one('SELECT COUNT(*) AS c FROM slots')['c']}"
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

        noon = current_ist.replace(
            hour=12,
            minute=0,
            second=0,
            microsecond=0,
        )

        today = current_ist.strftime(
            "%Y-%m-%d"
        )

        for guild in self.guilds:

            slots = get_active_slots(
                guild.id
            )

            for slot in slots:

                # ====================================================
                # AUTOMATIC DAILY RESET — 12:00 PM IST
                # ====================================================

                if (
                    current_ist >= noon
                    and slot["last_reset_date"] != today
                ):

                    db_execute(
                        """
                        UPDATE slots
                        SET
                            here_count = 0,
                            everyone_count = 0,
                            last_reset_date = ?
                        WHERE channel_id = ?
                        """,
                        (
                            today,
                            slot["channel_id"],
                        ),
                    )

                    slot = get_slot(
                        int(slot["channel_id"])
                    )

                    channel = await get_text_channel(
                        guild,
                        int(slot["channel_id"]),
                    )

                    if channel:

                        role_id = (
                            PREMIUM_ROLE_ID
                            if slot["slot_type"]
                            == "premium"
                            else STANDARD_ROLE_ID
                        )

                        allowed_mentions = discord.AllowedMentions(
                            roles=True,
                            users=False,
                            everyone=False,
                        )

                        try:

                            await channel.send(
                                content=(
                                    f"{role_mention(role_id)}\n"
                                    "### 🔄 Daily Ping Reset\n"
                                    "Your slot ping allowance has been "
                                    "**reset successfully**.\n\n"
                                    "You can now use your full daily "
                                    "ping allowance again."
                                ),
                                embed=reset_embed(),
                                allowed_mentions=allowed_mentions,
                            )

                        except (
                            discord.Forbidden,
                            discord.HTTPException,
                        ):
                            pass

                # ====================================================
                # EXPIRY
                # ====================================================

                if (
                    slot["status"] == "active"
                    and float(slot["expires_at"]) <= current
                ):

                    await revoke_slot(
                        guild,
                        slot,
                        action="expired",
                        reason="The slot reached its expiry time.",
                    )

                    continue

                # ====================================================
                # NO DMS HERE
                #
                # Removed:
                # 24h DM
                # 1h DM
                #
                # User explicitly wanted DM only for
                # revoked/expired slots.
                # ====================================================


bot = CupicBot()


# ============================================================
# MESSAGE / PING TRACKING
# ============================================================

@bot.event
async def on_message(message: discord.Message):

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

    # Only owner consumes ping allowance.
    if message.author.id != int(
        slot["owner_id"]
    ):
        return

    if slot["status"] != "active":
        return

    content = message.content

    # ========================================================
    # PING DETECTION
    #
    # @here                -> HERE allowance
    # @username            -> HERE allowance
    # @role                -> HERE allowance
    #
    # @everyone            -> EVERYONE allowance
    #
    # A message containing multiple user/role mentions still
    # counts as ONE here ping.
    # ========================================================

    has_here = "@here" in content

    has_user_or_role = (
        len(message.mentions) > 0
        or len(message.role_mentions) > 0
    )

    has_here_type_ping = (
        has_here
        or has_user_or_role
    )

    has_everyone = "@everyone" in content

    if (
        not has_here_type_ping
        and not has_everyone
    ):
        return

    new_here = (
        int(slot["here_count"])
        + (
            1
            if has_here_type_ping
            else 0
        )
    )

    new_everyone = (
        int(slot["everyone_count"])
        + (
            1
            if has_everyone
            else 0
        )
    )

    here_bad = (
        has_here_type_ping
        and new_here > int(
            slot["here_limit"]
        )
    )

    everyone_bad = (
        has_everyone
        and new_everyone > int(
            slot["everyone_limit"]
        )
    )

    # ========================================================
    # LIMIT EXCEEDED
    # ========================================================

    if here_bad or everyone_bad:

        reasons = []

        if here_bad:

            reasons.append(
                "The slot exceeded its "
                f"here/user/role ping limit "
                f"({slot['here_limit']} allowed)."
            )

        if everyone_bad:

            reasons.append(
                "The slot exceeded its "
                f"@everyone limit "
                f"({slot['everyone_limit']} allowed)."
            )

        await revoke_slot(
            message.guild,
            slot,
            action="revoked",
            reason=" ".join(reasons),
            delete_offending_message=message,
        )

        return

    # ========================================================
    # SAVE COUNTERS
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
    # PROFESSIONAL CONFIRMATION EMBED
    #
    # No raw:
    # @here 1/2
    #
    # ========================================================

    if has_here_type_ping:

        try:

            embed = ping_used_embed(
                slot,
                "here",
                new_here,
                int(slot["here_limit"]),
            )

            await message.channel.send(
                embed=embed
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    if has_everyone:

        try:

            embed = ping_used_embed(
                slot,
                "everyone",
                new_everyone,
                int(slot["everyone_limit"]),
            )

            await message.channel.send(
                embed=embed
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
    interaction: discord.Interaction,
) -> bool:

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
    interaction: discord.Interaction,
    channel: discord.TextChannel | None,
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
            "Use this command in a slot channel or select a slot channel.",
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
    interaction: discord.Interaction,
    slot,
) -> bool:

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
            "You do not have permission to use this slot command here.",
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
    time="How long the slot should last",
    unit_of_time="Time unit",
    slotname="Slot name",
    typeofslot="Premium or Standard",
    category="Category where the slot channel will be created",
    numberofpings="Allowed @here, user and role mentions",
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

    if not slotname.strip():

        await interaction.response.send_message(
            "Slot name cannot be empty.",
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
            "The configured slot role was not found.",
            ephemeral=True,
        )

        return

    me = interaction.guild.me

    if (
        me
        and role >= me.top_role
        and not me.guild_permissions.administrator
    ):

        await interaction.response.send_message(
            "Move the Cupic Bot role above the Standard/Premium roles.",
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
        slotname
    )

    channel_name = safe_channel_name(
        slotname
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

    if me:

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
            reason=(
                f"Cupic Slots: create {display_name}"
            ),
        )

    except discord.Forbidden:

        await interaction.followup.send(
            "I could not create the slot. "
            "Check Manage Channels and category permissions.",
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
            await channel.delete(
                reason="Cupic Slots: database error"
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

        await interaction.followup.send(
            "The slot could not be saved. No slot was created.",
            ephemeral=True,
        )

        return

    slot = get_slot(
        channel.id
    )

    # Automatic Premium / Standard role.
    role_added = await add_slot_role(
        interaction.guild,
        user,
        slot_type,
    )

    # Details
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

    # NO CREATION DM.
    # User explicitly requested minimal/no DM spam.

    role_note = ""

    if not role_added:
        role_note = (
            "\n⚠️ The slot role could not be assigned. "
            "Check the role hierarchy."
        )

    await interaction.followup.send(
        (
            f"### ✅ Slot Created\n"
            f"**Slot:** {channel.mention}\n"
            f"**Owner:** {user.mention}\n"
            f"**Type:** `{slot_type.title()}`"
            f"{role_note}"
        ),
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
    channel="Slot channel",
    reason="Reason for putting the slot on hold",
)
async def hold(
    interaction: discord.Interaction,
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

    if not reason.strip():

        await interaction.response.send_message(
            "A reason is required.",
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

    target = await get_text_channel(
        interaction.guild,
        int(slot["channel_id"]),
    )

    owner = await get_member(
        interaction.guild,
        int(slot["owner_id"]),
    )

    if owner and target:

        await set_owner_access(
            target,
            owner,
            False,
        )

        await remove_slot_role_if_unused(
            interaction.guild,
            owner,
            slot["slot_type"],
            int(slot["channel_id"]),
        )

        try:

            await target.send(
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
        "### ⏸️ Slot placed on hold\n"
        f"**Slot:** `{slot_display_name(slot['slot_name'])}`\n"
        f"**Reason:** {reason}",
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
    channel="Slot channel",
    reason="Reason for releasing the hold",
)
async def unhold(
    interaction: discord.Interaction,
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
            "This slot is not currently held.",
            ephemeral=True,
        )

        return

    if not reason.strip():

        await interaction.response.send_message(
            "A reason is required.",
            ephemeral=True,
        )

        return

    # IMPORTANT:
    # Unhold can bypass the old expiry/revoked restriction.
    # It is intentionally restoring the held slot.
    db_execute(
        """
        UPDATE slots
        SET
            status = 'active',
            here_count = 0,
            everyone_count = 0,
            notified_24h = 0,
            notified_1h = 0
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
        f"**Slot:** `{slot_display_name(slot['slot_name'])}`\n"
        f"**Reason:** {reason}",
        ephemeral=True,
    )


# ============================================================
# /SLOTS / SLOTINFO
# ============================================================

@bot.tree.command(
    name="slotinfo",
    description="View slot information",
)
@app_commands.describe(
    channel="Slot channel",
)
async def slotinfo(
    interaction: discord.Interaction,
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
            "This channel is not registered as a slot.",
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
            "The slot owner could not be found.",
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
    description="Show the slot rules",
)
async def srules(
    interaction: discord.Interaction,
):

    if not interaction.guild:

        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )

        return

    if not isinstance(
        interaction.channel,
        discord.TextChannel,
    ):

        await interaction.response.send_message(
            "Use this command inside a slot channel.",
            ephemeral=True,
        )

        return

    slot = get_slot(
        interaction.channel.id
    )

    if not slot:

        await interaction.response.send_message(
            "This channel is not registered as a slot.",
            ephemeral=True,
        )

        return

    if not await require_slot_access(
        interaction,
        slot,
    ):
        return

    await interaction.channel.send(
        embed=rules_embed(
            interaction.guild
        )
    )

    await interaction.response.send_message(
        "### 📜 Rules sent\n"
        "The current slot rules have been posted.",
        ephemeral=True,
    )


# ============================================================
# /SPRICE
# ============================================================

@bot.tree.command(
    name="sprice",
    description="Set the price displayed on a slot",
)
@app_commands.describe(
    price="Price to display, for example $5 or ₹400",
    channel="Slot channel",
)
async def sprice(
    interaction: discord.Interaction,
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

    price = price.strip()

    if not price:

        await interaction.response.send_message(
            "Price cannot be empty.",
            ephemeral=True,
        )

        return

    db_execute(
        """
        UPDATE slots
        SET price = ?
        WHERE channel_id = ?
        """,
        (
            price,
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
        "### 💰 Slot price updated\n"
        f"**Price:** `{price}`",
        ephemeral=True,
    )


# ============================================================
# /PINGS
#
# This is NOT required for the automatic reset.
# It manually resets and announces the reset.
# ============================================================

@bot.tree.command(
    name="pings",
    description="Manually reset all active slot pings",
)
async def pings(
    interaction: discord.Interaction,
):

    if not await require_staff(
        interaction
    ):
        return

    today = ist_now().strftime(
        "%Y-%m-%d"
    )

    db_execute(
        """
        UPDATE slots
        SET
            here_count = 0,
            everyone_count = 0,
            last_reset_date = ?
        WHERE guild_id = ?
        AND status = 'active'
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

    await interaction.response.send_message(
        content=(
            f"{role_mention(STANDARD_ROLE_ID)} "
            f"{role_mention(PREMIUM_ROLE_ID)}"
        ),
        embed=reset_embed(),
        allowed_mentions=allowed_mentions,
    )


# ============================================================
# /HELP
# ============================================================

@bot.tree.command(
    name="help",
    description="Show Cupic Slots commands",
)
async def help_command(
    interaction: discord.Interaction,
):

    embed = discord.Embed(
        title="🏅 Cupic Slots",
        description=(
            "### Slot Commands\n"
            "`/slotinfo` — View your slot information.\n"
            "`/srules` — Display the current slot rules.\n\n"

            "### Staff Commands\n"
            "`/create` — Create a new slot.\n"
            "`/hold` — Temporarily disable a slot.\n"
            "`/unhold` — Restore a held slot.\n"
            "`/sprice` — Set the displayed slot price.\n"
            "`/pings` — Manually reset slot pings.\n\n"

            "### Automatic Systems\n"
            "• Daily ping reset at **12:00 PM IST**.\n"
            "• Automatic slot expiry.\n"
            "• Automatic Standard/Premium roles.\n"
            "• User and role mentions count as slot pings.\n"
            "• Renewal button appears only for expired/revoked slots."
        ),
        color=YELLOW,
    )

    embed.set_footer(
        text="Cupic Slots • Management System"
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
