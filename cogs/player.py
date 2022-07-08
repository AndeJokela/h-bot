import disnake
from disnake.ext import commands


class Player(commands.Cog):
    """Plays audio in a voice channel."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command()
    async def test(self, inter: disnake.ApplicationCommandInteraction):
        """TEST COMMAND"""
        await inter.response.send_message(f"Latency: {round(self.bot.latency * 1000)} ms")


def setup(bot: commands.Bot):
    bot.add_cog(Player(bot))
