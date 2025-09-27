import discord
from discord.ext import commands
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

STAFF_ROLE_ID = 1227018119185903

# -------------------- OUTILS --------------------
def is_staff_or_admin():
    async def predicate(ctx: commands.Context) -> bool:
        if getattr(ctx.author.guild_permissions, "administrator", False):
            return True
        role = discord.utils.get(ctx.guild.roles, id=STAFF_ROLE_ID)
        return role in ctx.author.roles if role else False
    return commands.check(predicate)

# -------------------- REGISTRE MANUEL --------------------
# Chaque entrée: name, usage, summary, details, permissions, examples, aliases (optionnel)
HELP_DATA: Dict[str, List[Dict[str, object]]] = {
    "Modération": [
        {
            "name": "mute",
            "usage": "!mute @membre <durée> [raison]",
            "summary": "Applique un timeout au membre.",
            "details": "Durée au format 5s, 10m, 2h, 1d. Empêche le membre d'envoyer des messages.",
            "permissions": "Modérer les membres",
            "examples": ["!mute @User 10m", "!mute @User 2h Spam"],
        },
        {
            "name": "unmute",
            "usage": "!unmute @membre",
            "summary": "Retire le timeout d'un membre.",
            "details": "Met fin immédiatement au timeout en cours.",
            "permissions": "Modérer les membres",
            "examples": ["!unmute @User"],
        },
        {
            "name": "kick",
            "usage": "!kick @membre [raison]",
            "summary": "Expulse le membre du serveur.",
            "details": "Bouger un membre du serveur sans le bannir, il pourra de nouveau rejoindre si ça candidature est accepté.",
            "permissions": "Expulser des membres",
            "examples": ["!kick @User", "!kick @User Pub sauvage"],
        },
        {
            "name": "ban",
            "usage": "!ban @membre [durée] [raison]",
            "summary": "Bannie un utilisateur du serveur (optionnellement temporaire).",
            "details": "Durée au format 5m, 2h, 1d. À expiration, l'utilisateur est débanni automatiquement.",
            "permissions": "Bannir des membres",
            "examples": ["!ban @User 7d Multi-spam", "!ban @User"]
        },
        {
            "name": "unban",
            "usage": "!unban <id|mention>",
            "summary": "Débannie un utilisateur.",
            "details": "Fonctionne avec l'ID utilisateur (même s'il n'est plus sur le serveur).",
            "permissions": "Bannir des membres",
            "examples": ["!unban 123456789012345678"],
        },
        {
            "name": "slowmode",
            "usage": "!slowmode <secondes>",
            "summary": "Définit le slowmode du salon courant.",
            "details": "Valeurs de 0 à 21600 secondes.",
            "permissions": "Gérer les salons",
            "examples": ["!slowmode 5", "!slowmode 0"],
        },
    ],
    "Salons": [
        {
            "name": "lock",
            "usage": "!lock [#salon]",
            "summary": "Interdit l'envoi de messages dans le salon.",
            "details": "Désactive send_messages pour @everyone et les rôles ayant déjà des permissions explicites dans le salon.",
            "permissions": "Gérer les salons",
            "examples": ["!lock", "!lock #général"],
        },
        {
            "name": "unlock",
            "usage": "!unlock [#salon]",
            "summary": "Rétablit l'envoi de messages.",
            "details": "Supprime les overrides explicites d'interdiction d'envoi.",
            "permissions": "Gérer les salons",
            "examples": ["!unlock", "!unlock #général"],
        },
        {
            "name": "hide",
            "usage": "!hide [#salon]",
            "summary": "Cache le salon pour tout le monde.",
            "details": "Désactive view_channel pour @everyone et rôles déjà présents.",
            "permissions": "Gérer les salons",
            "examples": ["!hide", "!hide #staff"],
        },
        {
            "name": "unhide",
            "usage": "!unhide [#salon]",
            "summary": "Rend le salon à nouveau visible.",
            "details": "Rétablit view_channel en mode hérité.",
            "permissions": "Gérer les salons",
            "examples": ["!unhide", "!unhide #staff"],
        },
    ],
    "Infos": [
        {
            "name": "userinfo",
            "usage": "!userinfo [@membre]",
            "summary": "Affiche les informations d'un utilisateur.",
            "details": "Montre l'ID, la date de création, l'arrivée sur le serveur, les rôles, le top rôle.",
            "permissions": "Aucune (lecture)",
            "examples": ["!userinfo", "!userinfo @User"],
        }
    ],
    "Postulations": [
        {
            "name": "accepte",
            "usage": "!accepte <full|partiel> <mention|id|nom>",
            "summary": "Accepte une candidature (accès complet ou partiel).",
            "details": "Accepte une condidature automatiquement avec le rôle N'est pas pro ou Membre en fonction de l'accès précisé (full/partiel)",
            "permissions": "Administrateur",
            "examples": ["!accepte full @User", "!accepte partiel 123456789012345678", "!accepte full Nom Affichage"],
            "aliases": [],
        }
    ],
}

def _flatten_help(query: str = "", category: Optional[str] = None) -> List[Tuple[str, Dict[str, object]]]:
    items: List[Tuple[str, Dict[str, object]]] = []
    q = (query or "").strip().lower()
    cats = [category] if category else list(HELP_DATA.keys())
    for cat in cats:
        for entry in HELP_DATA.get(cat, []):
            text = f"{entry.get('name','')} {entry.get('summary','')} {entry.get('details','')} {entry.get('usage','')}".lower()
            if q and q not in text:
                continue
            items.append((cat, entry))
    return items


