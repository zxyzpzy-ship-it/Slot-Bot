import os
import discord
from discord.ext import commands, tasks
import datetime
import json

# =========================
# BOT SETUP
# =========================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=",", intents=intents)
bot.remove_command("help")


# =========================
# CONFIG
# =========================

with open("config.json", "r") as file:
    hmm = json.load(file)

GUILD_ID = int(hmm["guildid"])
CATEGORY_ID = int(hmm["categoryid"])

# Staff role - can manage everything
STAFF_ROLE_ID = 1535194766004846693

# Slot owner roles
PREMIUM_ROLE_ID = 1535194940982820904
STANDARD_ROLE_ID = 1535195023983910942


# =========================
# HELPERS
# =========================

def load_data():
    try:
        with open("data.json", "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_data(data):
    with open("data.json", "w") as file:
        json.dump(data, file, indent=4)


def get_role(guild, role_id):
    return guild.get_role(role_id)


def get_slot_role(guild, slot_type):
    slot_type = slot_type.lower()

    if slot_type == "premium":
        return get_role(guild, PREMIUM_ROLE_ID)

    if slot_type == "standard":
        return get_role(guild, STANDARD_ROLE_ID)

    return None


async def give_slot_role(member, slot_type):
    guild = member.guild

    premium_role = get_role(guild, PREMIUM_ROLE_ID)
    standard_role = get_role(guild, STANDARD_ROLE_ID)

    # Remove both first so a user cannot accidentally have both
    if premium_role and premium_role in member.roles:
        await member.remove_roles(premium_role)

    if standard_role and standard_role in member.roles:
        await member.remove_roles(standard_role)

    role = get_slot_role(guild, slot_type)

    if role:
        await member.add_roles(role)

    return role


async def remove_slot_roles(member):
    premium_role = get_role(member.guild, PREMIUM_ROLE_ID)
    standard_role = get_role(member.guild, STANDARD_ROLE_ID)

    roles = []

    if premium_role and premium_role in member.roles:
        roles.append(premium_role)

    if standard_role and standard_role in member.roles:
        roles.append(standard_role)

    if roles:
        await member.remove_roles(*roles)


def calculate_end_time(amount, unit):
    now = datetime.datetime.now().timestamp()

    unit = unit.lower()

    if unit == "d":
        return now + (amount * 24 * 60 * 60)

    if unit == "m":
        return now + (amount * 30 * 24 * 60 * 60)

    return None


# =========================
# READY
# =========================

@bot.event
async def on_ready():
    print(f"{bot.user} is Ready")

    if not expire.is_running():
        expire.start()


# =========================
# EXPIRE SLOTS
# =========================

@tasks.loop(hours=1)
async def expire():

    data = load_data()
    changed = False

    now = datetime.datetime.now().timestamp()

    for slot in data.copy():

        endtime = int(slot["endtime"])

        if now >= endtime:

            guild = bot.get_guild(GUILD_ID)

            if not guild:
                continue

            channel = bot.get_channel(int(slot["channelid"]))
            member = guild.get_member(int(slot["userid"]))

            if member:

                await remove_slot_roles(member)

            if channel:

                try:
                    await channel.send("⏰ **Slot expired.**")
                except:
                    pass

                if member:
                    try:
                        await channel.set_permissions(
                            member,
                            send_messages=False
                        )
                    except:
                        pass

            data.remove(slot)
            changed = True

    if changed:
        save_data(data)


# =========================
# HELP
# =========================

@bot.command()
async def help(ctx):

    embed = discord.Embed(
        title="Slot Bot Help",
        description=(
            "`,create @user 30 d premium`\n"
            "Create a premium slot.\n\n"

            "`,create @user 30 d standard`\n"
            "Create a standard slot.\n\n"

            "`,add @user #channel`\n"
            "Add a user to a slot.\n\n"

            "`,remove @user #channel`\n"
            "Remove slot permissions.\n\n"

            "`,renew @user #channel 30 d premium`\n"
            "Renew a slot.\n\n"

            "`,revoke @user #channel`\n"
            "Revoke a slot."
        ),
        color=0xFFFF00
    )

    if ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)

    await ctx.send(embed=embed, delete_after=30)


