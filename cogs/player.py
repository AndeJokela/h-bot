import asyncio
import datetime
import disnake
import yt_dlp
from disnake.ext import commands
from disnake.ext import tasks

ytdl_format_options = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36",
    "referer": "https://www.youtube.com/"
}

ffmpeg_options = {
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


class YTDLSource(disnake.PCMVolumeTransformer):
    def __init__(self, source, *, data, channel, volume=0.5):
        super().__init__(source, volume)

        self.data = data
        self.channel = channel

        self.title = data.get("title")
        self.url = data.get("url")
        self.webpage_url = data.get("webpage_url")
        self.thumbnail = data.get("thumbnail")
        self.duration = data.get("duration_string")

    @classmethod
    async def from_url(cls, query, channel, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()

        if "youtube.com" in query or "youtu.be" in query:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=not stream))
        elif "soundcloud" in query:
            return False
            # TODO: Add souncloud support
            # data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=not stream))
        else:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch:{query}", download=not stream))

        assert data

        if "entries" in data:
            # take first item from a playlist
            data = data["entries"][0]
        else:
            return False

        filename = data["url"] if stream else ytdl.prepare_filename(data)
        assert filename

        return cls(disnake.FFmpegPCMAudio(filename, **ffmpeg_options), data=data, channel=channel)


class Player(commands.Cog):
    """Plays audio in a voice channel."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
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
            print("Bot idle - disconnecting")

    @dc_timer.before_loop
    async def before_dc_timer(self):
        await self.bot.wait_until_ready()

    @commands.slash_command()
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
            await inter.response.send_message(":track_next:")

    @commands.slash_command()
    async def queue(
            self,
            inter: disnake.ApplicationCommandInteraction
    ):
        """Shows the current queue"""
        embed = disnake.Embed(
            title=f"Song Queue",
            color=disnake.Color.blue(),
        )

        i = 1
        for song in self.song_queue:
            embed.add_field(name=i, value=f"[{song.title}]({song.webpage_url})", inline=False)
            i += 1

        await inter.response.send_message(embed=embed)

    @commands.slash_command()
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

    @commands.slash_command()
    async def play(
            self,
            inter: disnake.ApplicationCommandInteraction,
            song: str
    ):
        """Play a song/video from YouTube"""
        await inter.response.defer()
        self.idle_counter = 0

        if not await self.ensure_voice(inter):
            return

        channel = inter.channel
        source = await YTDLSource.from_url(song, channel, loop=self.bot.loop, stream=False)

        if not source:
            await inter.edit_original_message("Couldn't find song.")
            return

        if inter.guild.voice_client.is_playing():
            self.song_queue.append(source)
            embed = disnake.Embed(
                title=f"Queued  -  {len(self.song_queue)}",
                color=disnake.Color.blue(),
                description=f"[{source.title}]({source.webpage_url})"
            )
            embed.set_thumbnail(url=source.thumbnail)
            embed.set_footer(text=source.duration)

            await inter.edit_original_message(embed=embed)
            return

        embed = disnake.Embed(
            title="Playing",
            color=disnake.Color.green(),
            description=f"[{source.title}]({source.webpage_url})"
        )
        embed.set_thumbnail(url=source.thumbnail)
        embed.set_footer(text=source.duration)

        await inter.edit_original_message(embed=embed)

        activity = disnake.Game(name=source.title)
        await self.bot.change_presence(activity=activity)

        print(f"{datetime.datetime.now()}: PLaying {source.title}")
        self.vc.play(source, after=self.check_queue)

    def check_queue(self, error=None):
        if error:
            print(f"Player error: {error}")

        if len(self.song_queue) > 0:
            source = self.song_queue.pop(0)

            embed = disnake.Embed(
                title="Playing",
                color=disnake.Color.green(),
                description=f"[{source.title}]({source.webpage_url})"
            )
            embed.set_thumbnail(url=source.thumbnail)
            embed.set_footer(text=source.duration)

            coro = source.channel.send(embed=embed)
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

            activity = disnake.Game(name=source.title)
            coro = self.bot.change_presence(activity=activity)
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

            print(f"{datetime.datetime.now()}: PLaying {source.title}")
            self.vc.play(source, after=self.check_queue)
        else:
            coro = self.bot.change_presence(activity=None)
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    async def ensure_voice(self, inter):
        if inter.guild.voice_client is None:
            if inter.author.voice:
                self.vc = await inter.author.voice.channel.connect()
            else:
                await inter.edit_original_message("You are not connected to a voice channel.")
                raise commands.CommandError("Author not connected to a voice channel.")
        return True


def setup(bot: commands.Bot):
    bot.add_cog(Player(bot))
