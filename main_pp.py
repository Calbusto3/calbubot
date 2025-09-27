import os
import asyncio
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv
import traceback


def get_prefix(_bot, message):
    return '!'


def create_bot() -> commands.Bot:
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)
    return bot

bot = create_bot()

@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} (id={bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="gestion des bots"))
    # Synchronisation des commandes slash
    try:
        synced = await bot.tree.sync()
        print(f"Slash commands synchronisées: {len(synced)}")
    except Exception as e:
        print(f"Erreur lors de la synchronisation des slash commands: {e}")
        traceback.print_exc()


async def load_cogs():
    # Chargement automatique de tous les cogs dans le dossier cogs/
    cogs_dir = os.path.join(os.path.dirname(__file__), 'cogs')
    if not os.path.isdir(cogs_dir):
        print("Aucun dossier 'cogs' trouvé.")
        return
    for fname in os.listdir(cogs_dir):
        if not fname.endswith('.py'):
            continue
        if fname.startswith('_') or fname == '__init__.py':
            continue
        module_name = fname[:-3]
        ext = f"cogs.{module_name}"
        try:
            await bot.load_extension(ext)
            print(f"Extension chargée: {ext}")
        except Exception as e:
            print(f"Erreur lors du chargement de {ext}: {e}")
            traceback.print_exc()


def main():
    load_dotenv()
    token = os.getenv('BOT_TOKEN')
    owner_id_env = os.getenv('OWNER_ID')
    if not token:
        raise RuntimeError("BOT_TOKEN manquant dans .env")
    if not owner_id_env:
        print("ATTENTION: OWNER_ID manquant dans .env. Les commandes owner-only utiliseront l'ID codé en dur si fourni dans le code.")

    async def runner():
        await load_cogs()
        await bot.start(token)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        print("Arrêt...")
    finally:
        if bot.is_closed() is False:
            asyncio.run(bot.close())


if __name__ == '__main__':
    main()

