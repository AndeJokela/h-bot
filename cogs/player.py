import asyncio
import datetime
import disnake
import yt_dlp
from disnake.ext import commands
from disnake.ext import tasks

ytdl_format_options = {
    "format": "bestaudio/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": False,
    "no_warnings": False,
    "default_search": "auto",
    "source_address": "0.0.0.0",  # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    #'options': '-loglevel 8 -vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


class YTDLSource(disnake.PCMVolumeTransformer):
    def __init__(self, source, *, data, channel, volume=0.2):
        super().__init__(source, volume)

        self.data = data
        self.channel = channel

        self.title = data.get("title")
        self.url = data.get("url")
        self.webpage_url = data.get("webpage_url")
        self.thumbnail = data.get("thumbnail")
        self.duration = data.get("duration_string")

    @classmethod
    async def get_source(cls, query, channel, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()

        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=not stream))
        except Exception:
            return False

        assert data
        if data is None or not data:
            return False

        if "entries" in data:
            # take first item from a playlist
            data = data["entries"][0]

        filename = data["url"] if stream else ytdl.prepare_filename(data)
        assert filename

        return cls(disnake.FFmpegPCMAudio(filename, **ffmpeg_options), data=data, channel=channel)


class Player:
    def __init__(self, guild, loop):
        self.guild = guild
        self.loop = loop
        self.vc = None
        self.song_queue = []
        self.idle_counter = 0
        self.dc_timer.start()

    @tasks.loop(seconds=10)
    async def dc_timer(self):
        if self.vc is None or self.vc.is_playing():
            self.idle_counter = 0
            return

        self.idle_counter += 10

        if self.idle_counter >= 600:
            self.song_queue.clear()
            await self.vc.disconnect(force=False)
            self.vc = None
            print(f"{datetime.datetime.now()}: Bot idle - disconnecting from {self.guild.id}")

    async def skip(
            self,
            inter: disnake.ApplicationCommandInteraction,
    ):
        """Skips current song"""
        if inter.guild.voice_client is None:
            await inter.response.send_message("The bot is not connected to voice.")
            return
        if inter.guild.voice_client.is_playing():
            inter.guild.voice_client.stop()
            await inter.response.send_message("skipping")
            await inter.delete_original_message(delay=10)
        else:
            await inter.response.send_message("Not playing anything")
            await inter.delete_original_message(delay=10)

    async def queue(
            self,
            inter: disnake.ApplicationCommandInteraction
    ):
        """Shows the current queue"""
        if len(self.song_queue) == 0:
            await inter.response.send_message("Queue is empty!")
            return

        description_str = ""
        i = 1
        for song in self.song_queue:
            description_str += f"{i}. [{song.title}]({song.webpage_url})\n"
            i += 1

        embed = disnake.Embed(
            title=f"Song Queue",
            color=disnake.Color.blue(),
            description=description_str
        )
        await inter.response.send_message(embed=embed)

    async def leave(
            self,
            inter: disnake.ApplicationCommandInteraction
    ):
        """Disconnects the bot from voice"""
        if inter.guild.voice_client is None:
            await inter.response.send_message("The bot is not connected to voice.")
            return
        await inter.guild.voice_client.disconnect(force=False)
        self.song_queue.clear()
        self.vc = None
        await inter.response.send_message(":wave:")

    async def play(
            self,
            inter: disnake.ApplicationCommandInteraction,
            song: str
    ):
        """Play a song or video from URL or search from youtube"""
        await inter.response.defer()
        self.idle_counter = 0

        if not await self.ensure_voice(inter):
            return

        channel = inter.channel

        source = await YTDLSource.get_source(song, channel, loop=self.loop, stream=True)

        if not source:
            await inter.edit_original_message("Couldn't find song.")
            return

        if inter.guild.voice_client.is_playing():
            self.song_queue.append(source)
            embed = disnake.Embed(
                title=f"Queued  -  {len(self.song_queue)}",
                color=disnake.Color.blue(),
                description=f"[{source.title}]({source.webpage_url})\n\n{source.duration}"
            )
            if source.thumbnail is not None:
                embed.set_thumbnail(url=source.thumbnail)

            await inter.edit_original_message(embed=embed)
            print(f"{datetime.datetime.now()}: Queued {source.title} in {inter.guild_id}")
            return

        embed = disnake.Embed(
            title="Playing",
            color=disnake.Color.green(),
            description=f"[{source.title}]({source.webpage_url})\n\n{source.duration}"
        )
        if source.thumbnail is not None:
            embed.set_thumbnail(url=source.thumbnail)

        await inter.edit_original_message(embed=embed)

        print(f"{datetime.datetime.now()}: Playing {source.title} in {self.guild.id}")
        self.vc.play(source, after=self.check_queue)

    def check_queue(self, error=None):
        if error:
            print(f"Player error: {error}")

        if len(self.song_queue) > 0:
            source = self.song_queue.pop(0)

            embed = disnake.Embed(
                title="Playing",
                color=disnake.Color.green(),
                description=f"[{source.title}]({source.webpage_url})\n\n{source.duration}"
            )
            if source.thumbnail is not None:
                embed.set_thumbnail(url=source.thumbnail)

            coro = source.channel.send(embed=embed)
            asyncio.run_coroutine_threadsafe(coro, self.loop)

            print(f"{datetime.datetime.now()}: Playing {source.title} in {self.guild.id}")
            self.vc.play(source, after=self.check_queue)

    async def ensure_voice(self, inter):
        if inter.author.voice:
            if inter.guild.voice_client is None:
                try:
                    self.vc = await inter.author.voice.channel.connect(timeout=10, reconnect=True)
                except TimeoutError:
                    await inter.edit_original_message("Couldn't connect to voice, try again.")
                    return False
            else:
                await inter.guild.voice_client.move_to(inter.author.voice.channel)
        else:
            await inter.edit_original_message("You are not connected to a voice channel.")
            return False
        return True
    

    
    def __del__(self):
        self.dc_timer.stop()


class PlayerCommands(commands.Cog):
    """Plays audio in a voice channel."""
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.players = {}

    @commands.Cog.listener()
    async def on_ready(self):
        print("Guilds:")
        for guild in self.bot.guilds:
            self.players.update({guild.id: Player(guild, loop=self.bot.loop)})
            print(guild.id)

    @commands.Cog.listener()
    async def on_guild_join(
            self,
            guild
    ):
        self.players.update({guild.id: Player(guild, loop=self.bot.loop)})
        print(f"{datetime.datetime.now()}: Joined guild {guild.id}")

    @commands.slash_command()
    async def skip(
            self,
            inter: disnake.ApplicationCommandInteraction
    ):
        """Skips current song"""
        player = self.players.get(inter.guild.id)
        await player.skip(inter)

    @commands.slash_command()
    async def queue(
            self,
            inter: disnake.ApplicationCommandInteraction
    ):
        """Shows the current queue"""
        player = self.players.get(inter.guild.id)
        await player.queue(inter)

    @commands.slash_command()
    async def leave(
            self,
            inter: disnake.ApplicationCommandInteraction
    ):
        """Disconnects the bot from voice"""
        player = self.players.get(inter.guild.id)
        await player.leave(inter)

    @commands.slash_command()
    async def play(
            self,
            inter: disnake.ApplicationCommandInteraction,
            song: str
    ):
        """Play a song or video from URL or search from youtube"""
        player = self.players.get(inter.guild.id)
        await player.play(inter, song)



def setup(bot: commands.InteractionBot):
    bot.add_cog(PlayerCommands(bot))
