import discord
from discord.ext import commands
from discord.ext.commands import Bot, Context
import asyncio
import os
from dotenv import load_dotenv
from datetime import timedelta
import re
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict
from discord import app_commands

message_deltas = defaultdict(int)  # guild_id -> message count
member_deltas = defaultdict(int)  # guild_id -> member join count

load_dotenv(dotenv_path=".env")
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

async def get_prefix(bot, message):
    """Get the prefix for the guild, with fallback to default."""
    if message.guild is None:
        return ","
    return custom_prefixes.get(message.guild.id, ",")

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)


# Helper: parse user from mention or ID
def get_user_from_arg(guild, arg):
    if arg.startswith("<@") and arg.endswith(">"):
        user_id = int(arg.replace("<@!", "").replace("<@", "").replace(">", ""))
        return guild.get_member(user_id)
    try:
        user_id = int(arg)
        return guild.get_member(user_id)
    except ValueError:
        return None


# In-memory set to track locked channels for this session
locked_channels = set()

# In-memory set to track hidden channels for this session
hidden_channels = set()

# In-memory dict to track hardlock overwrites
hardlock_overwrites = {}

# In-memory dict to track which moderator timed out each user
# Structure: {guild_id: {member_id: moderator_id}}
timeout_moderators = {}

# In-memory dict to track which moderator banned each user
# Structure: {guild_id: {user_id: moderator_id}}
ban_moderators = {}

# In-memory dict to track custom prefixes per guild
# Structure: {guild_id: prefix}
custom_prefixes = {}

# In-memory dict to track sticky reaction roles
# Structure: {guild_id: {message_id: {reaction: role_id}}}
sticky_reaction_roles = {}


# Helper: parse duration string (e.g., 30s, 5m, 2h, 1d)
def parse_time_arg(arg):
    match = re.match(r"^(\d+)([smhd])$", arg)
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == "s":
        return value
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    if unit == "d":
        return value * 86400
    return None


