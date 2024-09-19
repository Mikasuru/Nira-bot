import discord
from discord.ext import commands
from typing import Optional, List, Tuple
import aiohttp
import asyncio
import io
import time
from discord import app_commands
from discord.ext.commands import Context
import platform


# Utility Functions
async def _get_channel_properties(channel: discord.TextChannel) -> dict:
    """Retrieve a channel's properties."""
    return {
        'name': channel.name,
        'category': channel.category,
        'position': channel.position,
        'overwrites': channel.overwrites,
        'topic': channel.topic or "",
        'nsfw': channel.is_nsfw(),
        'slowmode_delay': channel.slowmode_delay,
        'permissions_synced': channel.permissions_synced,
        'is_news': channel.is_news()
    }


async def _fetch_channel_data(
    channel: discord.TextChannel
) -> Tuple[List[discord.Webhook], List[discord.Invite], List[discord.Message]]:
    """Fetch webhooks, invites, and pinned messages from a channel."""
    webhooks = await channel.webhooks()
    invites = await channel.invites()
    pinned_messages = await channel.pins()
    pinned_messages.sort(key=lambda m: m.created_at)
    return webhooks, invites, pinned_messages


async def _create_new_channel(guild: discord.Guild,
                              properties: dict) -> discord.TextChannel:
    """Create a new channel with the same properties."""
    new_channel = await guild.create_text_channel(
        name=properties['name'],
        category=properties['category'],
        overwrites=properties['overwrites'],
        position=properties['position'],
        topic=properties['topic'],
        nsfw=properties['nsfw'],
        slowmode_delay=properties['slowmode_delay'])
    if properties['is_news']:
        await new_channel.edit(type=discord.ChannelType.news)
    return new_channel


async def _recreate_channel_data(new_channel: discord.TextChannel,
                                 webhooks: List[discord.Webhook],
                                 invites: List[discord.Invite],
                                 pinned_messages: List[discord.Message]):
    """Recreate webhooks, invites, and pinned messages in the new channel."""
    for webhook in webhooks:
        avatar = None
        if webhook.avatar:
            avatar = await webhook.avatar.read()
        await new_channel.create_webhook(name=webhook.name, avatar=avatar)

    for invite in invites:
        await new_channel.create_invite(
            max_age=invite.max_age if invite.max_age != 0 else None,
            max_uses=invite.max_uses if invite.max_uses != 0 else None,
            temporary=invite.temporary,
            unique=invite.unique)

    pinned_messages.sort(key=lambda m: m.created_at)
    for message in pinned_messages:
        content = message.content
        embeds = [embed for embed in message.embeds if embed.type == 'rich']
        files = []
        for attachment in message.attachments:
            try:
                file_data = await attachment.read()
                file = discord.File(io.BytesIO(file_data),
                                    filename=attachment.filename)
                files.append(file)
            except discord.HTTPException:
                continue

        try:
            if content or embeds or files:
                new_message = await new_channel.send(content=content or None,
                                                     embeds=embeds,
                                                     files=files)
                await new_message.pin()
                await asyncio.sleep(0.5)
                if files:
                    await asyncio.sleep(1)
            else:
                print("Skipping empty message")
        except discord.HTTPException as e:
            if e.code == 429:  # Rate limit error
                retry_after = e.retry_after
                await asyncio.sleep(retry_after)
                try:
                    if content or embeds or files:
                        new_message = await new_channel.send(content=content
                                                             or None,
                                                             embeds=embeds,
                                                             files=files)
                        await new_message.pin()
                except discord.HTTPException as e2:
                    print(f"Failed to send message after rate limit: {e2}")
            else:
                print(f"Error recreating message: {e}")
        for file in files:
            file.close()
        files.clear()


