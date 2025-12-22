import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import json
import urllib.parse
import asyncio

# --- DỮ LIỆU ---
user_carts = {}    
active_tickets = {} 

def load_products():
    try:
        with open('products.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# --- 1. MODAL NHẬP SỐ LƯỢNG ---
class QtyModal(Modal):
    def __init__(self, product_id, product_name):
        super().__init__(title=f"Mua {product_name}")
        self.product_id = product_id
        self.qty_input = TextInput(label="Số lượng muốn mua", placeholder="Nhập số...", min_length=1, max_length=3)
        self.add_item(self.qty_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.qty_input.value.isdigit():
            return await interaction.response.send_message("❌ Vui lòng nhập số!", ephemeral=True)
        qty = int(self.qty_input.value)
        uid = interaction.user.id
        if uid not in user_carts: user_carts[uid] = {}
        user_carts[uid][self.product_id] = user_carts[uid].get(self.product_id, 0) + qty
        await interaction.response.send_message(f"✅ **Đã thêm {qty} sản phẩm vào giỏ hàng! Bạn có thể vuốt lên trên để [Thanh Toán] hoặc [Tìm Kiếm] để xem thêm sản phẩm khác.**", ephemeral=True)

# --- 2. VIEW THANH TOÁN (SAU KHI HIỆN QR) ---
class PostPaymentView(View):
    def __init__(self, total, detail, channel_jump_url):
        super().__init__(timeout=None)
        self.total = total
        self.detail = detail
        self.channel_jump_url = channel_jump_url

    @discord.ui.button(label="✅ ĐÃ THANH TOÁN", style=discord.ButtonStyle.success, emoji="💳")
    async def paid(self, interaction: discord.Interaction, button: Button):
        admin_chan = bot.get_channel(CHANNEL_ID_ADMIN)
        if admin_chan:
            embed = discord.Embed(title="🔔 **ĐƠN HÀNG MỚI**", color=0x2ecc71)
            embed.add_field(name="Khách hàng", value=interaction.user.mention, inline=True)
            embed.add_field(name="Ticket", value=f"[Đi tới Ticket]({self.channel_jump_url})", inline=True)
            embed.add_field(name="Chi tiết", value=f"```{self.detail}```", inline=False)
            embed.add_field(name="Tổng tiền", value=f"**{self.total:,} VNĐ**", inline=False)
            await admin_chan.send(content="@here", embed=embed)
        
        # XÓA GIỎ HÀNG SAU KHI THANH TOÁN THÀNH CÔNG
        uid = interaction.user.id
        if uid in user_carts: del user_carts[uid]
        
        # VÔ HIỆU HÓA TẤT CẢ CÁC NÚT TRONG VIEW NÀY
        for item in self.children:
            item.disabled = True
            
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🚀 **Đã báo cho Admin! Giỏ hàng đã được dọn sạch. Vui lòng chờ phản hồi. Lưu Ý không được đóng Ticket cho tới khi Admin nhận được đơn hàng từ bạn.**", ephemeral=True)

    @discord.ui.button(label="🗑️ XÓA GIỎ HÀNG", style=discord.ButtonStyle.danger, emoji="🧹")
    async def clear(self, interaction: discord.Interaction, button: Button):
        uid = interaction.user.id
        if uid in user_carts: 
            del user_carts[uid]
        
        # VÔ HIỆU HÓA TẤT CẢ CÁC NÚT KHI NHẤN XÓA
        for item in self.children:
            item.disabled = True
            
        # Cập nhật lại tin nhắn để các nút hiện màu xám (disabled)
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🧹 **Đã xóa sạch giỏ hàng! Bạn có thể lên đơn hàng mới**.", ephemeral=True)

# --- 3. MODAL TÌM KIẾM (CẬP NHẬT ĐIỀU KIỆN 3 KÝ TỰ) ---
class SearchModal(Modal, title="Tìm kiếm sản phẩm"):
    query = TextInput(
        label="Nhập tên sản phẩm", 
        placeholder="Nhập tối thiểu 3 ký tự để tìm kiếm...", 
        min_length=1, # Vẫn để 1 để tránh lỗi trống, nhưng logic code sẽ xử lý tiếp
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        search_value = self.query.value.strip() # Xóa khoảng trắng thừa

        # KIỂM TRA ĐỘ DÀI: Nếu dưới 3 ký tự thì báo lỗi ngay
        if len(search_value) < 3:
            return await interaction.response.send_message(
                "⚠️ Vui lòng nhập **tối thiểu 3 ký tự** để thực hiện tìm kiếm!", 
                ephemeral=True
            )

        products = load_products()
        # Tìm kiếm các sản phẩm có tên chứa cụm từ (không phân biệt hoa thường)
        results = {k: v for k, v in products.items() if search_value.lower() in v['name'].lower()}
        
        if not results: 
            return await interaction.response.send_message(
                f"❌ Không tìm thấy sản phẩm nào khớp với từ khóa: `{search_value}`", 
                ephemeral=True
            )
        
        embeds = []
        view = View()
        
        # Chỉ lấy tối đa 10 kết quả đầu tiên (giới hạn Discord)
        for pid, p in list(results.items())[:10]:
            embed = discord.Embed(
                title=f"✨ {p['name'].upper()}", 
                color=0x3498db
            )
            
            info_text = (
                f"**-Giá :** {p['price']:,} VNĐ\n"
                f"**-Mô tả :**\n{p['content']}\n"
                f"**-Lưu ý :** {p.get('note', 'Không có')}\n"
                f"──────────────────────────"
            )
            
            embed.description = info_text
            embeds.append(embed)
            
            btn = Button(label=f"Chọn {p['name']}", style=discord.ButtonStyle.primary)
            
            def mk_cb(p_id, p_name):
                async def cb(i: discord.Interaction): 
                    await i.response.send_modal(QtyModal(p_id, p_name))
                return cb
            
            btn.callback = mk_cb(pid, p['name'])
            view.add_item(btn)

        await interaction.response.send_message(embeds=embeds, view=view, ephemeral=True)

# --- 4. VIEW TRONG TICKET ---
class TicketShopView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔍 Tìm kiếm sản phẩm", style=discord.ButtonStyle.primary, emoji="🔎")
    async def search(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SearchModal())

    @discord.ui.button(label="💳 Giỏ hàng & Thanh toán", style=discord.ButtonStyle.success, emoji="🛒")
    async def checkout(self, interaction: discord.Interaction, button: Button):
        # Bước 1: Thông báo cho Discord là Bot đang xử lý (Tránh lỗi 3 giây)
        await interaction.response.defer(ephemeral=True)
        
        uid = interaction.user.id
        cart = user_carts.get(uid, {})
        if not cart: 
            return await interaction.followup.send("🛒 Giỏ hàng trống!", ephemeral=True)
        
        products = load_products()
        total, detail = 0, ""
        for pid, qty in cart.items():
            if pid in products:
                p = products[pid]
                total += p['price'] * qty
                detail += f"• {p['name']} x{qty} = {p['price']*qty:,}đ\n"

        # TẠO NỘI DUNG CHUYỂN KHOẢN
        transfer_code = f"DH{uid}"
        qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{ACCOUNT_NO}-compact2.png?amount={total}&addInfo={transfer_code}"
        
        # --- CẬP NHẬT EMBED HIỂN THỊ NỘI DUNG CK ---
        embed = discord.Embed(
            title="🧾 HÓA ĐƠN THANH TOÁN", 
            description=(
                f"{detail}\n"
                f"**-TỔNG: {total:,} VNĐ**\n"
                f"**-NỘI DUNG CK: `{transfer_code}`**" # Dòng hiển thị nội dung chuyển khoản
            ), 
            color=0xf1c40f
        )
        embed.set_image(url=qr_url)
        embed.set_footer(text="⚠️ Lưu ý: Quét mã QR để chuyển đúng [Nội Dung] và [Số Tiền]. Sau khi chuyển khoản hãy nhấn nút [Đã Thanh Toán] để báo cho Admin.")
        
        # Bước 2: Gửi hóa đơn (dùng followup thay cho send_message vì đã defer)
        await interaction.followup.send(
            embed=embed, 
            view=PostPaymentView(total, detail, interaction.channel.jump_url), 
            ephemeral=True
        )

    # NÚT ĐÓNG TICKET MÀU ĐỎ (DANGER)
    @discord.ui.button(label="✖️ Đóng Ticket", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⚠️ Kênh sẽ bị xóa vĩnh viễn sau 5 giây...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# --- 5. VIEW MỞ TICKET NGOÀI SHOP ---
class OpenTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 XEM CỬA HÀNG", style=discord.ButtonStyle.danger, custom_id="open_shop", emoji="🏪")
    async def open_shop(self, interaction: discord.Interaction, button: Button):
        uid = interaction.user.id
        
        # Kiểm tra nếu người dùng đã có Thread đang mở
        if uid in active_tickets:
            # Lấy thread từ bộ nhớ cache hoặc API
            old_thread = interaction.guild.get_thread(active_tickets[uid])
            if old_thread and not old_thread.archived:
                return await interaction.response.send_message(f"⚠️ Bạn đã có một chủ đề mua hàng: {old_thread.mention}", ephemeral=True)

        # Tránh lỗi 3 giây của Discord (Tạo thread có thể mất thời gian)
        await interaction.response.defer(ephemeral=True)

        # TẠO THREAD (CHỦ ĐỀ) TRONG KÊNH HIỆN TẠI
        # Lưu ý: Thread riêng tư (private_thread) yêu cầu Server đã Boost Level 2.
        # Nếu Server chưa boost, bạn hãy đổi sang: type=discord.ChannelType.public_thread
        thread = await interaction.channel.create_thread(
            name=f"🛒-{interaction.user.name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=1440 # Tự đóng sau 24h
        )
        
        # Lưu ID thread vào danh sách hoạt động
        active_tickets[uid] = thread.id
        # Thêm người dùng vào thread (bắt buộc với private thread)
        await thread.add_user(interaction.user)

        embed = discord.Embed(
            title="✨ CỬA HÀNG ABC KÍNH CHÀO QUÝ KHÁCH ✨", 
            description=f"Xin Chào {interaction.user.mention}, bạn có thể tìm kiếm sản phẩm và quản lý giỏ hàng ngay tại chủ đề này.",
            color=0xf1c40f
        )
        
        embed.add_field(name="📋 Hướng dẫn", value=(
            "1️⃣ Bấm **[Tìm kiếm]** để tìm và xem sản phẩm.\n"
            "2️⃣ Nhấn **[Chọn Sản Phẩm]** sau đó nhập **[Số Lượng]** muốn mua.\n"
            "3️⃣ Bấm **[Giỏ hàng & Thanh toán]** bên dưới để nhận mã QR và tiến hành thanh toán."
        ), inline=False)
        
        embed.add_field(name="⚠️ Lưu Ý", value=(
            "1️⃣ Không spam đơn hàng nếu bạn không muốn bị kick.\n"
            "2️⃣ Sau khi thanh toán nhớ bấm nút **[Đã Thanh Toán]** để báo cho Admin.\n"
            "3️⃣ Nếu bạn không thấy nút **[Đã Thanh Toán]** hãy nhấn lại nút **[Giỏ hàng & Thanh toán]** bên dưới."
        ), inline=False)
        
        embed.set_footer(text="Cửa hàng ABC chúc bạn một ngày tốt lành!")

        # Gửi tin nhắn vào Thread mới tạo
        await thread.send(embed=embed, view=TicketShopView())
        
        # Phản hồi cho người dùng ở tin nhắn ẩn
        await interaction.followup.send(f"✅ Đã tạo chủ đề mua hàng riêng cho bạn: {thread.mention}", ephemeral=True)

# --- KHỞI CHẠY ---
@bot.event
async def on_ready():
    print(f"✅ Bot đã sẵn sàng: {bot.user}")
    shop_channel = bot.get_channel(CHANNEL_ID_SHOP)
    if shop_channel:
        await shop_channel.purge(limit=5) # Dọn tin nhắn cũ
        embed = discord.Embed(
            title="🏪 HỆ THỐNG CỬA HÀNG TỰ ĐỘNG",
            description="Chào mừng bạn! Vui lòng bấm vào nút bên dưới để mở kênh mua hàng riêng biệt.",
            color=0xe74c3c
        )
        embed.set_footer(text="Hệ thống hoạt động 24/7")
        await shop_channel.send(embed=embed, view=OpenTicketView())

from flask import Flask
from threading import Thread

# Tạo Web Server nhỏ
app = Flask('')

@app.route('/')
def home():
    return "Bot đang hoạt động!"

def run():
    # Chạy server ở cổng 8080 (cổng mặc định của nhiều hosting)
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- PHẦN CHẠY BOT ---
keep_alive() # Gọi hàm chạy Web Server
bot.run(TOKEN)