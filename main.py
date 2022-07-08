import disnake
from disnake.ext import commands

bot = commands.InteractionBot(test_guilds=[])  # add guild IDs here


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("-----------------")


@bot.slash_command()
async def ping(inter):
    await inter.response.send_message("pong")


bot_token = input("Enter bot token: ")

bot.load_extension("cogs.player")
bot.run(bot_token)