class RoleInfoView(discord.ui.View):

    def __init__(self, role: discord.Role):
        super().__init__()
        self.role = role

    @discord.ui.button(label="Show Members", style=discord.ButtonStyle.primary)
    async def show_members(self, interaction: discord.Interaction,
                           button: discord.ui.Button):
        members = self.role.members
        if not members:
            await interaction.response.send_message(
                "No members have this role.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Members with {self.role.name} role",
                              color=self.role.color)
        member_list = [member.mention for member in members]
        chunks = [
            member_list[i:i + 20] for i in range(0, len(member_list), 20)
        ]
        for i, chunk in enumerate(chunks, 1):
            embed.add_field(name=f"Members {i}",
                            value="\n".join(chunk),
                            inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CustomButton(discord.ui.Button):
    """Custom button for Discord UI."""

    def __init__(self, label: str, emoji: str, url: str):
        super().__init__(label=label,
                         emoji=emoji,
                         style=discord.ButtonStyle.link,
                         url=url)


class AvatarView(discord.ui.View):
    """View containing a button to download a user's avatar."""

    def __init__(self, user_avatar_url: str):
        super().__init__()
        self.add_item(CustomButton("Download", "⬇️", user_avatar_url))


class ConfirmationView(discord.ui.View):
    """View for confirmation prompts."""

    def __init__(self, ctx: commands.Context, channel: discord.TextChannel):
        super().__init__(timeout=30)  # 30 seconds to respond
        self.ctx = ctx
        self.channel = channel
        self.value = None

    async def interaction_check(self,
                                interaction: discord.Interaction) -> bool:
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(
                "Only the user who initiated the command can interact.",
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes",
                       style=discord.ButtonStyle.danger,
                       emoji="✅")
    async def confirm(self, interaction: discord.Interaction,
                      button: discord.ui.Button):
        await interaction.response.defer()
        self.value = True
        self.stop()

    @discord.ui.button(label="No",
                       style=discord.ButtonStyle.secondary,
                       emoji="❌")
    async def cancel(self, interaction: discord.Interaction,
                     button: discord.ui.Button):
        await interaction.response.defer()
        self.value = False
        self.stop()


class ChannelSelect(discord.ui.Select):
    """Select menu for choosing a channel to get its ID."""

    def __init__(self, options: List[discord.SelectOption], cog):
        super().__init__(placeholder="Select a channel to get its ID...",
                         min_values=1,
                         max_values=1,
                         options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        selected_channel = discord.utils.get(interaction.guild.channels,
                                             id=int(self.values[0]))
        if selected_channel:
            await interaction.response.send_message(
                f"The Channel {selected_channel.mention}'s ID is: ||`{selected_channel.id}`||",
                ephemeral=True)


class PrevButton(discord.ui.Button):
    """Button to go to the previous page of the channel dropdown."""

    def __init__(self):
        super().__init__(label="Prev", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        view: ChannelDropdownView = self.view
        view.page -= 1
        view.update_dropdown_options()
        await interaction.response.edit_message(view=view)


class NextButton(discord.ui.Button):
    """Button to go to the next page of the channel dropdown."""

    def __init__(self):
        super().__init__(label="Next", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        view: ChannelDropdownView = self.view
        view.page += 1
        view.update_dropdown_options()
        await interaction.response.edit_message(view=view)


class ChannelDropdownView(discord.ui.View):
    """View containing the channel select dropdown and pagination buttons."""

    def __init__(self,
                 ctx: commands.Context,
                 channels: List[discord.TextChannel],
                 cog,
                 page: int = 0):
        super().__init__(timeout=20)  # Set timeout to 20 seconds
        self.ctx = ctx
        self.channels = channels
        self.page = page
        self.cog = cog
        self.message = None  # Store the message object to delete it later
        self.update_dropdown_options()

    def update_dropdown_options(self):
        start_index = self.page * 25
        end_index = start_index + 25
        channel_options = [
            discord.SelectOption(label=channel.name, value=str(channel.id))
            for channel in self.channels[start_index:end_index]
        ]
        self.clear_items()
        self.add_item(ChannelSelect(channel_options, cog=self.cog))
        if len(self.channels) > 25:
            if self.page > 0:
                self.add_item(PrevButton())
            if end_index < len(self.channels):
                self.add_item(NextButton())

    async def on_timeout(self):
        """Handles the timeout for the view after 20 seconds of inactivity."""
        for child in self.children:
            child.disabled = True  # Disable all buttons in the view
        if self.message:  # Check if the message has been stored
            await self.message.edit(content="This interaction has timed out.",
                                    view=self)
            await asyncio.sleep(
                5)  # Wait for 5 seconds before deleting the message
            await self.message.delete()

    async def start(self):
        """Start the view by sending a message and storing its reference."""
        self.message = await self.ctx.send("Select a channel to get its ID:",
                                           view=self)


class Moderation(commands.Cog):
    """Cog for moderation commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession = aiohttp.ClientSession()
        self.nuke_cooldowns = commands.CooldownMapping.from_cooldown(
            1, 300, commands.BucketType.member)

    async def cog_unload(self):
        """Cleanup resources when the cog is unloaded."""
        await self.session.close()

    @commands.hybrid_command()
    async def avatar(self,
                     ctx: commands.Context,
                     user: Optional[discord.User] = None):
        """Show a user's avatar."""
        user = user or ctx.author
        embed = discord.Embed(title=f"{user}'s Profile Picture",
                              color=discord.Color.random())
        embed.set_image(url=user.display_avatar.url)
        embed.set_footer(text=f"Requested by {ctx.author.name}",
                         icon_url=ctx.author.avatar.url)
        await ctx.send(embed=embed, view=AvatarView(user.display_avatar.url))

    @commands.hybrid_command()
    @commands.has_permissions(kick_members=True)
    async def kick(self,
                   ctx: commands.Context,
                   member: discord.Member,
                   *,
                   reason: Optional[str] = None):
        """Kick a member."""
        reason = reason or "No reason provided"
        await member.kick(reason=reason)
        await ctx.send(
            f"{member.mention} has been kicked by **{ctx.author}**.\nReason: {reason}",
            ephemeral=True)

    @commands.hybrid_command()
    @commands.has_permissions(ban_members=True)
    async def ban(self,
                  ctx: commands.Context,
                  member: discord.Member,
                  *,
                  reason: Optional[str] = None):
        """Ban a member."""
        reason = reason or "No reason provided"
        await member.ban(reason=reason)
        await ctx.send(f"{member.mention} has been banned from the guild.",
                       ephemeral=True)

    @commands.hybrid_command()
    @commands.has_permissions(kick_members=True)
    async def warn(self,
                   ctx: commands.Context,
                   member: discord.Member,
                   *,
                   reason: Optional[str] = None):
        """Warn a member."""
        reason = reason or "No reason provided"
        await ctx.send(
            f"{member.mention}, you have been warned by **{ctx.author}**.\nReason: {reason}"
        )

    @commands.hybrid_command(name="purge")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, amount: int = 2):
        """Delete messages."""
        if not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("This command can only be used in a text channel.",
                           ephemeral=True)
            return

        try:
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.send(f"Deleted {len(deleted)} messages.",
                           ephemeral=True,
                           delete_after=5)
        except discord.Forbidden:
            await ctx.send(
                "I don't have the required permissions to delete messages in this channel.",
                ephemeral=True)
        except discord.HTTPException as e:
            await ctx.send(
                f"An error occurred while trying to delete messages: {str(e)}",
                ephemeral=True)

    @commands.hybrid_command()
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: str):
        """Unban a user."""
        try:
            user = await self.bot.fetch_user(int(user_id))
            await ctx.guild.unban(user)
            await ctx.send(f"{user.mention} has been unbanned.",
                           ephemeral=True)
        except (ValueError, discord.NotFound):
            await ctx.send("User not found or not banned.", ephemeral=True)

    @commands.hybrid_command()
    async def channel_id(self, ctx: commands.Context):
        """Get a channel's ID by selecting from a dropdown menu."""
        accessible_channels = [
            channel for channel in ctx.guild.text_channels
            if channel.permissions_for(ctx.author).view_channel
        ]
        if not accessible_channels:
            await ctx.send("No accessible channels found.", ephemeral=True)
            return
        view = ChannelDropdownView(ctx, accessible_channels, self)
        await view.start()

    @commands.hybrid_command()
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context,
                       channel: discord.TextChannel, delay: int):
        """Set a channel's slowmode delay."""
        if delay < 0:
            await ctx.send(
                "The slowmode delay must be greater than or equal to 0 seconds.",
                ephemeral=True)
            return
        await channel.edit(slowmode_delay=delay)
        await ctx.send(
            f"Slowmode for {channel.mention} has been changed to {delay} seconds."
        )

    @commands.hybrid_command()
    @commands.has_permissions(manage_channels=True)
    async def nuke(self,
                   ctx: commands.Context,
                   channel: Optional[discord.TextChannel] = None):
        """Delete and recreate a channel, with confirmation."""
        # Check cooldown
        retry_after = self.nuke_cooldowns.update_rate_limit(ctx.message)
        if retry_after:
            await ctx.send(
                f"You need to wait {retry_after:.1f} seconds before using the nuke command again.",
                ephemeral=True)
            return

        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("This command can only be used in a text channel.",
                           ephemeral=True)
            return

        confirmation_view = ConfirmationView(ctx, channel)
        confirmation_message = await ctx.send(
            f"Are you sure you want to nuke {channel.mention if channel else 'this channel'}? This action cannot be undone.",
            view=confirmation_view)
        timeout = await confirmation_view.wait()

        for child in confirmation_view.children:
            child.disabled = True
        await confirmation_message.edit(view=confirmation_view)

        if confirmation_view.value is None:
            await ctx.send("Nuke command timed out. No action was taken.",
                           ephemeral=True)
            return
        elif confirmation_view.value is False:
            await ctx.send("Nuke command cancelled.", ephemeral=True)
            return

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        else:
            await ctx.typing()

        properties = await _get_channel_properties(channel)
        webhooks, invites, pinned_messages = await _fetch_channel_data(channel)
        await channel.delete(reason=f"Channel nuked by {ctx.author}")
        new_channel = await _create_new_channel(ctx.guild, properties)
        await _recreate_channel_data(new_channel, webhooks, invites,
                                     pinned_messages)

        message_content = (
            f"Channel has been nuked by {ctx.author.mention}\n"
            f"Channel {new_channel.mention} has been nuked and recreated.")
        try:
            await new_channel.send(message_content)
        except discord.Forbidden:
            await ctx.author.send(
                f"Nuke operation completed, but the bot couldn't send a message to {new_channel.mention} due to missing permissions."
            )
        try:
            if ctx.interaction:
                await ctx.interaction.followup.send(
                    f"Nuked and recreated {new_channel.mention}.",
                    ephemeral=True)
            else:
                await ctx.send(f"Nuked and recreated {new_channel.mention}.")
        except discord.HTTPException:
            pass

    @commands.hybrid_command(
        name="nick", description="Change the nickname of a user on a server.")
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    @app_commands.describe(user="The user that should have a new nickname.",
                           nickname="The new nickname that should be set.")
    async def nick(self,
                   context: Context,
                   user: discord.User,
                   *,
                   nickname: Optional[str] = None) -> None:
        """Change the nickname of a user on a server.
        :param context: The hybrid command context.
        :param user: The user that should have its nickname changed.
        :param nickname: The new nickname of the user. Default is None, which will reset the nickname.
        """
        member = context.guild.get_member(
            user.id) or await context.guild.fetch_member(user.id)
        try:
            await member.edit(nick=nickname)
            embed = discord.Embed(
                description=f"**{member}'s** new nickname is **{nickname}**!",
                color=0xBEBEFE)
            await context.send(embed=embed)
        except:
            embed = discord.Embed(
                description=
                "An error occurred while trying to change the nickname of the user. Make sure my role is above the role of the user you want to change the nickname.",
                color=0xE02B2B)
            await context.send(embed=embed)

    @commands.hybrid_command()
    async def ping(self, ctx: commands.Context):
        """Check bot latency."""
        websocket_latency = round(self.bot.latency * 1000, 2)
        start_time = time.time()
        message = await ctx.send("Pinging...")
        response_time = round((time.time() - start_time) * 1000, 2)
        embed = discord.Embed(title="🏓 Pong!", color=0x2f3131)
        embed.add_field(name="WebSocket Latency",
                        value=f"`{websocket_latency}ms`")
        embed.add_field(name="Response Time", value=f"`{response_time}ms`")
        embed.set_footer(text="Bot Latency Information")
        await message.edit(content=None, embed=embed)

    @commands.hybrid_command(name="role-add")
    @commands.has_permissions(manage_roles=True)
    async def role_add(self,
                       ctx: commands.Context,
                       member: discord.Member,
                       role: discord.Role,
                       time: Optional[int] = None):
        """Add a role to a user optionally for a limited duration."""
        await member.add_roles(role)
        await ctx.send(f"Added role {role.mention} to {member.mention}.",
                       ephemeral=True)
        if time:
            await asyncio.sleep(time)
            await member.remove_roles(role)
            await ctx.send(
                f"Removed role {role.mention} from {member.mention} after {time} seconds.",
                ephemeral=True)

    @commands.hybrid_command(name="role-remove")
    @commands.has_permissions(manage_roles=True)
    async def role_remove(self, ctx: commands.Context, member: discord.Member,
                          role: discord.Role):
        """Remove a role from a user."""
        await member.remove_roles(role)
        await ctx.send(f"Removed role {role.mention} from {member.mention}.",
                       ephemeral=True)

    @commands.hybrid_command(name="role-list")
    async def role_list(self, ctx: commands.Context, member: discord.Member):
        """List all roles of a user."""
        roles = [
            role.mention for role in member.roles
            if role != ctx.guild.default_role
        ]
        if roles:
            roles_str = "\n".join(f"- {role}" for role in roles)
            embed = discord.Embed(title=f"{member.display_name}'s Roles",
                                  description=roles_str,
                                  color=discord.Color.random())
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{member.mention} has no roles.")

    @commands.hybrid_command(name="role-info")
    async def role_info(self, ctx: commands.Context, role: discord.Role):
        """Get information about a role."""
        embed = discord.Embed(title=f"Role Info: {role.name}",
                              color=role.color)
        embed.add_field(name="ID", value=role.id)
        embed.add_field(name="Mentionable", value=role.mentionable)
        embed.add_field(name="Hoist", value=role.hoist)
        embed.add_field(name="Position", value=role.position)
        embed.add_field(name="Created At",
                        value=role.created_at.strftime("%Y-%m-%d %H:%M:%S"))
        embed.add_field(name="Member Count", value=len(role.members))
        view = RoleInfoView(role)
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="userinfo",
                             aliases=["user", "stats"],
                             description="Displays user information.")
    async def userinfo(self, ctx, member: discord.Member = None):
        """If member is not provided, use the author of the context"""
        if member is None:
            member = ctx.author
        # Create an embed with the user's information
        embed = discord.Embed(
            title=f"{member.display_name}'s User Information",
            description="All info about the user",
            color=discord.Color.blue())
        embed.set_author(name="User Info",
                         icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Name", value=member.name, inline=False)
        embed.add_field(name="NickName",
                        value=member.display_name,
                        inline=False)
        embed.add_field(name="ID", value=member.id, inline=False)
        embed.add_field(name="Top Role",
                        value=member.top_role.mention,
                        inline=False)
        embed.add_field(name="Status",
                        value=str(member.status).title(),
                        inline=False)
        embed.add_field(name="Bot User",
                        value="Yes" if member.bot else "No",
                        inline=False)
        embed.add_field(
            name="ID Creation",
            value=member.created_at.strftime("%A, %d. %B %Y at %H:%M:%S"),
            inline=False)
        # Send the embed in the context channel
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="serverinfo",
        description="Get some useful (or not) information about the server.")
    async def serverinfo(self, context: Context) -> None:
        """Get some useful (or not) information about the server."""
        guild = context.guild
        roles = [f"• {role.mention}"
                 for role in guild.roles]  # Add bullet before each role
        num_roles = len(roles)
        max_roles_per_field = 15

        # Get server owner
        owner = guild.owner

        # Embed setup
        embed = discord.Embed(
            title="**Server Information**",
            description=
            f"**Server Name:** {guild.name} -> Owner: {owner.display_name}",
            color=0xBEBEFE)

        # Set server profile picture as thumbnail
        if guild.icon is not None:
            embed.set_thumbnail(url=guild.icon.url)

        # Add roles to embed
        for i in range(0, min(num_roles, 15 * 25), max_roles_per_field):
            chunk = roles[i:i + max_roles_per_field]
            embed.add_field(name=f"Roles {i + 1} to {i + len(chunk)}",
                            value="\n".join(chunk),
                            inline=True)

        # Notice if more roles than can be displayed
        if num_roles > 15 * 25:
            embed.add_field(name="Notice",
                            value=f"Displaying first {15 * 25} roles only.",
                            inline=False)

        # Additional fields
        embed.add_field(name="Server ID", value=guild.id)
        embed.add_field(name="Member Count", value=guild.member_count)
        embed.add_field(name="Text/Voice Channels",
                        value=f"{len(guild.channels)}")
        embed.set_footer(text=f"Created at: {guild.created_at}")

        await context.send(embed=embed)

    @commands.hybrid_command(
        name="botinfo",
        description="Get some useful (or not) information about the bot.")
    async def botinfo(self, context: Context) -> None:
        """Get some useful (or not) information about the bot."""
        embed = discord.Embed(
            description="N.I.R.A -> NEURAL INTERACTIVE RESPONSIVE AGENT.",
            color=0xBEBEFE,
        )
        embed.set_author(name="Bot Information")
        embed.add_field(name="Owner:",
                        value="<@754188594461147217>",
                        inline=True)
        embed.add_field(name="Python Version:",
                        value=f"{platform.python_version()}",
                        inline=True)
        embed.add_field(
            name="Prefix:",
            value="/ (Slash Commands) or . command for normal commands",
            inline=False)
        embed.set_footer(text=f"Requested by {context.author}")
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="invite",
        description="Get the invite link of the bot to be able to invite it.")
    async def invite(self, context: Context) -> None:
        """Get the invite link of the bot to be able to invite it."""
        embed = discord.Embed(
            description=
            "Invite me by clicking [here](https://discord.gg/T5jgMenbQ8).",
            color=0xD75BF4)
        try:
            await context.author.send(embed=embed)
            await context.send("I sent you a private message!")
        except discord.Forbidden:
            await context.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Setup the Moderation cog."""
    await bot.add_cog(Moderation(bot))
