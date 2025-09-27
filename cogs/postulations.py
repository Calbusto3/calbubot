import discord
from discord.ext import commands
from typing import Optional, Tuple, List
from datetime import datetime, timezone

# IDs des rôles (adapter si besoin)
ROLE_FULL_ID = 1403174306904670248
ROLE_PARTIEL_ID = 1227017624378933359
LOG_CHANNEL_ID: Optional[int] = 1227562420227014716  # Salon des logs


def _get_application_answers(member: discord.Member) -> Optional[str]:
    """
    Récupère les réponses de candidature du membre.
    NOTE: Cette fonction est un emplacement prévu. Indique-moi où (ou comment)
    sont stockées les réponses afin que je la branche réellement (canal, base de données, message, etc.).
    Retourne une chaîne formatée ou None si inconnu.
    """
    return None


class ConfirmAcceptView(discord.ui.View):
    def __init__(self, author_id: int, mode_label: str, target: discord.Member, role: discord.Role, *, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.mode_label = mode_label
        self.target = target
        self.role = role
        self.result: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("Seul l'auteur de la commande peut utiliser ces boutons.", ephemeral=True)
        return False

    @discord.ui.button(label="Accepter", style=discord.ButtonStyle.success)
    async def btn_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = True
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.danger)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = False
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class Postulations(commands.Cog, name="Postulations"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------- Utilitaires ----------------
    def _get_log_channel(self, guild: Optional[discord.Guild]) -> Optional[discord.TextChannel]:
        if not guild or not LOG_CHANNEL_ID:
            return None
        chan = guild.get_channel(LOG_CHANNEL_ID)
        return chan if isinstance(chan, discord.TextChannel) else None

    async def _log(self, guild: Optional[discord.Guild], title: str, description: str, color: discord.Color = discord.Color.blurple(), **fields):
        chan = self._get_log_channel(guild)
        if not chan:
            return
        emb = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
        for k, v in fields.items():
            emb.add_field(name=k, value=v, inline=False)
        try:
            await chan.send(embed=emb)
        except Exception:
            pass

    async def _resolve_member(self, ctx: commands.Context, query: str) -> Tuple[Optional[discord.Member], Optional[str]]:
        """Résout un membre depuis une mention, un ID, un display name ou un username (approx.)."""
        guild = ctx.guild
        if not guild:
            return None, "Commande en DM non supportée."
        query = query.strip()

        # 1) Mention
        if len(ctx.message.mentions) == 1:
            m = ctx.message.mentions[0]
            if isinstance(m, discord.Member):
                return m, None

        # 2) ID
        if query.isdigit():
            uid = int(query)
            m = guild.get_member(uid)
            if m:
                return m, None
            try:
                m2 = await guild.fetch_member(uid)
                if isinstance(m2, discord.Member):
                    return m2, None
            except Exception:
                pass

        # 3) Recherche par nom/display/global_name (insensible à la casse)
        q = query.lower()
        candidates: List[discord.Member] = []
        for m in guild.members:
            names = [str(m), m.name]
            if getattr(m, "global_name", None):
                names.append(m.global_name)  # nouveau système de noms
            if getattr(m, "display_name", None):
                names.append(m.display_name)
            names = [n.lower() for n in names if n]
            if q in names or any(q in n for n in names):
                candidates.append(m)

        if not candidates:
            return None, "Membre introuvable. Réessaie avec une mention ou un ID."
        if len(candidates) > 5:
            return None, f"Trop de correspondances ({len(candidates)}). Sois plus précis (mention/ID)."
        if len(candidates) > 1:
            sample = ", ".join(f"{c} ({c.id})" for c in candidates[:5])
            return None, f"Plusieurs correspondances: {sample}. Sois plus précis."
        return candidates[0], None

    def _build_confirmation_embed(self, ctx: commands.Context, mode: str, member: discord.Member) -> discord.Embed:
        mode_readable = "accès complet" if mode == "full" else "accès partiel"
        emb = discord.Embed(
            title="Confirmation d'acceptation",
            description=f"Es-tu sûr d'accepter {member.mention} avec {mode_readable} sur le serveur ?",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        emb.set_thumbnail(url=member.display_avatar.url if member.display_avatar else discord.Embed.Empty)
        emb.add_field(name="Membre", value=f"{member} (ID: {member.id})", inline=False)
        answers = _get_application_answers(member)
        if answers:
            emb.add_field(name="Réponses de la candidature", value=answers[:1024], inline=False)
        else:
            emb.add_field(name="Réponses de la candidature", value="(non disponibles) — précisez-moi où les récupérer pour les afficher ici.", inline=False)
        emb.set_footer(text=f"Demandé par {ctx.author}")
        return emb

    async def _assign_role_and_dm(self, ctx: commands.Context, member: discord.Member, role: discord.Role, mode: str, *, edit_message: Optional[discord.Message] = None):
        # Attribution du rôle
        try:
            await member.add_roles(role, reason=f"Accepté ({mode}) par {ctx.author}")
        except discord.Forbidden:
            await ctx.reply("Je n'ai pas la permission d'ajouter ce rôle au membre.")
            return
        except discord.HTTPException:
            await ctx.reply("Impossible d'ajouter le rôle (erreur HTTP).")
            return

        # DM d'information
        try:
            emb = discord.Embed(
                title="Bienvenue !",
                color=discord.Color.green() if mode == "full" else discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            if mode == "full":
                emb.description = (
                    "Ta candidature a été acceptée ! Tu as maintenant l'accès complet au serveur. "
                    "Soit pas timide, dis bonjour surtout retient que nous sommes entres frère à Private Place, aucune formalité."
                )
            else:
                emb.description = (
                    "Ta candidature a été acceptée ! Tu as un accès partiel (salons de discussion normaux). "
                    "Si tu veux une information demande au staff ('A')."
                )
            await member.send(embed=emb)
        except Exception:
            # DM fermés ou échec — ignorer silencieusement
            pass

        # Editer le message d'origine pour éviter de polluer le salon
        if edit_message:
            try:
                done = discord.Embed(
                    title="Membre accepté",
                    description=f"{member.mention} a été accepté avec le rôle {role.mention}.",
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc),
                )
                await edit_message.edit(embed=done, view=None)
            except Exception:
                pass
        # Log
        await self._log(
            ctx.guild,
            "Acceptation",
            f"{member} accepté ({'full' if mode == 'full' else 'partiel'})",
            discord.Color.green() if mode == "full" else discord.Color.orange(),
            Membre=f"{member} ({member.id})",
            Rôle=role.mention,
            Par=str(ctx.author),
        )

    @commands.command(name="accepte")
    @commands.has_permissions(administrator=True)
    async def accepte(self, ctx: commands.Context, mode: str, *, cible: str):
        """!accepte <full|partiel> <mention|id|nom> — Accepte un membre et lui attribue le bon rôle, avec confirmation.

        Note: <nom> peut contenir des espaces (capture gourmande)."""
        guild = ctx.guild
        if not guild:
            return await ctx.reply("Cette commande ne peut être utilisée qu'en serveur.")

        mode = mode.lower()
        if mode not in {"full", "partiel"}:
            return await ctx.reply("Mode invalide. Utilise `full` ou `partiel`. Exemple: `!accepte full @membre`.")

        # Résoudre le membre
        member, err = await self._resolve_member(ctx, cible)
        if err:
            return await ctx.reply(err)

        role_id = ROLE_FULL_ID if mode == "full" else ROLE_PARTIEL_ID
        role = guild.get_role(role_id)
        if not isinstance(role, discord.Role):
            return await ctx.reply("Rôle introuvable. Vérifie les IDs des rôles.")

        # Vérif de hiérarchie de rôles
        me: Optional[discord.Member] = guild.me
        if not me or me.top_role <= role:
            return await ctx.reply("Je ne peux pas attribuer ce rôle (hiérarchie insuffisante).")

        # Toujours retirer le rôle PARTIEL s'il est présent (un autre bot peut l'avoir ajouté)
        partial_role = guild.get_role(ROLE_PARTIEL_ID)
        if isinstance(partial_role, discord.Role) and partial_role in member.roles:
            # Vérifier hiérarchie pour retirer
            if me.top_role <= partial_role:
                return await ctx.reply("Je ne peux pas retirer le rôle partiel (hiérarchie insuffisante).")
            try:
                await member.remove_roles(partial_role, reason=f"Nettoyage avant acceptation ({mode}) par {ctx.author}")
            except discord.Forbidden:
                return await ctx.reply("Je n'ai pas la permission de retirer le rôle partiel de ce membre.")
            except discord.HTTPException:
                return await ctx.reply("Impossible de retirer le rôle partiel (erreur HTTP).")

        # Construire embed + view de confirmation
        embed = self._build_confirmation_embed(ctx, mode, member)
        view = ConfirmAcceptView(ctx.author.id, "Accès complet" if mode == "full" else "Accès partiel", member, role)
        msg = await ctx.send(embed=embed, view=view)

        # Attendre confirmation
        await view.wait()
        # Nettoyer la vue une fois terminé (si pas déjà fait par edit)
        try:
            await msg.edit(view=view)
        except Exception:
            pass

        if view.result is None:
            # timeout
            try:
                timeout_emb = discord.Embed(
                    title="Confirmation expirée",
                    description=f"La confirmation d'acceptation de {member.mention} a expiré.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                await msg.edit(embed=timeout_emb, view=None)
            except Exception:
                pass
            return
        if view.result is False:
            # Editer le message initial avec l'état annulé
            try:
                cancel_emb = discord.Embed(
                    title="Acceptation annulée",
                    description=f"L'acceptation de {member.mention} a été annulée.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                await msg.edit(embed=cancel_emb, view=None)
            except Exception:
                pass
            # Log
            await self._log(ctx.guild, "Acceptation annulée", f"Acceptation annulée pour {member}", discord.Color.red(), Membre=f"{member} ({member.id})", Par=str(ctx.author))
            return

        # Procéder à l'acceptation
        await self._assign_role_and_dm(ctx, member, role, mode, edit_message=msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(Postulations(bot))

