import datetime
import disnake
from disnake.ext import commands

bot = commands.InteractionBot(
    test_guilds=[],  # add guild IDs here
    sync_commands_debug=True,
)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("-----------------")


@bot.event
async def on_slash_command(inter):
    print(f"{datetime.datetime.now()}: {inter.author.name} used /{inter.application_command.name}")


@bot.slash_command()
async def ping(inter):
    await inter.response.send_message("pong")


bot_token = input("Enter bot token: ")

bot.load_extension("cogs.player")
bot.run(bot_token)
