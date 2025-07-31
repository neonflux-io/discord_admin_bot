import discord
from discord.ext import commands
from discord.ext.commands import Bot, Context
import asyncio
import os
from dotenv import load_dotenv
from datetime import timedelta
import re
import sys
import traceback
from datetime import datetime, timezone
import math
from typing import Optional
from dateutil.relativedelta import relativedelta
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

bot = commands.Bot(command_prefix=",", intents=intents, help_command=None)


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
    await ctx.message.delete(delay=0.5)
    member = None
    amount = 50
    # Parse arguments
    if not args:
        return
    # If first arg is a mention or user ID
    if args[0].startswith("<@") or args[0].isdigit():
        member = get_user_from_arg(ctx.guild, args[0])
        if not member:
            return
        if len(args) > 1 and args[1].isdigit():
            amount = int(args[1])
    elif args[0].isdigit():
        amount = int(args[0])
    else:
        return
    # Perform deletion
    if member:

        def is_user(m):
            return m.author == member

        deleted = await ctx.channel.purge(limit=1000, check=is_user)
        deleted = deleted[:amount]
    else:
        deleted = await ctx.channel.purge(limit=amount)


# ,nuke
@bot.command()
@commands.has_permissions(manage_channels=True)
async def nuke(ctx: Context):
    channel = ctx.channel
    new_channel = await channel.clone(reason="Channel nuked")
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
        embed = discord.Embed(
            description=f"{ctx.author.mention}: {member.display_name} has been banned.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
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
        await ctx.message.add_reaction("✅")
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


@bot.command(aliases=["to"])
@commands.has_permissions(moderate_members=True)
async def timeout(ctx: Context, user: str, duration: str = None, *, reason: str = None):
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
@bot.command(aliases=["ulall", "ul_all", "unlock_all"])
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
@bot.command(aliases=["ua"])
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
@bot.command(aliases=["uhall", "uh_all", "unhide_all"])
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
    hardlock_overwrites[channel.id][f"role_{everyone.id}"] = current_everyone_overwrite
    for perm in perms_to_lock:
        setattr(current_everyone_overwrite, perm, False)
    await channel.set_permissions(everyone, overwrite=current_everyone_overwrite)

    # Lock all roles
    for role in ctx.guild.roles:
        if role == everyone:
            continue
        current_overwrite = channel.overwrites_for(role)
        hardlock_overwrites[channel.id][f"role_{role.id}"] = current_overwrite
        for perm in perms_to_lock:
            setattr(current_overwrite, perm, False)
        await channel.set_permissions(role, overwrite=current_overwrite)

    # Lock all members with overrides
    for member in channel.overwrites:
        if isinstance(member, discord.Member):
            current_overwrite = channel.overwrites_for(member)
            hardlock_overwrites[channel.id][f"member_{member.id}"] = current_overwrite
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
    await ctx.send(f"✅ Restored permissions for **{restored_count}** roles/members.")


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


@bot.command()
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


@bot.command(aliases=["roles info"])
async def roles(ctx: Context):
    """Lists all roles in the server."""
    roles = [r for r in ctx.guild.roles if r != ctx.guild.default_role]
    roles = sorted(roles, key=lambda r: -r.position)

    def fmt(i, r):
        return f"{i} {r.mention}"

    view = PaginatedEmbedView(ctx, roles, ctx.guild.name, "Roles", entry_formatter=fmt)
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
        name=g.name, icon_url=g.icon.url if g.icon else discord.Embed.Empty
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


@bot.command(aliases=["uta", "untimeout_all"])
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


# ,drag / ,d <user>
@bot.command(aliases=["d"])
async def drag(ctx: Context, user: str):
    """Moves a user to your current voice channel."""
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
    
    if member.voice and member.voice.channel == ctx.author.voice.channel:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: {member.display_name} is already in your voice channel.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
        return
    
    try:
        # Attempt to move the user - Discord API will handle the error if they're not in voice
        await member.move_to(ctx.author.voice.channel, reason=f"Moved by {ctx.author.display_name}")
        embed = discord.Embed(
            description=f"✅ {ctx.author.mention}: Moved {member.mention} to {ctx.author.voice.channel.name}.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: I don't have permission to move that user.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        if e.status == 400 and "Target user is not connected to voice" in str(e):
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: {member.display_name} is not in a voice channel. They need to join a voice channel first before I can move them.",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                description=f"⚠️ {ctx.author.mention}: Failed to move user: {e}",
                color=0x4C4C54,
            )
            await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Failed to move user: {e}",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


# ,vc reject <user> / ,vcreject <user>
@bot.command(aliases=["vcreject"])
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
    else:
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: Unknown subcommand '{subcommand}'. Use 'reject'.",
            color=0x4C4C54,
        )
        await ctx.send(embed=embed)


@bot.tree.command(name="ping", description="Check the bot's latency.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")


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


# Remove default help
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# To run the bot, replace 'YOUR_BOT_TOKEN' with your actual bot token
# bot.run('YOUR_BOT_TOKEN')
bot.run(TOKEN)