async def send_mod_dm(
    member: discord.Member,
    *,
    moderator: discord.Member,
    action_type: str,
    reason: Optional[str] = None,
    duration: Optional[str] = None,
):
    """Sends a standardized moderation DM embed to a member."""
    guild = moderator.guild
    reason_str = reason or "No reason provided"
    embed = None

    if action_type == "timed_out":
        embed = discord.Embed(
            title="Timed Out", color=0x4C4C54, timestamp=discord.utils.utcnow()
        )
        embed.description = f"**You have been timed out in**\n{guild.name}"
        embed.add_field(name="Moderator", value=moderator.display_name, inline=True)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        embed.add_field(name="Reason", value=reason_str, inline=False)
        embed.set_footer(
            text="If you would like to dispute this punishment, contact a staff member."
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

    elif action_type == "untimeout":
        embed = discord.Embed(
            title="Lifted Timeout", color=0x4C4C54, timestamp=discord.utils.utcnow()
        )
        embed.description = f"**You are no longer timed out in**\n{guild.name}"
        embed.add_field(name="Moderator", value=moderator.display_name, inline=True)
        embed.add_field(name="Reason", value=reason_str, inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)

    elif action_type == "banned":
        embed = discord.Embed(
            title="Banned", color=0x4C4C54, timestamp=discord.utils.utcnow()
        )
        embed.description = f"**You have been banned from**\n{guild.name}"
        embed.add_field(name="Moderator", value=moderator.display_name, inline=True)
        embed.add_field(name="Reason", value=reason_str, inline=False)
        embed.set_footer(
            text="If you would like to dispute this punishment, contact a staff member."
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

    elif action_type == "kicked":
        embed = discord.Embed(
            title="Kicked", color=0x4C4C54, timestamp=discord.utils.utcnow()
        )
        embed.description = f"**You have been kicked from**\n{guild.name}"
        embed.add_field(name="Moderator", value=moderator.display_name, inline=True)
        embed.add_field(name="Reason", value=reason_str, inline=False)
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

    elif action_type == "muted":
        embed = discord.Embed(
            title="Muted", color=0x4C4C54, timestamp=discord.utils.utcnow()
        )
        embed.description = f"**You have been muted in**\n{guild.name}"
        embed.add_field(name="Moderator", value=moderator.display_name, inline=True)
        embed.add_field(name="Reason", value=reason_str, inline=False)
        embed.set_footer(
            text="If you would like to dispute this punishment, contact a staff member."
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

    elif action_type == "unmuted":
        embed = discord.Embed(
            title="Unmuted", color=0x4C4C54, timestamp=discord.utils.utcnow()
        )
        embed.description = f"**You have been unmuted in**\n{guild.name}"
        embed.add_field(name="Moderator", value=moderator.display_name, inline=True)
        embed.add_field(name="Reason", value=reason_str, inline=False)
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

    if embed:
        try:
            await member.send(embed=embed)
            return True
        except discord.Forbidden:
            return False
    return False


# ,purge / ,c / ,clear [amount] or [@user/userID] [amount]
@bot.command(aliases=["c", "clear", "purge"])
@commands.has_permissions(manage_messages=True)
async def purge_or_clear(ctx: Context, *args):
    await ctx.message.add_reaction("👍")
    try:
        await ctx.message.delete(delay=0.5)
    except:
        pass  # Ignore if we can't delete the command message
    member = None
    amount = 50
    
    # Parse arguments
    if not args:
        # Default behavior: delete 50 messages
        deleted = await ctx.channel.purge(limit=amount+1)
        embed = discord.Embed(
            description=f"✅ {ctx.author.mention}: Deleted {len(deleted)-1} messages.",
            color=0x4C4C54,
        )
        success_message = await ctx.send(embed=embed, delete_after=3)
        await success_message.add_reaction("🗑️")
        return
    
    # Check if first arg is a user mention
    if args[0].startswith("<@"):
        member = get_user_from_arg(ctx.guild, args[0])
        if not member:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Could not find the specified user.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        # Check if there's a second argument for amount
        if len(args) > 1 and args[1].isdigit():
            amount = int(args[1])
            if amount <= 0:
                embed = discord.Embed(
                    description=f"⚠️ {ctx.author.mention}: Amount must be greater than 0.",
                    color=0x4C4C54,
                )
                await ctx.send(embed=embed)
                return
            if amount > 100:
                embed = discord.Embed(
                    description=f"⚠️ {ctx.author.mention}: Amount cannot exceed 100 messages.",
                    color=0x4C4C54,
                )
                await ctx.send(embed=embed)
                return
    # Check if first arg is a number (amount)
    elif args[0].isdigit():
        amount = int(args[0])
        if amount <= 0:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Amount must be greater than 0.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        if amount > 100:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Amount cannot exceed 100 messages.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        # Check if there's a second argument for user
        if len(args) > 1:
            if args[1].startswith("<@"):
                member = get_user_from_arg(ctx.guild, args[1])
                if not member:
                    embed = discord.Embed(
                        description=f"⚠️ {ctx.author.mention}: Could not find the specified user.",
                        color=0x4C4C54,
                    )
                    await ctx.send(embed=embed)
                    return
    else:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Invalid arguments. Usage: `,c [amount]` or `,c [@user] [amount]`",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    # Perform deletion
    try:
        if member:
            def is_user(m):
                return m.author == member

            deleted = await ctx.channel.purge(limit=1000, check=is_user)
            deleted = deleted[:amount]
            embed = discord.Embed(
                description=f"✅ {ctx.author.mention}: Deleted {len(deleted)} messages from {member.display_name}.",
                color=0x4C4C54,
            )
        else:
            deleted = await ctx.channel.purge(limit=amount+1)
            embed = discord.Embed(
                description=f"✅ {ctx.author.mention}: Deleted {len(deleted)-1} messages.",
                color=0x4C4C54,
            )
        
        success_message = await ctx.send(embed=embed, delete_after=3)
        await success_message.add_reaction("🗑️")
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to delete messages in this channel.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except Exception:
        pass


# ,nuke
@bot.command()
@commands.has_permissions(manage_channels=True)
async def nuke(ctx: Context):
    channel = ctx.channel
    guild = ctx.guild
    name = channel.name
    position = channel.position
    overwrites = channel.overwrites
    category = channel.category

    await ctx.message.delete()
    # Schedule channel creation before deletion for speed
    async def recreate():
        # Wait for the channel to be deleted
        while guild.get_channel(channel.id) is not None:
            await asyncio.sleep(0.1)
        # Create the new channel
        new_channel = await guild.create_text_channel(
            name,
            overwrites=overwrites,
            category=category,
            position=position,
            reason="Channel nuked"
        )
        # Bot types in the new channel immediately
        async with new_channel.typing():
            await asyncio.sleep(2)

    asyncio.create_task(recreate())
    await channel.delete()


# ,ban or ,b @user [reason] or userID [reason]
@bot.command(aliases=["b"])
@commands.has_permissions(ban_members=True)
async def ban(ctx: Context, user: str, *, reason: str = None):
    member = get_user_from_arg(ctx.guild, user)
    if not member:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Could not find the specified user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return

    await send_mod_dm(
        member,
        moderator=ctx.author,
        action_type="banned",
        reason=reason,
    )

    try:
        await member.ban(reason=reason)
        
        # Track the moderator
        guild_id = ctx.guild.id
        if guild_id not in ban_moderators:
            ban_moderators[guild_id] = {}
        ban_moderators[guild_id][member.id] = ctx.author.id
        
        embed = discord.Embed(
            description=f"{ctx.author.mention}: {member.display_name} has been banned.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🔨")
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I do not have permission to ban this user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to ban: {e}", color=0x4C4C54
        )
        await ctx.send(embed=embed)


@bot.command(aliases=["k"])
@commands.has_permissions(kick_members=True)
async def kick(ctx: Context, user: str, *, reason: str = None):
    """Kicks a member from the server."""
    member = get_user_from_arg(ctx.guild, user)
    if not member:
        await ctx.message.add_reaction("❌")
        return

    dm_sent = await send_mod_dm(
        member,
        moderator=ctx.author,
        action_type="kicked",
        reason=reason,
    )

    try:
        await member.kick(reason=reason)
        await ctx.message.add_reaction("🦶")
    except discord.Forbidden:
        await ctx.message.add_reaction("❌")
    except Exception as e:
        await ctx.message.add_reaction("❌")


# ,timeout or ,to @user [duration] [reason] or userID [duration] [reason]
def parse_duration(duration):
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        if duration[-1] in units:
            return int(duration[:-1]) * units[duration[-1]]
        return int(duration)
    except:
        return None


def format_duration(seconds: int) -> str:
    """Formats seconds into a human-readable string like '1 day 2 hours'."""
    if seconds <= 0:
        return "0 seconds"
    periods = [
        ("day", 86400),
        ("hour", 3600),
        ("minute", 60),
        ("second", 1),
    ]
    parts = []
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            parts.append(
                f"{int(period_value)} {period_name}{'s' if period_value != 1 else ''}"
            )
    return " ".join(parts)


@bot.command(aliases=["to", "t"])
@commands.has_permissions(moderate_members=True)
async def timeout(ctx: Context, user: str, duration: str = None, *, reason: str = None):
    """Mutes the provided member using Discord's timeout feature."""
    if not user:
        embed = discord.Embed(
            description=f"ℹ️ {ctx.author.mention}: Try `,tl` to see all timeouts or `,untimeoutall` to remove them all.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    """Mutes the provided member using Discord's timeout feature."""
    member = get_user_from_arg(ctx.guild, user)
    if not member:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Could not find the specified user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    if duration is None:
        duration = "5m"
    seconds = parse_duration(duration)
    if not seconds or seconds < 1 or seconds > 2419200:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Invalid duration format. Use s, m, h, d (e.g., 10m, 1h).",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You cannot timeout yourself.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    try:
        await member.timeout(
            discord.utils.utcnow() + timedelta(seconds=seconds), reason=reason
        )
        guild_id = ctx.guild.id
        if guild_id not in timeout_moderators:
            timeout_moderators[guild_id] = {}
        timeout_moderators[guild_id][member.id] = ctx.author.id
        formatted_duration = format_duration(seconds)
        await send_mod_dm(
            member,
            moderator=ctx.author,
            action_type="timed_out",
            reason=reason,
            duration=formatted_duration,
        )
        embed = discord.Embed(
            description=f"{ctx.author.mention}: {member.display_name} is now timed out for **{formatted_duration}**",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("⏱️")
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Missing permissions to **timeout** the user, make sure I have the **Moderate Members** permission.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to timeout: {e}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


# ,lock
@bot.command(aliases=["l"])
@commands.has_permissions(manage_channels=True)
async def lock(ctx: Context, *args):
    duration = None
    if args and parse_time_arg(args[0]):
        duration = parse_time_arg(args[0])
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.message.add_reaction("🔒")
    if duration:

        async def auto_unlock():
            await asyncio.sleep(duration)
            overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = True
            await ctx.channel.set_permissions(
                ctx.guild.default_role, overwrite=overwrite
            )
            try:
                
                await ctx.message.add_reaction("🔓")
            except:
                pass

        asyncio.create_task(auto_unlock())


# ,ul / ,unlock
@bot.command(aliases=["ul"])
@commands.has_permissions(manage_channels=True)
async def unlock(ctx: Context):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.message.add_reaction("🔓")


# ,hide / ,h (with 'all' and timed support)
@bot.command(aliases=["h"])
@commands.has_permissions(manage_channels=True)
async def hide(ctx: Context, *args):
    duration = None
    user_mention = ctx.author.mention
    # Timed hide for all channels
    if args and args[0].lower() == "all":
        if len(args) > 1 and parse_time_arg(args[1]):
            duration = parse_time_arg(args[1])
        for channel in ctx.guild.text_channels:
            for role in [ctx.guild.default_role] + list(ctx.guild.roles):
                overwrite = channel.overwrites_for(role)
                if overwrite.view_channel is not False:
                    overwrite.view_channel = False
                    await channel.set_permissions(role, overwrite=overwrite)
            hidden_channels.add(channel.id)
        await ctx.message.add_reaction("🙈")
        
        if duration:

            async def auto_unhide_all():
                await asyncio.sleep(duration)
                for channel in ctx.guild.text_channels:
                    for role in [ctx.guild.default_role] + list(ctx.guild.roles):
                        overwrite = channel.overwrites_for(role)
                        if overwrite.view_channel is False:
                            overwrite.view_channel = None
                            await channel.set_permissions(role, overwrite=overwrite)
                    hidden_channels.discard(channel.id)
                try:
                    
                    await ctx.message.add_reaction("🙉")
                except:
                    pass

            asyncio.create_task(auto_unhide_all())
        return
    # Timed hide for single channel
    if args and parse_time_arg(args[0]):
        duration = parse_time_arg(args[0])
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.message.add_reaction("🙈")
    
    if duration:

        async def auto_unhide():
            await asyncio.sleep(duration)
            overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
            overwrite.view_channel = True
            await ctx.channel.set_permissions(
                ctx.guild.default_role, overwrite=overwrite
            )
            try:
                
                await ctx.message.add_reaction("🙉")
            except:
                pass

        asyncio.create_task(auto_unhide())


# ,unhide / ,uh (with 'all' support)
@bot.command(aliases=["uh"])
@commands.has_permissions(manage_channels=True)
async def unhide(ctx: Context, *args):
    if args and args[0].lower() == "all":
        # Unhide all channels logic
        for channel in ctx.guild.text_channels:
            for role in [ctx.guild.default_role] + list(ctx.guild.roles):
                overwrite = channel.overwrites_for(role)
                if overwrite.view_channel is False:
                    overwrite.view_channel = None
                    await channel.set_permissions(role, overwrite=overwrite)
            hidden_channels.discard(channel.id)
        await ctx.message.add_reaction("🙉")
        
        return
    # Single channel unhide
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.message.add_reaction("🙉")


# ,lock all
@bot.command()
@commands.has_permissions(manage_channels=True)
async def lockall(ctx: Context, *args):
    duration = None
    if args and parse_time_arg(args[-1]):
        duration = parse_time_arg(args[-1])
    for channel in ctx.guild.text_channels:
        for role in [ctx.guild.default_role] + list(ctx.guild.roles):
            overwrite = channel.overwrites_for(role)
            if overwrite.send_messages is not False:
                overwrite.send_messages = False
                await channel.set_permissions(role, overwrite=overwrite)
        locked_channels.add(channel.id)
    await ctx.message.add_reaction("🔒")
    
    if duration:

        async def auto_unlock_all():
            await asyncio.sleep(duration)
            for channel in ctx.guild.text_channels:
                if channel.id in locked_channels:
                    for role in [ctx.guild.default_role] + list(ctx.guild.roles):
                        overwrite = channel.overwrites_for(role)
                        if overwrite.send_messages is False:
                            overwrite.send_messages = None
                            await channel.set_permissions(role, overwrite=overwrite)
                    locked_channels.remove(channel.id)
            try:
                
                await ctx.message.add_reaction("🔓")
            except:
                pass

        asyncio.create_task(auto_unlock_all())


# ,unlock all / ,ul all
@bot.command(aliases=["ulall", "ul_all", "unlock_all", "ula", "ua"])
@commands.has_permissions(manage_channels=True)
async def unlockall(ctx: Context):
    for channel in ctx.guild.text_channels:
        if channel.id in locked_channels:
            for role in [ctx.guild.default_role] + list(ctx.guild.roles):
                overwrite = channel.overwrites_for(role)
                if overwrite.send_messages is False:
                    overwrite.send_messages = None
                    await channel.set_permissions(role, overwrite=overwrite)
            locked_channels.remove(channel.id)
    await ctx.message.add_reaction("🔓")


# ,unlockall <category_id> / ,ua <category_id>
@bot.command(aliases=["uac"])
@commands.has_permissions(manage_channels=True)
async def unlockall_category(ctx: Context, category_id: str):
    """Unlocks all voice channels in the specified category."""
    try:
        category_id = int(category_id)
        category = ctx.guild.get_channel(category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Could not find the specified category.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        voice_channels = [c for c in category.channels if isinstance(c, discord.VoiceChannel)]
        if not voice_channels:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: No voice channels found in the specified category.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        unlocked_count = 0
        for channel in voice_channels:
            for role in [ctx.guild.default_role] + list(ctx.guild.roles):
                overwrite = channel.overwrites_for(role)
                if overwrite.connect is False:
                    overwrite.connect = None
                    await channel.set_permissions(role, overwrite=overwrite)
                    unlocked_count += 1
        
        embed = discord.Embed(
            description=f"✅ Successfully unlocked {unlocked_count} voice channels in {category.name}.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except ValueError:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Invalid category ID. Please provide a valid number.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to unlock channels: {e}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


# ,hide all / ,hall / ,h_all
@bot.command(aliases=["hall", "h_all"])
@commands.has_permissions(manage_channels=True)
async def hideall(ctx: Context):
    for channel in ctx.guild.text_channels:
        for role in [ctx.guild.default_role] + list(ctx.guild.roles):
            overwrite = channel.overwrites_for(role)
            if overwrite.view_channel is not False:
                overwrite.view_channel = False
                await channel.set_permissions(role, overwrite=overwrite)
        hidden_channels.add(channel.id)
    await ctx.message.add_reaction("🙈")


# ,unhide all / ,uh all
@bot.command(aliases=["uhall", "uh_all", "unhide_all", "reveal"])
@commands.has_permissions(manage_channels=True)
async def unhideall(ctx: Context):
    for channel in ctx.guild.text_channels:
        if channel.id in hidden_channels:
            for role in [ctx.guild.default_role] + list(ctx.guild.roles):
                overwrite = channel.overwrites_for(role)
                if overwrite.view_channel is False:
                    overwrite.view_channel = None
                    await channel.set_permissions(role, overwrite=overwrite)
            hidden_channels.remove(channel.id)
    await ctx.message.add_reaction("🙉")


@bot.command(aliases=["hl"])
@commands.has_permissions(manage_channels=True)
async def hardlock(ctx: Context):
    channel = ctx.channel
    perms_to_lock = [
        "send_messages",
        "send_messages_in_threads",
        "create_public_threads",
        "create_private_threads",
    ]

    # Save the current overwrites
    if channel.id not in hardlock_overwrites:
        hardlock_overwrites[channel.id] = {}

    # Lock @everyone
    everyone = ctx.guild.default_role
    current_everyone_overwrite = channel.overwrites_for(everyone)
    hardlock_overwrites[channel.id][f"role_{everyone.id}"] = copy.deepcopy(current_everyone_overwrite)
    for perm in perms_to_lock:
        setattr(current_everyone_overwrite, perm, False)
    await channel.set_permissions(everyone, overwrite=current_everyone_overwrite)

    # Lock all roles
    for role in ctx.guild.roles:
        if role == everyone:
            continue
        current_overwrite = channel.overwrites_for(role)
        hardlock_overwrites[channel.id][f"role_{role.id}"] = copy.deepcopy(current_overwrite)
        for perm in perms_to_lock:
            setattr(current_overwrite, perm, False)
        await channel.set_permissions(role, overwrite=current_overwrite)

    # Lock all members with overrides
    for member in channel.overwrites:
        if isinstance(member, discord.Member):
            current_overwrite = channel.overwrites_for(member)
            hardlock_overwrites[channel.id][f"member_{member.id}"] = copy.deepcopy(current_overwrite)
            for perm in perms_to_lock:
                setattr(current_overwrite, perm, False)
            await channel.set_permissions(member, overwrite=current_overwrite)

    await ctx.message.add_reaction("🔒")


@bot.command(aliases=["uhl"])
@commands.has_permissions(manage_channels=True)
async def unhardlock(ctx: Context):
    channel = ctx.channel

    if channel.id not in hardlock_overwrites:
        await ctx.send("🔓 This channel hasn't been hardlocked before.")
        return

    # Restore original overwrites
    restored_count = 0
    for key, old_overwrite in hardlock_overwrites[channel.id].items():
        if key.startswith("role_"):
            role_id = int(key.split("_")[1])
            role = ctx.guild.get_role(role_id)
            if role:
                await channel.set_permissions(role, overwrite=old_overwrite)
                restored_count += 1

        elif key.startswith("member_"):
            member_id = int(key.split("_")[1])
            member = ctx.guild.get_member(member_id)
            if member:
                await channel.set_permissions(member, overwrite=old_overwrite)
                restored_count += 1

    # Clean up
    del hardlock_overwrites[channel.id]

    await ctx.message.add_reaction("🔓")


class TimeoutListView(discord.ui.View):
    def __init__(self, ctx, timed_out_members, timeout_moderators, per_page=3):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.timed_out_members = timed_out_members
        self.timeout_moderators = timeout_moderators
        self.per_page = per_page
        self.page = 0
        self.max_page = math.ceil(len(timed_out_members) / per_page) - 1
        self.message: Optional[discord.Message] = None
        self.update_view()

    def update_view(self):
        self.clear_items()

        start = self.page * self.per_page
        end = start + self.per_page
        members_on_page = self.timed_out_members[start:end]

        for i, member in enumerate(members_on_page, start + 1):
            guild_id = self.ctx.guild.id
            mod_id = self.timeout_moderators.get(guild_id, {}).get(member.id)
            if mod_id:  # Only add Untimeout button if not manual
                row = (i - 1 - start) // 5
                self.add_item(self.UntimeoutButton(member, i, row))

        nav_row = math.ceil(len(members_on_page) / 5)
        if self.max_page > 0:
            self.add_item(self.PrevButton(self, row=nav_row))
            self.add_item(self.NextButton(self, row=nav_row))

        # Add Untimeout All button if there are members to untimeout
        if self.timed_out_members:
            self.add_item(self.UntimeoutAllButton(self, row=nav_row))

    async def refresh_and_respond(self, interaction: discord.Interaction):
        timed_out_members = []
        now = datetime.now(timezone.utc)
        for member in self.ctx.guild.members:
            if member.timed_out_until and member.timed_out_until > now:
                timed_out_members.append(member)

        self.timed_out_members = sorted(
            timed_out_members, key=lambda m: m.timed_out_until
        )

        self.max_page = math.ceil(len(self.timed_out_members) / self.per_page) - 1
        if self.page > self.max_page:
            self.page = self.max_page if self.max_page >= 0 else 0

        if not self.timed_out_members:
            embed = discord.Embed(
                description="✅ All members have been untimed out.",
                color=discord.Color.green(),
            )
            await interaction.message.edit(embed=embed, view=None)
            self.stop()
            return

        self.update_view()
        embed = self.make_embed()
        await interaction.message.edit(embed=embed, view=self)

    def make_embed(self):
        now = datetime.now(timezone.utc)
        start = self.page * self.per_page
        end = start + self.per_page
        members = self.timed_out_members[start:end]
        lines = []
        for i, member in enumerate(members, start + 1):
            remaining = member.timed_out_until - now
            minutes = int(remaining.total_seconds() // 60)
            seconds = int(remaining.total_seconds() % 60)
            time_str = (
                f"{minutes} minute{'s' if minutes != 1 else ''}"
                if minutes > 0
                else f"{seconds} second{'s' if seconds != 1 else ''}"
            )

            guild_id = self.ctx.guild.id
            mod_id = self.timeout_moderators.get(guild_id, {}).get(member.id)
            mod_str = f"by <@{mod_id}>" if mod_id else "manually"

            lines.append(
                f"`{i}.` {member.mention} expires in **{time_str}** (timed out {mod_str})"
            )

        embed = discord.Embed(
            title="Timed Out Members",
            description="\n".join(lines),
            color=0x4C4C54,
        )
        embed.set_footer(
            text=f"Page {self.page+1}/{self.max_page+1} ({len(self.timed_out_members)} entr{'y' if len(self.timed_out_members)==1 else 'ies'})"
        )
        return embed

    class UntimeoutButton(discord.ui.Button):
        def __init__(self, member, list_number, row):
            super().__init__(
                style=discord.ButtonStyle.secondary,
                label=f"Untimeout {list_number}",
                custom_id=f"untimeout_{member.id}",
                row=row,
            )
            self.member_id = member.id

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            if not interaction.user.guild_permissions.moderate_members:
                await interaction.response.send_message(
                    "You don't have permission to untimeout members.", ephemeral=True
                )
                return

            member = view.ctx.guild.get_member(self.member_id)
            if not member:
                await interaction.response.send_message(
                    "User not found.", ephemeral=True
                )
                await view.refresh_and_respond(interaction)
                return

            try:
                await member.timeout(
                    None, reason=f"Untimed out by {interaction.user.name}"
                )
                await send_mod_dm(
                    member,
                    moderator=interaction.user,
                    action_type="untimeout",
                    reason=f"Manually by {interaction.user.name}",
                )
                await interaction.response.send_message(
                    f"Untimed out {member.mention}.", ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to untimeout that user.", ephemeral=True
                )
                return
            except Exception as e:
                await interaction.response.send_message(
                    f"An error occurred: {e}", ephemeral=True
                )
                return

            await view.refresh_and_respond(interaction)

    class UntimeoutAllButton(discord.ui.Button):
        def __init__(self, view, row):
            super().__init__(
                style=discord.ButtonStyle.danger, label="Untimeout All", row=row
            )

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            if not interaction.user.guild_permissions.moderate_members:
                await interaction.response.send_message(
                    "You don't have permission to untimeout members.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)

            errors = []
            success_count = 0

            tasks = []
            for member in view.timed_out_members:
                tasks.append(
                    member.timeout(
                        None,
                        reason=f"Untimed out by {interaction.user.name} via Untimeout All",
                    )
                )

            dm_tasks = []
            for member in view.timed_out_members:
                dm_tasks.append(
                    send_mod_dm(
                        member,
                        moderator=interaction.user,
                        action_type="untimeout",
                        reason=f"Untimed out by {interaction.user.name} via Untimeout All",
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*dm_tasks)  # Send DMs concurrently

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    errors.append(
                        f"Failed to untimeout {view.timed_out_members[i].mention}: {result}"
                    )
                else:
                    success_count += 1

            if errors:
                await interaction.followup.send(
                    f"Untimed out {success_count} members. The following errors occurred:\n"
                    + "\n".join(errors),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"Successfully untimed out all {success_count} members.",
                    ephemeral=True,
                )

            await view.refresh_and_respond(interaction)

    class NextButton(discord.ui.Button):
        def __init__(self, view, row):
            super().__init__(style=discord.ButtonStyle.primary, label="Next ▶️", row=row)

        async def callback(self, interaction):
            if self.view.page < self.view.max_page:
                self.view.page += 1
                self.view.update_view()
                await interaction.response.edit_message(
                    embed=self.view.make_embed(), view=self.view
                )

    class PrevButton(discord.ui.Button):
        def __init__(self, view, row):
            super().__init__(style=discord.ButtonStyle.primary, label="◀️ Prev", row=row)

        async def callback(self, interaction):
            if self.view.page > 0:
                self.view.page -= 1
                self.view.update_view()
                await interaction.response.edit_message(
                    embed=self.view.make_embed(), view=self.view
                )


class BanListView(discord.ui.View):
    def __init__(self, ctx, banned_users, ban_moderators, per_page=3):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.banned_users = banned_users
        self.ban_moderators = ban_moderators
        self.per_page = per_page
        self.page = 0
        self.max_page = math.ceil(len(banned_users) / per_page) - 1
        self.message: Optional[discord.Message] = None
        self.update_view()

    def update_view(self):
        self.clear_items()

        start = self.page * self.per_page
        end = start + self.per_page
        bans_on_page = self.banned_users[start:end]

        for i, ban_entry in enumerate(bans_on_page, start + 1):
            guild_id = self.ctx.guild.id
            mod_id = self.ban_moderators.get(guild_id, {}).get(ban_entry.user.id)
            if mod_id:  # Only add Unban button if not manual
                row = (i - 1 - start) // 5
                self.add_item(self.UnbanButton(ban_entry.user, i, row))

        nav_row = math.ceil(len(bans_on_page) / 5)
        if self.max_page > 0:
            self.add_item(self.PrevButton(self, row=nav_row))
            self.add_item(self.NextButton(self, row=nav_row))

        # Add Unban All button if there are bans to remove
        if self.banned_users:
            self.add_item(self.UnbanAllButton(self, row=nav_row))

    async def refresh_and_respond(self, interaction: discord.Interaction):
        banned_users = []
        try:
            async for ban_entry in self.ctx.guild.bans():
                banned_users.append(ban_entry)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to view bans!", ephemeral=True)
            return

        self.banned_users = banned_users
        self.max_page = math.ceil(len(self.banned_users) / self.per_page) - 1
        if self.page > self.max_page:
            self.page = self.max_page if self.max_page >= 0 else 0

        if not self.banned_users:
            embed = discord.Embed(
                description="✅ No banned users found.",
                color=discord.Color.green(),
            )
            await interaction.message.edit(embed=embed, view=None)
            self.stop()
            return

        self.update_view()
        embed = self.make_embed()
        await interaction.message.edit(embed=embed, view=self)

    def make_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        bans = self.banned_users[start:end]
        lines = []
        for i, ban_entry in enumerate(bans, start + 1):
            user = ban_entry.user
            reason = ban_entry.reason or "No reason provided"

            guild_id = self.ctx.guild.id
            mod_id = self.ban_moderators.get(guild_id, {}).get(user.id)
            mod_str = f"by <@{mod_id}>" if mod_id else "manually"

            lines.append(
                f"`{i}.` {user.mention} - **{reason}** (banned {mod_str})"
            )

        embed = discord.Embed(
            title="Banned Users",
            description="\n".join(lines),
            color=0xFF0000,
        )
        embed.set_footer(
            text=f"Page {self.page+1}/{self.max_page+1} ({len(self.banned_users)} entr{'y' if len(self.banned_users)==1 else 'ies'})"
        )
        return embed

    class UnbanButton(discord.ui.Button):
        def __init__(self, user, list_number, row):
            super().__init__(
                style=discord.ButtonStyle.secondary,
                label=f"Unban {list_number}",
                custom_id=f"unban_{user.id}",
                row=row,
            )
            self.user_id = user.id

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            if not interaction.user.guild_permissions.ban_members:
                await interaction.response.send_message(
                    "You don't have permission to unban users.", ephemeral=True
                )
                return

            user = discord.Object(self.user_id)
            try:
                await interaction.guild.unban(user, reason=f"Unbanned by {interaction.user.name}")
                await interaction.response.send_message(
                    f"Unbanned user with ID {self.user_id}.", ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to unban that user.", ephemeral=True
                )
                return
            except Exception as e:
                await interaction.response.send_message(
                    f"An error occurred: {e}", ephemeral=True
                )
                return

            await view.refresh_and_respond(interaction)

    class UnbanAllButton(discord.ui.Button):
        def __init__(self, view, row):
            super().__init__(
                style=discord.ButtonStyle.danger, label="Unban All", row=row
            )

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            if not interaction.user.guild_permissions.ban_members:
                await interaction.response.send_message(
                    "You don't have permission to unban users.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)

            errors = []
            success_count = 0

            tasks = []
            for ban_entry in view.banned_users:
                tasks.append(
                    interaction.guild.unban(
                        ban_entry.user,
                        reason=f"Unbanned by {interaction.user.name} via Unban All",
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    errors.append(
                        f"Failed to unban {view.banned_users[i].user.mention}: {result}"
                    )
                else:
                    success_count += 1

            if errors:
                await interaction.followup.send(
                    f"Unbanned {success_count} users. The following errors occurred:\n"
                    + "\n".join(errors),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"Successfully unbanned all {success_count} users.",
                    ephemeral=True,
                )

            await view.refresh_and_respond(interaction)

    class NextButton(discord.ui.Button):
        def __init__(self, view, row):
            super().__init__(style=discord.ButtonStyle.primary, label="Next ▶️", row=row)

        async def callback(self, interaction):
            if self.view.page < self.view.max_page:
                self.view.page += 1
                self.view.update_view()
                await interaction.response.edit_message(
                    embed=self.view.make_embed(), view=self.view
                )

    class PrevButton(discord.ui.Button):
        def __init__(self, view, row):
            super().__init__(style=discord.ButtonStyle.primary, label="◀️ Prev", row=row)

        async def callback(self, interaction):
            if self.view.page > 0:
                self.view.page -= 1
                self.view.update_view()
                await interaction.response.edit_message(
                    embed=self.view.make_embed(), view=self.view
                )


@bot.command(aliases=["to list", "timeouts", "timeouts list", "tl", "timeoutlist"])
@commands.has_permissions(moderate_members=True)
async def timeout_list(ctx: Context):
    timed_out_members = []
    now = datetime.now(timezone.utc)
    for member in ctx.guild.members:
        if member.timed_out_until and member.timed_out_until > now:
            timed_out_members.append(member)
    if not timed_out_members:
        embed = discord.Embed(
            description=f"🔎 {ctx.author.mention}: **No members are currently timed out!**",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    view = TimeoutListView(ctx, timed_out_members, timeout_moderators)
    embed = view.make_embed()
    await ctx.send(embed=embed, view=view)
    await ctx.message.add_reaction("📋")


class PaginatedEmbedView(discord.ui.View):
    def __init__(
        self, ctx, entries, title, field_name, per_page=10, entry_formatter=None
    ):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.entries = entries
        self.title = title
        self.field_name = field_name
        self.per_page = per_page
        self.page = 0
        self.max_page = max(0, (len(entries) - 1) // per_page)
        self.entry_formatter = entry_formatter or (lambda i, e: str(e))
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        if self.max_page > 0:
            self.add_item(self.PrevButton(self))
            self.add_item(self.NextButton(self))

    def make_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        entries = self.entries[start:end]
        lines = [
            self.entry_formatter(i + 1 + start, entry)
            for i, entry in enumerate(entries)
        ]
        embed = discord.Embed(title=self.title, color=0x4C4C54)
        embed.add_field(
            name=self.field_name,
            value="\n".join(lines) if lines else "No entries.",
            inline=False,
        )
        embed.set_footer(
            text=f"Page {self.page+1}/{self.max_page+1} ({len(self.entries)} entr{'y' if len(self.entries)==1 else 'ies'})"
        )
        return embed

    class NextButton(discord.ui.Button):
        def __init__(self, view):
            super().__init__(style=discord.ButtonStyle.primary, label="Next ▶️")
            self.view = view

        async def callback(self, interaction):
            if self.view.page < self.view.max_page:
                self.view.page += 1
                self.view.update_buttons()
                await interaction.response.edit_message(
                    embed=self.view.make_embed(), view=self.view
                )

    class PrevButton(discord.ui.Button):
        def __init__(self, view):
            super().__init__(style=discord.ButtonStyle.primary, label="◀️ Prev")
            self.view = view

        async def callback(self, interaction):
            if self.view.page > 0:
                self.view.page -= 1
                self.view.update_buttons()
                await interaction.response.edit_message(
                    embed=self.view.make_embed(), view=self.view
                )


@bot.command(aliases=["roles"])
async def inrole(ctx: Context, *, role: discord.Role = None):
    """Lists all members in the specified role."""
    if not role:
        await ctx.send("Please specify a role.")
        return
    members = [m for m in role.members]

    def fmt(i, m):
        return f"{i} {m.mention}{' (you)' if m.id == ctx.author.id else ''}"

    view = PaginatedEmbedView(
        ctx, members, f"Members in {role.name}", "", entry_formatter=fmt
    )
    embed = view.make_embed()
    await ctx.send(embed=embed, view=view)





@bot.command(aliases=["avatar", "pfp"])
async def av(ctx: Context, member: discord.Member = None):
    """Shows the avatar of a user."""
    member = member or ctx.author
    avatar_url = member.display_avatar.replace(size=1024).url
    embed = discord.Embed(
        description=f"[{member.display_name}'s avatar]({avatar_url})",
        color=0x4C4C54,
    )
    embed.set_image(url=avatar_url)
    await ctx.send(embed=embed)
    await ctx.message.add_reaction("👤")


@bot.command(aliases=["userinfo", "whois", "info"])
async def ui(ctx: Context, member: discord.Member = None):
    """Shows user info in a styled embed."""
    member = member or ctx.author
    now = discord.utils.utcnow()
    created = member.created_at
    joined = member.joined_at or now
    roles = [r for r in member.roles if r != ctx.guild.default_role]
    roles_str = ", ".join(r.mention for r in roles) if roles else "None"
    # Join position
    members_sorted = sorted(ctx.guild.members, key=lambda m: m.joined_at or now)
    join_pos = members_sorted.index(member) + 1 if member in members_sorted else "?"
    # Mutual servers
    mutuals = sum(1 for g in ctx.bot.guilds if g.get_member(member.id))
    embed = discord.Embed(
        description=f"**{member.display_name}** ({member.id})",
        color=0x4C4C54,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    # Dates
    created_fmt = created.strftime("%m/%d/%Y, %I:%M %p")
    joined_fmt = joined.strftime("%m/%d/%Y, %I:%M %p")
    # Use relativedelta for better humanized output
    created_delta = relativedelta(now, created)
    joined_delta = relativedelta(now, joined)

    def humanize(rd):
        if rd.years > 0:
            return f"{rd.years} year{'s' if rd.years != 1 else ''} ago"
        elif rd.months > 0:
            return f"{rd.months} month{'s' if rd.months != 1 else ''} ago"
        else:
            days = rd.days
            return f"{days} day{'s' if days != 1 else ''} ago"

    embed.add_field(
        name="Dates",
        value=f"**Created:** {created_fmt} ({humanize(created_delta)})\n"
        f"**Joined:** {joined_fmt} ({humanize(joined_delta)})",
        inline=False,
    )
    embed.add_field(name=f"Roles ({len(roles)})", value=roles_str, inline=False)
    embed.set_footer(
        text=f"Join position: {join_pos} • {mutuals} mutual server{'s' if mutuals != 1 else ''}"
    )
    await ctx.send(embed=embed)
    await ctx.message.add_reaction("👤")


@bot.command(aliases=["guildinfo", "si"])
async def serverinfo(ctx: Context):
    """Shows server info in a styled embed."""
    g = ctx.guild
    now = discord.utils.utcnow()
    created_fmt = g.created_at.strftime("%B %d, %Y")
    created_delta = (now - g.created_at).days
    owner = g.owner.mention if g.owner else "Unknown"
    # Members
    total = g.member_count
    humans = len([m for m in g.members if not m.bot])
    bots = total - humans
    # Channels
    text_ch = len([c for c in g.text_channels])
    voice_ch = len([c for c in g.voice_channels])
    cat_ch = len([c for c in g.categories])
    # Roles
    roles = len(g.roles)
    # Emojis
    emojis = len(g.emojis)
    # Boosts
    boosts = g.premium_subscription_count or 0
    boost_level = g.premium_tier
    # Verification
    verification = str(g.verification_level).capitalize()
    # Splash, Banner, Icon
    splash = g.splash.url if g.splash else "N/A"
    banner = g.banner.url if g.banner else "N/A"
    icon = f"[Click here]({g.icon.url})" if g.icon else "N/A"
    # Shard info (if available)
    if (
        hasattr(g, "shard_id")
        and g.shard_id is not None
        and ctx.bot.shard_count is not None
    ):
        shard = f"{g.shard_id + 1}/{ctx.bot.shard_count}"
    else:
        shard = "1/1"

    embed = discord.Embed(
        description=f"**{g.name}**\n\nServer created on {created_fmt} ({created_delta} day{'s' if created_delta != 1 else ''} ago)\n"
        f"{g.name} is on bot shard ID: **{shard}**",
        color=0x4C4C54,
        timestamp=now,
    )
    embed.set_author(
        name=g.name, icon_url=g.icon.url if g.icon else None
    )
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner", value=owner, inline=True)
    embed.add_field(
        name="Members",
        value=f"Total: {total}\nHumans: {humans}\nBots: {bots}",
        inline=True,
    )
    embed.add_field(
        name="Information",
        value=f"Verification: {verification}\nBoosts: {boosts} (level {boost_level})",
        inline=True,
    )
    embed.add_field(
        name="Design",
        value=f"Splash: {splash}\nBanner: {banner}\nIcon: {icon}",
        inline=True,
    )
    embed.add_field(
        name=f"Channels ({text_ch+voice_ch+cat_ch})",
        value=f"Text: {text_ch}\nVoice: {voice_ch}\nCategory: {cat_ch}",
        inline=True,
    )
    embed.add_field(
        name="Counts",
        value=f"Roles: {roles}/250\nEmojis: {emojis}/100\nBoosters: {boosts}",
        inline=True,
    )
    embed.set_footer(text=f"Guild ID: {g.id} • Today at {now.strftime('%I:%M %p')}")
    await ctx.send(embed=embed)
    await ctx.message.add_reaction("📋")


@bot.command()
async def mc(ctx: Context):
    """Shows server statistics in a styled embed."""
    g = ctx.guild
    total = g.member_count
    humans = len([m for m in g.members if not m.bot])
    bots = total - humans
    msg_delta = message_deltas[g.id]
    mem_delta = member_deltas[g.id]
    embed = discord.Embed(color=0x4C4C54)  # Discord dark theme color
    embed.add_field(name="Users", value=f"**{total}**", inline=True)
    embed.add_field(name="Humans", value=f"**{humans}**", inline=True)
    embed.add_field(name="Bots", value=f"**{bots}**", inline=True)
    if g.icon:
        embed.set_author(name="nova statistics", icon_url=g.icon.url)
    else:
        embed.set_author(name="nova statistics")
    await ctx.send(embed=embed)


@bot.command(aliases=["uto"])
@commands.has_permissions(moderate_members=True)
async def untimeout(ctx: Context, user: str):
    """Removes timeout from a user."""
    member = get_user_from_arg(ctx.guild, user)
    if not member:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Could not find the specified user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    if not member.timed_out_until or member.timed_out_until < discord.utils.utcnow():
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: That user is not currently timed out.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    try:
        await member.timeout(None, reason=f"Untimed out by {ctx.author.display_name}")
        # Send DM to user
        await send_mod_dm(
            member,
            moderator=ctx.author,
            action_type="untimeout",
            reason=f"Manually by {ctx.author.display_name}",
        )
        embed = discord.Embed(
            description=f"{ctx.author.mention}: {member.display_name} is no longer timed out.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Missing permissions to untimeout the user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to untimeout: {e}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command(aliases=["uta", "untimeout_all", "massuntime", "mass untime", "mass untimeout", "massuntimeout"])
@commands.has_permissions(moderate_members=True)
async def untimeoutall(ctx: Context):
    """Removes timeout from all currently timed out users in the server."""
    now = datetime.now(timezone.utc)
    timed_out_members = [m for m in ctx.guild.members if m.timed_out_until and m.timed_out_until > now]
    if not timed_out_members:
        embed = discord.Embed(
            description=f"🔎 {ctx.author.mention}: **No members are currently timed out!**",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    errors = []
    success_count = 0
    tasks = []
    dm_tasks = []
    for member in timed_out_members:
        tasks.append(member.timeout(None, reason=f"Untimed out by {ctx.author.display_name} via untimeoutall"))
        dm_tasks.append(
            send_mod_dm(
                member,
                moderator=ctx.author,
                action_type="untimeout",
                reason=f"Untimed out by {ctx.author.display_name} via untimeoutall",
            )
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.gather(*dm_tasks)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            errors.append(f"Failed to untimeout {timed_out_members[i].mention}: {result}")
        else:
            success_count += 1
    if errors:
        embed = discord.Embed(
            description=f"Untimed out {success_count} members. The following errors occurred:\n" + "\n".join(errors),
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            description=f"✅ Successfully untimed out all {success_count} members.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🕊️")


# ,drag / ,d <user>
@bot.command(aliases=["d"])
async def drag(ctx: Context, user: str):
    """Drags a user to your voice channel."""
    # Check if the command user is in a voice channel
    if not ctx.author.voice:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You must be in a voice channel to use this command.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return

    # Get the target user
    target_user = get_user_from_arg(ctx.guild, user)
    if not target_user:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: User not found. Please provide a valid user mention or ID.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return

    # Check if target user is in a voice channel
    if not target_user.voice:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: {target_user.mention} is not in a voice channel.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return

    try:
        # Move the target user to the command user's voice channel
        await target_user.move_to(ctx.author.voice.channel)
        embed = discord.Embed(
            description=f"🎣 {ctx.author.mention}: Dragged {target_user.mention} to your voice channel.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🎣")
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to move users.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except discord.HTTPException:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to move {target_user.mention}.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command(aliases=["dp", "dragpriv", "dragprivate"])
async def drag_private(ctx: Context, *users: str):
    """Creates a private voice channel and drags users to it."""
    # Check if the command user is in a voice channel
    if not ctx.author.voice:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You must be in a voice channel to use this command.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return

    # Get all target users (automatically include the command user)
    target_users = []
    for user_arg in users:
        target_user = get_user_from_arg(ctx.guild, user_arg)
        if target_user and target_user != ctx.author:  # Don't add the command user twice
            target_users.append(target_user)

    try:
        # Create a private voice channel
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ctx.author: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        }
        
        # Add permissions for target users
        for user in target_users:
            overwrites[user] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)

        # Create the channel below the current voice channel
        parent_category = ctx.author.voice.channel.category
        position = ctx.author.voice.channel.position + 1 if ctx.author.voice.channel.position else None
        
        private_channel = await ctx.guild.create_voice_channel(
            name=f"{ctx.author.name} room",
            overwrites=overwrites,
            category=parent_category,
            position=position,
            reason=f"Private room created by {ctx.author}"
        )

        # Move all users to the private channel (including the command user)
        moved_users = []
        all_users = [ctx.author] + target_users
        for user in all_users:
            if user.voice:
                try:
                    await user.move_to(private_channel)
                    moved_users.append(user.mention)
                except:
                    continue

        embed = discord.Embed(
            description=f"🎣 {ctx.author.mention}: Created private room and moved {', '.join(moved_users)} to it.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🎣")

        # Start monitoring the channel for when everyone leaves
        bot.loop.create_task(monitor_private_channel(private_channel))

    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to create channels or move users.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except discord.HTTPException:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to create private room.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


async def monitor_private_channel(channel):
    """Monitor a private channel and delete it when everyone leaves."""
    while True:
        await asyncio.sleep(5)  # Check every 5 seconds
        
        # Check if channel still exists
        try:
            if not channel.members:  # If no one is in the channel
                await channel.delete(reason="Private room auto-deleted - everyone left")
                break
        except discord.NotFound:
            # Channel was already deleted
            break
        except Exception:
            # Any other error, stop monitoring
            break


# ,vc reject <user> / ,vcreject <user>
@bot.command(aliases=["vcreject", "voicemaster reject", "voice reject", "voiceban", "voicemaster ban", "voice ban", "vc ban"])
async def vc_reject(ctx: Context, user: str):
    """Disconnects a user from your voice channel and removes their permission to connect back."""
    # Check if user has permission or is server owner
    is_owner = (ctx.author.id == ctx.guild.owner_id or 
                ctx.author == ctx.guild.owner or 
                ctx.author.guild_permissions.administrator)
    
    if not (ctx.author.guild_permissions.move_members or is_owner):
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You do not have permission to use this command.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    # Check if bot has permission
    if not ctx.guild.me.guild_permissions.move_members:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to move members. Please give me the 'Move Members' permission.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    member = get_user_from_arg(ctx.guild, user)
    if not member:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Could not find the specified user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    if not ctx.author.voice:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You must be in a voice channel to use this command.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    if not member.voice or member.voice.channel != ctx.author.voice.channel:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: {member.display_name} is not in your voice channel.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    try:
        # Disconnect the user
        await member.move_to(None, reason=f"Disconnected by {ctx.author.display_name}")
        
        embed = discord.Embed(
            description=f"✅ {ctx.author.mention}: Disconnected {member.mention} from {ctx.author.voice.channel.name}.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to disconnect that user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to disconnect user: {e}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


# ,vc permit <user>
@bot.command(aliases=["vcpermit", "voicemaster permit", "voice permit", "vc allow", "voicemaster allow", "voice allow"])
async def vc_permit(ctx: Context, user: str):
    """Gives a user permission to connect, view, speak, and use voice activity in your voice channel."""
    # Check if user has permission or is server owner
    is_owner = (ctx.author.id == ctx.guild.owner_id or 
                ctx.author == ctx.guild.owner or 
                ctx.author.guild_permissions.administrator)
    
    if not (ctx.author.guild_permissions.manage_channels or is_owner):
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You do not have permission to use this command.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    # Check if bot has permission
    if not ctx.guild.me.guild_permissions.manage_channels:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to manage channels. Please give me the 'Manage Channels' permission.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    member = get_user_from_arg(ctx.guild, user)
    if not member:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Could not find the specified user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    if not ctx.author.voice:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You must be in a voice channel to use this command.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    voice_channel = ctx.author.voice.channel
    
    try:
        # Set channel overwrites for the user
        overwrite = discord.PermissionOverwrite()
        overwrite.connect = True
        overwrite.view_channel = True
        overwrite.speak = True
        overwrite.use_voice_activity = True
        
        await voice_channel.set_permissions(member, overwrite=overwrite, reason=f"Permitted by {ctx.author.display_name}")
        
        embed = discord.Embed(
            description=f"✅ {ctx.author.mention}: Gave {member.mention} permission to connect, view, speak, and use voice activity in {voice_channel.name}.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to modify channel permissions.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to give permission: {e}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


# Handle ,vc reject <user> syntax
@bot.command(name="vc")
async def vc_group(ctx: Context, subcommand: str, *, user: str = None):
    """Voice channel management commands."""
    if subcommand.lower() == "reject":
        if user is None:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Please specify a user to reject.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        # Call the same logic as vc_reject
        await vc_reject(ctx, user)
    elif subcommand.lower() == "permit":
        if user is None:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Please specify a user to permit.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        # Call the same logic as vc_permit
        await vc_permit(ctx, user)
    else:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Unknown subcommand '{subcommand}'. Use 'reject' or 'permit'.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command(aliases=["r"])
@commands.has_permissions(manage_roles=True)
async def role(ctx: Context, subcommand: str, *, args: str = None):
    """Role management commands."""
    if subcommand.lower() == "create":
        if not args:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Usage: `,r create <role name>`",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        try:
            role = await ctx.guild.create_role(name=args, reason=f"Role created by {ctx.author}")
            embed = discord.Embed(
                description=f"✅ {ctx.author.mention}: Role '{role.name}' created successfully!",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: I don't have permission to create roles.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Error creating role: {str(e)}",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
    
    elif subcommand.lower() == "delete":
        if not args:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Usage: `,r delete <role name>`",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        role = discord.utils.get(ctx.guild.roles, name=args)
        if not role:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Role '{args}' not found.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        try:
            await role.delete(reason=f"Role deleted by {ctx.author}")
            embed = discord.Embed(
                description=f"✅ {ctx.author.mention}: Role '{role.name}' deleted successfully!",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: I don't have permission to delete roles.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Error deleting role: {str(e)}",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
    
    else:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Unknown subcommand. Use 'create' or 'delete'.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)






    

# Store original overwrites for hardhide/unhardhide
hardhide_overwrites = {}
# ,hardhide / ,hh
@bot.command(aliases=["hh"])
@commands.has_permissions(manage_channels=True)
async def hardhide(ctx: Context):
    channel = ctx.channel
    # Save the current overwrites
    if channel.id not in hardhide_overwrites:
        hardhide_overwrites[channel.id] = {}

    # Hide @everyone
    everyone = ctx.guild.default_role
    current_everyone_overwrite = channel.overwrites_for(everyone)
    hardhide_overwrites[channel.id][f"role_{everyone.id}"] = copy.deepcopy(current_everyone_overwrite)
    current_everyone_overwrite.view_channel = False
    await channel.set_permissions(everyone, overwrite=current_everyone_overwrite)

    # Hide all roles
    for role in ctx.guild.roles:
        if role == everyone:
            continue
        current_overwrite = channel.overwrites_for(role)
        hardhide_overwrites[channel.id][f"role_{role.id}"] = copy.deepcopy(current_overwrite)
        current_overwrite.view_channel = False
        await channel.set_permissions(role, overwrite=current_overwrite)

    # Hide all members with overrides
    for member in channel.overwrites:
        if isinstance(member, discord.Member):
            current_overwrite = channel.overwrites_for(member)
            hardhide_overwrites[channel.id][f"member_{member.id}"] = copy.deepcopy(current_overwrite)
            current_overwrite.view_channel = False
            await channel.set_permissions(member, overwrite=current_overwrite)

    await ctx.message.add_reaction("🙈")

# ,unhardhide / ,unhh
@bot.command(aliases=["ub"])
@commands.has_permissions(ban_members=True)
async def unban(ctx: Context, user: str, *, reason: str = None):
    """Unban a user from the server."""
    try:
        # Get user from argument
        user_id = None
        if user.startswith("<@") and user.endswith(">"):
            user_id = int(user.replace("<@!", "").replace("<@", "").replace(">", ""))
        else:
            try:
                user_id = int(user)
            except ValueError:
                embed = discord.Embed(
                    description=f"⚠️ {ctx.author.mention}: Invalid user ID or mention.",
                    color=0x4C4C54,
                )
                await ctx.send(embed=embed)
                return
        
        # Check if user is banned
        try:
            ban_entry = await ctx.guild.fetch_ban(discord.Object(user_id))
        except discord.NotFound:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: User is not banned.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        # Unban the user
        await ctx.guild.unban(discord.Object(user_id), reason=reason)
        
        # Send confirmation
        embed = discord.Embed(
            title="🕊️ User Unbanned",
            description=f"**<@!{user_id}>** has been unbanned from the server by **{ctx.author.mention}**.",
            color=0x4C4C54,
        )
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🕊️")
        
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to unban users!")
    except discord.HTTPException as e:
        await ctx.send(f"❌ Error unbanning user: {e}")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {e}")


@bot.command(aliases=["uba"])
@commands.has_permissions(ban_members=True)
async def unbanall(ctx: Context):
    """Unbans all banned users in the server."""
    try:
        bans = [ban async for ban in ctx.guild.bans()]
        if not bans:
            embed = discord.Embed(
                description=f"ℹ️ {ctx.author.mention}: No banned users found.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        unbanned_count = 0
        for ban_entry in bans:
            try:
                await ctx.guild.unban(ban_entry.user, reason=f"Unbanned by {ctx.author}")
                unbanned_count += 1
                await asyncio.sleep(1)  # Rate limiting
            except discord.Forbidden:
                continue
            except discord.HTTPException:
                continue
        
        embed = discord.Embed(
            description=f"🕊️ {ctx.author.mention}: Successfully unbanned **{unbanned_count}** users.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🕊️")
        
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to unban users.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command()
async def prefix(ctx: Context, new_prefix: str = None):
    """Shows or changes the bot's prefix for this server."""
    current_prefix = custom_prefixes.get(ctx.guild.id, ",")
    
    if new_prefix is None:
        # Show current prefix (like bleed bot)
        embed = discord.Embed(
            description=f"{ctx.author.mention}: Server Prefix: `{current_prefix}`",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    # Change prefix (requires admin permission)
    if not ctx.author.guild_permissions.administrator:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You need Administrator permission to change the prefix.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    if len(new_prefix) > 5:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Prefix must be 5 characters or less.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    custom_prefixes[ctx.guild.id] = new_prefix
    embed = discord.Embed(
        description=f"✅ {ctx.author.mention}: Bot prefix changed to `{new_prefix}`",
        color=0x4C4C54,
    )
    await ctx.send(embed=embed)


# Mute role management
async def get_or_create_mute_role(guild, role_type):
    """Get or create mute roles with specific permissions."""
    role_name = role_type
    role = discord.utils.get(guild.roles, name=role_name)
    
    if not role:
        # Create the role
        role = await guild.create_role(name=role_name, reason=f"Created {role_type} role")
        
        # Set permissions based on role type
        if role_type == "muted":
            # Remove: Send Messages, Send Messages in Threads, Create Public Threads, Create Private Threads
            for channel in guild.channels:
                if isinstance(channel, (discord.TextChannel, discord.Thread)):
                    await channel.set_permissions(role, send_messages=False, send_messages_in_threads=False, create_public_threads=False, create_private_threads=False)
        elif role_type == "imuted":
            # Remove: Attach Files, Embed Links
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel):
                    await channel.set_permissions(role, attach_files=False, embed_links=False)
        elif role_type == "rmuted":
            # Remove: Add Reactions, Use External Emoji, Use External Stickers
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel):
                    await channel.set_permissions(role, add_reactions=False, use_external_emojis=False, use_external_stickers=False)
    
    return role


@bot.command(aliases=["m"])
@commands.has_permissions(manage_roles=True)
async def mute(ctx: Context, user: str, *, reason: str = None):
    """Applies muted role to a user."""
    target_user = get_user_from_arg(ctx.guild, user)
    if not target_user:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: User not found. Please provide a valid user mention or ID.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    try:
        mute_role = await get_or_create_mute_role(ctx.guild, "muted")
        await target_user.add_roles(mute_role, reason=f"Muted by {ctx.author}: {reason or 'No reason'}")
        
        # Send DM to muted user
        await send_mod_dm(
            member=target_user,
            moderator=ctx.author,
            action_type="muted",
            reason=reason
        )
        
        embed = discord.Embed(
            description=f"🔇 {ctx.author.mention}: Muted {target_user.mention}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🔇")
        
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to manage roles.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command(aliases=["im", "imagemute", "image mute"])
@commands.has_permissions(manage_roles=True)
async def imute(ctx: Context, user: str, *, reason: str = None):
    """Applies imuted role to a user."""
    target_user = get_user_from_arg(ctx.guild, user)
    if not target_user:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: User not found. Please provide a valid user mention or ID.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    try:
        mute_role = await get_or_create_mute_role(ctx.guild, "imuted")
        await target_user.add_roles(mute_role, reason=f"Image muted by {ctx.author}: {reason or 'No reason'}")
        
        # Send DM to muted user
        await send_mod_dm(
            member=target_user,
            moderator=ctx.author,
            action_type="muted",
            reason=reason
        )
        
        embed = discord.Embed(
            description=f"🔇 {ctx.author.mention}: Image muted {target_user.mention}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🔇")
        
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to manage roles.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command(aliases=["rm", "reactionmute"])
@commands.has_permissions(manage_roles=True)
async def rmute(ctx: Context, user: str, *, reason: str = None):
    """Applies rmuted role to a user."""
    target_user = get_user_from_arg(ctx.guild, user)
    if not target_user:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: User not found. Please provide a valid user mention or ID.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    try:
        mute_role = await get_or_create_mute_role(ctx.guild, "rmuted")
        await target_user.add_roles(mute_role, reason=f"Reaction muted by {ctx.author}: {reason or 'No reason'}")
        
        # Send DM to muted user
        await send_mod_dm(
            member=target_user,
            moderator=ctx.author,
            action_type="muted",
            reason=reason
        )
        
        embed = discord.Embed(
            description=f"🔇 {ctx.author.mention}: Reaction muted {target_user.mention}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        await ctx.message.add_reaction("🔇")
        
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to manage roles.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(manage_roles=True)
async def roleicon(ctx: Context, url: str, *, role: discord.Role):
    """Changes the icon of the specified role."""
    try:
        # Validate URL
        if not url.startswith(('http://', 'https://')):
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Please provide a valid image URL.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        # Set the role icon
        await role.edit(display_icon=url, reason=f"Role icon changed by {ctx.author}")
        
        embed = discord.Embed(
            description=f"✅ {ctx.author.mention}: Successfully changed icon for role {role.mention}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to manage this role.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except discord.HTTPException:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to set role icon. Please check the URL and try again.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command(aliases=["sr"])
@commands.has_permissions(manage_roles=True)
async def stickyreactionrole(ctx: Context, subcommand: str, *, args: str = None):
    """Manages sticky reaction roles."""
    if subcommand.lower() == "add":
        if not args:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Usage: `,sr add \"message content\" \"emoji\" \"role name\"`",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        # Parse quoted arguments
        import shlex
        try:
            parsed_args = shlex.split(args)
            if len(parsed_args) != 3:
                embed = discord.Embed(
                    description=f"⚠️ {ctx.author.mention}: Usage: `,sr add \"message content\" \"emoji\" \"role name\"`\nAll arguments must be in quotes.",
                    color=0x4C4C54,
                )
                await ctx.send(embed=embed)
                return
            
            message_content = parsed_args[0]
            reaction = parsed_args[1]
            role_name = parsed_args[2]
            
        except ValueError:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Invalid quotes. Usage: `,sr add \"message content\" \"emoji\" \"role name\"`",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        # Find the role by name (case-insensitive)
        role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), ctx.guild.roles)
        if not role:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Role '{role_name}' not found.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        try:
            # Send the embed message
            embed = discord.Embed(
                description=message_content,
                color=0x4C4C54,
            )
            message = await ctx.send(embed=embed)
            
            # Add the reaction
            await message.add_reaction(reaction)
            
            # Store the sticky reaction role
            if ctx.guild.id not in sticky_reaction_roles:
                sticky_reaction_roles[ctx.guild.id] = {}
            if message.id not in sticky_reaction_roles[ctx.guild.id]:
                sticky_reaction_roles[ctx.guild.id][message.id] = {}
            
            sticky_reaction_roles[ctx.guild.id][message.id][reaction] = role.id
            
            embed = discord.Embed(
                description=f"✅ {ctx.author.mention}: Sticky reaction role set up successfully!",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            
        except discord.HTTPException:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Invalid reaction emoji.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
    
    elif subcommand.lower() == "remove":
        if not args:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Usage: `,sr remove <message ID or link>`",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        message_id = args.strip()
        # Extract message ID from link if provided
        if "/" in message_id:
            message_id = message_id.split("/")[-1]
        
        try:
            message_id = int(message_id)
        except ValueError:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Invalid message ID.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        if ctx.guild.id in sticky_reaction_roles and message_id in sticky_reaction_roles[ctx.guild.id]:
            del sticky_reaction_roles[ctx.guild.id][message_id]
            embed = discord.Embed(
                description=f"✅ {ctx.author.mention}: Sticky reaction role removed successfully!",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: No sticky reaction role found for that message.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
    
    elif subcommand.lower() == "list":
        if ctx.guild.id not in sticky_reaction_roles or not sticky_reaction_roles[ctx.guild.id]:
            embed = discord.Embed(
                description=f"ℹ️ {ctx.author.mention}: No sticky reaction roles set up.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        embed = discord.Embed(
            title="Sticky Reaction Roles",
            description="Current sticky reaction roles:",
            color=0x4C4C54,
        )
        
        for message_id, reactions in sticky_reaction_roles[ctx.guild.id].items():
            for reaction, role_id in reactions.items():
                role = ctx.guild.get_role(role_id)
                role_name = role.name if role else "Unknown Role"
                embed.add_field(
                    name=f"Message {message_id}",
                    value=f"Reaction: {reaction} → Role: {role_name}",
                    inline=False
                )
        
        await ctx.send(embed=embed)
    
    else:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Unknown subcommand. Use 'add', 'remove', or 'list'.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx: Context, user: str, *, reason: str = None):
    """Removes all mute roles from a user."""
    target_user = get_user_from_arg(ctx.guild, user)
    if not target_user:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: User not found. Please provide a valid user mention or ID.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    try:
        mute_roles = ["muted", "imuted", "rmuted"]
        removed_roles = []
        
        for role_name in mute_roles:
            role = discord.utils.get(ctx.guild.roles, name=role_name)
            if role and role in target_user.roles:
                await target_user.remove_roles(role, reason=f"Unmuted by {ctx.author}: {reason or 'No reason'}")
                removed_roles.append(role_name)
        
        if removed_roles:
            # Send DM to unmuted user
            await send_mod_dm(
                member=target_user,
                moderator=ctx.author,
                action_type="unmuted",
                reason=reason
            )
            
            embed = discord.Embed(
                description=f"🔊 {ctx.author.mention}: Unmuted {target_user.mention} (removed: {', '.join(removed_roles)})",
                color=0x4C4C54,
            )
        else:
            embed = discord.Embed(
                description=f"ℹ️ {ctx.author.mention}: {target_user.mention} is not muted.",
                color=0x4C4C54,
            )
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to manage roles.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.command(aliases=["unhh"])
@commands.has_permissions(manage_channels=True)
async def unhardhide(ctx: Context):
    channel = ctx.channel

    if channel.id not in hardhide_overwrites:
        await ctx.send("🙉 This channel hasn't been hardhidden before.")
        return

    # Restore original overwrites
    restored_count = 0
    for key, old_overwrite in hardhide_overwrites[channel.id].items():
        if key.startswith("role_"):
            role_id = int(key.split("_")[1])
            role = ctx.guild.get_role(role_id)
            if role:
                await channel.set_permissions(role, overwrite=old_overwrite)
                restored_count += 1

        elif key.startswith("member_"):
            member_id = int(key.split("_")[1])
            member = ctx.guild.get_member(member_id)
            if member:
                await channel.set_permissions(member, overwrite=old_overwrite)
                restored_count += 1

    # Clean up
    del hardhide_overwrites[channel.id]

    await ctx.message.add_reaction("🙉")


# AFK System
afk_users = {}  # {user_id: {"reason": str, "timestamp": datetime, "original_nickname": str, "scope": str}}
global_afk_users = {}  # {user_id: {"reason": str, "timestamp": datetime, "original_nickname": str}}


class AFKChoiceView(discord.ui.View):
    def __init__(self, author, reason, original_nickname, message=None):
        super().__init__(timeout=10)
        self.author = author
        self.reason = reason
        self.original_nickname = original_nickname
        self.choice_made = False
        self.message = message
    
    @discord.ui.button(label="🌐 Global AFK", style=discord.ButtonStyle.primary)
    async def global_afk(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ This is not your AFK choice!", ephemeral=True)
            return
        
        await self.set_global_afk()
        await interaction.response.defer()
    
    @discord.ui.button(label="🏠 Server AFK", style=discord.ButtonStyle.secondary)
    async def server_afk(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ This is not your AFK choice!", ephemeral=True)
            return
        
        await self.set_server_afk(interaction.guild.id)
        await interaction.response.defer()
    
    async def set_global_afk(self):
        """Set global AFK status."""
        self.choice_made = True
        user_id = self.author.id
        
        # Try to change nickname
        try:
            new_nickname = f"[AFK] {self.original_nickname}"
            if len(new_nickname) <= 32:
                await self.author.edit(nick=new_nickname)
        except discord.Forbidden:
            pass
        
        # Store global AFK info
        global_afk_users[user_id] = {
            "reason": self.reason,
            "timestamp": datetime.now(timezone.utc),
            "original_nickname": self.original_nickname
        }
        
        # Send AFK embed message
        embed = discord.Embed(
            title="🌐 Global AFK Status",
            description=f"{self.author.mention} is now globally AFK with the status: **{self.reason}**",
            color=0x4C4C54,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="You'll be notified when you return")
        
        # Find the original message and edit it
        try:
            await self.message.edit(content=None, embed=embed, view=None)
        except:
            pass
    
    async def set_server_afk(self, guild_id):
        """Set server-specific AFK status."""
        self.choice_made = True
        user_id = self.author.id
        
        # Try to change nickname
        try:
            new_nickname = f"[AFK] {self.original_nickname}"
            if len(new_nickname) <= 32:
                await self.author.edit(nick=new_nickname)
        except discord.Forbidden:
            pass
        
        # Store server AFK info
        afk_users[user_id] = {
            "reason": self.reason,
            "timestamp": datetime.now(timezone.utc),
            "original_nickname": self.original_nickname,
            "scope": "server"
        }
        
        # Send AFK embed message
        embed = discord.Embed(
            title="🏠 Server AFK Status",
            description=f"{self.author.mention} is now server AFK with the status: **{self.reason}**",
            color=0x4C4C54,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="You'll be notified when you return")
        
        # Find the original message and edit it
        try:
            await self.message.edit(content=None, embed=embed, view=None)
        except:
            pass

@bot.command()
async def afk(ctx: Context, *, reason: str = "AFK"):
    """Set your AFK status with an optional reason."""
    user_id = ctx.author.id
    original_nickname = ctx.author.display_name
    
    # Create AFK choice embed
    embed = discord.Embed(
        title="AFK Status",
        description=f"<@{user_id}> choose your AFK status from the buttons below!\n- You have 10 seconds or we'll set it to Global by default.",
        color=0x4C4C54,
    )
    
    # Create view with buttons
    message = await ctx.send(embed=embed, view=None)
    view = AFKChoiceView(ctx.author, reason, original_nickname, message)
    await message.edit(embed=embed, view=view)
    
    # Set default to Global after 10 seconds
    await asyncio.sleep(10)
    if not view.choice_made:
        await view.set_global_afk()


async def reverse_then_forward_counter(message, total_seconds):
    """Reverse counter (countdown) then forward counter."""
    # First: Reverse counting (countdown)
    for i in range(total_seconds, 0, -1):
        try:
            embed = discord.Embed(
                description=f"👋 Welcome back! You were gone for **{i} seconds**",
                color=0x4C4C54,
            )
            await message.edit(embed=embed)
        except Exception as e:
            break
        await asyncio.sleep(1)
    
    # Then: Forward counting (countup)
    for i in range(1, 61):  # Count up to 60 seconds
        try:
            embed = discord.Embed(
                description=f"👋 Welcome back! You were gone for **{i} seconds**",
                color=0x4C4C54,
            )
            await message.edit(embed=embed)
        except Exception as e:
            break
        await asyncio.sleep(1)


@bot.event
async def on_message(message):
    """Handle AFK system and message mentions."""
    # Ignore messages from bots
    if message.author.bot:
        return

    # Process commands first
    await bot.process_commands(message)
    
    # Check if this is the AFK command itself - if so, don't process AFK return
    if message.content.startswith(('afk', ',afk', '.afk')):
        return
    
    # Check if message author was AFK (both global and server)
    user_id = message.author.id
    afk_info = None
    is_global = False
    
    # Check global AFK first
    if user_id in global_afk_users:
        afk_info = global_afk_users.pop(user_id)
        is_global = True
    # Then check server AFK
    elif user_id in afk_users:
        afk_info = afk_users.pop(user_id)
        is_global = False
    
    if afk_info:
        # Try to restore original nickname
        try:
            if afk_info["original_nickname"] != message.author.display_name:
                await message.author.edit(nick=afk_info["original_nickname"])
        except discord.Forbidden:
            pass
        
        # Calculate AFK duration
        duration = datetime.now(timezone.utc) - afk_info["timestamp"]
        total_seconds = int(duration.total_seconds())
        
        # Send initial welcome back message with reverse counter
        scope_text = "**Global AFK**" if is_global else "**Server AFK**"
        embed = discord.Embed(
            description=f"👋 {message.author.mention}: Welcome back! You were gone for **{total_seconds} seconds** ({scope_text})",
            color=0x4C4C54,
        )
        welcome_msg = await message.channel.send(embed=embed)
        
        # Start reverse counter then forward counter
        bot.loop.create_task(reverse_then_forward_counter(welcome_msg, total_seconds))
    
    # Check if someone mentioned an AFK user (both global and server)
    for mention in message.mentions:
        mention_id = mention.id
        
        # Check global AFK first
        if mention_id in global_afk_users:
            afk_info = global_afk_users[mention_id]
            embed = discord.Embed(
                description=f"💤 {mention.display_name} is **Global AFK**: {afk_info['reason']}",
                color=0x4C4C54,
            )
            await message.channel.send(embed=embed)
        # Then check server AFK
        elif mention_id in afk_users:
            afk_info = afk_users[mention_id]
            embed = discord.Embed(
                description=f"💤 {mention.display_name} is **Server AFK**: {afk_info['reason']}",
                color=0x4C4C54,
            )
            await message.channel.send(embed=embed)


@bot.event
async def on_message_edit(before, after):
    """Re-trigger commands when messages are edited."""
    # Ignore edits from bots
    if after.author.bot:
        return
    
    # Only process if the content actually changed
    if before.content == after.content:
        return
    
    # Process the edited message as a command
    await bot.process_commands(after)





# Giveaways System
active_giveaways = {}  # {message_id: {"channel_id": int, "guild_id": int, "prize": str, "end_time": datetime, "host_id": int}}

def parse_giveaway_duration(duration_str):
    """Parse flexible duration strings for giveaways."""
    import re
    
    # Remove extra spaces and convert to lowercase
    duration_str = re.sub(r'\s+', '', duration_str.lower())
    
    # Define patterns for different time units
    patterns = {
        r'(\d+)s(?:ec(?:ond)?s?)?$': 1,  # seconds
        r'(\d+)m(?:in(?:ute)?s?)?$': 60,  # minutes
        r'(\d+)h(?:r(?:s)?|our(?:s)?)?$': 3600,  # hours
        r'(\d+)d(?:ay(?:s)?)?$': 86400,  # days
        r'(\d+)w(?:eek(?:s)?)?$': 604800,  # weeks
        r'(\d+)mo(?:nth(?:s)?)?$': 2592000,  # months (30 days)
    }
    
    for pattern, multiplier in patterns.items():
        match = re.match(pattern, duration_str)
        if match:
            value = int(match.group(1))
            return value * multiplier
    
    return None


@bot.command(aliases=["gw"])
async def giveaways(ctx: Context, subcommand: str = None, *args):
    """Manage giveaways."""
    if subcommand is None:
        embed = discord.Embed(
            title="🎉 Giveaways Help",
            description="**Usage:** `.giveaways <subcommand> <args>`\n**Alias:** `.gw`\n\n**Subcommands:**\n• `start <duration> <prize>` - Start a new giveaway\n\n**Duration Examples:**\n• `30s`, `5m`, `2h`, `1d`, `1w`, `1mo`\n• `30 seconds`, `5 minutes`, `2 hours`, `1 day`, `1 week`, `1 month`\n\n**Examples:**\n• `.gw start 30m Nitro Code`\n• `.gw start 2h Discord Nitro`\n• `.gw start 7d Amazon Gift Card`",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    if subcommand.lower() == "start":
        if len(args) < 2:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Usage: `.gw start <duration> <prize>`",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        duration_str = args[0]
        prize = " ".join(args[1:])
        
        # Parse duration
        duration_seconds = parse_giveaway_duration(duration_str)
        if not duration_seconds:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Invalid duration format. Examples: 30s, 5m, 2h, 1d, 1w, 1mo",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        # Calculate end time
        end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        
        # Create giveaway embed
        embed = discord.Embed(
            description=f"{prize}\n\nReact with 🎉 to enter the giveaway.\n\nEnds: <t:{int(end_time.timestamp())}:R> (<t:{int(end_time.timestamp())}:F>)\n\nEntries: 0\n\nHosted by: {ctx.author.display_name}\n\n**Winners**\nNo winners were chosen!",
            color=0x4C4C54,
        )
        embed.set_author(name="nova", icon_url=bot.user.avatar.url if bot.user.avatar else None)
        
        # Send giveaway message
        giveaway_msg = await ctx.send(embed=embed)
        await giveaway_msg.add_reaction("🎉")
        
        # Store giveaway info
        active_giveaways[giveaway_msg.id] = {
            "channel_id": ctx.channel.id,
            "guild_id": ctx.guild.id,
            "prize": prize,
            "end_time": end_time,
            "host_id": ctx.author.id,
            "message_id": giveaway_msg.id
        }
        
        # Schedule end of giveaway
        bot.loop.create_task(end_giveaway(giveaway_msg.id, duration_seconds))
        
    else:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Unknown subcommand. Use 'start'.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


async def end_giveaway(message_id: int, delay: int):
    """End a giveaway after the specified delay."""
    await asyncio.sleep(delay)
    
    if message_id not in active_giveaways:
        return
    
    giveaway_info = active_giveaways.pop(message_id)
    channel = bot.get_channel(giveaway_info["channel_id"])
    if not channel:
        return
    
    try:
        message = await channel.fetch_message(message_id)
        reaction = discord.utils.get(message.reactions, emoji="🎉")
        
        if not reaction or reaction.count <= 1:  # Only bot reaction
            embed = discord.Embed(
                description=f"{giveaway_info['prize']}\n\nReact with 🎉 to enter the giveaway.\n\nEnded: <t:{int(giveaway_info['end_time'].timestamp())}:R> (<t:{int(giveaway_info['end_time'].timestamp())}:F>)\n\nEntries: 0\n\nHosted by: {bot.get_user(giveaway_info['host_id']).display_name}\n\n**Winners**\nNo winners were chosen!",
                color=0x4C4C54,
            )
            embed.set_author(name="nova", icon_url=bot.user.avatar.url if bot.user.avatar else None)
            await message.edit(embed=embed)
            return
        
        # Get users who reacted (excluding the bot)
        users = []
        async for user in reaction.users():
            if not user.bot:
                users.append(user)
        
        if not users:
            embed = discord.Embed(
                description=f"{giveaway_info['prize']}\n\nReact with 🎉 to enter the giveaway.\n\nEnded: <t:{int(giveaway_info['end_time'].timestamp())}:R> (<t:{int(giveaway_info['end_time'].timestamp())}:F>)\n\nEntries: {len(users)}\n\nHosted by: {bot.get_user(giveaway_info['host_id']).display_name}\n\n**Winners**\nNo winners were chosen!",
                color=0x4C4C54,
            )
            embed.set_author(name="nova", icon_url=bot.user.avatar.url if bot.user.avatar else None)
            await message.edit(embed=embed)
            return
        
        # Pick random winner
        import random
        winner = random.choice(users)
        
        embed = discord.Embed(
            description=f"{giveaway_info['prize']}\n\nReact with 🎉 to enter the giveaway.\n\nEnded: <t:{int(giveaway_info['end_time'].timestamp())}:R> (<t:{int(giveaway_info['end_time'].timestamp())}:F>)\n\nEntries: {len(users)}\n\nHosted by: {bot.get_user(giveaway_info['host_id']).display_name}\n\n**Winners**\n🎊 {winner.mention} 🎊",
            color=0x4C4C54,
        )
        embed.set_author(name="nova", icon_url=bot.user.avatar.url if bot.user.avatar else None)
        
        await message.edit(embed=embed)
        await channel.send(f"🎉 Congratulations {winner.mention}! You won: **{giveaway_info['prize']}**!")
        
    except discord.NotFound:
        # Message was deleted
        pass
    except Exception:
        pass


@bot.tree.command(name="ping", description="Check the bot's latency.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    try:
        await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")
    except discord.errors.HTTPException:
        # If interaction was already acknowledged, send a followup
        await interaction.followup.send(f"🏓 Pong! Latency: {latency}ms")


@bot.event
async def on_raw_reaction_add(payload):
    """Handles reaction role assignments and giveaway entries."""
    if payload.member.bot:
        return
    
    guild_id = payload.guild_id
    message_id = payload.message_id
    reaction = str(payload.emoji)
    
    # Handle sticky reaction roles
    if guild_id in sticky_reaction_roles and message_id in sticky_reaction_roles[guild_id]:
        if reaction in sticky_reaction_roles[guild_id][message_id]:
            role_id = sticky_reaction_roles[guild_id][message_id][reaction]
            guild = bot.get_guild(guild_id)
            role = guild.get_role(role_id)
            member = payload.member
            
            if role and not role in member.roles:
                try:
                    await member.add_roles(role, reason="Sticky reaction role")
                except discord.Forbidden:
                    pass
    
    # Handle giveaway entries
    if message_id in active_giveaways and reaction == "🎉":
        # Update the giveaway message with new entry count
        try:
            channel = bot.get_channel(active_giveaways[message_id]["channel_id"])
            if channel:
                message = await channel.fetch_message(message_id)
                reaction_obj = discord.utils.get(message.reactions, emoji="🎉")
                
                if reaction_obj:
                    # Count non-bot users who reacted
                    users = []
                    async for user in reaction_obj.users():
                        if not user.bot:
                            users.append(user)
                    
                    # Update the embed with new entry count
                    embed = message.embeds[0] if message.embeds else None
                    if embed:
                        # Update the entries count in the description
                        lines = embed.description.split('\n')
                        for i, line in enumerate(lines):
                            if line.startswith('Entries:'):
                                lines[i] = f"Entries: {len(users)}"
                                break
                        
                        new_description = '\n'.join(lines)
                        embed.description = new_description
                        await message.edit(embed=embed)
        except Exception as e:
            print(f"Error updating giveaway entries: {e}")


@bot.event
async def on_raw_reaction_remove(payload):
    """Handles reaction role removals and giveaway entry removals."""
    guild_id = payload.guild_id
    message_id = payload.message_id
    reaction = str(payload.emoji)
    
    # Handle sticky reaction roles
    if guild_id in sticky_reaction_roles and message_id in sticky_reaction_roles[guild_id]:
        if reaction in sticky_reaction_roles[guild_id][message_id]:
            role_id = sticky_reaction_roles[guild_id][message_id][reaction]
            guild = bot.get_guild(guild_id)
            role = guild.get_role(role_id)
            member = guild.get_member(payload.user_id)
            
            if role and member and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Sticky reaction role removed")
                except discord.Forbidden:
                    pass
    
    # Handle giveaway entry removals
    if message_id in active_giveaways and reaction == "🎉":
        # Update the giveaway message with new entry count
        try:
            channel = bot.get_channel(active_giveaways[message_id]["channel_id"])
            if channel:
                message = await channel.fetch_message(message_id)
                reaction_obj = discord.utils.get(message.reactions, emoji="🎉")
                
                if reaction_obj:
                    # Count non-bot users who reacted
                    users = []
                    async for user in reaction_obj.users():
                        if not user.bot:
                            users.append(user)
                    
                    # Update the embed with new entry count
                    embed = message.embeds[0] if message.embeds else None
                    if embed:
                        # Update the entries count in the description
                        lines = embed.description.split('\n')
                        for i, line in enumerate(lines):
                            if line.startswith('Entries:'):
                                lines[i] = f"Entries: {len(users)}"
                                break
                        
                        new_description = '\n'.join(lines)
                        embed.description = new_description
                        await message.edit(embed=embed)
        except Exception as e:
            print(f"Error updating giveaway entries: {e}")


@bot.event
async def on_command_error(ctx: Context, error: commands.CommandError):
    # Ignore unknown commands
    if isinstance(error, commands.CommandNotFound):
        return
    # Build a styled error/help embed
    embed = discord.Embed(color=0x4C4C54)
    embed.set_author(
        name=f"{ctx.me.display_name} help", icon_url=ctx.me.display_avatar.url
    )

    # Default values
    command_name = ctx.command.qualified_name if ctx.command else "Unknown"
    embed.title = f"Command: {command_name}"
    usage = None
    example = None
    description = None

    if isinstance(error, commands.MissingPermissions):
        description = "You do not have permission to use this command."
    elif isinstance(error, commands.CommandInvokeError) and isinstance(
        error.original, discord.Forbidden
    ):
        description = "I do not have the required permissions to perform this action. Please check my role and channel permissions."
    elif isinstance(error, commands.MissingRequiredArgument):
        description = "You are missing a required argument."
    elif isinstance(error, commands.BadArgument):
        description = "One or more arguments are invalid."
    elif isinstance(error, commands.TooManyArguments):
        description = "Too many arguments were provided."
    elif isinstance(error, commands.CommandNotFound):
        description = "That command does not exist."
    else:
        description = f"An error occurred: {str(error)}"

    # Try to show usage and example if available
    if ctx.command:
        params = []
        for param_name, param in ctx.command.params.items():
            if param_name in ("self", "ctx"):
                continue
            if param.kind == param.VAR_POSITIONAL:
                params.append(f"({param.name}...)")
                continue
            if param.default is not param.empty:
                params.append(f"({param.name})")
            else:
                params.append(f"<{param.name}>")
        usage = f",{ctx.command.qualified_name} {' '.join(params)}"
        # Example: use the first alias or the command name
        example = f",{ctx.command.aliases[0] if ctx.command.aliases else ctx.command.qualified_name} ..."

    embed.description = description
    if usage:
        embed.add_field(
            name="", value=f"```Syntax: {usage}\nExample: {example}```", inline=False
        )

    await ctx.send(embed=embed)
    # Do NOT print any errors to the terminal
@bot.command(aliases=["banlist", "ban list", "bl", "bans"])
@commands.has_permissions(ban_members=True)
async def ban_list(ctx: Context):
    """Show all banned users in the server with unban buttons."""
    try:
        # Fetch all bans
        banned_users = []
        async for ban_entry in ctx.guild.bans():
            banned_users.append(ban_entry)
        
        if not banned_users:
            embed = discord.Embed(
                description=f"📋 {ctx.author.mention}: No banned users found in this server.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
            return
        
        # Create BanListView
        view = BanListView(ctx, banned_users, ban_moderators, per_page=3)
        embed = view.make_embed()
        await ctx.send(embed=embed, view=view)
        
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to view bans.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: An error occurred while fetching bans: {str(e)}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)

# Remove default help
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception:
        pass


# To run the bot, replace 'YOUR_BOT_TOKEN' with your actual bot token
# bot.run('YOUR_BOT_TOKEN')
bot.run(TOKEN)
