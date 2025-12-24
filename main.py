import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput, Select 
import json
import os
import asyncio
import re
import imaplib
import email
from email.header import decode_header
from flask import Flask
from threading import Thread
from dotenv import load_dotenv
import random 
import string 
import pymongo

load_dotenv()

# --- CẤU HÌNH ---
TOKEN = os.getenv("TOKEN")
BANK_ID = os.getenv("BANK_ID")
ACCOUNT_NO = os.getenv("ACCOUNT_NO")
EMAIL_USER = os.getenv("EMAIL_USER")       
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") 
IMAP_SERVER = "imap.gmail.com"

CHANNEL_ID_SHOP = int(os.getenv("CHANNEL_ID_SHOP"))
CHANNEL_ID_ADMIN = int(os.getenv("CHANNEL_ID_ADMIN"))
CHANNEL_ID_CONSULT = int(os.getenv("CHANNEL_ID_CONSULT"))
CHANNEL_ID_LOG = int(os.getenv("CHANNEL_ID_LOG", 0))
CHANNEL_ID_IMAGE = int(os.getenv("CHANNEL_ID_IMAGE", 0))
CHANNEL_ID_MANAGEMENT = int(os.getenv("CHANNEL_ID_MANAGEMENT"))

# --- CẤU HÌNH MONGODB ---
MONGO_URI = os.getenv("MONGO_URI") 
mongo_client = pymongo.MongoClient(MONGO_URI)
db = mongo_client["GachazShop"] 

# 1. Collection lưu đơn hàng/ảnh
col_images = db["order_images"] 

# 2. Collection lưu sản phẩm
col_products = db["products"] 

# --- CẤU HÌNH DANH SÁCH ---
LIST_GAMES = ["Genshin Impact", "Wuthering Waves", "Honkai: Star Rail", "Zenless Zone Zero"]
LIST_BOOSTERS = ["Không chọn (Mặc định)", "Live 2", "Live 3", "Live 5", "Live 6","Live 7","Live 8","Live 9","Live 10","Live 12","Live 13","Live 15","Live 19","Live 20","Live 21","Live 22","Live 23","Live 24"]

# --- BIẾN TOÀN CỤC & CACHE ---
user_carts = {}    
active_tickets = {} 
user_choices = {} 
CACHED_PRODUCTS = None # Biến lưu danh sách sản phẩm tạm thời

# --- HÀM LOAD DATA TỪ MONGODB (CACHE + FIX _ID) ---
def load_products(force_update=False):
    """
    Đọc toàn bộ sản phẩm từ MongoDB và lưu vào Cache.
    Sửa lỗi: Dùng _id thay vì pid.
    """
    global CACHED_PRODUCTS
    
    # Nếu đã có Cache và không bắt buộc update -> Dùng luôn
    if CACHED_PRODUCTS is not None and not force_update:
        return CACHED_PRODUCTS

    try:
        data = {}
        cursor = col_products.find({})
        
        for doc in cursor:
            # QUAN TRỌNG: Lấy _id làm mã sản phẩm
            pid = doc.get('_id')
            if not pid: continue
            
            product_info = {
                "name": doc.get("name"),
                "content": doc.get("content"),
                "price": doc.get("price"),
                "note": doc.get("note", "Trống"),
                "game": doc.get("game")
            }
            data[pid] = product_info
            
        CACHED_PRODUCTS = data
        print(f"⚡ Đã cập nhật Cache: {len(data)} sản phẩm.")
        return data
    except Exception as e:
        print(f"❌ Lỗi đọc MongoDB Products: {e}")
        return {}

# --- KHỞI TẠO BOT (ĐÂY LÀ PHẦN BẠN BỊ THIẾU) ---
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# ==========================================
# --- PHẦN 1: USER / MUA HÀNG ---
# ==========================================