# =========================
# ADD
# =========================

@bot.command()
@commands.has_role(STAFF_ROLE_ID)
async def add(
    ctx,
    member: discord.Member = None,
    channel: discord.TextChannel = None
):

    if member is None:
        await ctx.reply("❌ Member not found.")
        return

    if channel is None:
        await ctx.reply("❌ Channel not found.")
        return

    data = load_data()

    slot_exists = any(
        int(slot["channelid"]) == channel.id
        for slot in data
    )

    if not slot_exists:
        await ctx.reply("❌ Slot not found in database.")
        return

    await channel.set_permissions(
        member,
        view_channel=True,
        send_messages=True,
        mention_everyone=True
    )

    await ctx.reply(
        f"✅ Successfully added {member.mention} to {channel.mention}"
    )


# =========================
# REMOVE
# =========================

@bot.command()
@commands.has_role(STAFF_ROLE_ID)
async def remove(
    ctx,
    member: discord.Member = None,
    channel: discord.TextChannel = None
):

    if member is None:
        await ctx.reply("❌ Member not found.")
        return

    if channel is None:
        await ctx.reply("❌ Channel not found.")
        return

    data = load_data()

    slot_exists = any(
        int(slot["channelid"]) == channel.id
        for slot in data
    )

    if not slot_exists:
        await ctx.reply("❌ Slot not found in database.")
        return

    await channel.set_permissions(
        member,
        send_messages=False,
        mention_everyone=False
    )

    await ctx.reply(
        f"✅ Removed {member.mention} from {channel.mention}"
    )


# =========================
# REVOKE
# =========================

@bot.command()
@commands.has_role(STAFF_ROLE_ID)
async def revoke(
    ctx,
    member: discord.Member = None,
    channel: discord.TextChannel = None
):

    if member is None:
        await ctx.reply("❌ Member not found.")
        return

    if channel is None:
        await ctx.reply("❌ Channel not found.")
        return

    data = load_data()

    slot_exists = any(
        int(slot["channelid"]) == channel.id
        for slot in data
    )

    if not slot_exists:
        await ctx.reply("❌ Slot not found in database.")
        return

    await channel.set_permissions(
        member,
        send_messages=False,
        mention_everyone=False
    )

    await remove_slot_roles(member)

    await ctx.reply(
        f"🚫 Slot revoked for {member.mention}"
    )


# =========================
# CREATE
# =========================

@bot.command()
@commands.has_role(STAFF_ROLE_ID)
async def create(
    ctx,
    member: discord.Member = None,
    amount: int = None,
    unit: str = None,
    slot_type: str = None,
    *,
    channel_name: str = None
):

    if member is None:
        await ctx.reply("❌ User not found.")
        return

    if amount is None:
        await ctx.reply("❌ Enter slot duration.")
        return

    if amount <= 0:
        await ctx.reply("❌ Duration must be greater than 0.")
        return

    if unit is None:
        await ctx.reply("❌ Use `d` for days or `m` for months.")
        return

    if slot_type is None:
        await ctx.reply(
            "❌ Slot type required.\n"
            "Use `premium` or `standard`."
        )
        return

    slot_type = slot_type.lower()

    if slot_type not in ["premium", "standard"]:
        await ctx.reply(
            "❌ Invalid slot type.\n"
            "Use `premium` or `standard`."
        )
        return

    endtime = calculate_end_time(amount, unit)

    if endtime is None:
        await ctx.reply(
            "❌ Invalid duration.\n"
            "Use `d` for days or `m` for months."
        )
        return

    # =========================
    # CHANNEL
    # =========================

    if channel_name is None:
        channel_name = member.display_name

    category = ctx.guild.get_channel(CATEGORY_ID)

    if category is None or not isinstance(
        category,
        discord.CategoryChannel
    ):
        await ctx.reply("❌ Slot category not found.")
        return

    overwrites = {

        ctx.guild.default_role:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False
            ),

        member:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                mention_everyone=True
            )
    }

    channel = await ctx.guild.create_text_channel(
        channel_name,
        category=category,
        overwrites=overwrites
    )

    # =========================
    # GIVE ROLE
    # =========================

    role = await give_slot_role(member, slot_type)

    if role is None:
        await ctx.reply(
            f"⚠️ Slot created, but `{slot_type}` role was not found."
        )

    # =========================
    # RULES EMBED
    # =========================

    embed = discord.Embed(
        title="Slot Rules",
        description="Your Slot Rules *",
        color=0xFFFF00
    )

    if ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)

    await channel.send(embed=embed)

    # =========================
    # SLOT INFO
    # =========================

    embed = discord.Embed(
        description=(
            f"**Slot Owner:** {member.mention}\n"
            f"**Type:** {slot_type.title()}\n"
            f"**End:** <t:{int(endtime)}:R>"
        ),
        color=0xFFFF00
    )

    embed.set_footer(text=ctx.guild.name)
    embed.set_author(name=member.display_name)

    await channel.send(embed=embed)

    # =========================
    # DATABASE
    # =========================

    data = load_data()

    data.append({
        "endtime": endtime,
        "userid": member.id,
        "channelid": channel.id,
        "type": slot_type
    })

    save_data(data)

    await ctx.reply(
        f"✅ Successfully created {slot_type.title()} slot "
        f"{channel.mention}"
    )


