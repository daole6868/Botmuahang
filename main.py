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
# Nếu chưa có biến này trong .env, hãy thêm vào hoặc để mặc định là 0
CHANNEL_ID_LOG = int(os.getenv("CHANNEL_ID_LOG", 0))

# --- CẤU HÌNH DANH SÁCH ---
LIST_GAMES = ["Genshin Impact", "Wuthering Waves", "Honkai: Star Rail", "Zenless Zone Zero"]
LIST_BOOSTERS = ["Không chọn (Mặc định)", "Live 2", "Live 3", "Live 5", "Live 6","Live 7","Live 8","Live 9","Live 10","Live 12","Live 13","Live 15","Live 19","Live 20","Live 21","Live 22","Live 23","Live 24"]

# --- DỮ LIỆU ---
user_carts = {}    
active_tickets = {} 
user_choices = {} 

def load_products():
    try:
        with open('products.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# --- 1. MODAL NHẬP SỐ LƯỢNG ---
class QtyModal(Modal):
    # 1. Thêm tham số product_price vào __init__
    def __init__(self, product_id, product_name, product_content, product_price):
        super().__init__(title=f"Mua {product_name}")
        self.product_id = product_id
        self.product_name = product_name
        self.product_content = product_content
        self.product_price = product_price # Lưu giá tiền lại
        
        self.qty_input = TextInput(
            label="Số lượng muốn mua", 
            placeholder="Nhập số lượng (Ví dụ: 1, 2, 5...)", 
            min_length=1, 
            max_length=3
        )
        self.add_item(self.qty_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Kiểm tra đầu vào
        if not self.qty_input.value.isdigit():
            return await interaction.response.send_message("❌ Vui lòng nhập số!", ephemeral=True)
        
        qty = int(self.qty_input.value)
        if qty <= 0:
             return await interaction.response.send_message("❌ Số lượng phải lớn hơn 0!", ephemeral=True)

        # Lưu vào giỏ hàng
        uid = interaction.user.id
        if uid not in user_carts: user_carts[uid] = {}
        user_carts[uid][self.product_id] = user_carts[uid].get(self.product_id, 0) + qty

        # --- TÍNH TOÁN TỔNG TIỀN ---
        total_price = self.product_price * qty

        # --- TẠO EMBED ĐẸP ---
        embed = discord.Embed(
            title="🛒 ĐÃ THÊM VÀO GIỎ HÀNG", 
            description=f"Sản phẩm **{self.product_name}** đã được thêm thành công.",
            color=0x2ecc71 # Màu xanh lá
        )
        
        # Tạo nội dung chi tiết dạng khối
        info_text = (
            f"**📦 Sản phẩm:** {self.product_name}\n"
            f"**📝 Mô tả:** \n{self.product_content}\n"
            f"**──────────────────────**\n"
            f"**💵 Đơn giá:** {self.product_price:,} VNĐ\n"
            f"**🔢 Số lượng:** {qty}\n"
            f"**💰 TẠM TÍNH:** **{total_price:,} VNĐ**" # Dòng này hiển thị tổng tiền
        )
        
        embed.add_field(name="Chi tiết đơn hàng", value=info_text, inline=False)
        embed.set_footer(text="Nhấn nút [Giỏ hàng & Thanh toán] để hoàn tất đơn hàng.")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

# --- 2. VIEW THANH TOÁN ---
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

# --- 3. MODAL TÌM KIẾM ---
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
        products = load_products()
        
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
        
        for pid, p in list(results.items())[:10]:
            embed = discord.Embed(title=f"✨ {p['name'].upper()}", color=0x3498db)
            
            info_text = (
                f"**-Game:** {p.get('game', 'Chưa phân loại')}\n"
                f"**-Giá :** {p['price']:,} VNĐ\n"
                f"**-Mô tả :**\n{p['content']}\n"
                f"**-Lưu ý :** {p.get('note', 'Không có')}\n"
                f"──────────────────────────"
            )
            embed.description = info_text
            embeds.append(embed)
            
            btn = Button(label=f"Chọn {p['name'][:20]}", style=discord.ButtonStyle.primary)
            def mk_cb(p_id, p_name, p_content, p_price):
                async def cb(i: discord.Interaction): await i.response.send_modal(QtyModal(p_id, p_name, p_content, p_price))
                return cb
            
            btn.callback = mk_cb(pid, p['name'], p['content'], p['price'])
            view.add_item(btn)

        await interaction.response.send_message(embeds=embeds, view=view, ephemeral=True)

# --- 4. DROPDOWN MENU ---
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

# --- 5. VIEW TRONG TICKET ---
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
        
        # Tạo danh sách sản phẩm
        for pid, qty in cart.items():
            if pid in products:
                p = products[pid]
                total += p['price'] * qty
                detail_list.append(f"• {p['name']} x{qty} = {p['price']*qty:,}đ")
        
        # Chuyển danh sách sản phẩm thành chuỗi text
        detail_text = "\n".join(detail_list)

        # Lấy tên người cày
        booster_name = user_choices.get(uid, {}).get('booster', "Không chọn")

        transfer_code = f"DH{uid}"
        qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{ACCOUNT_NO}-compact2.png?amount={total}&addInfo={transfer_code}"
        
        # --- TẠO EMBED HÓA ĐƠN THEO STYLE MỚI ---
        embed = discord.Embed(title="🧾 HÓA ĐƠN THANH TOÁN", color=0xf1c40f)
        
        # 1. Phần chi tiết sản phẩm (để trong Code Block cho đẹp)
        embed.add_field(name="Chi Tiết Đơn Hàng", value=f"```{detail_text}```", inline=False)
        
        # 2. Phần thông tin thanh toán (Gom vào Description cho thẳng hàng)
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
            
            # --- STYLE MỚI: DÙNG DESCRIPTION CHO NOTI ADMIN ---
            current_game = user_choices.get(interaction.user.id, {}).get('game', "Chưa chọn game")
            
            desc_lines = [
                f"**Khách Hàng :** {interaction.user.mention}\n",
                f"**Tại Ticket :** [Bấm vào đây để hỗ trợ]({interaction.channel.jump_url})\n",
                f"**Đang Quan Tâm :** {current_game}"
            ]
            
            embed.description = "\n".join(desc_lines)
            embed.set_footer(text=f"User ID: {interaction.user.id}")
            
            await consult_channel.send(content="@here ⚠️ **Yêu cầu hỗ trợ mới!**", embed=embed)
        else:
            print("❌ Chưa cấu hình CHANNEL_ID_CONSULT.")

    @discord.ui.button(label="✖️ Đóng Ticket", style=discord.ButtonStyle.danger, emoji="🔒", row=2)
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⚠️ Kênh sẽ bị xóa vĩnh viễn sau 5 giây...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# --- VIEW MỞ TICKET NGOÀI SHOP ---
class OpenTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 XEM CỬA HÀNG", style=discord.ButtonStyle.danger, custom_id="open_shop", emoji="🏪")
    async def open_shop(self, interaction: discord.Interaction, button: Button):
        uid = interaction.user.id
        
        if uid in active_tickets:
            old_thread = interaction.guild.get_thread(active_tickets[uid])
            if old_thread and not old_thread.archived:
                return await interaction.response.send_message(f"⚠️ Bạn đã có một chủ đề mua hàng: {old_thread.mention}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        thread = await interaction.channel.create_thread(
            name=f"🛒-{interaction.user.name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=1440
        )
        
        active_tickets[uid] = thread.id
        await thread.add_user(interaction.user)

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
        
        embed.add_field(name="⚠️ Lưu Ý", value=(
            "1️⃣ Không Spam đơn hàng.\n"
            "2️⃣ Chuyển khoản đúng **[Nội Dung]** và **[Số Tiền]**.\n"
            "3️⃣ Nếu không thấy mã QR, hãy nhấn lại nút **[Giỏ hàng & Thanh toán]**."
        ), inline=False)
        
        embed.set_footer(text="Cửa hàng Gachaz chúc bạn một ngày tốt lành!")

        await thread.send(embed=embed, view=TicketShopView())
        await interaction.followup.send(f"✅ Đã tạo chủ đề mua hàng riêng cho bạn: {thread.mention}", ephemeral=True)

# --- VIEW QUẢN LÝ ĐƠN HÀNG CHO ADMIN (ĐƯỢC CHUYỂN LÊN ĐÂY ĐỂ TRÁNH LỖI) ---
class AdminOrderView(View):
    def __init__(self, order_data):
        super().__init__(timeout=None)
        self.order_data = order_data

    @discord.ui.button(label="✅ XÁC NHẬN XONG ĐƠN", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_done(self, interaction: discord.Interaction, button: Button):
        # 1. Gửi log vào kênh Log
        log_chan = bot.get_channel(CHANNEL_ID_LOG)
        if log_chan:
            embed = discord.Embed(title="**✧ 🎉ĐƠN HÀNG HOÀN THÀNH ✧**", color=0x3498db)
            
            # --- SỬA ĐỔI: DÙNG DESCRIPTION ĐỂ HIỆN CÙNG 1 DÒNG ---
            desc_lines = []

            # 1. NGƯỜI CÀY (Nếu có)
            if self.order_data['booster'] != "Không chọn":
                desc_lines.append(f"**Người Cày :** {self.order_data['booster']}\n")

            # 2. MÃ ĐƠN
            desc_lines.append(f"**Mã Đơn :** `#{self.order_data['order_id']}`\n")

            # 3. GIÁ TIỀN
            desc_lines.append(f"**Giá Tiền :** **{self.order_data['amount']:,} VNĐ**")
            
            # --> Gán danh sách trên vào description (ngăn cách bằng xuống dòng)
            embed.description = "\n".join(desc_lines)

            # 4. NỘI DUNG (Giữ nguyên add_field để chứa khung Code)
            embed.add_field(name="Nội Dung", value=f"```{self.order_data['details']}```", inline=False)
            
            embed.set_footer(text="Cảm ơn quý khách đã tin tưởng sử dụng dịch vụ!")
            embed.timestamp = discord.utils.utcnow()
            
            await log_chan.send(embed=embed)
        
        # 2. Tắt nút (Disable) để không bấm lại được
        button.label = "ĐÃ HOÀN THÀNH"
        button.style = discord.ButtonStyle.secondary
        button.disabled = True
        
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("✅ **Đã báo xong đơn và gửi log thành công!**", ephemeral=True)

# --- LOGIC XỬ LÝ THANH TOÁN ---
# --- LOGIC XỬ LÝ THANH TOÁN (ĐÃ TỐI ƯU ĐỘ TRỄ) ---
async def process_successful_payment(user_id, amount_received, description):
    # 1. Kiểm tra giỏ hàng
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
    
    # Tạo mã đơn hàng random
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    if amount_received < total_expected: return

    print(f"🔄 Đang xử lý đơn #{order_id} cho User {user_id}...")

    # ======================================================
    # BƯỚC 1: GỬI THÔNG BÁO CHO KHÁCH HÀNG (TRONG TICKET)
    # ======================================================
    ticket_jump_url = "https://discord.com" 
    
    if user_id in active_tickets:
        try:
            thread_id = active_tickets[user_id]
            thread = bot.get_channel(thread_id)
            if thread:
                ticket_jump_url = thread.jump_url 
                
                embed_cus = discord.Embed(title="✅ THANH TOÁN THÀNH CÔNG", color=0x2ecc71)
                
                # --- PHẦN SỬA ĐỔI QUAN TRỌNG ---
                # Gom tất cả các thông tin muốn nằm cùng dòng vào biến description
                # Sử dụng \n để xuống dòng giữa các mục
                
                desc_lines = [
                    "**Cảm ơn bạn! Hệ thống đã ghi nhận giao dịch.**\n",
                    f"**Mã Đơn Hàng :** `#{order_id}`\n",
                    f"**Số Tiền :** {amount_received:,} VNĐ\n"
                ]
                
                # Nếu có người cày thì thêm vào list này
                if booster_name != "Không chọn":
                    desc_lines.append(f"**Người Cày :** {booster_name}")
                
                # Gán list trên vào description của embed
                embed_cus.description = "\n".join(desc_lines)
                
                # --- PHẦN NỘI DUNG ---
                # Riêng phần Nội Dung giữ nguyên add_field để chứa khung Code đẹp
                embed_cus.add_field(name="**Nội Dung**", value=f"```{detail_text}```", inline=False)
                
                embed_cus.set_footer(text="Admin sẽ sớm liên hệ. Vui lòng KHÔNG đóng ticket này.")
                embed_cus.timestamp = discord.utils.utcnow()
                
                await thread.send(content=f"||<@{user_id}>|| **✧ 🎟️Phiếu Xác Nhận Đơn Hàng🎟️ ✧**", embed=embed_cus)
                print(f"-> ✅ Đã gửi thông báo cho Khách (Ticket).")
        except Exception as e:
            print(f"-> ⚠️ Lỗi gửi khách hàng: {e} (Vẫn tiếp tục xử lý...)")

    # --- QUAN TRỌNG: NGỦ 2 GIÂY ĐỂ TRÁNH LAG/RATE LIMIT ---
    await asyncio.sleep(2) 

    # ======================================================
    # BƯỚC 2: CHUẨN BỊ DỮ LIỆU & GỬI CHO ADMIN
    # ======================================================
    try:
        # Chuẩn bị dữ liệu cho nút bấm
        order_data = {
            "order_id": order_id,
            "amount": amount_received,
            "details": raw_product_text, 
            "booster": booster_name
        }

        admin_chan = bot.get_channel(CHANNEL_ID_ADMIN)
        if admin_chan:
            # Lấy thông tin user
            try:
                user_obj = await bot.fetch_user(user_id)
                user_mention = user_obj.mention
            except:
                user_mention = f"User ID: {user_id}"

            embed = discord.Embed(title=f"🔔 **ĐƠN HÀNG MỚI #{order_id}**", color=0x2ecc71)
            
            # --- TẠO DANH SÁCH CÁC DÒNG HIỂN THỊ CÙNG HÀNG ---
            desc_lines = []

            # 1. NGƯỜI CÀY
            if booster_name != "Không chọn":
                # Lưu ý: Mình bỏ \n ở cuối vì tí nữa join sẽ tự thêm
                desc_lines.append(f"**Người Cày :** **{booster_name}**")

            # 2. KHÁCH HÀNG
            desc_lines.append(f"**Khách Hàng :** {user_mention}")

            # 3. MÃ ĐƠN
            desc_lines.append(f"**Mã Đơn :** `#{order_id}`")

            # 4. TỔNG TIỀN
            desc_lines.append(f"**Tổng Tiền :** **{amount_received:,} VNĐ**")

            # 5. TICKET
            desc_lines.append(f"**Ticket :** [Đi tới Ticket]({ticket_jump_url})")

            # --- SỬA LỖI Ở ĐÂY ---
            # Dùng dấu + để nối chuỗi và dòng kẻ
            embed.description = "\n".join(desc_lines) + "\n**──────────────────────**"

            # --- PHẦN NỘI DUNG ---
            embed.add_field(name="**Nội Dung**", value=f"```{detail_text}```", inline=False)
            
            # Thêm thời gian gửi
            embed.timestamp = discord.utils.utcnow()

            # Gửi kèm View (Nút bấm)
            await admin_chan.send(content="**▸▸▸🌸 TIỀN VỀ SẾP ƠI 💸 @here◂◂◂**", embed=embed, view=AdminOrderView(order_data))
            print(f"-> ✅ Đã gửi thông báo cho Admin.")
        else:
            print("-> ❌ Không tìm thấy kênh Admin (Check lại CHANNEL_ID_ADMIN).")

    except Exception as e:
        print(f"-> ❌ LỖI NGHIÊM TRỌNG KHI GỬI ADMIN: {e}")

    # ======================================================
    # BƯỚC 3: DỌN DẸP DỮ LIỆU
    # ======================================================
    if user_id in user_carts: del user_carts[user_id]
    if user_id in user_choices: del user_choices[user_id]
    print(f"-> 🧹 Đã dọn dẹp giỏ hàng user {user_id}.")

    # ======================================================
    # BƯỚC 3: DỌN DẸP DỮ LIỆU
    # ======================================================
    if user_id in user_carts: del user_carts[user_id]
    if user_id in user_choices: del user_choices[user_id]
    print(f"-> 🧹 Đã dọn dẹp giỏ hàng user {user_id}.")

# --- ON READY & GMAIL ---
@bot.event
async def on_ready():
    print(f"✅ Bot đã sẵn sàng: {bot.user}")
    
    if not check_gmail_task.is_running():
        check_gmail_task.start()
        print("📧 Đã bật tính năng đọc Gmail tự động (60s/lần).")

    shop_channel = bot.get_channel(CHANNEL_ID_SHOP)
    if shop_channel:
        embed = discord.Embed(
            title="🏪 HỆ THỐNG CỬA HÀNG TỰ ĐỘNG",
            description="Chào mừng bạn đến với dịch vụ mua hàng tự động của chúng tôi! Vui lòng bấm vào nút bên dưới để mở kênh mua hàng riêng biệt.",
            color=0xe74c3c
        )
        embed.set_image(url="https://media.discordapp.net/attachments/1452524630546972722/1452894382721335306/Screenshot_20251223_122209_Text_On_Photo.png?ex=694b78d6&is=694a2756&hm=86dfa27a7fe41d7a96aa4bbc860a9014827e4b9d984ad48bf277821804ca5cc1&=&format=webp&quality=lossless&width=1295&height=386") 
        embed.set_footer(text="Hệ thống hoạt động 24/7")
        await shop_channel.send(embed=embed, view=OpenTicketView())

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

def read_emails():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN)')
        email_ids = messages[0].split()

        if email_ids:
            print(f"📩 Đang xử lý {len(email_ids)} email mới...")

        for e_id in email_ids:
            try:
                res, msg_data = mail.fetch(e_id, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        subject = decode_header(msg["Subject"])[0][0]
                        if isinstance(subject, bytes): subject = subject.decode()
                        
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body = part.get_payload(decode=True).decode()
                                    break
                        else:
                            body = msg.get_payload(decode=True).decode()
                        
                        full_content = f"{subject} {clean_html(body)}"
                        
                        amount = 0
                        match_plus = re.search(r'\+\s*([\d,.]+)', full_content)
                        if match_plus:
                            raw_money = match_plus.group(1).split('.')[0]
                            clean_num = raw_money.replace(',', '')
                            if clean_num.isdigit(): amount = int(clean_num)
                        
                        if amount == 0:
                             match_money = re.findall(r'[\d,.]+', full_content)
                             for m in match_money:
                                clean_num = m.replace(',', '').replace('.', '')
                                if clean_num.isdigit() and len(clean_num) < 12: 
                                    val = int(clean_num)
                                    if val > 1000 and val > amount: amount = val

                        found_codes = re.findall(r'DH(\d+)', full_content, re.IGNORECASE)

                        if amount > 0 and found_codes:
                            for code_str in found_codes:
                                uid = int(code_str)
                                if uid in user_carts:
                                    print(f"-> ✅ KHỚP LỆNH: DH{uid} - {amount} VNĐ")
                                    asyncio.run_coroutine_threadsafe(
                                        process_successful_payment(uid, amount, full_content[:100]),
                                        bot.loop
                                    )
                                    break 
            except Exception as e:
                print(f"Lỗi đọc mail ID {e_id}: {e}")

        mail.close()
        mail.logout()
    except Exception as e:
        print(f"Lỗi kết nối Gmail: {e}")

@tasks.loop(seconds=60)
async def check_gmail_task():
    await bot.loop.run_in_executor(None, read_emails)

app = Flask('')
@app.route('/')
def home(): return "Bot đang hoạt động!"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive() 
bot.run(TOKEN)