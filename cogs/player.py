import asyncio

import disnake
import youtube_dl
from disnake.ext import commands

ytdl_format_options = {
    "format": "bestaudio/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",  # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {"options": "-vn"}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class Song:
    """123"""
    def __init__(self, query):
        self.query = query


class YTDLSource(disnake.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get("title")
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        assert data

        if "entries" in data:
            # take first item from a playlist
            data = data["entries"][0]

        filename = data["url"] if stream else ytdl.prepare_filename(data)
        assert filename

        return cls(disnake.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)


class Player(commands.Cog):
    """Plays audio in a voice channel."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.vc = None
        self.song_queue = []

    @commands.slash_command()
    async def test(self, inter: disnake.ApplicationCommandInteraction):
        """TEST COMMAND"""
        await inter.response.send_message(f"Latency: {round(self.bot.latency * 1000)} ms")

    @commands.slash_command()
    async def play(
            self,
            inter: disnake.ApplicationCommandInteraction,
            url: str
    ):
        """Streams from a url"""
        if not await self.ensure_voice(inter):
            return

        player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
        await inter.response.send_message(f"Playing: {player.title}")
        self.vc.play(
            player, after=lambda e: print(f"Player error: {e}") if e else None
            )

    async def ensure_voice(self, inter):
        if inter.guild.voice_client is None:
            if inter.author.voice:
                self.vc = await inter.author.voice.channel.connect()
            else:
                await inter.response.send_message("You are not connected to a voice channel.")
                raise commands.CommandError("Author not connected to a voice channel.")
        elif inter.guild.voice_client.is_playing():
            inter.guild.voice_client.stop()
        return True



def setup(bot: commands.Bot):
    bot.add_cog(Player(bot))
