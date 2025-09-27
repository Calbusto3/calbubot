import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv


def _parse_duration(arg: str) -> Optional[timedelta]:
    try:
        value = int(arg[:-1])
        unit = arg[-1].lower()
    except Exception:
        return None
    if value < 1:
        return None
    if unit == 's':
        return timedelta(seconds=value)
    if unit == 'm':
        return timedelta(minutes=value)
    if unit == 'h':
        return timedelta(hours=value)
    if unit in ('j', 'd'):
        return timedelta(days=value)
    return None


class Systeme(commands.Cog, name="Systèmes"):
    """Gestion du blocage/déblocage des bots et auto-kick."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        load_dotenv()
        owner = os.getenv('OWNER_ID')
        try:
            self.owner_id = int(owner) if owner else None
        except ValueError:
            self.owner_id = None

        # Etat par serveur
        # guild_id -> dict(blocked: bool, unblock_until: Optional[datetime])
        self.state: Dict[int, Dict[str, Optional[datetime]]] = {}

    # --------------- Utils ---------------
    def _ensure_guild_state(self, guild_id: int) -> Dict[str, Optional[datetime]]:
        st = self.state.get(guild_id)
        if not st:
            st = {"blocked": True, "unblock_until": None}
            self.state[guild_id] = st
        return st

    def _bots_currently_allowed(self, guild_id: int) -> bool:
        st = self._ensure_guild_state(guild_id)
        until = st.get("unblock_until")
        if until and datetime.now(timezone.utc) < until:
            return True
        return False

    def _is_owner(self, user_id: int) -> bool:
        return self.owner_id is not None and user_id == self.owner_id

    # --------------- Commands ---------------
    @commands.command(name='bot_block')
    async def cmd_bot_block(self, ctx: commands.Context):
        """Bloque l'arrivée de tout bot (owner uniquement)."""
        if not self._is_owner(ctx.author.id):
            return await ctx.reply("Cette commande est réservée au propriétaire.")
        st = self._ensure_guild_state(ctx.guild.id)
        st["blocked"] = True
        st["unblock_until"] = None
        await ctx.reply("Les bots sont maintenant BLOQUÉS. Toute arrivée de bot sera kick automatiquement.")

    @commands.command(name='bot_unblock')
    async def cmd_bot_unblock(self, ctx: commands.Context, duree: str):
        """Débloque temporairement l'arrivée des bots pour <durée> (ex: 5s, 10m, 2h, 1j)."""
        if not self._is_owner(ctx.author.id):
            return await ctx.reply("Cette commande est réservée au propriétaire.")
        delta = _parse_duration(duree)
        if not delta:
            return await ctx.reply("Durée invalide. Exemples valides: 5s, 10m, 2h, 1j")
        st = self._ensure_guild_state(ctx.guild.id)
        st["blocked"] = True  # reste en mode bloqué par défaut
        st["unblock_until"] = datetime.now(timezone.utc) + delta
        until_str = discord.utils.format_dt(st["unblock_until"], style='R')  # relative
        await ctx.reply(f"Les bots sont DÉBLOQUÉS jusqu'à {until_str}. Ils pourront rejoindre durant cette période.")

    # --------------- Events ---------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # On ne concerne que les bots
        if not member.bot or not member.guild:
            return
        guild = member.guild
        st = self._ensure_guild_state(guild.id)

        # Si les bots sont actuellement autorisés (fenêtre d'unblock), on laisse passer
        if self._bots_currently_allowed(guild.id):
            return

        # Sinon, on kick le bot et on DM la personne qui l'a ajouté si possible
        try:
            await member.kick(reason="Bots non autorisé")
        except discord.Forbidden:
            # Bot n'a pas la permission de kick
            return
        except discord.HTTPException:
            return

        inviter = None
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.bot_add):
                if entry.target.id == member.id:
                    inviter = entry.user
                    break
        except discord.Forbidden:
            inviter = None
        except discord.HTTPException:
            inviter = None

        # Construire un embed d'information
        embed = discord.Embed(
            title="Bots bloqués",
            description=(
                "Pour qu'un bot soit ajouté au serveur, il faut que Calbusto donne l'autorisation.\n"
                "Votre bot a été kick automatiquement."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Demande à Calbusto de l'autoriser.")

        # DM l'inviteur si on l'a trouvé et si c'est possible
        if inviter and not inviter.bot:
            try:
                await inviter.send(embed=embed)
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Systeme(bot))

