import os
import json
import io
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands
from discord import app_commands


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
CONFIG_PATH = os.path.join(DATA_DIR, 'tickets_config.json')
STATE_PATH = os.path.join(DATA_DIR, 'tickets_state.json')


def ensure_data_dir():
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def default_guild_config(guild_id: int) -> Dict[str, Any]:
    return {
        "staff_role_id": None,
        "category_id": None,
        "transcript_channel_id": None,
        "log_channel_id": 1227642409404600370,
        "max_open_per_user": 1,
        "panel_channel_id": None,
        "panel_title": "Support - Tickets",
        "panel_description": "Cliquez sur le bouton ci-dessous pour créer un ticket.",
        "ticket_types": [
            {"label": "Support", "emoji": "💬"},
            {"label": "Signalement", "emoji": "🚨"},
        ],
        "roles_allowed_ids": [],
        "ping_staff_on_open": True,
        "welcome_message": "Expliquez votre demande. Un membre du staff vous répondra bientôt.",
        "open_method": "button",  # button | select | reaction
        "reasons": ["Support", "Signalement"],
        "panel_message_ids": [],
        "force_reason": False,
    }


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Créer un ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket:create")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        cfg = self.cog.get_guild_config(self.guild_id)
        if not cfg:
            return await interaction.response.send_message("Configuration tickets incomplète.", ephemeral=True)

        guild = interaction.guild
        assert guild is not None
        user = interaction.user

        # Respecter le max par utilisateur
        open_count = self.cog.count_user_open_tickets(guild.id, user.id)
        if open_count >= cfg.get("max_open_per_user", 1):
            return await interaction.response.send_message("Vous avez déjà atteint le nombre maximum de tickets ouverts.", ephemeral=True)

        # Si une raison est requise (méthode bouton)
        if cfg.get('force_reason'):
            reasons: List[str] = cfg.get('reasons') or []
            if reasons:
                view = ReasonSelectForOpen(self.cog, self.guild_id, source_interaction=interaction)
                opts = [discord.SelectOption(label=r) for r in reasons[:25]]
                view.select.options = opts
                return await interaction.response.send_message("Choisissez une raison: ", view=view, ephemeral=True)
            else:
                modal = ReasonModal(self.cog, self.guild_id, source_interaction=interaction)
                return await interaction.response.send_modal(modal)

        category_id = cfg.get("category_id")
        category = guild.get_channel(category_id) if category_id else None
        if category_id and not isinstance(category, discord.CategoryChannel):
            category = None

        staff_role = guild.get_role(cfg.get("staff_role_id")) if cfg.get("staff_role_id") else None

        # Création du salon
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)

        name = f"ticket-{user.name.lower()}-{user.discriminator if hasattr(user, 'discriminator') else str(user.id)[-4:]}"
        try:
            channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites, reason=f"Ticket créé par {user}")
        except discord.Forbidden:
            return await interaction.response.send_message("Je n'ai pas la permission de créer le salon.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("Impossible de créer le salon.", ephemeral=True)

        self.cog.register_open_ticket(guild.id, user.id, channel.id)

        # Envoyer message initial avec boutons de gestion
        manage_view = TicketManageView(self.cog, guild.id, opener_id=user.id)
        welcome = cfg.get("welcome_message") or "Expliquez votre demande. Un membre du staff vous répondra bientôt."
        embed = discord.Embed(title="Ticket ouvert", color=discord.Color.blurple(), description=welcome)
        embed.add_field(name="Auteur", value=f"{user.mention} (ID: {user.id})", inline=False)
        # Ping staff/roles autorisés si configuré
        ping_txt = user.mention
        if cfg.get("ping_staff_on_open", True):
            pings: List[str] = []
            if staff_role:
                pings.append(staff_role.mention)
            for rid in cfg.get("roles_allowed_ids", []) or []:
                role = guild.get_role(rid)
                if role:
                    pings.append(role.mention)
            if pings:
                ping_txt += " " + " ".join(pings)
        await channel.send(content=ping_txt, embed=embed, view=manage_view)
        await self.cog.log_ticket(guild, f"Ticket ouvert", f"Salon: {channel.mention}", auteur=user)

        try:
            await interaction.response.send_message(f"Ticket créé: {channel.mention}", ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(f"Ticket créé: {channel.mention}", ephemeral=True)


class TicketManageView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int, opener_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.opener_id = opener_id

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket:close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        await self.cog.close_ticket_channel(channel, closed_by=interaction.user)
        await interaction.response.send_message("Ticket fermé.", ephemeral=True)

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, emoji="🧾", custom_id="ticket:transcript")
    async def transcript_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        await interaction.response.defer(ephemeral=True)
        file = await self.cog.make_transcript(channel)
        await interaction.followup.send(content="Transcript généré.", file=file, ephemeral=True)

    @discord.ui.button(label="Ajouter membre", style=discord.ButtonStyle.success, emoji="➕", custom_id="ticket:add")
    async def add_member(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        modal = AddRemoveUserModal(self.cog, add=True)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Retirer membre", style=discord.ButtonStyle.secondary, emoji="➖", custom_id="ticket:remove")
    async def remove_member(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        modal = AddRemoveUserModal(self.cog, add=False)
        await interaction.response.send_modal(modal)


class AddRemoveUserModal(discord.ui.Modal, title="Gestion des membres du ticket"):
    def __init__(self, cog: 'Tickets', add: bool):
        super().__init__(timeout=None)
        self.cog = cog
        self.add = add
        self.user_id = discord.ui.TextInput(label="ID utilisateur", placeholder="Entrez l'ID de l'utilisateur", required=True, max_length=20)
        self.add_item(self.user_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            uid = int(str(self.user_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("ID invalide.", ephemeral=True)
        user = interaction.client.get_user(uid) or await interaction.client.fetch_user(uid)
        if not user:
            return await interaction.response.send_message("Utilisateur introuvable.", ephemeral=True)

        overwrites = channel.overwrites
        if self.add:
            overwrites[user] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            await channel.edit(overwrites=overwrites)
            return await interaction.response.send_message(f"{user} ajouté au ticket.", ephemeral=True)
        else:
            if user in overwrites:
                del overwrites[user]
                await channel.edit(overwrites=overwrites)
                return await interaction.response.send_message(f"{user} retiré du ticket.", ephemeral=True)
            return await interaction.response.send_message("Cet utilisateur n'avait pas accès.", ephemeral=True)


class Tickets(commands.Cog, name="Tickets"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_data_dir()
        self.config: Dict[str, Any] = load_json(CONFIG_PATH, {})
        self.state: Dict[str, Any] = load_json(STATE_PATH, {})

    async def cog_load(self) -> None:
        # Enregistrer les views persistantes pour TOUS les serveurs connus
        try:
            for gid_str in list(self.config.keys()):
                try:
                    gid = int(gid_str)
                except ValueError:
                    continue
                # Ces views ont timeout=None et des custom_id fixes, donc elles survivront aux redémarrages
                self.bot.add_view(TicketPanelView(self, gid))
                self.bot.add_view(TicketSelectView(self, gid))
                # ManageView ne dépend pas strictement d'opener_id (récupéré à la fermeture via l'embed)
                self.bot.add_view(TicketManageView(self, gid))
        except Exception:
            pass

        # Ajouter le groupe slash /config si non présent
        try:
            # Évite l'erreur si déjà enregistré
            existing = next((c for c in self.bot.tree.get_commands() if isinstance(c, discord.app_commands.Group) and c.name == self.config.name), None)
            if not existing:
                self.bot.tree.add_command(self.config)
        except Exception:
            pass

    # ========= Slash group /config ticket =========
    config = app_commands.Group(name="config", description="Configuration du bot")

    @config.command(name="ticket", description="Ouvre le panneau de configuration des tickets")
    @app_commands.default_permissions(manage_guild=True)
    async def slash_config_ticket(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Cette commande doit être utilisée dans un serveur.", ephemeral=True)
        cfg = self.get_guild_config(interaction.guild.id)
        embed = self.build_config_embed(interaction.guild, cfg, tab="general")
        view = TicketConfigView(self, interaction.guild.id, tab="general")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ---------------- Utils persistence ----------------
    def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        key = str(guild_id)
        if key not in self.config:
            self.config[key] = default_guild_config(guild_id)
            save_json(CONFIG_PATH, self.config)
        return self.config[key]

    def set_guild_config(self, guild_id: int, data: Dict[str, Any]):
        self.config[str(guild_id)] = data
        save_json(CONFIG_PATH, self.config)

    def count_user_open_tickets(self, guild_id: int, user_id: int) -> int:
        g = self.state.setdefault(str(guild_id), {})
        u = g.setdefault("user_open", {})
        chan_ids: List[int] = u.get(str(user_id), [])
        return len(chan_ids)

    def register_open_ticket(self, guild_id: int, user_id: int, channel_id: int) -> None:
        g = self.state.setdefault(str(guild_id), {})
        u = g.setdefault("user_open", {})
        chan_ids: List[int] = u.get(str(user_id), [])
        if channel_id not in chan_ids:
            chan_ids.append(channel_id)
        u[str(user_id)] = chan_ids
        save_json(STATE_PATH, self.state)

    def unregister_open_ticket(self, guild_id: int, user_id: int, channel_id: int) -> None:
        g = self.state.setdefault(str(guild_id), {})
        u = g.setdefault("user_open", {})
        chan_ids: List[int] = u.get(str(user_id), [])
        if channel_id in chan_ids:
            chan_ids.remove(channel_id)
        u[str(user_id)] = chan_ids
        save_json(STATE_PATH, self.state)

    async def make_transcript(self, channel: discord.TextChannel) -> discord.File:
        lines: List[str] = []
        async for msg in channel.history(limit=None, oldest_first=True):
            created = msg.created_at.replace(tzinfo=timezone.utc) if msg.created_at.tzinfo is None else msg.created_at
            line = f"[{created.isoformat()}] {msg.author} ({msg.author.id}): {msg.content}"
            lines.append(line)
        content = "\n".join(lines) if lines else "(aucun message)"
        bytes_io = io.BytesIO(content.encode('utf-8'))
        filename = f"transcript_{channel.name}.txt"
        return discord.File(bytes_io, filename=filename)

    async def close_ticket_channel(self, channel: discord.TextChannel, closed_by: Optional[discord.abc.User] = None):
        # Récupérer l'auteur depuis le message d'ouverture (embed) si possible
        opener_id: Optional[int] = None
        try:
            async for msg in channel.history(limit=50, oldest_first=True):
                if msg.embeds:
                    emb = msg.embeds[0]
                    for f in emb.fields:
                        if f.name == "Auteur" and "ID:" in (f.value or ""):
                            try:
                                opener_id = int(f.value.split("ID:")[-1].strip().strip(")"))
                            except Exception:
                                opener_id = None
                            break
                    break
        except Exception:
            pass

        # Transcript vers salon dédié si configuré
        cfg = self.get_guild_config(channel.guild.id)
        transcript_chan = channel.guild.get_channel(cfg.get("transcript_channel_id")) if cfg.get("transcript_channel_id") else None
        try:
            file = await self.make_transcript(channel)
            if isinstance(transcript_chan, discord.TextChannel):
                embed = discord.Embed(title="Ticket fermé", color=discord.Color.red())
                embed.add_field(name="Salon", value=channel.mention, inline=False)
                if opener_id:
                    embed.add_field(name="Auteur", value=f"<@{opener_id}> ({opener_id})", inline=False)
                if closed_by:
                    embed.add_field(name="Fermé par", value=f"{closed_by} ({closed_by.id})", inline=False)
                await transcript_chan.send(embed=embed, file=file)
            # Log dans le salon de logs
            await self.log_ticket(channel.guild, "Ticket fermé", f"Salon: {channel.mention}", auteur=(f"<@{opener_id}>" if opener_id else None), ferme_par=closed_by)
        except Exception:
            pass

        # Mettre à jour l'état
        if opener_id:
            self.unregister_open_ticket(channel.guild.id, opener_id, channel.id)

        try:
            await channel.delete(reason=f"Ticket fermé par {closed_by}" if closed_by else "Ticket fermé")
        except Exception:
            pass

    # ---------------- Commands (prefix) ----------------
    @commands.group(name='ticket', invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def ticket_group(self, ctx: commands.Context):
        """Commandes de configuration des tickets. Utilisez les sous-commandes."""
        cfg = self.get_guild_config(ctx.guild.id)
        embed = discord.Embed(title="Configuration Tickets", color=discord.Color.blurple())
        embed.add_field(name="Rôle staff", value=str(ctx.guild.get_role(cfg.get('staff_role_id'))) if cfg.get('staff_role_id') else 'Non défini', inline=False)
        embed.add_field(name="Catégorie", value=str(ctx.guild.get_channel(cfg.get('category_id'))) if cfg.get('category_id') else 'Non définie', inline=False)
        embed.add_field(name="Salon transcripts", value=str(ctx.guild.get_channel(cfg.get('transcript_channel_id'))) if cfg.get('transcript_channel_id') else 'Non défini', inline=False)
        embed.add_field(name="Max tickets/utilisateur", value=str(cfg.get('max_open_per_user', 1)), inline=False)
        embed.add_field(name="Panel channel", value=str(ctx.guild.get_channel(cfg.get('panel_channel_id'))) if cfg.get('panel_channel_id') else 'Non défini', inline=False)
        await ctx.reply(embed=embed)

    @ticket_group.command(name='setstaff')
    @commands.has_permissions(manage_guild=True)
    async def ticket_set_staff(self, ctx: commands.Context, role: discord.Role):
        cfg = self.get_guild_config(ctx.guild.id)
        cfg['staff_role_id'] = role.id
        self.set_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"Rôle staff défini: {role.mention}")

    @ticket_group.command(name='setcategory')
    @commands.has_permissions(manage_guild=True)
    async def ticket_set_category(self, ctx: commands.Context, category: discord.CategoryChannel):
        cfg = self.get_guild_config(ctx.guild.id)
        cfg['category_id'] = category.id
        self.set_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"Catégorie définie: {category.name}")

    @ticket_group.command(name='settranscripts')
    @commands.has_permissions(manage_guild=True)
    async def ticket_set_transcripts(self, ctx: commands.Context, channel: discord.TextChannel):
        cfg = self.get_guild_config(ctx.guild.id)
        cfg['transcript_channel_id'] = channel.id
        self.set_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"Salon de transcripts défini: {channel.mention}")

    @ticket_group.command(name='setmax')
    @commands.has_permissions(manage_guild=True)
    async def ticket_set_max(self, ctx: commands.Context, nombre: int):
        cfg = self.get_guild_config(ctx.guild.id)
        cfg['max_open_per_user'] = max(1, min(5, nombre))
        self.set_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"Max tickets par utilisateur: {cfg['max_open_per_user']}")

    @ticket_group.command(name='panelhere')
    @commands.has_permissions(manage_guild=True)
    async def ticket_panel_here(self, ctx: commands.Context):
        cfg = self.get_guild_config(ctx.guild.id)
        cfg['panel_channel_id'] = ctx.channel.id
        self.set_guild_config(ctx.guild.id, cfg)
        await ctx.reply("Ce salon est défini comme panel des tickets.")

    @ticket_group.command(name='deploy')
    @commands.has_permissions(manage_guild=True)
    async def ticket_deploy(self, ctx: commands.Context):
        cfg = self.get_guild_config(ctx.guild.id)
        panel_channel = ctx.guild.get_channel(cfg.get('panel_channel_id')) if cfg.get('panel_channel_id') else ctx.channel
        if not isinstance(panel_channel, discord.TextChannel):
            panel_channel = ctx.channel
        title = cfg.get('panel_title', 'Support - Tickets')
        desc = cfg.get('panel_description', 'Cliquez sur le bouton ci-dessous pour créer un ticket.')
        embed = discord.Embed(title=title, description=desc, color=discord.Color.green())
        method = (cfg.get('open_method') or 'button').lower()
        if method == 'button':
            view = TicketPanelView(self, ctx.guild.id)
            await panel_channel.send(embed=embed, view=view)
        elif method == 'select':
            view = TicketSelectView(self, ctx.guild.id)
            await panel_channel.send(embed=embed, view=view)
        elif method == 'reaction':
            # Envoyer l'embed seul puis ajouter la réaction sur ce message
            msg = await panel_channel.send(embed=embed)
            try:
                await msg.add_reaction("🎫")
            except Exception:
                pass
            st = self.state.setdefault(str(ctx.guild.id), {})
            panel_ids: List[int] = st.setdefault('panel_message_ids', [])
            panel_ids.append(msg.id)
            save_json(STATE_PATH, self.state)
        await ctx.reply("Panel de tickets déployé.")

    # ===================== CONFIG PANEL =====================
    def build_config_embed(self, guild: discord.Guild, cfg: Dict[str, Any], tab: str = "general") -> discord.Embed:
        embed = discord.Embed(title=f"Configuration Tickets — {tab.capitalize()}", color=discord.Color.teal())
        staff_role = guild.get_role(cfg.get('staff_role_id')) if cfg.get('staff_role_id') else None
        category = guild.get_channel(cfg.get('category_id')) if cfg.get('category_id') else None
        transcript = guild.get_channel(cfg.get('transcript_channel_id')) if cfg.get('transcript_channel_id') else None
        log_chan = guild.get_channel(cfg.get('log_channel_id')) if cfg.get('log_channel_id') else None
        roles_allowed = [guild.get_role(rid) for rid in (cfg.get('roles_allowed_ids') or [])]
        roles_allowed_str = ", ".join(r.mention for r in roles_allowed if r) or "(aucun)"
        reasons = cfg.get('reasons') or []
        if tab == "general":
            embed.add_field(name="Rôle staff", value=staff_role.mention if staff_role else "Non défini", inline=False)
            embed.add_field(name="Catégorie", value=getattr(category, 'name', 'Non définie'), inline=False)
            embed.add_field(name="Salon transcripts", value=transcript.mention if isinstance(transcript, discord.TextChannel) else "Non défini", inline=False)
            embed.add_field(name="Salon logs tickets", value=log_chan.mention if isinstance(log_chan, discord.TextChannel) else f"Par défaut (ID 1227642409404600370)", inline=False)
            embed.add_field(name="Max tickets/utilisateur", value=str(cfg.get('max_open_per_user', 1)), inline=True)
        elif tab == "panel":
            embed.add_field(name="Méthode d'ouverture", value=cfg.get('open_method', 'button'), inline=True)
            embed.add_field(name="Raison obligatoire (bouton/réaction)", value="Oui" if cfg.get('force_reason') else "Non", inline=True)
            embed.add_field(name="Ping staff à l'ouverture", value="Oui" if cfg.get('ping_staff_on_open', True) else "Non", inline=True)
            embed.add_field(name="Titre du panel", value=cfg.get('panel_title') or '-', inline=False)
            embed.add_field(name="Description du panel", value=(cfg.get('panel_description') or '-')[:500], inline=False)
        elif tab == "acces":
            embed.add_field(name="Rôles autorisés", value=roles_allowed_str, inline=False)
            embed.add_field(name="Raisons (Select)", value=", ".join(reasons) if reasons else "(aucune)", inline=False)
            wm = cfg.get('welcome_message') or ""
            if wm:
                embed.add_field(name="Message d'accueil", value=wm[:1000], inline=False)
        return embed

    @ticket_group.command(name='config')
    @commands.has_permissions(manage_guild=True)
    async def ticket_config(self, ctx: commands.Context):
        cfg = self.get_guild_config(ctx.guild.id)
        embed = self.build_config_embed(ctx.guild, cfg)
        view = TicketConfigView(self, ctx.guild.id)
        await ctx.reply(embed=embed, view=view)

    # ---------------- Logs helper ----------------
    def _get_ticketlog_channel(self, guild: Optional[discord.Guild]) -> Optional[discord.TextChannel]:
        if not guild:
            return None
        cfg = self.get_guild_config(guild.id)
        chan_id = cfg.get('log_channel_id') or 1227642409404600370
        chan = guild.get_channel(chan_id)
        return chan if isinstance(chan, discord.TextChannel) else None

    async def log_ticket(self, guild: Optional[discord.Guild], title: str, description: str, auteur: Optional[discord.abc.User] = None, ferme_par: Optional[discord.abc.User] = None):
        chan = self._get_ticketlog_channel(guild)
        if not chan:
            return
        embed = discord.Embed(title=title, description=description, color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        if auteur:
            embed.add_field(name="Auteur", value=f"{auteur}", inline=False)
        if ferme_par:
            embed.add_field(name="Fermé par", value=f"{ferme_par}", inline=False)
        try:
            await chan.send(embed=embed)
        except Exception:
            pass

    # ---------------- Open by Select ----------------
    # View pour Select listant les raisons configurées
class TicketSelectView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        cfg = self.cog.get_guild_config(guild_id)
        reasons: List[str] = cfg.get('reasons') or []
        options = [discord.SelectOption(label=r) for r in reasons[:25]] or [discord.SelectOption(label="Support")]
        self.select = discord.ui.Select(placeholder="Choisissez la raison", options=options, custom_id="ticket:select_reason")
        self.select.callback = self._on_select  # type: ignore
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        reason = self.select.values[0]
        cfg = self.cog.get_guild_config(self.guild_id)
        guild = interaction.guild
        user = interaction.user
        if not guild:
            return
        # Reuse creation logic from button handler
        # Category and permissions
        category_id = cfg.get("category_id")
        category = guild.get_channel(category_id) if category_id else None
        if category_id and not isinstance(category, discord.CategoryChannel):
            category = None
        staff_role = guild.get_role(cfg.get("staff_role_id")) if cfg.get("staff_role_id") else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
        for rid in cfg.get("roles_allowed_ids", []) or []:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        name = f"ticket-{user.name.lower()}-{user.discriminator if hasattr(user, 'discriminator') else str(user.id)[-4:]}"
        try:
            channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites, reason=f"Ticket créé par {user} (raison: {reason})")
        except Exception:
            return await interaction.response.send_message("Impossible de créer le salon.", ephemeral=True)
        self.cog.register_open_ticket(guild.id, user.id, channel.id)
        manage_view = TicketManageView(self.cog, guild.id, opener_id=user.id)
        welcome = cfg.get("welcome_message") or "Expliquez votre demande. Un membre du staff vous répondra bientôt."
        embed = discord.Embed(title=f"Ticket ouvert — {reason}", color=discord.Color.blurple(), description=welcome)
        embed.add_field(name="Auteur", value=f"{user.mention} (ID: {user.id})", inline=False)
        ping_txt = user.mention
        if cfg.get("ping_staff_on_open", True):
            pings: List[str] = []
            if staff_role:
                pings.append(staff_role.mention)
            for rid in cfg.get("roles_allowed_ids", []) or []:
                role = guild.get_role(rid)
                if role:
                    pings.append(role.mention)
            if pings:
                ping_txt += " " + " ".join(pings)
        await channel.send(content=ping_txt, embed=embed, view=manage_view)
        await self.cog.log_ticket(guild, f"Ticket ouvert", f"Salon: {channel.mention} (raison: {reason})", auteur=user)
        try:
            await interaction.response.send_message(f"Ticket créé: {channel.mention}", ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(f"Ticket créé: {channel.mention}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))


# ===================== CONFIG VIEWS =====================
class TicketConfigView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int, tab: str = "general"):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.tab = tab

    def _refresh_embed(self, message: discord.Message):
        guild = message.guild
        if not guild:
            return None
        cfg = self.cog.get_guild_config(guild.id)
        return self.cog.build_config_embed(guild, cfg, tab=self.tab)

    def _apply_tab_visibility(self):
        # Désactiver les boutons qui ne correspondent pas à l'onglet courant pour épurer l'UI
        tab_map = {
            'general': {"ticketcfg:staff", "ticketcfg:category", "ticketcfg:transcript", "ticketcfg:logs", "ticketcfg:max"},
            'panel': {"ticketcfg:method", "ticketcfg:paneltext", "ticketcfg:ping", "ticketcfg:forceraison"},
            'acces': {"ticketcfg:roles", "ticketcfg:reasons", "ticketcfg:welcome"},
        }
        active = tab_map.get(self.tab, set())
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                cid = getattr(item, 'custom_id', '')
                # Les boutons onglets restent toujours actifs
                if cid in {"ticketcfg:tab:general", "ticketcfg:tab:panel", "ticketcfg:tab:acces"}:
                    item.disabled = False
                else:
                    item.disabled = cid not in active

    @discord.ui.button(label="Rôle staff", style=discord.ButtonStyle.primary, emoji="👤", custom_id="ticketcfg:staff")
    async def set_staff(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        guild = interaction.guild
        if not guild:
            return
        roles = [r for r in guild.roles if r != guild.default_role]
        options = [discord.SelectOption(label=r.name[:100], value=str(r.id)) for r in roles[:25]]
        if not options:
            return await interaction.response.send_message("Aucun rôle disponible.", ephemeral=True)
        view = RolePickView(self.cog, self.guild_id, target_field='staff_role_id', source_message=interaction.message)
        view.select.options = options
        await interaction.response.send_message("Choisissez le rôle staff:", view=view, ephemeral=True)

    @discord.ui.button(label="Catégorie", style=discord.ButtonStyle.secondary, emoji="📂", custom_id="ticketcfg:category")
    async def set_category(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        guild = interaction.guild
        if not guild:
            return
        cats = [c for c in guild.categories]
        options = [discord.SelectOption(label=c.name[:100], value=str(c.id)) for c in cats[:25]]
        if not options:
            return await interaction.response.send_message("Aucune catégorie disponible.", ephemeral=True)
        view = ChannelPickView(self.cog, self.guild_id, target_field='category_id', source_message=interaction.message)
        view.select.options = options
        await interaction.response.send_message("Choisissez la catégorie:", view=view, ephemeral=True)

    @discord.ui.button(label="Transcripts", style=discord.ButtonStyle.secondary, emoji="🧾", custom_id="ticketcfg:transcript")
    async def set_transcripts(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        guild = interaction.guild
        if not guild:
            return
        chans = [ch for ch in guild.text_channels]
        options = [discord.SelectOption(label=ch.name[:100], value=str(ch.id)) for ch in chans[:25]]
        if not options:
            return await interaction.response.send_message("Aucun salon texte disponible.", ephemeral=True)
        view = ChannelPickView(self.cog, self.guild_id, target_field='transcript_channel_id', source_message=interaction.message)
        view.select.options = options
        await interaction.response.send_message("Choisissez le salon de transcripts:", view=view, ephemeral=True)

    @discord.ui.button(label="Logs tickets", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="ticketcfg:logs")
    async def set_logs(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        guild = interaction.guild
        if not guild:
            return
        chans = [ch for ch in guild.text_channels]
        options = [discord.SelectOption(label=ch.name[:100], value=str(ch.id)) for ch in chans[:25]]
        if not options:
            return await interaction.response.send_message("Aucun salon texte disponible.", ephemeral=True)
        view = ChannelPickView(self.cog, self.guild_id, target_field='log_channel_id', source_message=interaction.message)
        view.select.options = options
        await interaction.response.send_message("Choisissez le salon de logs:", view=view, ephemeral=True)

    @discord.ui.button(label="Ping staff: ON/OFF", style=discord.ButtonStyle.success, emoji="🔔", custom_id="ticketcfg:ping")
    async def toggle_ping(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        guild = interaction.guild
        if not guild:
            return
        cfg = self.cog.get_guild_config(guild.id)
        cfg['ping_staff_on_open'] = not cfg.get('ping_staff_on_open', True)
        self.cog.set_guild_config(guild.id, cfg)
        await interaction.response.send_message(f"Ping staff: {'activé' if cfg['ping_staff_on_open'] else 'désactivé'}", ephemeral=True)
        try:
            await interaction.message.edit(embed=self._refresh_embed(interaction.message), view=self)
        except Exception:
            pass

    @discord.ui.button(label="Méthode", style=discord.ButtonStyle.primary, emoji="⚙️", custom_id="ticketcfg:method")
    async def set_method(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        options = [
            discord.SelectOption(label='button', value='button'),
            discord.SelectOption(label='select', value='select'),
            discord.SelectOption(label='reaction', value='reaction'),
        ]
        view = SimpleChoiceView(self.cog, self.guild_id, target_field='open_method', source_message=interaction.message, options=options)
        await interaction.response.send_message("Choisissez la méthode d'ouverture:", view=view, ephemeral=True)

    @discord.ui.button(label="Raisons", style=discord.ButtonStyle.secondary, emoji="📝", custom_id="ticketcfg:reasons")
    async def manage_reasons(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        # Sous-menu: ajouter ou retirer
        view = ReasonsManageView(self.cog, self.guild_id, source_message=interaction.message)
        await interaction.response.send_message("Gérer les raisons (ajouter/retirer)", view=view, ephemeral=True)

    @discord.ui.button(label="Texte du panel", style=discord.ButtonStyle.secondary, emoji="🖊️", custom_id="ticketcfg:paneltext")
    async def edit_panel_text(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        modal = PanelTextModal(self.cog, self.guild_id, source_message=interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Rôles autorisés", style=discord.ButtonStyle.secondary, emoji="🔐", custom_id="ticketcfg:roles")
    async def manage_roles_allowed(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        guild = interaction.guild
        if not guild:
            return
        roles = [r for r in guild.roles if r != guild.default_role]
        options = [discord.SelectOption(label=r.name[:100], value=str(r.id)) for r in roles[:25]]
        view = MultiRolePickView(self.cog, self.guild_id, source_message=interaction.message)
        view.select.options = options
        await interaction.response.send_message("Sélectionnez les rôles autorisés au ticket:", view=view, ephemeral=True)

    @discord.ui.button(label="Message d'accueil", style=discord.ButtonStyle.secondary, emoji="💬", custom_id="ticketcfg:welcome")
    async def set_welcome(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        modal = WelcomeModal(self.cog, self.guild_id, source_message=interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Max par user", style=discord.ButtonStyle.secondary, emoji="#️⃣", custom_id="ticketcfg:max")
    async def set_max_per_user(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        modal = MaxTicketModal(self.cog, self.guild_id, source_message=interaction.message)
        await interaction.response.send_modal(modal)


class RolePickView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int, target_field: str, source_message: Optional[discord.Message]):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.target_field = target_field
        self.source_message = source_message
        self.select = discord.ui.Select(placeholder="Choisissez un rôle", min_values=1, max_values=1)
        self.add_item(self.select)

        async def on_select(interaction: discord.Interaction):
            val = int(self.select.values[0])
            cfg = self.cog.get_guild_config(self.guild_id)
            cfg[self.target_field] = val
            self.cog.set_guild_config(self.guild_id, cfg)
            await interaction.response.send_message("Enregistré.", ephemeral=True)
            try:
                if self.source_message:
                    await self.source_message.edit(embed=self.cog.build_config_embed(interaction.guild, cfg), view=TicketConfigView(self.cog, self.guild_id))
            except Exception:
                pass

        self.select.callback = on_select  # type: ignore


class ChannelPickView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int, target_field: str, source_message: Optional[discord.Message]):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.target_field = target_field
        self.source_message = source_message
        self.select = discord.ui.Select(placeholder="Choisissez un salon/catégorie", min_values=1, max_values=1)
        self.add_item(self.select)

        async def on_select(interaction: discord.Interaction):
            val = int(self.select.values[0])
            cfg = self.cog.get_guild_config(self.guild_id)
            cfg[self.target_field] = val
            self.cog.set_guild_config(self.guild_id, cfg)
            await interaction.response.send_message("Enregistré.", ephemeral=True)
            try:
                if self.source_message:
                    await self.source_message.edit(embed=self.cog.build_config_embed(interaction.guild, cfg), view=TicketConfigView(self.cog, self.guild_id))
            except Exception:
                pass

        self.select.callback = on_select  # type: ignore


class SimpleChoiceView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int, target_field: str, source_message: Optional[discord.Message], options: List[discord.SelectOption]):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.target_field = target_field
        self.source_message = source_message
        self.select = discord.ui.Select(placeholder="Choisissez", options=options, min_values=1, max_values=1)
        self.add_item(self.select)

        async def on_select(interaction: discord.Interaction):
            val = self.select.values[0]
            cfg = self.cog.get_guild_config(self.guild_id)
            cfg[self.target_field] = val
            self.cog.set_guild_config(self.guild_id, cfg)
            await interaction.response.send_message("Enregistré.", ephemeral=True)
            try:
                if self.source_message:
                    await self.source_message.edit(embed=self.cog.build_config_embed(interaction.guild, cfg), view=TicketConfigView(self.cog, self.guild_id))
            except Exception:
                pass

        self.select.callback = on_select  # type: ignore


class WelcomeModal(discord.ui.Modal, title="Message d'accueil"):
    def __init__(self, cog: 'Tickets', guild_id: int, source_message: Optional[discord.Message]):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.source_message = source_message
        self.text = discord.ui.TextInput(label="Texte", style=discord.TextStyle.long, max_length=1000, required=False)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cfg = self.cog.get_guild_config(self.guild_id)
        cfg['welcome_message'] = str(self.text.value)
        self.cog.set_guild_config(self.guild_id, cfg)
        await interaction.response.send_message("Message enregistré.", ephemeral=True)
        try:
            if self.source_message:
                await self.source_message.edit(embed=self.cog.build_config_embed(interaction.guild, cfg), view=TicketConfigView(self.cog, self.guild_id))
        except Exception:
            pass


class MaxTicketModal(discord.ui.Modal, title="Max tickets par utilisateur"):
    def __init__(self, cog: 'Tickets', guild_id: int, source_message: Optional[discord.Message]):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.source_message = source_message
        self.number = discord.ui.TextInput(label="Nombre (1-5)", max_length=2, required=True)
        self.add_item(self.number)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            n = int(str(self.number.value))
        except ValueError:
            n = 1
        n = max(1, min(5, n))
        cfg = self.cog.get_guild_config(self.guild_id)
        cfg['max_open_per_user'] = n
        self.cog.set_guild_config(self.guild_id, cfg)
        await interaction.response.send_message("Enregistré.", ephemeral=True)
        try:
            if self.source_message:
                await self.source_message.edit(embed=self.cog.build_config_embed(interaction.guild, cfg), view=TicketConfigView(self.cog, self.guild_id))
        except Exception:
            pass


class ReasonsManageView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int, source_message: Optional[discord.Message]):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.source_message = source_message

    @discord.ui.button(label="Ajouter", style=discord.ButtonStyle.success, emoji="➕")
    async def add_reason(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        modal = ReasonAddModal(self.cog, self.guild_id, self.source_message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Retirer", style=discord.ButtonStyle.danger, emoji="➖")
    async def remove_reason(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
        cfg = self.cog.get_guild_config(self.guild_id)
        reasons: List[str] = cfg.get('reasons') or []
        if not reasons:
            return await interaction.response.send_message("Aucune raison à retirer.", ephemeral=True)
        options = [discord.SelectOption(label=r, value=r) for r in reasons[:25]]
        view = SimpleChoiceView(self.cog, self.guild_id, target_field='reasons_remove', source_message=self.source_message, options=options)

        async def on_select(inter: discord.Interaction):
            val = view.select.values[0]
            cfg2 = self.cog.get_guild_config(self.guild_id)
            arr = cfg2.get('reasons') or []
            if val in arr:
                arr.remove(val)
            cfg2['reasons'] = arr
            self.cog.set_guild_config(self.guild_id, cfg2)
            await inter.response.send_message("Supprimée.", ephemeral=True)
            try:
                if self.source_message:
                    await self.source_message.edit(embed=self.cog.build_config_embed(inter.guild, cfg2), view=TicketConfigView(self.cog, self.guild_id))
            except Exception:
                pass

        view.select.callback = on_select  # type: ignore
        await interaction.response.send_message("Choisissez la raison à retirer:", view=view, ephemeral=True)


class PanelTextModal(discord.ui.Modal, title="Texte du panel"):
    def __init__(self, cog: 'Tickets', guild_id: int, source_message: Optional[discord.Message]):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.source_message = source_message
        self.title_input = discord.ui.TextInput(label="Titre", max_length=100, required=False)
        self.desc_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.long, max_length=1000, required=False)
        self.add_item(self.title_input)
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cfg = self.cog.get_guild_config(self.guild_id)
        if str(self.title_input.value).strip():
            cfg['panel_title'] = str(self.title_input.value)
        if str(self.desc_input.value).strip():
            cfg['panel_description'] = str(self.desc_input.value)
        self.cog.set_guild_config(self.guild_id, cfg)
        await interaction.response.send_message("Texte du panel mis à jour.", ephemeral=True)
        try:
            if self.source_message:
                await self.source_message.edit(embed=self.cog.build_config_embed(interaction.guild, cfg), view=TicketConfigView(self.cog, self.guild_id))
        except Exception:
            pass


# ===== Raison à l'ouverture (bouton ou via réaction) =====
class ReasonSelectForOpen(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int, source_interaction: Optional[discord.Interaction] = None):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.source_interaction = source_interaction
        self.select = discord.ui.Select(placeholder="Choisissez une raison", min_values=1, max_values=1, custom_id="ticket:force_reason_select")
        self.add_item(self.select)

        async def on_select(interaction: discord.Interaction):
            reason = self.select.values[0]
            # Continuer le flux de création via bouton après choix
            if self.source_interaction:
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                await go_create_ticket_with_reason(self.cog, self.guild_id, self.source_interaction, reason)
            else:
                await interaction.response.send_message(f"Raison prise en compte: {reason}", ephemeral=True)

        self.select.callback = on_select  # type: ignore


class ReasonModal(discord.ui.Modal, title="Raison du ticket"):
    def __init__(self, cog: 'Tickets', guild_id: int, source_interaction: discord.Interaction):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.source_interaction = source_interaction
        self.text = discord.ui.TextInput(label="Raison", max_length=100, required=True)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        reason = str(self.text.value)
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        await go_create_ticket_with_reason(self.cog, self.guild_id, self.source_interaction, reason)


async def go_create_ticket_with_reason(cog: 'Tickets', guild_id: int, source_interaction: discord.Interaction, reason: str):
    # Reprise du flux de TicketPanelView.create_ticket avec raison imposée
    guild = source_interaction.guild
    user = source_interaction.user
    if not guild:
        return
    cfg = cog.get_guild_config(guild_id)
    open_count = cog.count_user_open_tickets(guild.id, user.id)
    if open_count >= cfg.get("max_open_per_user", 1):
        try:
            return await source_interaction.followup.send("Vous avez déjà atteint le nombre maximum de tickets ouverts.", ephemeral=True)
        except Exception:
            return
    category_id = cfg.get("category_id")
    category = guild.get_channel(category_id) if category_id else None
    if category_id and not isinstance(category, discord.CategoryChannel):
        category = None
    staff_role = guild.get_role(cfg.get("staff_role_id")) if cfg.get("staff_role_id") else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
    for rid in cfg.get("roles_allowed_ids", []) or []:
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    name = f"ticket-{user.name.lower()}-{user.discriminator if hasattr(user, 'discriminator') else str(user.id)[-4:]}"
    try:
        channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites, reason=f"Ticket créé par {user} (raison: {reason})")
    except Exception:
        try:
            await source_interaction.followup.send("Impossible de créer le salon.", ephemeral=True)
        except Exception:
            pass
        return
    cog.register_open_ticket(guild.id, user.id, channel.id)
    manage_view = TicketManageView(cog, guild.id, opener_id=user.id)
    welcome = cfg.get("welcome_message") or "Expliquez votre demande. Un membre du staff vous répondra bientôt."
    embed = discord.Embed(title=f"Ticket ouvert — {reason}", color=discord.Color.blurple(), description=welcome)
    embed.add_field(name="Auteur", value=f"{user.mention} (ID: {user.id})", inline=False)
    ping_txt = user.mention
    if cfg.get("ping_staff_on_open", True):
        pings: List[str] = []
        if staff_role:
            pings.append(staff_role.mention)
        for rid in cfg.get("roles_allowed_ids", []) or []:
            role = guild.get_role(rid)
            if role:
                pings.append(role.mention)
        if pings:
            ping_txt += " " + " ".join(pings)
    await channel.send(content=ping_txt, embed=embed, view=manage_view)
    await cog.log_ticket(guild, f"Ticket ouvert", f"Salon: {channel.mention} (raison: {reason})", auteur=user)
    try:
        await source_interaction.followup.send(f"Ticket créé: {channel.mention}", ephemeral=True)
    except Exception:
        pass


class ReasonAddModal(discord.ui.Modal, title="Ajouter une raison"):
    def __init__(self, cog: 'Tickets', guild_id: int, source_message: Optional[discord.Message]):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.source_message = source_message
        self.text = discord.ui.TextInput(label="Raison", max_length=100, required=True)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cfg = self.cog.get_guild_config(self.guild_id)
        arr: List[str] = cfg.get('reasons') or []
        val = str(self.text.value).strip()
        if val and val not in arr:
            arr.append(val)
        cfg['reasons'] = arr
        self.cog.set_guild_config(self.guild_id, cfg)
        await interaction.response.send_message("Ajoutée.", ephemeral=True)
        try:
            if self.source_message:
                await self.source_message.edit(embed=self.cog.build_config_embed(interaction.guild, cfg), view=TicketConfigView(self.cog, self.guild_id))
        except Exception:
            pass


class MultiRolePickView(discord.ui.View):
    def __init__(self, cog: 'Tickets', guild_id: int, source_message: Optional[discord.Message]):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.source_message = source_message
        self.select = discord.ui.Select(placeholder="Choisissez les rôles", min_values=0, max_values=25)
        self.add_item(self.select)

        async def on_select(interaction: discord.Interaction):
            values = [int(v) for v in self.select.values]
            cfg = self.cog.get_guild_config(self.guild_id)
            cfg['roles_allowed_ids'] = values
            self.cog.set_guild_config(self.guild_id, cfg)
            await interaction.response.send_message("Enregistré.", ephemeral=True)
            try:
                if self.source_message:
                    await self.source_message.edit(embed=self.cog.build_config_embed(interaction.guild, cfg), view=TicketConfigView(self.cog, self.guild_id))
            except Exception:
                pass

        self.select.callback = on_select  # type: ignore