class QtyModal(Modal):
    def __init__(self, product_id, product_name, product_content, product_price):
        super().__init__(title=f"Mua {product_name}")
        self.product_id = product_id
        self.product_name = product_name
        self.product_content = product_content
        self.product_price = product_price
        
        self.qty_input = TextInput(
            label="Số lượng muốn mua", 
            placeholder="Nhập số lượng (Ví dụ: 1, 2, 5...)", 
            min_length=1, 
            max_length=3
        )
        self.add_item(self.qty_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.qty_input.value.isdigit():
            return await interaction.response.send_message("❌ Vui lòng nhập số!", ephemeral=True)
        
        qty = int(self.qty_input.value)
        if qty <= 0:
             return await interaction.response.send_message("❌ Số lượng phải lớn hơn 0!", ephemeral=True)

        uid = interaction.user.id
        if uid not in user_carts: user_carts[uid] = {}
        user_carts[uid][self.product_id] = user_carts[uid].get(self.product_id, 0) + qty

        total_price = self.product_price * qty

        embed = discord.Embed(
            title="🛒 ĐÃ THÊM VÀO GIỎ HÀNG", 
            description=f"Sản phẩm **{self.product_name}** đã được thêm thành công.",
            color=0x2ecc71
        )
        
        info_text = (
            f"**📦 Sản phẩm:** {self.product_name}\n"
            f"**📝 Mô tả:** \n{self.product_content}\n"
            f"**──────────────────────**\n"
            f"**💵 Đơn giá:** {self.product_price:,} VNĐ\n"
            f"**🔢 Số lượng:** {qty}\n"
            f"**💰 TẠM TÍNH:** **{total_price:,} VNĐ**"
        )
        
        embed.add_field(name="Chi tiết đơn hàng", value=info_text, inline=False)
        embed.set_footer(text="Nhấn nút [Giỏ hàng & Thanh toán] để hoàn tất đơn hàng.")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class PostPaymentView(View):
    def __init__(self, channel_jump_url):
        super().__init__(timeout=None)
        self.channel_jump_url = channel_jump_url

    @discord.ui.button(label="🗑️ XÓA GIỎ HÀNG", style=discord.ButtonStyle.danger, emoji="🧹")
    async def clear(self, interaction: discord.Interaction, button: Button):
        uid = interaction.user.id
        if uid in user_carts: 
            del user_carts[uid]
        
        for item in self.children:
            item.disabled = True
            
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🧹 **Đã xóa sạch giỏ hàng! Bạn có thể lên đơn hàng mới**.", ephemeral=True)

class SearchModal(Modal, title="Tìm kiếm sản phẩm"):
    query = TextInput(
        label="Nhập tên sản phẩm", 
        placeholder="Để trống để xem tất cả list game đã chọn...", 
        min_length=0, 
        max_length=50,
        required=False
    )

    def __init__(self, selected_game=None):
        super().__init__()
        self.selected_game = selected_game

    async def on_submit(self, interaction: discord.Interaction):
        search_value = self.query.value.strip().lower()
        products = load_products() # Load từ Cache
        
        results = {}
        for pid, p in products.items():
            if self.selected_game:
                if p.get('game') != self.selected_game:
                    continue
            
            if search_value:
                if search_value not in p['name'].lower():
                    continue
            
            results[pid] = p
        
        if not results: 
            msg = "❌ Không tìm thấy sản phẩm nào."
            if self.selected_game: msg += f" (Game: **{self.selected_game}**)"
            if search_value: msg += f" (Từ khóa: `{search_value}`)"
            return await interaction.response.send_message(msg, ephemeral=True)
        
        embeds = []
        view = View()
        
        count = 0
        for pid, p in results.items():
            if count >= 10: break
            
            embed = discord.Embed(title=f"✨ {p['name'].upper()}", color=0x3498db)
            info_text = (
                f"**-Game :** {p.get('game', 'Chưa phân loại')}\n"
                f"**-Giá :** {p['price']:,} VNĐ\n"
                f"**-Mô tả :**\n{p['content']}\n"
                f"**-Lưu ý :** {p.get('note', 'Không có')}\n"
                f"──────────────────────────"
            )
            embed.description = info_text
            embeds.append(embed)
            
            btn = Button(label=f"Chọn {p['name'][:15]}...", style=discord.ButtonStyle.primary)
            
            def mk_cb(p_id, p_name, p_content, p_price):
                async def cb(i: discord.Interaction): 
                    await i.response.send_modal(QtyModal(p_id, p_name, p_content, p_price))
                return cb
            
            btn.callback = mk_cb(pid, p['name'], p['content'], p['price'])
            view.add_item(btn)
            count += 1

        await interaction.response.send_message(embeds=embeds, view=view, ephemeral=True)

class GameSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=game, emoji="🎮") for game in LIST_GAMES]
        super().__init__(placeholder="🎮 Chọn Game muốn tìm...", min_values=1, max_values=1, options=options, custom_id="select_game")

    async def callback(self, interaction: discord.Interaction):
        uid = interaction.user.id
        selected_game = self.values[0]
        
        if uid not in user_choices: user_choices[uid] = {}
        user_choices[uid]['game'] = selected_game
        
        await interaction.response.send_message(f"✅ Đã chọn Game: **{selected_game}**. Nhấn nút **[🔍 Tìm kiếm]** để xem sản phẩm.", ephemeral=True)

class BoosterSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=booster, emoji="👤") for booster in LIST_BOOSTERS]
        super().__init__(placeholder="👤 Chọn Người cày thuê (Nếu cần)...", min_values=1, max_values=1, options=options, custom_id="select_booster")

    async def callback(self, interaction: discord.Interaction):
        uid = interaction.user.id
        selected_booster = self.values[0]
        
        if uid not in user_choices: user_choices[uid] = {}
        user_choices[uid]['booster'] = selected_booster
        
        msg = f"✅ Đã chọn người cày: **{selected_booster}**"
        if selected_booster == "Không chọn (Mặc định)":
             msg = "✅ Đã hủy chọn người cày."
             
        await interaction.response.send_message(msg, ephemeral=True)

