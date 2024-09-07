from typing import Optional, Dict, Any
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from aiohttp import ClientSession


class AniListCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot: commands.Bot = bot
        self.anilist_client_id: str = '21007'
        self.anilist_client_secret: str = 'LJyHm3cISMWdWgpY2x7LZkCJ8WujGfb3C5BAcRHj'
        self.anilist_redirect_uri: str = 'https://anilist.co/api/v2/oauth/pin'
        self.anilist_auth_url: str = f'https://anilist.co/api/v2/oauth/authorize?client_id={self.anilist_client_id}&response_type=code&redirect_uri={self.anilist_redirect_uri}'
        self.anilist_token_url: str = 'https://anilist.co/api/v2/oauth/token'
        self.anilist_api_url: str = 'https://graphql.anilist.co'

    @app_commands.command(name="anilist",
                          description="Connect to AniList or view your stats")
    async def anilist(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("AniList Integration",
                                                view=AniListView(self),
                                                ephemeral=True)

    async def get_access_token(self, auth_code: str) -> Optional[str]:
        data: Dict[str, str] = {
            'grant_type': 'authorization_code',
            'client_id': self.anilist_client_id,
            'client_secret': self.anilist_client_secret,
            'redirect_uri': self.anilist_redirect_uri,
            'code': auth_code
        }

        async with ClientSession() as session:
            async with session.post(self.anilist_token_url,
                                    data=data) as response:
                if response.status == 200:
                    token_data: Dict[str, Any] = await response.json()
                    return token_data.get('access_token')
        return None

    async def fetch_anilist_data(
            self, access_token: str) -> Optional[Dict[str, Any]]:
        query: str = '''
        query {
            Viewer {
                name
                avatar {
                    medium
                }
                statistics {
                    anime {
                        count
                        episodesWatched
                        minutesWatched
                    }
                    manga {
                        count
                        chaptersRead
                        volumesRead
                    }
                }
            }
        }
        '''

        headers: Dict[str, str] = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        async with ClientSession() as session:
            async with session.post(self.anilist_api_url,
                                    json={'query': query},
                                    headers=headers) as response:
                if response.status == 200:
                    data: Dict[str, Any] = await response.json()
                    if 'errors' in data:
                        error_message = data['errors'][0]['message']
                        raise Exception(f"AniList API Error: {error_message}")
                    return data.get('data', {}).get('Viewer')
                else:
                    raise Exception(
                        f"AniList API returned status code {response.status}")

    def create_stats_embed(self, stats: Dict[str, Any]) -> discord.Embed:
        embed: discord.Embed = discord.Embed(
            title=f"AniList Stats for {stats['name']}", color=0x02a9ff)
        embed.set_thumbnail(url=stats['avatar']['medium'])

        anime_stats: Dict[str, int] = stats['statistics']['anime']
        manga_stats: Dict[str, int] = stats['statistics']['manga']

        embed.add_field(name="Anime",
                        value=f"Count: {anime_stats['count']}\n"
                        f"Episodes: {anime_stats['episodesWatched']}\n"
                        f"Time: {anime_stats['minutesWatched'] // 1440} days",
                        inline=False)

        embed.add_field(name="Manga",
                        value=f"Count: {manga_stats['count']}\n"
                        f"Chapters: {manga_stats['chaptersRead']}\n"
                        f"Volumes: {manga_stats['volumesRead']}",
                        inline=False)

        return embed


class AniListView(discord.ui.View):

    def __init__(self, cog: AniListCog):
        super().__init__()
        self.cog: AniListCog = cog

    @discord.ui.button(label="Get Auth Code",
                       style=discord.ButtonStyle.primary)
    async def get_auth_code(self, interaction: discord.Interaction,
                            button: discord.ui.Button) -> None:
        instructions: str = (
            f"Please follow these steps to get your authorization code:\n\n"
            f"1. Click this link: {self.cog.anilist_auth_url}\n"
            f"2. If prompted, log in to your AniList account and authorize the application.\n"
            f"3. You will be redirected to a page that says 'Authorization Complete'.\n"
            f"4. On that page, you will see a 'PIN' or 'Authorization Code'. Copy this code.\n"
            f"5. Come back here and click the 'Enter Auth Code' button to enter the code you copied.\n\n"
            f"If you have any issues, please let me know!")
        await interaction.response.send_message(instructions, ephemeral=True)

    @discord.ui.button(label="Enter Auth Code",
                       style=discord.ButtonStyle.green)
    async def enter_auth_code(self, interaction: discord.Interaction,
                              button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AniListAuthModal(self.cog))


class AniListAuthModal(discord.ui.Modal, title='Enter AniList Auth Code'):

    def __init__(self, cog: AniListCog):
        super().__init__()
        self.cog: AniListCog = cog

    auth_code: discord.ui.TextInput = discord.ui.TextInput(
        label='Enter your AniList authorization code',
        placeholder='Paste your auth code here...',
        required=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            access_token: Optional[str] = await self.cog.get_access_token(
                self.auth_code.value)
            if not access_token:
                await interaction.followup.send(
                    "Failed to authenticate. Please try again.",
                    ephemeral=True)
                return

            stats: Optional[Dict[
                str, Any]] = await self.cog.fetch_anilist_data(access_token)
            if not stats:
                await interaction.followup.send(
                    "Failed to fetch AniList data. Please try again.",
                    ephemeral=True)
                return

            embed: discord.Embed = self.cog.create_stats_embed(stats)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}",
                                            ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AniListCog(bot))
