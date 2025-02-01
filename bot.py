import asyncio
import os
import disnake
import yt_dlp
from disnake.ext import commands, tasks
import logging
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Ensure a download directory exists
DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# YTDL options
ytdl_format_options = {
    "format": "bestaudio/best",
    "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
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
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)
executor = ThreadPoolExecutor()

class YTDLSource(disnake.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.3):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("url")
        self.webpage_url = data.get("webpage_url")
        self.thumbnail = data.get("thumbnail")
        self.duration = data.get("duration_string") or "Unknown"

    @classmethod
    async def create_source(cls, query, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(executor, lambda: ytdl.extract_info(query, download=not stream))
        except Exception as e:
            logging.error(f"Error extracting info: {e}")
            return None

        if not data:
            logging.error("No data found for the query.")
            return None

        if "entries" in data:
            data = data["entries"][0]

        if not stream:
            filename = os.path.abspath(ytdl.prepare_filename(data))
            if not os.path.exists(filename):
                logging.error(f"Downloaded file not found: {filename}")
                return None
            source = disnake.FFmpegPCMAudio(filename)
        else:
            filename = data["url"]
            source = disnake.FFmpegPCMAudio(filename, **ffmpeg_options)

        return cls(source, data=data)

class Player:
    def __init__(self, guild, loop, on_remove):
        self.guild = guild
        self.loop = loop
        self.voice_client = None
        self.queue = []
        self.idle_counter = 0
        self.last_channel = None
        self.on_remove = on_remove
        self.dc_timer.start()

    @tasks.loop(seconds=10)
    async def dc_timer(self):
        if self.voice_client and not self.voice_client.is_playing():
            self.idle_counter += 10
            if self.idle_counter >= 600:
                await self.disconnect()
        else:
            self.idle_counter = 0

    async def disconnect(self):
        if self.voice_client:
            await self.voice_client.disconnect()
            self.voice_client = None
            self.queue.clear()
            logging.info(f"Disconnected from {self.guild.name}.")
        self.dc_timer.stop()
        if self.on_remove:
            self.on_remove()

    async def play_song(self, inter, query):
        await inter.response.defer()
        self.last_channel = inter.channel

        if not await self.ensure_voice(inter):
            return

        source = await YTDLSource.create_source(query, loop=self.loop, stream=True)
        if not source:
            await inter.edit_original_message(content="Could not find the requested song.")
            return

        if self.voice_client.is_playing():
            self.queue.append(source)
            embed = disnake.Embed(
                title="Queued",
                color=disnake.Color.blue(),
                description=f"[{source.title}]({source.webpage_url})\n\nDuration: {source.duration}"
            )
            if source.thumbnail:
                embed.set_thumbnail(url=source.thumbnail)
            await inter.edit_original_message(embed=embed)
        else:
            self.voice_client.play(source, after=lambda e: self.loop.call_soon_threadsafe(lambda: self.play_next(e)))
            embed = disnake.Embed(
                title="Now Playing",
                color=disnake.Color.green(),
                description=f"[{source.title}]({source.webpage_url})\n\nDuration: {source.duration}"
            )
            if source.thumbnail:
                embed.set_thumbnail(url=source.thumbnail)
            await inter.edit_original_message(embed=embed)

    def play_next(self, error=None):
        if error:
            logging.error(f"Playback error: {error}")
            if self.last_channel:
                coro = self.last_channel.send(f"An error occurred: {error}")
                asyncio.run_coroutine_threadsafe(coro, self.loop)
        
        if self.queue:
            source = self.queue.pop(0)
            self.voice_client.play(source, after=lambda e: self.loop.call_soon_threadsafe(lambda: self.play_next(e)))
            if self.last_channel:
                embed = disnake.Embed(
                    title="Now Playing",
                    color=disnake.Color.green(),
                    description=f"[{source.title}]({source.webpage_url})\n\nDuration: {source.duration}"
                )
                if source.thumbnail:
                    embed.set_thumbnail(url=source.thumbnail)
                coro = self.last_channel.send(embed=embed)
                asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def ensure_voice(self, inter):
        try:
            if inter.author.voice:
                if not self.voice_client:
                    try:
                        self.voice_client = await inter.author.voice.channel.connect(timeout=30)
                    except (asyncio.TimeoutError, disnake.Forbidden) as e:
                        await inter.edit_original_message(content=f"Failed to connect: {e}")
                        return False
                elif self.voice_client.channel != inter.author.voice.channel:
                    await self.voice_client.move_to(inter.author.voice.channel)
                return True
            else:
                await inter.edit_original_message(content="You must be in a voice channel to use this command.")
                return False
        except Exception as e:
            logging.error(f"Voice connection error: {e}")
            await inter.edit_original_message(content=f"Voice connection error: {e}")
            return False

    async def show_queue(self, inter):
        if not self.queue:
            await inter.response.send_message("The queue is currently empty.")
            return

        description = []
        for idx, song in enumerate(self.queue, 1):
            description.append(f"{idx}. [{song.title}]({song.webpage_url})\n")
        
        description = "".join(description)
        if len(description) > 4096:
            description = description[:4093] + "..."

        embed = disnake.Embed(
            title="Queue",
            color=disnake.Color.purple(),
            description=description
        )
        await inter.response.send_message(embed=embed)

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info("Bot is ready.")

    @commands.slash_command()
    async def play(self, inter, song: str):
        """Play a song from YouTube."""
        guild_id = inter.guild.id
        
        if guild_id not in self.players:
            def remove_player():
                if guild_id in self.players:
                    del self.players[guild_id]
            
            self.players[guild_id] = Player(inter.guild, self.bot.loop, on_remove=remove_player)
        
        player = self.players[guild_id]
        await player.play_song(inter, song)

    @commands.slash_command()
    async def skip(self, inter):
        """Skip the currently playing song."""
        player = self.players.get(inter.guild.id)
        if player and player.voice_client and player.voice_client.is_playing():
            player.voice_client.stop()
            await inter.response.send_message(content="Song skipped.")
        else:
            await inter.response.send_message(content="Nothing is playing to skip.")

    @commands.slash_command()
    async def leave(self, inter):
        """Disconnect the bot from the voice channel."""
        player = self.players.pop(inter.guild.id, None)
        if player:
            await player.disconnect()
            await inter.response.send_message(content="Disconnected from the voice channel.")
        else:
            await inter.response.send_message(content="The bot is not connected to any voice channel.")

    @commands.slash_command()
    async def queue(self, inter):
        """Show the current queue."""
        player = self.players.get(inter.guild.id)
        if player:
            await player.show_queue(inter)
        else:
            await inter.response.send_message("The queue is currently empty.")

bot = commands.InteractionBot()

token = os.getenv("DISCORD_BOT_TOKEN")
if not token:
    raise ValueError("Bot token not found. Set DISCORD_BOT_TOKEN as an environment variable.")

bot.add_cog(MusicCog(bot))
logging.info("Starting bot...")
bot.run(token)