# =========================
# RENEW
# =========================

@bot.command()
@commands.has_role(STAFF_ROLE_ID)
async def renew(
    ctx,
    member: discord.Member = None,
    channel: discord.TextChannel = None,
    amount: int = None,
    unit: str = None,
    slot_type: str = None
):

    if member is None:
        await ctx.reply("❌ Member not found.")
        return

    if channel is None:
        await ctx.reply("❌ Channel not found.")
        return

    if amount is None:
        await ctx.reply("❌ Duration missing.")
        return

    if unit is None:
        await ctx.reply("❌ Use `d` or `m`.")
        return

    if slot_type is None:
        await ctx.reply(
            "❌ Slot type required: `premium` or `standard`."
        )
        return

    slot_type = slot_type.lower()

    if slot_type not in ["premium", "standard"]:
        await ctx.reply(
            "❌ Invalid slot type."
        )
        return

    endtime = calculate_end_time(amount, unit)

    if endtime is None:
        await ctx.reply(
            "❌ Invalid duration. Use `d` or `m`."
        )
        return

    data = load_data()

    slot = None

    for item in data:
        if int(item["channelid"]) == channel.id:
            slot = item
            break

    if slot is None:
        await ctx.reply("❌ Slot not found in database.")
        return

    # Update database
    slot["endtime"] = endtime
    slot["userid"] = member.id
    slot["type"] = slot_type

    save_data(data)

    # Permissions
    await channel.set_permissions(
        member,
        view_channel=True,
        send_messages=True,
        mention_everyone=True
    )

    # Update role
    await give_slot_role(member, slot_type)

    # Delete old messages
    try:
        await channel.purge(limit=1000)
    except:
        pass

    # Rules
    embed = discord.Embed(
        title="Slot Rules",
        description="Your Slot Rules",
        color=0xFFFF00
    )

    if ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)

    await channel.send(embed=embed)

    # Slot info
    embed = discord.Embed(
        description=(
            f"**Slot Owner:** {member.mention}\n"
            f"**Type:** {slot_type.title()}\n"
            f"**End:** <t:{int(endtime)}:R>"
        ),
        color=0xFFFF00
    )

    embed.set_footer(text=ctx.guild.name)
    embed.set_author(name=member.display_name)

    await channel.send(embed=embed)

    await ctx.reply(
        f"✅ Successfully renewed {channel.mention} "
        f"as **{slot_type.title()}**"
    )


# =========================
# ERROR HANDLER
# =========================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.MissingRole):
        await ctx.reply(
            "❌ You don't have the required **Staff** role."
        )
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(
            "❌ Missing argument. Use `,help` to see the commands."
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.reply(
            "❌ Invalid argument. Check the user/channel and try again."
        )
        return

    print(f"Command Error: {error}")


# =========================
# TOKEN
# =========================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing"
    )

bot.run(TOKEN)
