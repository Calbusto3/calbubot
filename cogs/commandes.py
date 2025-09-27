import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from typing import Optional


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
    if unit == 'd':
        return timedelta(days=value)
    return None


class Commandes(commands.Cog, name="Commandes"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------- Logging helper (prefix) ----------------
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

    @commands.command(name='mute')
    @commands.has_permissions(moderate_members=True)
    async def cmd_mute(self, ctx: commands.Context, member: discord.Member, duration: str, *, reason: Optional[str] = None):
        """!mute @membre <durée> [raison] — applique un timeout"""
        delta = parse_duration(duration)
        if not delta:
            return await ctx.reply("Durée invalide. Exemples: 5m, 2h, 1j")
        until = datetime.now(timezone.utc) + delta
        try:
            await member.edit(timed_out_until=until, reason=reason or f"Timeout par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Je n'ai pas la permission de mute ce membre.")
        except discord.HTTPException:
            return await ctx.reply("Impossible de mute ce membre.")

        embed = discord.Embed(title="Sanction: Mute", color=discord.Color.orange())
        embed.add_field(name="Serveur", value=ctx.guild.name if ctx.guild else "?", inline=False)
        embed.add_field(name="Durée", value=duration, inline=False)
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        try:
            await member.send(embed=embed)
        except Exception:
            pass
        await ctx.reply(f"{member.mention} a été mute pour {duration}.")
        await self._log_action(ctx.guild, "Mute", f"{member} mute", discord.Color.orange(),
                               Membre=f"{member} ({member.id})", Par=str(ctx.author), Durée=duration, Raison=reason or "-")

    @commands.command(name='unmute')
    @commands.has_permissions(moderate_members=True)
    async def cmd_unmute(self, ctx: commands.Context, member: discord.Member):
        try:
            await member.edit(timed_out_until=None, reason=f"Unmute par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Je n'ai pas la permission d'unmute ce membre.")
        except discord.HTTPException:
            return await ctx.reply("Impossible d'unmute ce membre.")
        embed = discord.Embed(title="Sanction levée: Unmute", color=discord.Color.green())
        embed.add_field(name="Serveur", value=ctx.guild.name if ctx.guild else "?", inline=False)
        try:
            await member.send(embed=embed)
        except Exception:
            pass
        await ctx.reply(f"{member.mention} a été unmute.")
        await self._log_action(ctx.guild, "Unmute", f"{member} unmute", discord.Color.green(),
                               Membre=f"{member} ({member.id})", Par=str(ctx.author))

    @commands.command(name='kick')
    @commands.has_permissions(kick_members=True)
    async def cmd_kick(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None):
        embed = discord.Embed(title="Sanction: Kick", color=discord.Color.red())
        embed.add_field(name="Serveur", value=ctx.guild.name if ctx.guild else "?", inline=False)
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        try:
            await member.send(embed=embed)
        except Exception:
            pass
        try:
            await member.kick(reason=reason or f"Kick par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Je n'ai pas la permission de kick ce membre.")
        except discord.HTTPException:
            return await ctx.reply("Impossible de kick ce membre.")
        await ctx.reply(f"{member} a été kick.")
        await self._log_action(ctx.guild, "Kick", f"{member} kick", discord.Color.red(),
                               Membre=f"{member} ({getattr(member, 'id', '?')})", Par=str(ctx.author), Raison=reason or "-")

    @commands.command(name='ban')
    @commands.has_permissions(ban_members=True)
    async def cmd_ban(self, ctx: commands.Context, member: discord.User, duration: Optional[str] = None, *, reason: Optional[str] = None):
        guild = ctx.guild
        assert guild is not None
        embed = discord.Embed(title="Sanction: Ban", color=discord.Color.dark_red())
        embed.add_field(name="Serveur", value=guild.name, inline=False)
        if duration:
            embed.add_field(name="Durée", value=duration, inline=False)
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        try:
            await member.send(embed=embed)
        except Exception:
            pass
        try:
            await guild.ban(member, reason=reason or f"Ban par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Je n'ai pas la permission de ban ce membre.")
        except discord.HTTPException:
            return await ctx.reply("Impossible de ban ce membre.")
        await ctx.reply(f"{member} a été banni.")
        await self._log_action(ctx.guild, "Ban", f"{member} banni", discord.Color.dark_red(),
                               Membre=f"{member} ({getattr(member, 'id', '?')})", Par=str(ctx.author), Durée=duration or "-", Raison=reason or "-")

        delta = parse_duration(duration) if duration else None
        if delta:
            async def unban_later(g: discord.Guild, uid: int, wait: float):
                try:
                    await discord.utils.sleep_until(datetime.now(timezone.utc) + delta)
                    await g.unban(discord.Object(id=uid), reason="Fin du ban temporaire")
                except Exception:
                    pass
            self.bot.loop.create_task(unban_later(guild, member.id, delta.total_seconds()))

    @commands.command(name='unban')
    @commands.has_permissions(ban_members=True)
    async def cmd_unban(self, ctx: commands.Context, user: discord.User):
        guild = ctx.guild
        assert guild is not None
        try:
            await guild.unban(user, reason=f"Unban par {ctx.author}")
        except discord.NotFound:
            return await ctx.reply("Cet utilisateur n'est pas banni.")
        except discord.Forbidden:
            return await ctx.reply("Je n'ai pas la permission d'unban cet utilisateur.")
        except discord.HTTPException:
            return await ctx.reply("Impossible d'unban cet utilisateur.")
        await ctx.reply(f"{user} a été unban.")
        await self._log_action(ctx.guild, "Unban", f"{user} unban", discord.Color.green(),
                               Membre=f"{user} ({user.id})", Par=str(ctx.author))

    @commands.command(name='slowmode')
    @commands.has_permissions(manage_channels=True)
    async def cmd_slowmode(self, ctx: commands.Context, delay: int):
        """!slowmode <secondes> — Définit le slowmode du salon courant"""
        if delay < 0 or delay > 21600:
            return await ctx.reply("Valeur invalide. 0 à 21600 secondes.")
        try:
            await ctx.channel.edit(slowmode_delay=delay, reason=f"Slowmode modifié par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Je n'ai pas la permission de modifier ce salon.")
        except discord.HTTPException:
            return await ctx.reply("Impossible de modifier le slowmode.")
        await ctx.reply(f"Slowmode défini à {delay} secondes.")

    @commands.command(name='userinfo')
    async def cmd_userinfo(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        roles = ', '.join(r.mention for r in member.roles[1:]) if hasattr(member, 'roles') else 'N/A'
        embed = discord.Embed(title=f"Infos utilisateur - {member}", color=discord.Color.blurple())
        embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else discord.Embed.Empty)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Créé le", value=discord.utils.format_dt(member.created_at, style='F'), inline=True)
        if isinstance(member, discord.Member):
            embed.add_field(name="A rejoint le", value=discord.utils.format_dt(member.joined_at, style='F') if member.joined_at else 'N/A', inline=True)
            embed.add_field(name="Rôles", value=roles or 'Aucun', inline=False)
            embed.add_field(name="Top rôle", value=getattr(member.top_role, 'mention', 'N/A'), inline=True)
        await ctx.reply(embed=embed)

    # ---------------- Channel management ----------------
    @commands.command(name='lock')
    @commands.has_permissions(manage_channels=True)
    async def cmd_lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """!lock [#salon] — Empêche tous les rôles d'écrire dans le salon"""
        channel = channel or ctx.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await ctx.reply("Ce type de salon n'est pas supporté.")
        overwrites = channel.overwrites
        # Bloquer @everyone
        overwrites[ctx.guild.default_role] = overwrites.get(ctx.guild.default_role, discord.PermissionOverwrite())
        overwrites[ctx.guild.default_role].send_messages = False
        # Bloquer tous les rôles déjà présents dans les overwrites
        for target, perms in list(overwrites.items()):
            if isinstance(target, discord.Role):
                perms.send_messages = False
                overwrites[target] = perms
        try:
            await channel.edit(overwrites=overwrites, reason=f"Lock par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Permissions insuffisantes pour modifier le salon.")
        await ctx.reply(f"{channel.mention} est verrouillé (messages désactivés).")

    @commands.command(name='unlock')
    @commands.has_permissions(manage_channels=True)
    async def cmd_unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """!unlock [#salon] — Rétablit l'envoi de messages"""
        channel = channel or ctx.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await ctx.reply("Ce type de salon n'est pas supporté.")
        overwrites = channel.overwrites
        # Remettre à None (hérite) pour @everyone et tous les rôles déjà dans les overwrites
        if ctx.guild.default_role in overwrites:
            overwrites[ctx.guild.default_role].send_messages = None
        for target, perms in list(overwrites.items()):
            if isinstance(target, discord.Role):
                perms.send_messages = None
                overwrites[target] = perms
        try:
            await channel.edit(overwrites=overwrites, reason=f"Unlock par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Permissions insuffisantes pour modifier le salon.")
        await ctx.reply(f"{channel.mention} est déverrouillé (messages rétablis).")

    @commands.command(name='hide')
    @commands.has_permissions(manage_channels=True)
    async def cmd_hide(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """!hide [#salon] — Cache le salon à tous les rôles"""
        channel = channel or ctx.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await ctx.reply("Ce type de salon n'est pas supporté.")
        overwrites = channel.overwrites
        overwrites[ctx.guild.default_role] = overwrites.get(ctx.guild.default_role, discord.PermissionOverwrite())
        overwrites[ctx.guild.default_role].view_channel = False
        for target, perms in list(overwrites.items()):
            if isinstance(target, discord.Role):
                perms.view_channel = False
                overwrites[target] = perms
        try:
            await channel.edit(overwrites=overwrites, reason=f"Hide par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Permissions insuffisantes pour modifier le salon.")
        await ctx.reply(f"{channel.mention} est maintenant caché.")

    @commands.command(name='unhide')
    @commands.has_permissions(manage_channels=True)
    async def cmd_unhide(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """!unhide [#salon] — Rétablit la visibilité"""
        channel = channel or ctx.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await ctx.reply("Ce type de salon n'est pas supporté.")
        overwrites = channel.overwrites
        if ctx.guild.default_role in overwrites:
            overwrites[ctx.guild.default_role].view_channel = None
        for target, perms in list(overwrites.items()):
            if isinstance(target, discord.Role):
                perms.view_channel = None
                overwrites[target] = perms
        try:
            await channel.edit(overwrites=overwrites, reason=f"Unhide par {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply("Permissions insuffisantes pour modifier le salon.")
        await ctx.reply(f"{channel.mention} est visible à nouveau.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Commandes(bot))
