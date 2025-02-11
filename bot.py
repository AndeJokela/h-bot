import asyncio
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import disnake
import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError
from disnake.ext import commands, tasks
from disnake.ext.commands import CommandSyncFlags
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# YTDL options (downloads are disabled; we always stream)
ytdl_format_options = {
    "format": "bestaudio/best",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
}

ffmpeg_options = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)
executor = ThreadPoolExecutor()


def format_duration(duration: Optional[float]) -> str:
    """Convert a duration in seconds into a MM:SS string."""
    if duration is None:
        return "Unknown"
    minutes, seconds = divmod(int(duration), 60)
    return f"{minutes:02d}:{seconds:02d}"


class YTDLSource(disnake.PCMVolumeTransformer):
    def __init__(self, source: disnake.AudioSource, *, data: dict, volume: float = 0.3):
        super().__init__(source, volume)
        self.data = data
        self.title: str = data.get("title", "Unknown Title")
        self.url: str = data.get("url", "")
        self.webpage_url: str = data.get("webpage_url", "")
        self.thumbnail: Optional[str] = data.get("thumbnail")
        self.duration: str = data.get("duration_string") or format_duration(data.get("duration"))

    @classmethod
    async def create_source(
        cls, query: str, *, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> Optional["YTDLSource"]:
        """
        Extract song info and create an audio source for streaming.
        """
        loop = loop or asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(executor, lambda: ytdl.extract_info(query, download=False))
        except DownloadError as e:
            logging.error(f"YT-DLP download error: {e}")
            return None
        except ExtractorError as e:
            logging.error(f"YT-DLP extractor error: {e}")
            return None
        except Exception as e:
            logging.error(f"Error extracting info: {e}")
            return None

        if not data:
            logging.error("No data found for the query.")
            return None

        # If a playlist or multiple entries are returned, use the first one.
        if "entries" in data:
            try:
                data = data["entries"][0]
            except (IndexError, KeyError):
                logging.error("Create source failed: No entries found.")
                return None

        source_url = data.get("url")
        if not source_url:
            logging.error("No streaming URL found in data.")
            return None

        source = disnake.FFmpegPCMAudio(source_url, **ffmpeg_options)
        return cls(source, data=data)


class Player:
    def __init__(self, guild: disnake.Guild, loop: asyncio.AbstractEventLoop, on_remove: Callable[[], None]):
        self.guild = guild
        self.loop = loop
        self.voice_client: Optional[disnake.VoiceClient] = None
        self.queue: list[YTDLSource] = []
        self.idle_counter = 0
        self.last_channel: Optional[disnake.TextChannel] = None
        self.on_remove = on_remove
        self.dc_timer.start()  # Start the disconnect timer

    def create_embed(self, embed_title: str, source: YTDLSource, embed_color: disnake.Color) -> disnake.Embed:
        """
        Create an embed for a given source.
        """
        embed = disnake.Embed(
            title=embed_title,
            color=embed_color,
            description=f"[{source.title}]({source.webpage_url})\n\nDuration: {source.duration}"
        )
        if source.thumbnail:
            embed.set_thumbnail(url=source.thumbnail)
        return embed

    @tasks.loop(seconds=10)
    async def dc_timer(self) -> None:
        """Disconnect the bot if idle for 10 minutes."""
        if self.voice_client and not self.voice_client.is_playing():
            self.idle_counter += 10
            if self.idle_counter >= 600:
                await self.disconnect()
        else:
            self.idle_counter = 0

    async def disconnect(self) -> None:
        """Disconnect from the voice channel and perform cleanup."""
        if self.voice_client:
            await self.voice_client.disconnect()
            self.voice_client = None
            self.queue.clear()
            logging.info(f"Disconnected from {self.guild.name}.")
        self.dc_timer.stop()
        if self.on_remove:
            self.on_remove()

    async def play_song(self, inter: disnake.ApplicationCommandInteraction, query: str) -> None:
        """Play or queue a requested song."""
        await inter.response.defer()
        self.last_channel = inter.channel  # Save channel for follow-up messages

        # Log when a song is requested
        logging.info(f"Song requested by {inter.author} in guild '{inter.guild.name}': {query}")

        if not await self.ensure_voice(inter):
            return

        source = await YTDLSource.create_source(query, loop=self.loop)
        if not source:
            await inter.edit_original_message(
                content="An error occurred while retrieving the song. Please check your query or try a different song."
            )
            return

        if self.voice_client.is_playing():
            self.queue.append(source)
            embed = self.create_embed("Queued", source, disnake.Color.blue())
            await inter.edit_original_message(embed=embed)
            logging.info(f"Song queued: {source.title} (requested by {inter.author} in '{inter.guild.name}')")
        else:
            self.voice_client.play(
                source,
                after=lambda err: self.loop.call_soon_threadsafe(self.play_next, err)
            )
            embed = self.create_embed("Now Playing", source, disnake.Color.green())
            await inter.edit_original_message(embed=embed)
            logging.info(f"Now playing: {source.title} (requested by {inter.author} in '{inter.guild.name}')")

    def play_next(self, error: Optional[Exception] = None) -> None:
        """Play the next song in the queue."""
        if error:
            logging.error(f"Playback error: {error}")
            if self.last_channel:
                coro = self.last_channel.send(f"An error occurred during playback: {error}")
                asyncio.run_coroutine_threadsafe(coro, self.loop)

        if self.queue:
            next_source = self.queue.pop(0)
            if self.voice_client:
                self.voice_client.play(
                    next_source,
                    after=lambda err: self.loop.call_soon_threadsafe(self.play_next, err)
                )
            if self.last_channel:
                embed = self.create_embed("Now Playing", next_source, disnake.Color.green())
                coro = self.last_channel.send(embed=embed)
                asyncio.run_coroutine_threadsafe(coro, self.loop)
            logging.info(f"Now playing next song: {next_source.title} in guild '{self.guild.name}'")

    async def ensure_voice(self, inter: disnake.ApplicationCommandInteraction) -> bool:
        """Ensure the bot is connected to the caller's voice channel."""
        try:
            if inter.author.voice and inter.author.voice.channel:
                channel = inter.author.voice.channel
                if not self.voice_client:
                    try:
                        self.voice_client = await channel.connect(timeout=30)
                    except (asyncio.TimeoutError, disnake.Forbidden) as e:
                        await inter.edit_original_message(content=f"Failed to connect: {e}")
                        return False
                elif self.voice_client.channel != channel:
                    await self.voice_client.move_to(channel)
                return True
            else:
                await inter.edit_original_message(content="You must be in a voice channel to use this command.")
                return False
        except Exception as e:
            logging.error(f"Voice connection error: {e}")
            await inter.edit_original_message(content=f"Voice connection error: {e}")
            return False

    async def show_queue(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Display the current song queue."""
        if not self.queue:
            await inter.response.send_message("The queue is currently empty.")
            return

        description = ""
        for idx, song in enumerate(self.queue, 1):
            description += f"{idx}. [{song.title}]({song.webpage_url})\n"

        if len(description) > 4096:
            description = description[:4093] + "..."

        embed = disnake.Embed(title="Queue", color=disnake.Color.purple(), description=description)
        await inter.response.send_message(embed=embed)


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        # Mapping of guild IDs to Player instances
        self.players: dict[int, Player] = {}

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        logging.info("MusicCog is ready.")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState
    ) -> None:
        """
        Automatically clean up when the bot is manually disconnected from a voice channel.
        """
        if member.id != self.bot.user.id:
            return

        # Bot was previously connected and now is not connected to any channel.
        if before.channel is not None and after.channel is None:
            guild_id = member.guild.id
            if guild_id in self.players:
                player = self.players[guild_id]
                await player.disconnect()  # Cleanup and removal via on_remove callback

    @commands.slash_command(description="Play a song from URL or YouTube search")
    async def play(self, inter: disnake.ApplicationCommandInteraction, song: str) -> None:
        guild_id = inter.guild.id

        if guild_id not in self.players:
            def remove_player() -> None:
                if guild_id in self.players:
                    del self.players[guild_id]

            self.players[guild_id] = Player(inter.guild, self.bot.loop, on_remove=remove_player)

        player = self.players[guild_id]
        await player.play_song(inter, song)

    @commands.slash_command(description="Skip the currently playing song")
    async def skip(self, inter: disnake.ApplicationCommandInteraction) -> None:
        player = self.players.get(inter.guild.id)
        if player and player.voice_client and player.voice_client.is_playing():
            player.voice_client.stop()
            await inter.response.send_message(content="Song skipped.")
        else:
            await inter.response.send_message(content="Nothing is playing to skip.")

    @commands.slash_command(description="Disconnect the bot from the voice channel")
    async def leave(self, inter: disnake.ApplicationCommandInteraction) -> None:
        player = self.players.pop(inter.guild.id, None)
        if player:
            await player.disconnect()
            await inter.response.send_message(content="Disconnected from the voice channel.")
        else:
            await inter.response.send_message(content="The bot is not connected to any voice channel.")

    @commands.slash_command(description="Show the current song queue")
    async def queue(self, inter: disnake.ApplicationCommandInteraction) -> None:
        player = self.players.get(inter.guild.id)
        if player:
            await player.show_queue(inter)
        else:
            await inter.response.send_message("The queue is currently empty.")

    @commands.slash_command(description="Clear the current song queue")
    async def clear(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Clear all songs in the current queue."""
        player = self.players.get(inter.guild.id)
        if player and player.queue:
            player.queue.clear()
            await inter.response.send_message("The queue has been cleared.")
        else:
            await inter.response.send_message("The queue is already empty.")

    @commands.slash_command(description="Remove a specific song from the queue")
    async def remove(self, inter: disnake.ApplicationCommandInteraction, position: str) -> None:
        """
        Remove a song from the queue by its position (1-indexed).
        Use -1 or "last" to remove the last song.
        For example, /remove 2 will remove the second song in the queue.
        """
        player = self.players.get(inter.guild.id)
        if not player or not player.queue:
            await inter.response.send_message("There is no active queue to remove songs from.")
            return

        # Determine the position to remove
        if position.lower() == "last" or position == "-1":
            pos = len(player.queue)
        else:
            try:
                pos = int(position)
            except ValueError:
                await inter.response.send_message("Invalid input. Please provide a number or 'last'.")
                return

        if pos < 1 or pos > len(player.queue):
            await inter.response.send_message("Invalid song position. Please provide a valid number.")
            return

        removed_song = player.queue.pop(pos - 1)
        await inter.response.send_message(f"Removed **{removed_song.title}**.")



# Create the bot instance and add the cog
sync_flags = CommandSyncFlags(sync_commands_debug=True)
bot = commands.InteractionBot(command_sync_flags=sync_flags)

# Global listener to log whenever any command is used.
@bot.listen()
async def on_application_command(inter: disnake.ApplicationCommandInteraction):
    logging.info(f"Command '{inter.data.name}' was invoked by {inter.author} in guild '{inter.guild.name}'.")

token = os.getenv("DISCORD_BOT_TOKEN")
if not token:
    raise ValueError("Bot token not found. Set DISCORD_BOT_TOKEN as an environment variable.")

bot.add_cog(MusicCog(bot))
logging.info("Starting bot...")
bot.run(token)
