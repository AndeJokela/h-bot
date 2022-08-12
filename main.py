import datetime
import disnake
from disnake.ext import commands


bot = commands.InteractionBot(
    sync_commands_debug=True
)

start_time = datetime.datetime.now()

songs_played = 0


@bot.event
async def on_ready():
    print(f"{datetime.datetime.now()}: Logged in as {bot.user} (ID: {bot.user.id})")
    print("-----------------")


@bot.event
async def on_slash_command(inter):
    print(f"{datetime.datetime.now()}: {inter.author.name} used /{inter.application_command.name} in {inter.guild.name}")
    if inter.application_command.name == "play":
        global songs_played
        songs_played += 1


@bot.slash_command()
async def status(inter: disnake.ApplicationCommandInteraction):
    """Shows bot information"""
    embed = disnake.Embed(
        title=f"{bot.user.name} status",
        color=disnake.Color.yellow(),
        timestamp=datetime.datetime.now()
    )

    uptime = datetime.datetime.now()-start_time

    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)} ms", inline=True)
    embed.add_field(name="Uptime", value=str(uptime)[:-7], inline=True)
    embed.add_field(name="Songs Played", value=str(songs_played), inline=True)
    # embed.set_image(url="https://upload.wikimedia.org/wikipedia/commons/e/ee/Hervanta1.jpg")

    await inter.response.send_message(embed=embed)

bot_token = input("Enter bot token: ")

bot.load_extension("cogs.player")
bot.run(bot_token)
