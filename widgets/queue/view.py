"""View с пагинацией очереди (◀ ▶ ⏮ ⏭ 🔄 ✖)"""
import discord

from audio import Track

from .embed import build_queue_embed


QUEUE_PAGE_SIZE = 20


class QueueView(discord.ui.View):
    def __init__(self, get_items, *, page_size: int = QUEUE_PAGE_SIZE, owner_id: int | None = None):
        super().__init__(timeout=180)
        self.get_items = get_items
        self.items: list[Track] = list(get_items())
        self.page_size = page_size
        self.page = 0
        self.owner_id = owner_id
        self.message: discord.Message | None = None
        self._recompute_max_page()
        self._sync_buttons()

    def _recompute_max_page(self):
        self.max_page = max(0, (len(self.items) - 1) // self.page_size)

    def _sync_buttons(self):
        at_start = self.page == 0
        at_end = self.page >= self.max_page
        self.first.disabled = at_start
        self.prev.disabled = at_start
        self.next.disabled = at_end
        self.last.disabled = at_end

    def _refresh_items(self):
        self.items = list(self.get_items())
        self._recompute_max_page()
        self.page = min(self.page, self.max_page)
        self._sync_buttons()

    def build_embed(self) -> discord.Embed:
        return build_queue_embed(
            self.items,
            page=self.page,
            page_size=self.page_size,
            max_page=self.max_page,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Кнопки доступны только вызвавшему команду"""
        if self.owner_id is None or interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            'Это чужой список — вызовите /queue сами', ephemeral=True,
        )
        return False

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label='⏮', style=discord.ButtonStyle.secondary, row=0)
    async def first(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.page = 0
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary, row=0)
    async def prev(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label='▶', style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label='⏭', style=discord.ButtonStyle.secondary, row=0)
    async def last(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.page = self.max_page
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label='🔄', style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self._refresh_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label='✖', style=discord.ButtonStyle.danger, row=1)
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.stop()
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            try:
                await interaction.response.edit_message(view=None)
            except discord.HTTPException:
                pass
