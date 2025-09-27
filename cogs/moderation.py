import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Tuple

import discord
from discord import app_commands
from discord.ext import commands


def parse_duration(arg: Optional[str]) -> Optional[timedelta]:
    if not arg:
        return None
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
    if unit in ('d', 'j'):
        return timedelta(days=value)
    return None


class Moderation(commands.Cog, name="Modération"):
    """Commandes de modération (/mute, /unmute, /kick, /ban, /unban)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) -> asyncio.Task pour unban auto
        self.temp_bans: Dict[Tuple[int, int], asyncio.Task] = {}

    # ------------------ Logging helper ------------------
    def _get_modlog_channel(self, guild: Optional[discord.Guild]) -> Optional[discord.TextChannel]:
        if not guild:
            return None
        chan = guild.get_channel(1420554390477082735)
        return chan if isinstance(chan, discord.TextChannel) else None

    async def _log_action(self, guild: Optional[discord.Guild], title: str, description: str, color: discord.Color = discord.Color.blurple(), **fields):
        chan = self._get_modlog_channel(guild)
        if not chan:
            return
        embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
        for k, v in fields.items():
            embed.add_field(name=k, value=v, inline=False)
        try:
            await chan.send(embed=embed)
        except Exception:
            pass

    # ------------------ Helpers ------------------
    async def _dm_user(self, user: discord.abc.User, embed: discord.Embed):
        try:
            await user.send(embed=embed)
        except Exception:
            pass

    # ------------------ Slash Commands ------------------
    @app_commands.command(name="mute", description="Rend muet (timeout) un membre pour une durée donnée. Ex: 10m, 2h, 1j")
    @app_commands.describe(member="Membre à mute", duration="Durée: 10m/2h/1j", reason="Raison (optionnel)")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: Optional[str] = None):
        delta = parse_duration(duration)
        if not delta:
            return await interaction.response.send_message("Durée invalide. Exemples: 5m, 2h, 1j", ephemeral=True)
        until = datetime.now(timezone.utc) + delta
        try:
            await member.edit(timed_out_until=until, reason=reason or f"Timeout par {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message("Je n'ai pas la permission de mute ce membre.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("Impossible de mute ce membre.", ephemeral=True)

        embed = discord.Embed(title="Sanction: Mute", color=discord.Color.orange())
        embed.add_field(name="Serveur", value=interaction.guild.name if interaction.guild else "?", inline=False)
        embed.add_field(name="Durée", value=duration, inline=False)
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        await self._dm_user(member, embed)

        await interaction.response.send_message(f"{member.mention} a été mute pour {duration}.")
        await self._log_action(interaction.guild, "Mute", f"{member} mute", discord.Color.orange(),
                               Membre=f"{member} ({member.id})", Par=str(interaction.user), Durée=duration, Raison=reason or "-")

    @app_commands.command(name="unmute", description="Enlève le mute (timeout) d'un membre.")
    @app_commands.describe(member="Membre à unmute")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        try:
            await member.edit(timed_out_until=None, reason=f"Unmute par {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message("Je n'ai pas la permission d'unmute ce membre.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("Impossible d'unmute ce membre.", ephemeral=True)

        embed = discord.Embed(title="Sanction levée: Unmute", color=discord.Color.green())
        embed.add_field(name="Serveur", value=interaction.guild.name if interaction.guild else "?", inline=False)
        await self._dm_user(member, embed)

        await interaction.response.send_message(f"{member.mention} a été unmute.")
        await self._log_action(interaction.guild, "Unmute", f"{member} unmute", discord.Color.green(),
                               Membre=f"{member} ({member.id})", Par=str(interaction.user))

    @app_commands.command(name="kick", description="Expulse un membre du serveur.")
    @app_commands.describe(member="Membre à kick", reason="Raison (optionnel)")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
        embed = discord.Embed(title="Sanction: Kick", color=discord.Color.red())
        embed.add_field(name="Serveur", value=interaction.guild.name if interaction.guild else "?", inline=False)
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        await self._dm_user(member, embed)
        try:
            await member.kick(reason=reason or f"Kick par {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message("Je n'ai pas la permission de kick ce membre.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("Impossible de kick ce membre.", ephemeral=True)
        await interaction.response.send_message(f"{member} a été kick.")
        await self._log_action(interaction.guild, "Kick", f"{member} kick", discord.Color.red(),
                               Membre=f"{member} ({getattr(member, 'id', '?')})", Par=str(interaction.user), Raison=reason or "-")

    @app_commands.command(name="ban", description="Bannit un membre. Optionnellement temporaire si durée fournie.")
    @app_commands.describe(user="Membre à bannir", duration="Durée optionnelle: 10m/2h/1j", reason="Raison (optionnel)")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.User, duration: Optional[str] = None, reason: Optional[str] = None):
        guild = interaction.guild
        assert guild is not None
        embed = discord.Embed(title="Sanction: Ban", color=discord.Color.dark_red())
        embed.add_field(name="Serveur", value=guild.name, inline=False)
        if duration:
            embed.add_field(name="Durée", value=duration, inline=False)
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        await self._dm_user(user, embed)

        try:
            await guild.ban(user, reason=reason or f"Ban par {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message("Je n'ai pas la permission de ban ce membre.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("Impossible de ban ce membre.", ephemeral=True)

        await interaction.response.send_message(f"{user} a été banni.")
        await self._log_action(interaction.guild, "Ban", f"{user} banni", discord.Color.dark_red(),
                               Membre=f"{user} ({user.id})", Par=str(interaction.user), Durée=duration or "-", Raison=reason or "-")

        delta = parse_duration(duration) if duration else None
        if delta:
            async def unban_later(g: discord.Guild, uid: int, wait: float):
                try:
                    await asyncio.sleep(wait)
                    await g.unban(discord.Object(id=uid), reason="Fin du ban temporaire")
                except Exception:
                    pass
            task = asyncio.create_task(unban_later(guild, user.id, delta.total_seconds()))
            self.temp_bans[(guild.id, user.id)] = task

    @app_commands.command(name="unban", description="Débannit un utilisateur par ID ou nom#discrim.")
    @app_commands.describe(user="Utilisateur à débannir")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user: discord.User):
        guild = interaction.guild
        assert guild is not None
        try:
            await guild.unban(user, reason=f"Unban par {interaction.user}")
        except discord.NotFound:
            return await interaction.response.send_message("Cet utilisateur n'est pas banni.", ephemeral=True)
        except discord.Forbidden:
            return await interaction.response.send_message("Je n'ai pas la permission d'unban cet utilisateur.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("Impossible d'unban cet utilisateur.", ephemeral=True)

        await interaction.response.send_message(f"{user} a été unban.")
        await self._log_action(interaction.guild, "Unban", f"{user} unban", discord.Color.green(),
                               Membre=f"{user} ({user.id})", Par=str(interaction.user))


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
