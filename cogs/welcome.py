import discord
from discord.ext import commands
from discord import app_commands
from utils.wel import get_welcome_card
from database import Database


class WelcomeModal(discord.ui.Modal, title="Set Welcome Message"):
    message = discord.ui.TextInput(
        label="Welcome Message",
        style=discord.TextStyle.long,
        placeholder="Enter your custom welcome message here...",
        max_length=2000,
        required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.set_message_callback(interaction, str(self.message))


class WelcomeCmds(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = Database()

    async def cog_load(self):
        await self.db.initialize()
        await self.create_welcome_table()

    async def cog_unload(self):
        await self.db.close()

    async def create_welcome_table(self):
        """Create the 'welcome' table if it doesn't exist"""
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS welcome (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT,
                message TEXT
            )
            """)

    @app_commands.command(name="welcome-channel",
                          description="Set the welcome channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel(self, interaction: discord.Interaction,
                          channel: discord.TextChannel):
        """
        Set the channel where welcome messages will be sent.

        Parameters:
        - channel: The text channel to set as the welcome channel.
        """
        await interaction.response.defer(ephemeral=True)
        await self.db.execute(
            "INSERT INTO welcome (guild_id, channel_id) VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET channel_id = $2",
            interaction.guild.id,
            channel.id,
        )
        await interaction.followup.send(
            f"Set welcome channel to {channel.mention}", ephemeral=True)

    @app_commands.command(name="welcome-message",
                          description="Set the welcome message")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_message(self, interaction: discord.Interaction):
        """
        Open a modal to set the custom welcome message for new members.
        """
        check = await self.db.fetch(
            "SELECT * FROM welcome WHERE guild_id = $1", interaction.guild.id)
        if not check:
            await interaction.response.send_message(
                "Please set a welcome channel first!", ephemeral=True)
            return

        modal = WelcomeModal(self)
        await interaction.response.send_modal(modal)

    async def set_message_callback(self, interaction: discord.Interaction,
                                   message: str):
        """Callback for the welcome message modal submission"""
        await self.db.execute(
            "UPDATE welcome SET message = $1 WHERE guild_id = $2",
            message,
            interaction.guild.id,
        )
        await interaction.followup.send(f"Set welcome message to: {message}",
                                        ephemeral=True)

    @app_commands.command(name="welcome-test",
                          description="Test the welcome message")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test(self,
                   interaction: discord.Interaction,
                   user: discord.Member = None):
        """
        Test the welcome message by simulating a new member join.

        Parameters:
        - user: The user to simulate as a new member (optional, defaults to the command user).
        """
        await interaction.response.defer(ephemeral=True)
        user = user or interaction.user
        sent = await self.on_member_join(user)
        if sent:
            await interaction.followup.send("Sent test welcome message!",
                                            ephemeral=True)
        else:
            await interaction.followup.send(
                "Failed to send test welcome message. Please check your welcome channel and message settings.",
                ephemeral=True)

    async def on_member_join(self, member: discord.Member):
        record = await self.db.fetch(
            "SELECT * FROM welcome WHERE guild_id = $1", member.guild.id)
        if not record:
            return False  # No welcome settings for this guild
        channel_id = record[0]['channel_id']
        message = record[0].get('message')
        channel = member.guild.get_channel(channel_id)
        if not channel:
            return False  # Channel not found

        try:
            welcome_card = await get_welcome_card(member)
        except Exception:
            welcome_card = None

        # Prepare placeholder values
        placeholders = {
            "user.mention": member.mention,
            "user.name": member.name,
            "user.id": member.id,
            "guild.name": member.guild.name,
            "guild.member_count": member.guild.member_count,
        }

        # Default embed message using all placeholders and markdown
        default_embed = discord.Embed(
            title=f"Welcome to {placeholders['guild.name']}! 🎉",
            description=
            (f"**Hello {placeholders['user.mention']}!**\n\n"
             f"Welcome to our vibrant community. We're thrilled to have you join us as our **{placeholders['guild.member_count']}th** member!\n\n"
             "🔹 **Your Details:**\n"
             f"• Name: `{placeholders['user.name']}`\n"
             f"• ID: `{placeholders['user.id']}`\n\n"
             "🔹 **Getting Started:**\n"
             "• Check out our rules and guidelines\n"
             "• Introduce yourself in the introductions channel\n"
             "• Explore our various topic-specific channels\n\n"
             "If you have any questions, feel free to ask our friendly community or moderators.\n\n"
             "We hope you have a fantastic time here! 🌟"),
            color=discord.Color.blue(),
        )

        # Format the message with placeholders if a custom message is set
        if message:
            try:
                formatted_message = message.format(**placeholders)
                embed = discord.Embed(description=formatted_message,
                                      color=discord.Color.blue())
            except KeyError:
                # If there's an error with placeholders, fall back to the default embed
                embed = default_embed
        else:
            embed = default_embed

        if welcome_card:
            welcome_card.seek(0)
            file = discord.File(welcome_card, filename="welcome.png")
            embed.set_image(url="attachment://welcome.png")
        else:
            file = None
            embed.set_thumbnail(url=member.display_avatar.url)

        try:
            await channel.send(content=member.mention, embed=embed, file=file)
            return True
        except discord.errors.Forbidden:
            return False  # Bot doesn't have permission to send messages in the channel
        except Exception:
            return False  # Error sending welcome message


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCmds(bot))