class TicketShopView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GameSelect())
        self.add_item(BoosterSelect())

    @discord.ui.button(label="🔍 Tìm kiếm / Hiện List", style=discord.ButtonStyle.primary, emoji="🔎", row=2)
    async def search(self, interaction: discord.Interaction, button: Button):
        uid = interaction.user.id
        current_game = user_choices.get(uid, {}).get('game', None)
        await interaction.response.send_modal(SearchModal(selected_game=current_game))

    @discord.ui.button(label="💳 Giỏ hàng & Thanh toán", style=discord.ButtonStyle.success, emoji="🛒", row=2)
    async def checkout(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        cart = user_carts.get(uid, {})
        if not cart: 
            return await interaction.followup.send("🛒 Giỏ hàng trống!", ephemeral=True)
        
        products = load_products()
        total, detail_list = 0, []
        
        for pid, qty in cart.items():
            if pid in products:
                p = products[pid]
                total += p['price'] * qty
                detail_list.append(f"• {p['name']} x{qty} = {p['price']*qty:,}đ")
        
        detail_text = "\n".join(detail_list)
        booster_name = user_choices.get(uid, {}).get('booster', "Không chọn")

        transfer_code = f"DH{uid}"
        qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{ACCOUNT_NO}-compact2.png?amount={total}&addInfo={transfer_code}"
        
        embed = discord.Embed(title="🧾 HÓA ĐƠN THANH TOÁN", color=0xf1c40f)
        embed.add_field(name="Chi Tiết Đơn Hàng", value=f"```{detail_text}```", inline=False)
        
        desc_lines = []
        if booster_name != "Không chọn":
            desc_lines.append(f"**Người Cày :** {booster_name}")
            
        desc_lines.append(f"**Tổng Thanh Toán :** **{total:,} VNĐ**")
        desc_lines.append(f"**Nội Dung CK :** `{transfer_code}`")
        
        embed.description = "\n".join(desc_lines) + "\n\n⚠️ **Lưu ý:** Quét Mã QR để điền đúng [Nội Dung] và [Số Tiền]."
        embed.set_image(url=qr_url)
        embed.set_footer(text="Hệ thống sẽ TỰ ĐỘNG duyệt đơn sau 1-5 phút khi tiền về.")
        
        await interaction.followup.send(embed=embed, view=PostPaymentView(interaction.channel.jump_url), ephemeral=True)

    @discord.ui.button(label="📞 Yêu cầu Tư vấn", style=discord.ButtonStyle.secondary, emoji="🆘", row=2)
    async def consult(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("✅ **Đã gửi yêu cầu hỗ trợ! Admin sẽ sớm có mặt. Vui lòng không spam !!!**", ephemeral=True)
        
        consult_channel = bot.get_channel(CHANNEL_ID_CONSULT)
        if consult_channel:
            embed = discord.Embed(title="🆘 CÓ KHÁCH CẦN TƯ VẤN!", color=0xe74c3c, timestamp=discord.utils.utcnow())
            current_game = user_choices.get(interaction.user.id, {}).get('game', "Chưa chọn game")
            
            desc_lines = [
                f"**Khách Hàng :** {interaction.user.mention}\n",
                f"**Tại Ticket :** [Bấm vào đây để hỗ trợ]({interaction.channel.jump_url})\n",
                f"**Đang Quan Tâm :** {current_game}"
            ]
            embed.description = "\n".join(desc_lines)
            embed.set_footer(text=f"User ID: {interaction.user.id}")
            
            await consult_channel.send(content="@here ⚠️ **Yêu cầu hỗ trợ mới!**", embed=embed)

    @discord.ui.button(label="✖️ Đóng Ticket", style=discord.ButtonStyle.danger, emoji="🔒", row=2)
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⚠️ Kênh sẽ bị xóa vĩnh viễn sau 5 giây...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# --- HÀM HỖ TRỢ: TÌM HOẶC TẠO THREAD ---
async def get_or_create_thread(interaction: discord.Interaction):
    uid = interaction.user.id
    if uid in active_tickets:
        old_thread_id = active_tickets[uid]
        old_thread = interaction.guild.get_thread(old_thread_id)
        if old_thread and not old_thread.archived:
            return old_thread, False

    try:
        thread = await interaction.channel.create_thread(
            name=f"🛒-{interaction.user.name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=1440
        )
        await thread.add_user(interaction.user)
        active_tickets[uid] = thread.id 
        return thread, True 
    except Exception as e:
        print(f"Lỗi tạo thread: {e}")
        return None, False

class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Đóng Ticket", style=discord.ButtonStyle.red, emoji="🔒", custom_id="btn_close_ticket_lookup")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⚠️ **Ticket sẽ được đóng và xóa trong 5 giây...**", ephemeral=True)
        await asyncio.sleep(5)
        if interaction.channel:
            await interaction.channel.delete()

    @discord.ui.button(label="Xem Cửa Hàng", style=discord.ButtonStyle.blurple, emoji="🛍️", custom_id="btn_view_shop_lookup")
    async def view_shop(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="✨ CỬA HÀNG GACHAZ KÍNH CHÀO QUÝ KHÁCH ✨", 
            description=f"Xin Chào {interaction.user.mention}, bạn có thể tìm kiếm sản phẩm và quản lý giỏ hàng ngay tại chủ đề này.",
            color=0xf1c40f
        )
        embed.add_field(name="📋 Hướng dẫn", value=(
            "1️⃣ Chọn **Game** và **Người cày** (nếu cần) ở Menu bên dưới.\n"
            "2️⃣ Bấm **[Tìm kiếm]** để xem sản phẩm.\n"
            "3️⃣ Chọn **[Sản Phẩm]** & nhập **[Số Lượng]**.\n"
            "4️⃣ Bấm **[Giỏ hàng & Thanh toán]** để lấy mã QR."
        ), inline=False)
        embed.set_footer(text="Cửa hàng Gachaz chúc bạn một ngày tốt lành!")
        view = TicketShopView()
        await interaction.response.send_message(embed=embed, view=view)

class CheckOrderModal(Modal, title="Tra Cứu Đơn Hàng"):
    order_id_input = TextInput(
        label="Nhập Mã Đơn Hàng", 
        placeholder="Ví dụ: 7X8H9Z...", 
        required=True,
        min_length=3
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        input_code = self.order_id_input.value.strip()
        order_data = col_images.find_one({"order_id": input_code})
        
        if not order_data:
            return await interaction.followup.send(f"❌ Không tìm thấy đơn hàng nào có mã: **{input_code}**", ephemeral=True)

        thread, is_new = await get_or_create_thread(interaction)
        if not thread:
            return await interaction.followup.send("❌ Lỗi hệ thống: Không thể tạo kênh hỗ trợ.", ephemeral=True)

        embed = discord.Embed(title=f"🔎 KẾT QUẢ TRA CỨU: #{input_code}", color=0x2ecc71)
        desc_lines = []
        price = order_data.get('amount', 0)
        desc_lines.append(f"**Giá Trị :** **{price:,} VNĐ**")
        
        booster_db = order_data.get('booster', 'Không chọn')
        if booster_db != "Không chọn":
            desc_lines.append(f"**Người Cày :** {booster_db}")

        date_info = order_data.get('updated_at') or order_data.get('saved_at')
        if date_info:
            date_str = date_info.strftime("%H:%M %d/%m/%Y")
            desc_lines.append(f"**Cập Nhật :** {date_str}")
        
        admin_note = order_data.get('note')
        if admin_note:
             desc_lines.append(f"**Ghi Chú Admin :** {admin_note}")

        embed.description = "\n".join(desc_lines) + "\n**──────────────────────**"
        details = order_data.get('details', 'Không có chi tiết')
        embed.add_field(name="**Nội Dung Đơn**", value=f"```{details}```", inline=False)
        
        images = order_data.get('images', [])
        embeds_to_send = [embed]
        if images:
            embed.set_image(url=images[0])
            embed.set_footer(text=f"Hình ảnh xác nhận 1/{len(images)}")
            for i in range(1, len(images)):
                if i >= 9: break 
                img_embed = discord.Embed(url="https://discord.com")
                img_embed.set_image(url=images[i])
                embeds_to_send.append(img_embed)
        else:
            embed.set_footer(text="Đơn hàng chưa có ảnh chứng minh.")

        view_ticket = TicketControlView()
        await thread.send(content=f"{interaction.user.mention} Đây là thông tin đơn hàng bạn tra cứu:", embeds=embeds_to_send, view=view_ticket)
        await interaction.followup.send(f"✅ Đã gửi kết quả tra cứu vào: {thread.mention}", ephemeral=True)

class OpenTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 XEM CỬA HÀNG", style=discord.ButtonStyle.danger, custom_id="open_shop", emoji="🏪")
    async def open_shop(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        thread, is_new = await get_or_create_thread(interaction)
        
        if not thread:
            return await interaction.followup.send("❌ Lỗi không tạo được kênh.", ephemeral=True)

        if not is_new:
            await thread.send(content="🔄 **Bạn đã yêu cầu xem lại Menu:**", view=TicketShopView())
            return await interaction.followup.send(f"⚠️ Bạn đang có phiên mua hàng tại: {thread.mention}", ephemeral=True)

        embed = discord.Embed(
            title="✨ CỬA HÀNG GACHAZ KÍNH CHÀO QUÝ KHÁCH ✨", 
            description=f"Xin Chào {interaction.user.mention}, bạn có thể tìm kiếm sản phẩm và quản lý giỏ hàng ngay tại chủ đề này.",
            color=0xf1c40f
        )
        embed.add_field(name="📋 Hướng dẫn", value=(
            "1️⃣ Chọn **Game** và **Người cày** (nếu cần) ở Menu bên dưới.\n"
            "2️⃣ Bấm **[Tìm kiếm]** để xem sản phẩm.\n"
            "3️⃣ Chọn **[Sản Phẩm]** & nhập **[Số Lượng]**.\n"
            "4️⃣ Bấm **[Giỏ hàng & Thanh toán]** để lấy mã QR."
        ), inline=False)
        embed.set_footer(text="Cửa hàng Gachaz chúc bạn một ngày tốt lành!")

        await thread.send(embed=embed, view=TicketShopView())
        await interaction.followup.send(f"✅ Đã tạo chủ đề mua hàng riêng cho bạn: {thread.mention}", ephemeral=True)

    @discord.ui.button(label="🔍 TRA CỨU ĐƠN HÀNG", style=discord.ButtonStyle.secondary, custom_id="lookup_order", emoji="📦")
    async def lookup_order(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(CheckOrderModal())

# ==========================================
# --- PHẦN 2: ADMIN XỬ LÝ ĐƠN HÀNG ---
# ==========================================

class NoteModal(Modal, title="Thêm Ghi Chú Đơn Hàng"):
    note_input = TextInput(
        label="Nội dung ghi chú", 
        style=discord.TextStyle.paragraph, 
        placeholder="Nhập ghi chú cho đơn hàng này...", 
        required=True
    )

    def __init__(self, order_data):
        super().__init__()
        self.order_data = order_data

    async def on_submit(self, interaction: discord.Interaction):
        order_id = self.order_data['order_id']
        try:
            col_images.update_one(
                {"order_id": order_id},
                {
                    "$set": {
                        "order_id": order_id,
                        "amount": self.order_data['amount'],
                        "details": self.order_data['details'],
                        "booster": self.order_data.get('booster', 'Không chọn'),
                        "note": self.note_input.value,
                        "updated_at": discord.utils.utcnow()
                    }
                },
                upsert=True
            )
            await interaction.response.send_message(f"✅ **Đã lưu ghi chú:** {self.note_input.value}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Lỗi lưu ghi chú: {e}", ephemeral=True)

class ConfirmNoImageView(View):
    def __init__(self, thread_view_instance, interaction_curr):
        super().__init__(timeout=60)
        self.thread_view = thread_view_instance
        self.interaction_curr = interaction_curr

    @discord.ui.button(label="⚠️ VẪN BÁO XONG (Không cần ảnh)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        await self.thread_view.finish_order_logic(interaction, force=True)

    @discord.ui.button(label="Hủy Bỏ", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="🚫 Đã hủy thao tác. Hãy gửi ảnh vào đây và bấm Lưu lại.", view=None)

# --- TÌM ĐOẠN CLASS NÀY VÀ THAY THẾ TOÀN BỘ ---
class ThreadOrderView(View):
    def __init__(self, order_data, original_message):
        super().__init__(timeout=None)
        self.order_data = order_data
        self.original_message = original_message 
        self.has_saved_image = False 

    @discord.ui.button(label="💾 Lưu Ảnh", style=discord.ButtonStyle.primary, emoji="📸", row=1)
    async def save_image(self, interaction: discord.Interaction, button: Button):
        # 1. Defer để bot có thời gian tải và up ảnh (tránh lỗi timeout)
        await interaction.response.defer()
        
        # Kiểm tra kênh Log có tồn tại không
        log_chan = interaction.guild.get_channel(CHANNEL_ID_IMAGE)
        if not log_chan:
            return await interaction.followup.send("❌ Lỗi: Không tìm thấy kênh LOG để lưu trữ ảnh (Kiểm tra lại CHANNEL_ID_IMAGE trong .env).", ephemeral=True)

        # 2. Tìm ảnh trong Thread hiện tại
        files_to_save = []
        async for msg in interaction.channel.history(limit=50):
            if msg.attachments:
                for att in msg.attachments:
                    if att.content_type and "image" in att.content_type:
                        # Chuẩn bị file để re-upload
                        try:
                            file = await att.to_file()
                            files_to_save.append(file)
                        except:
                            pass

        if not files_to_save:
            return await interaction.followup.send("❌ Không tìm thấy ảnh nào trong chủ đề này!", ephemeral=True)

        try:
            # 3. Gửi ảnh sang kênh LOG (Để lưu vĩnh viễn)
            saved_urls = []
            
            # Discord chỉ cho gửi tối đa 10 file 1 lần, ta chia nhỏ nếu cần, ở đây giả sử < 10 ảnh
            uploaded_msg = await log_chan.send(
                content=f"📸 **Lưu trữ ảnh đơn hàng #{self.order_data['order_id']}**", 
                files=files_to_save
            )
            
            # 4. Lấy URL mới từ kênh LOG
            for att in uploaded_msg.attachments:
                saved_urls.append(att.url)

            # 5. Lưu URL mới vào MongoDB
            order_id = self.order_data['order_id']
            col_images.update_one(
                {"order_id": order_id},
                {
                    "$set": {
                        "order_id": order_id,
                        "amount": self.order_data['amount'],
                        "details": self.order_data['details'],
                        "booster": self.order_data.get('booster', 'Không chọn'),
                        "images": saved_urls, # Lưu URL vĩnh viễn
                        "saved_at": discord.utils.utcnow()
                    }
                },
                upsert=True
            )
            self.has_saved_image = True
            await interaction.followup.send(f"✅ **Đã sao lưu {len(saved_urls)} Thành Công !!**", ephemeral=True)
            
        except Exception as e:
            print(f"Lỗi Lưu Ảnh: {e}")
            await interaction.followup.send(f"❌ Lỗi khi xử lý ảnh: {e}", ephemeral=True)

    @discord.ui.button(label="📝 Ghi Chú", style=discord.ButtonStyle.secondary, emoji="✏️", row=1)
    async def add_note(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(NoteModal(self.order_data))

    @discord.ui.button(label="✅ Báo Xong Đơn", style=discord.ButtonStyle.success, emoji="📢", row=2)
    async def report_done(self, interaction: discord.Interaction, button: Button):
        if not self.has_saved_image:
            check_db = col_images.find_one({"order_id": self.order_data['order_id']})
            # Kiểm tra kỹ hơn: DB có ảnh không và ảnh đó có phải ảnh sống không (tạm thời chỉ check có ảnh)
            if not check_db or "images" not in check_db or not check_db["images"]:
                view_warning = ConfirmNoImageView(self, interaction)
                return await interaction.response.send_message(
                    "⚠️ **CẢNH BÁO:** Bạn chưa **Lưu Ảnh**.\nNếu bạn báo xong đơn ngay, ảnh sẽ bị MẤT và khách không xem được.\nBạn có chắc chắn muốn tiếp tục?", 
                    view=view_warning, 
                    ephemeral=True
                )
            else:
                self.has_saved_image = True 

        await self.finish_order_logic(interaction)

    @discord.ui.button(label="🗑️ Xóa Chủ Đề", style=discord.ButtonStyle.secondary, emoji="🗑️", row=2)
    async def delete_thread(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⏳ Chủ đề sẽ xóa trong 5 giây...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

    async def finish_order_logic(self, interaction: discord.Interaction, force=False):
        db_data = col_images.find_one({"order_id": self.order_data['order_id']})
        note_content = db_data.get("note", "Không có") if db_data else "Không có"

        log_chan = interaction.guild.get_channel(CHANNEL_ID_LOG)
        if log_chan:
            embed = discord.Embed(title="**✧ 🎉ĐƠN HÀNG HOÀN THÀNH ✧**", color=0x3498db)
            desc_lines = []
            if self.order_data['booster'] != "Không chọn":
                desc_lines.append(f"**Người Cày :** {self.order_data['booster']}\n")
            desc_lines.append(f"**Mã Đơn :** `#{self.order_data['order_id']}`\n")
            desc_lines.append(f"**Giá Tiền :** **{self.order_data['amount']:,} VNĐ**")

            embed.description = "\n".join(desc_lines)
            embed.add_field(name="Nội Dung", value=f"```{self.order_data['details']}```", inline=False)
            embed.set_footer(text="Cảm ơn quý khách đã tin tưởng sử dụng dịch vụ!")
            embed.timestamp = discord.utils.utcnow()
            
            # Gửi thông báo hoàn thành
            await log_chan.send(embed=embed)

        try:
            disabled_view = AdminOrderView(self.order_data)
            disabled_view.children[0].label = "ĐÃ HOÀN THÀNH (Thread)"
            disabled_view.children[0].style = discord.ButtonStyle.secondary
            disabled_view.children[0].disabled = True
            await self.original_message.edit(view=disabled_view)
        except Exception as e:
            print(f"Không thể sửa tin nhắn gốc: {e}")

        msg = "✅ **Đã báo cáo đơn hàng hoàn thành!**"
        if force: msg += " (Lưu ý: Đơn này chưa được lưu ảnh)."
        
        if interaction.response.is_done():
            await interaction.followup.send(msg)
        else:
            await interaction.response.send_message(msg)
            
        button = [x for x in self.children if x.label == "✅ Báo Xong Đơn"][0]
        button.disabled = True
        await interaction.message.edit(view=self)

class AdminOrderView(View):
    def __init__(self, order_data):
        super().__init__(timeout=None)
        self.order_data = order_data

    @discord.ui.button(label="✅ XÁC NHẬN XONG ĐƠN", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_done(self, interaction: discord.Interaction, button: Button):
        thread_name = f"Done-Order-#{self.order_data['order_id']}"
        try:
            thread = await interaction.message.create_thread(name=thread_name, auto_archive_duration=1440)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Không thể tạo Thread: {e}", ephemeral=True)

        embed = discord.Embed(
            title=f"📁 XỬ LÝ ĐƠN HÀNG #{self.order_data['order_id']}", 
            description="Quy trình: **Gửi Ảnh** -> **Lưu Ảnh** -> (Tùy chọn: **Ghi Chú**) -> **Báo Xong Đơn**.", 
            color=0xe67e22
        )
        embed.add_field(name="Chi tiết đơn", value=f"```{self.order_data['details']}```")
        
        await thread.send(embed=embed, view=ThreadOrderView(self.order_data, interaction.message))
        await interaction.response.send_message(f"✅ Đã mở tiến trình xử lý tại: {thread.mention}", ephemeral=True)

async def process_successful_payment(user_id, amount_received, description):
    if user_id not in user_carts: return 

    cart = user_carts[user_id]
    products = load_products() 
    total_expected = 0
    raw_product_text = "" 
    detail_text = "" 

    for pid, qty in cart.items():
        if pid in products:
            p = products[pid]
            total_expected += p['price'] * qty
            detail_text += f"• {p['name']} x{qty} = {p['price']*qty:,}đ\n"
            raw_product_text += f"• {p['name']} x{qty}\n"

    booster_name = user_choices.get(user_id, {}).get('booster', "Không chọn")
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    if amount_received < total_expected: return

    print(f"🔄 Đang xử lý đơn #{order_id} cho User {user_id}...")

    ticket_jump_url = "https://discord.com" 
    if user_id in active_tickets:
        try:
            thread_id = active_tickets[user_id]
            thread = bot.get_channel(thread_id)
            if thread:
                ticket_jump_url = thread.jump_url 
                embed_cus = discord.Embed(title="✅ THANH TOÁN THÀNH CÔNG", color=0x2ecc71)
                desc_lines = [
                    "**Cảm ơn bạn! Hệ thống đã ghi nhận giao dịch.**\n",
                    f"**Mã Đơn Hàng :** `#{order_id}`\n",
                    f"**Số Tiền :** {amount_received:,} VNĐ\n"
                ]
                if booster_name != "Không chọn":
                    desc_lines.append(f"**Người Cày :** {booster_name}")
                
                embed_cus.description = "\n".join(desc_lines)
                embed_cus.add_field(name="**Nội Dung**", value=f"```{detail_text}```", inline=False)
                embed_cus.set_footer(text="Admin sẽ sớm liên hệ. Vui lòng KHÔNG đóng ticket này.")
                embed_cus.timestamp = discord.utils.utcnow()
                await thread.send(content=f"||<@{user_id}>|| **✧ 🎟️Phiếu Xác Nhận Đơn Hàng🎟️ ✧**", embed=embed_cus)
        except Exception as e:
            print(f"-> ⚠️ Lỗi gửi khách hàng: {e}")

    await asyncio.sleep(2) 

    try:
        order_data = {
            "order_id": order_id,
            "amount": amount_received,
            "details": raw_product_text, 
            "booster": booster_name
        }

        admin_chan = bot.get_channel(CHANNEL_ID_ADMIN)
        if admin_chan:
            try:
                user_obj = await bot.fetch_user(user_id)
                user_mention = user_obj.mention
            except:
                user_mention = f"User ID: {user_id}"

            embed = discord.Embed(title=f"🔔 **ĐƠN HÀNG MỚI #{order_id}**", color=0x2ecc71)
            desc_lines = []
            if booster_name != "Không chọn":
                desc_lines.append(f"**Người Cày :** **{booster_name}**")

            desc_lines.append(f"**Khách Hàng :** {user_mention}")
            desc_lines.append(f"**Mã Đơn :** `#{order_id}`")
            desc_lines.append(f"**Tổng Tiền :** **{amount_received:,} VNĐ**")
            desc_lines.append(f"**Ticket :** [Đi tới Ticket]({ticket_jump_url})")

            embed.description = "\n".join(desc_lines) + "\n**──────────────────────**"
            embed.add_field(name="**Nội Dung**", value=f"```{detail_text}```", inline=False)
            embed.timestamp = discord.utils.utcnow()

            await admin_chan.send(content="**▸▸▸🌸 TIỀN VỀ SẾP ƠI 💸 @here◂◂◂**", embed=embed, view=AdminOrderView(order_data))
    except Exception as e:
        print(f"-> ❌ Lỗi gửi ADMIN: {e}")

    if user_id in user_carts: del user_carts[user_id]
    if user_id in user_choices: del user_choices[user_id]

# ==========================================
# --- PHẦN 3: ADMIN PANEL (FIX _ID + CACHE) ---
# ==========================================

class ConfirmEditView(View):
    def __init__(self, product_id, new_data):
        super().__init__(timeout=60)
        self.product_id = product_id
        self.new_data = new_data

    @discord.ui.button(label="LƯU THAY ĐỔI", style=discord.ButtonStyle.green, emoji="💾")
    async def confirm(self, interaction: discord.Interaction, button: Button):
        try:
            col_products.update_one({"_id": self.product_id}, {"$set": self.new_data})
            load_products(force_update=True)
            await interaction.response.edit_message(content=f"✅ **Đã cập nhật thành công:** {self.new_data['name']}", view=None, embed=None)
        except Exception as e:
             await interaction.response.edit_message(content=f"❌ Lỗi MongoDB: {e}", view=None)

    @discord.ui.button(label="Hủy Bỏ", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="🚫 Đã hủy thao tác sửa.", view=None, embed=None)

class ConfirmDeleteView(View):
    def __init__(self, product_id, product_name):
        super().__init__(timeout=60)
        self.product_id = product_id
        self.product_name = product_name

    @discord.ui.button(label="XÁC NHẬN XÓA", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: Button):
        try:
            col_products.delete_one({"_id": self.product_id})
            load_products(force_update=True)
            await interaction.response.edit_message(content=f"✅ **Đã xóa vĩnh viễn:** {self.product_name}", view=None, embed=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"❌ Lỗi MongoDB: {e}", view=None)

    @discord.ui.button(label="Hủy Bỏ", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="🚫 Đã hủy thao tác xóa.", view=None, embed=None)

class EditProductFullModal(Modal):
    def __init__(self, product_id, current_data):
        super().__init__(title=f"Sửa: {current_data['name'][:20]}...")
        self.product_id = product_id
        self.current_data = current_data 
        
        self.name = TextInput(label="Tên Sản Phẩm", default=current_data['name'], required=True)
        self.price = TextInput(label="Giá (Số)", default=str(current_data['price']), required=True)
        self.content = TextInput(label="Nội Dung", default=current_data['content'], style=discord.TextStyle.paragraph, required=True)
        self.note = TextInput(label="Ghi Chú", default=current_data.get('note', 'Trống'), required=False)
        
        self.add_item(self.name)
        self.add_item(self.price)
        self.add_item(self.content)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_price = int(self.price.value)
        except ValueError:
            return await interaction.response.send_message("❌ Giá tiền phải là số!", ephemeral=True)

        new_data = {
            "name": self.name.value,
            "price": new_price,
            "content": self.content.value,
            "note": self.note.value
        }
        
        embed = discord.Embed(title="⚠️ XÁC NHẬN THAY ĐỔI", description="Chỉ những mục sau sẽ được cập nhật:", color=0xf1c40f)
        changes_count = 0 
        if new_data['name'] != self.current_data['name']:
            embed.add_field(name="🏷️ Tên Sản Phẩm", value=f"Cũ: {self.current_data['name']}\n**Mới: {new_data['name']}**", inline=False)
            changes_count += 1
        if new_data['price'] != self.current_data['price']:
            embed.add_field(name="💰 Giá Tiền", value=f"Cũ: {self.current_data['price']:,}\n**Mới: {new_data['price']:,}**", inline=False)
            changes_count += 1
        if new_data['content'] != self.current_data['content']:
            embed.add_field(name="📄 Nội Dung", value="*(Đã thay đổi nội dung mới)*", inline=False)
            changes_count += 1
        if new_data['note'] != self.current_data.get('note', 'Trống'):
            embed.add_field(name="📝 Ghi Chú", value=f"Cũ: {self.current_data.get('note')}\n**Mới: {new_data['note']}**", inline=False)
            changes_count += 1

        if changes_count == 0:
            return await interaction.response.send_message("💤 Bạn chưa thay đổi thông tin nào cả!", ephemeral=True)
        
        view = ConfirmEditView(self.product_id, new_data)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class AdminProductResultView(View):
    def __init__(self, mode, product_id, product_data):
        super().__init__(timeout=None)
        self.product_id = product_id
        self.product_data = product_data
        if mode == 'edit':
            btn = Button(label="🛠️ Sửa Sản Phẩm Này", style=discord.ButtonStyle.primary)
            btn.callback = self.edit_callback
            self.add_item(btn)
        elif mode == 'delete':
            btn = Button(label="🗑️ Xóa Sản Phẩm Này", style=discord.ButtonStyle.danger)
            btn.callback = self.delete_callback
            self.add_item(btn)

    async def edit_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EditProductFullModal(self.product_id, self.product_data))

    async def delete_callback(self, interaction: discord.Interaction):
        view = ConfirmDeleteView(self.product_id, self.product_data['name'])
        await interaction.response.send_message(
            f"⚠️ **CẢNH BÁO:** Bạn có chắc chắn muốn XÓA vĩnh viễn sản phẩm **{self.product_data['name']}** không?", 
            view=view, 
            ephemeral=True
        )

class AdminSearchModal(Modal):
    query = TextInput(label="Nhập tên sản phẩm cần tìm", placeholder="Nhập tên sản phẩm...")
    def __init__(self, mode, selected_game):
        title_str = "Tìm để SỬA" if mode == 'edit' else "Tìm để XÓA"
        super().__init__(title=f"{title_str}: {selected_game}")
        self.mode = mode
        self.selected_game = selected_game

    async def on_submit(self, interaction: discord.Interaction):
        search_str = self.query.value.strip().lower()
        products = load_products() 
        results = {}
        for pid, pdata in products.items():
            if pdata.get('game') == self.selected_game:
                if search_str in pdata['name'].lower():
                    results[pid] = pdata
        
        if not results:
            return await interaction.response.send_message(f"❌ Không tìm thấy sản phẩm nào tên chứa: **{search_str}**", ephemeral=True)

        await interaction.response.send_message(f"🔎 Tìm thấy **{len(results)}** sản phẩm:", ephemeral=True)
        count = 0
        for pid, pdata in results.items():
            if count >= 5: break
            embed = discord.Embed(title=f"✨ {pdata['name'].upper()}", color=0x3498db)
            info_text = (
                f"**-ID : ** `{pid}`\n" 
                f"**-Game : ** {pdata.get('game', 'Chưa phân loại')}\n"
                f"**-Giá : ** {pdata['price']:,} VNĐ\n"
                f"**-Mô tả :**\n{pdata['content']}\n" 
                f"**-Lưu ý : ** {pdata.get('note', 'Trống')}\n"
                f"──────────────────────────"
            )
            embed.description = info_text
            view = AdminProductResultView(self.mode, pid, pdata)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            count += 1

class AddProductModal(Modal):
    def __init__(self, selected_game):
        super().__init__(title=f"Thêm vào: {selected_game}")
        self.selected_game = selected_game
        self.pid = TextInput(label="Mã ID (Viết liền, KHÔNG DẤU)", placeholder="vd: map02", min_length=3)
        self.name = TextInput(label="Tên (name)", placeholder="vd: Long Tích Tuyết Sơn")
        self.content = TextInput(label="Nội dung (content)", style=discord.TextStyle.paragraph, placeholder="Mô tả...")
        self.price = TextInput(label="Giá (price)", placeholder="vd: 120000")
        self.note = TextInput(label="Ghi chú (note)", required=False, placeholder="Trống")
        self.add_item(self.pid)
        self.add_item(self.name)
        self.add_item(self.content)
        self.add_item(self.price)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price_int = int(self.price.value)
        except:
            return await interaction.response.send_message("❌ Giá tiền phải là số!", ephemeral=True)
        
        new_id = self.pid.value.strip()
        if col_products.find_one({"_id": new_id}):
            return await interaction.response.send_message(f"❌ ID **{new_id}** đã tồn tại!", ephemeral=True)

        new_doc = {
            "_id": new_id,
            "name": self.name.value,
            "content": self.content.value,
            "price": price_int,
            "note": self.note.value if self.note.value else "Trống",
            "game": self.selected_game 
        }
        try:
            col_products.insert_one(new_doc)
            load_products(force_update=True)
            await interaction.response.send_message(f"✅ Đã thêm: **{self.name.value}** (ID: {new_id})", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Lỗi MongoDB khi thêm: {e}", ephemeral=True)

class AdminActionView(View):
    def __init__(self, selected_game):
        super().__init__(timeout=None)
        self.selected_game = selected_game

    @discord.ui.button(label="Thêm Sản Phẩm", style=discord.ButtonStyle.success, emoji="➕")
    async def add_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddProductModal(self.selected_game))

    @discord.ui.button(label="Sửa Sản Phẩm", style=discord.ButtonStyle.primary, emoji="🛠️")
    async def edit_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AdminSearchModal(mode='edit', selected_game=self.selected_game))

    @discord.ui.button(label="Xóa Sản Phẩm", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def del_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AdminSearchModal(mode='delete', selected_game=self.selected_game))

class AdminGameSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=g, emoji="🎮") for g in LIST_GAMES]
        super().__init__(placeholder="👇 Chọn Game để quản lý kho hàng...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        game = self.values[0]
        embed = discord.Embed(title=f"🔧 QUẢN LÝ: {game.upper()}", description="Hãy chọn thao tác bên dưới.", color=0xf1c40f)
        await interaction.response.send_message(embed=embed, view=AdminActionView(game), ephemeral=True)

class AdminPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AdminGameSelect())

# ==========================================
# --- LOOP & RUN ---
# ==========================================

@tasks.loop(seconds=60) 
async def check_gmail_task():
    try:
        await bot.loop.run_in_executor(None, read_emails)
    except Exception as e:
        print(f"⚠️ Lỗi trong vòng lặp check mail: {e}")

@check_gmail_task.before_loop
async def before_check_gmail():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f"✅ Bot đã sẵn sàng: {bot.user}")
    load_products(force_update=True) # Load Cache ngay khi bot bật
    
    if not check_gmail_task.is_running():
        check_gmail_task.start()
        print("📧 Đã bật tính năng đọc Gmail (Chu kỳ: 60s).")

    try:
        manager_channel = bot.get_channel(CHANNEL_ID_MANAGEMENT)
        if manager_channel:
            embed_admin = discord.Embed(
                title="🛡️ HỆ THỐNG QUẢN TRỊ KHO HÀNG",
                description="Chọn Game bên dưới để **Thêm/Sửa/Xóa** sản phẩm.",
                color=0x2b2d31
            )
            embed_admin.set_footer(text="Admin Panel - Only for Staff")
            await manager_channel.send(embed=embed_admin, view=AdminPanelView())
            print("-> ✅ Đã gửi Panel Quản lý.")
    except Exception as e:
        print(f"-> ❌ Lỗi gửi kênh quản lý: {e}")

    try:
        shop_channel = bot.get_channel(CHANNEL_ID_SHOP)
        if shop_channel:
            embed_shop = discord.Embed(
                title="🏪 HỆ THỐNG CỬA HÀNG TỰ ĐỘNG",
                description="Chào mừng bạn đến với dịch vụ mua hàng tự động! Bấm nút bên dưới để bắt đầu.",
                color=0xe74c3c
            )
            embed_shop.set_image(url="https://media.discordapp.net/attachments/1452524630546972722/1452894382721335306/Screenshot_20251223_122209_Text_On_Photo.png")
            embed_shop.set_footer(text="Hệ thống hoạt động 24/7")
            
            await shop_channel.send(embed=embed_shop, view=OpenTicketView())
            print("-> ✅ Đã gửi bảng Ticket vào kênh Shop.")
    except Exception as e:
        print(f"-> ❌ Lỗi gửi kênh Shop: {e}")

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

from email.utils import parseaddr # <--- THÊM DÒNG NÀY Ở ĐẦU FILE CÙNG CÁC IMPORT KHÁC

# --- CẤU HÌNH MAIL CHUẨN ---
TRUSTED_EMAIL = "mailalert@acb.com.vn" 

def read_emails():
    print("--- 🔄 BẮT ĐẦU QUÉT MAIL ---")
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN)')
        email_ids = messages[0].split()

        if not email_ids:
            print("📭 Không có email MỚI.")
        else:
            print(f"📩 Tìm thấy {len(email_ids)} email chưa đọc. Đang kiểm tra bảo mật...")

        for e_id in email_ids:
            try:
                # 1. PEEK HEADER
                res, header_data = mail.fetch(e_id, '(BODY.PEEK[HEADER.FIELDS (FROM)])')
                raw_header = b""
                for response_part in header_data:
                    if isinstance(response_part, tuple):
                        raw_header += response_part[1]

                msg_header = email.message_from_bytes(raw_header)
                from_header = msg_header.get("From")
                
                # 2. GIẢI MÃ HEADER (Xử lý tiếng Việt/Ký tự lạ)
                decoded_header = str(from_header)
                try:
                    decoded_list = decode_header(from_header)
                    parts = []
                    for part, encoding in decoded_list:
                        if isinstance(part, bytes):
                            parts.append(part.decode(encoding or "utf-8"))
                        else:
                            parts.append(part)
                    decoded_header = "".join(parts)
                except:
                    pass

                # 3. 🛡️ BÓC TÁCH ĐỊA CHỈ THỰC (QUAN TRỌNG NHẤT) 🛡️
                # parseaddr sẽ tách: "ACB Bank <mailalert@acb.com.vn>" thành ("ACB Bank", "mailalert@acb.com.vn")
                real_name, real_email_address = parseaddr(decoded_header)
                
                # Chuyển về chữ thường để so sánh cho chắc
                real_email_address = real_email_address.lower().strip()
                
                print(f"   👀 Mail hiển thị: {decoded_header}")
                print(f"   🕵️ Mail GỐC thực tế: {real_email_address}")

                # 4. SO SÁNH TUYỆT ĐỐI (==)
                if real_email_address != TRUSTED_EMAIL:
                    print(f"   🚫 CẢNH BÁO GIẢ MẠO: Mail gốc là '{real_email_address}'. BỎ QUA!")
                    continue # Bỏ qua ngay lập tức
                
                # ============================================
                # NẾU VƯỢT QUA ĐƯỢC BƯỚC NÀY LÀ MAIL XỊN 100%
                # ============================================
                print(f"   ✅ MAIL CHÍNH CHỦ ACB! Đang xử lý...")

                res, msg_data = mail.fetch(e_id, "(RFC822)") 
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        subject = "No Subject"
                        if msg["Subject"]:
                            s_dec = decode_header(msg["Subject"])[0][0]
                            subject = s_dec.decode() if isinstance(s_dec, bytes) else s_dec

                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    payload = part.get_payload(decode=True)
                                    if payload: body = payload.decode()
                                    break
                        else:
                            payload = msg.get_payload(decode=True)
                            if payload: body = payload.decode()
                        
                        full_content = f"{subject} {clean_html(body)}"
                        
                        # --- TÌM TIỀN & MÃ ĐƠN ---
                        amount = 0
                        match_plus = re.search(r'\+\s*([\d,.]+)', full_content)
                        if match_plus:
                            raw = match_plus.group(1).split('.')[0].replace(',', '').replace('.', '')
                            if raw.isdigit(): amount = int(raw)
                        
                        if amount == 0:
                             match_money = re.findall(r'[\d,.]+', full_content)
                             for m in match_money:
                                raw = m.replace(',', '').replace('.', '')
                                if raw.isdigit() and len(raw) < 12 and int(raw) > 1000 and int(raw) > amount:
                                    amount = int(raw)

                        found_codes = re.findall(r'DH(\d+)', full_content, re.IGNORECASE)

                        if amount > 0 and found_codes:
                            for code_str in found_codes:
                                uid = int(code_str)
                                if uid in user_carts:
                                    print(f"      💰 => KHỚP LỆNH: DH{uid} - {amount:,} VNĐ.")
                                    asyncio.run_coroutine_threadsafe(
                                        process_successful_payment(uid, amount, full_content[:100]),
                                        bot.loop
                                    )
                                    break
                                else:
                                    print(f"      ⚠️ => Có mã DH{uid} nhưng không có đơn hàng chờ.")
                        else:
                            print("      ⚠️ => Không tìm thấy Tiền/Mã DH.")

            except Exception as e:
                print(f"❌ Lỗi mail ID {e_id}: {e}")

        mail.close()
        mail.logout()
        print("--- ✅ QUÉT XONG ---")
    except Exception as e:
        print(f"❌ LỖI GMAIL: {e}")

app = Flask('')
@app.route('/')
def home(): return "Bot đang hoạt động!"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive() 
bot.run(TOKEN)