# -------------------- COG --------------------
class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    class HelpView(discord.ui.View):
        def __init__(self, cog: "Help", ctx: commands.Context, query: Optional[str] = None, category: Optional[str] = None, page: int = 0):
            super().__init__(timeout=180)
            self.cog = cog
            self.ctx = ctx
            self.query = (query or "").strip()
            self.category = category
            self.page = page
            self.per_page = 15
            self.items: List[Tuple[str, Dict[str, object]]] = _flatten_help(self.query, self.category)
            self._build_components()

        def _page_slice(self) -> Tuple[int, int]:
            start = self.page * self.per_page
            end = start + self.per_page
            return start, end

        def _build_components(self):
            self.clear_items()

            # Sélecteur de catégorie
            categories = list(HELP_DATA.keys())
            cat_options = [discord.SelectOption(label="Toutes catégories", value="__all__", default=(self.category is None))]
            for cat in categories:
                cat_options.append(discord.SelectOption(label=cat, value=cat, default=(self.category == cat)))

            cat_select = discord.ui.Select(placeholder="Catégorie", min_values=1, max_values=1, options=cat_options)

            async def on_cat_change(inter: discord.Interaction):
                val = inter.data.get("values", ["__all__"]) [0]
                self.category = None if val == "__all__" else val
                self.page = 0
                self.items = _flatten_help(self.query, self.category)
                self._build_components()
                await self.refresh(inter)

            cat_select.callback = on_cat_change
            self.add_item(cat_select)

            # Liste des commandes paginée
            start, end = self._page_slice()
            page_items = self.items[start:end]
            options: List[discord.SelectOption] = []
            for cat, entry in page_items[:25]:
                label = f"!{entry['name']}"
                desc = f"[{cat}] {entry.get('summary','')}"
                options.append(discord.SelectOption(label=label[:100], value=f"{cat}:{entry['name']}", description=desc[:100]))

            if not options:
                options.append(discord.SelectOption(label="Aucune commande trouvée", value="none", description="Modifie la recherche/filtre"))

            cmd_select = discord.ui.Select(placeholder="Choisis une commande", options=options, min_values=1, max_values=1)

            async def on_select(inter: discord.Interaction):
                val = inter.data.get("values", ["none"]) [0]
                if val == "none":
                    return await inter.response.defer()
                cat, name = val.split(":", 1)
                entries = [e for e in HELP_DATA.get(cat, []) if e.get("name") == name]
                if not entries:
                    return await inter.response.send_message("Commande introuvable.", ephemeral=True)
                entry = entries[0]
                await inter.response.edit_message(embed=self.cog.build_entry_embed(cat, entry), view=self)

            cmd_select.callback = on_select
            self.add_item(cmd_select)

            total_pages = max(1, (len(self.items) + self.per_page - 1) // self.per_page)
            prev_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Précédent", disabled=(self.page<=0))
            next_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Suivant", disabled=(self.page>=total_pages-1))
            refresh_btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Rafraîchir")

            async def on_prev(inter: discord.Interaction):
                self.page = max(0, self.page - 1)
                self._build_components()
                await self.refresh(inter)

            async def on_next(inter: discord.Interaction):
                self.page = min(total_pages - 1, self.page + 1)
                self._build_components()
                await self.refresh(inter)

            async def on_refresh(inter: discord.Interaction):
                self.items = _flatten_help(self.query, self.category)
                self._build_components()
                await self.refresh(inter)

            prev_btn.callback = on_prev
            next_btn.callback = on_next
            refresh_btn.callback = on_refresh

            self.add_item(prev_btn)
            self.add_item(next_btn)
            self.add_item(refresh_btn)

        async def refresh(self, inter: discord.Interaction):
            await inter.response.edit_message(embed=self.cog.build_main_embed(self.query, self.category, self.page, len(self.items), self.per_page), view=self)

    def build_main_embed(self, query: str, category: Optional[str], page: int, total_items: int, per_page: int) -> discord.Embed:
        desc = [
            "Parcourez les catégories et sélectionnez une commande pour voir les détails.",
            "- Recherchez: `!aide <texte>`",
            "- Préfixe: `!`",
        ]
        if query:
            desc.append(f"\nFiltre actif: `{query}`")
        if category:
            desc.append(f"\nCatégorie: `{category}`")

        emb = discord.Embed(
            title="Guide des commandes",
            description="\n".join(desc),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        emb.set_footer(text=f"Page {page+1} • Résultats: {total_items}")
        return emb

    def build_entry_embed(self, category: str, entry: Dict[str, object]) -> discord.Embed:
        title = f"📖 !{entry.get('name','')}"
        emb = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        emb.add_field(name="Catégorie", value=category, inline=False)
        if entry.get("summary"):
            emb.add_field(name="Résumé", value=str(entry.get("summary")), inline=False)
        if entry.get("details"):
            emb.add_field(name="Détails", value=str(entry.get("details"))[:1024], inline=False)
        if entry.get("usage"):
            emb.add_field(name="Usage", value=f"`{entry.get('usage')}`", inline=False)
        if entry.get("permissions"):
            emb.add_field(name="Permissions", value=str(entry.get("permissions")), inline=True)
        aliases = entry.get("aliases") or []
        if aliases:
            emb.add_field(name="Alias", value=", ".join(f"!{a}" for a in aliases), inline=True)
        examples = entry.get("examples") or []
        if examples:
            emb.add_field(name="Exemples", value="\n".join(f"`{ex}`" for ex in examples)[:1024], inline=False)
        return emb

    @commands.command(name="aide")
    @is_staff_or_admin()
    async def aide(self, ctx: commands.Context, *, recherche: Optional[str] = None):
        view = self.HelpView(self, ctx, query=recherche or "")
        await ctx.send(embed=self.build_main_embed(recherche or "", None, 0, len(view.items), view.per_page), view=view)


async def setup(bot):
    await bot.add_cog(Help(bot))