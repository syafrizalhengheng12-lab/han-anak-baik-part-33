"""
AUTO MANCING v1.3 — PYROFORK + CLAUDE AI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEW: Auto jawab captcha pake Claude AI.

Flow:
1. Kirim /mancing → tunggu sesi selesai
2. Kalau ada captcha → kirim ke Claude AI → auto klik jawaban
3. Hasil mancing → scan ikan rare → /favorite → /jual semua
4. Ulangi

ENV VARIABLES (Railway):
  API_ID           = API ID telegram lo
  API_HASH         = API HASH telegram lo
  SESSION_STRING   = Session string lo
  FISHING_BOT      = username bot mancing (default: fish_it_vip_bot)
  MANCING_INTERVAL = jeda antar sesi dalam detik (default: 310)
  ANTHROPIC_API_KEY = API key Claude
"""

import os
import re
import time
import asyncio
import aiohttp
import json
from pyrogram import Client, filters, idle
from pyrogram.errors import FloodWait
from pyrogram.handlers import MessageHandler

# ━━━ CONFIG ━━━
API_ID            = int(os.environ.get("API_ID", "0"))
API_HASH          = os.environ.get("API_HASH", "")
SESSION_STRING    = os.environ.get("SESSION_STRING", "")
FISHING_BOT       = os.environ.get("FISHING_BOT", "fish_it_vip_bot").lstrip("@")
MANCING_INTERVAL  = int(os.environ.get("MANCING_INTERVAL", "310"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Emoji ikan rare yang mau di-favorite
RARE_EMOJIS = ["🟤", "✨", "☀️", "🌟"]

# Keyword captcha
CAPTCHA_KEYWORDS = ["verifikasi", "captcha", "robot", "buktikan", "pilih", "berapa", "hitung"]

# Keyword konfirmasi jual
CONFIRM_KEYWORDS = ["ya, jual semua", "ya,jual semua", "jual semua", "✅"]
CANCEL_KEYWORDS  = ["batal", "cancel", "❌"]

# ━━━ STATE ━━━
state = {
    "total_catch": 0,
    "rare_inventory_nums": [],
    "waiting_result": False,
    "scanning_pages": False,
    "waiting_sell": False,
}

# ━━━ UTILS ━━━
def log(step, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{step}] {msg}", flush=True)


async def ask_claude(question):
    """
    Kirim soal captcha ke Claude AI, minta jawaban nama tombol yang harus diklik.
    """
    if not ANTHROPIC_API_KEY:
        log("AI", "❌ ANTHROPIC_API_KEY tidak ada!")
        return None

    prompt = f"""Kamu membantu menjawab captcha dari bot Telegram game mancing.

Soal captcha:
{question}

Jawab HANYA dengan teks tombol yang harus diklik, tidak perlu penjelasan apapun.
Contoh jawaban: "🐟 Ikan Kecil" atau "12" atau "7"
"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": prompt}]
                }
            ) as resp:
                data = await resp.json()
                answer = data["content"][0]["text"].strip()
                log("AI", f"🤖 Claude jawab: {answer}")
                return answer
    except Exception as e:
        log("AI", f"❌ Error Claude API: {e}")
        return None


def is_captcha(text, message):
    """Deteksi apakah pesan ini captcha."""
    text_lower = text.lower()
    has_keyword = any(kw in text_lower for kw in CAPTCHA_KEYWORDS)
    has_buttons = (message.reply_markup and
                   hasattr(message.reply_markup, 'inline_keyboard') and
                   len(message.reply_markup.inline_keyboard) > 0)
    return has_keyword and has_buttons


async def solve_captcha(client, message):
    """Jawab captcha otomatis pake Claude AI."""
    text = message.text or message.caption or ""
    log("CAPTCHA", f"🔐 Captcha detected: {text[:80]}...")

    # Kumpulin semua opsi tombol
    buttons = []
    if message.reply_markup and hasattr(message.reply_markup, 'inline_keyboard'):
        for row in message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.text:
                    buttons.append(btn.text)

    # Bikin soal lengkap untuk Claude
    question = f"Pertanyaan: {text}\nPilihan tombol: {', '.join(buttons)}"
    answer = await ask_claude(question)

    if not answer:
        log("CAPTCHA", "❌ Claude tidak bisa jawab captcha")
        return False

    # Cari tombol yang cocok dengan jawaban Claude
    for r, row in enumerate(message.reply_markup.inline_keyboard):
        for c, btn in enumerate(row):
            btn_text = (btn.text or "").strip()
            if answer.strip().lower() in btn_text.lower() or btn_text.lower() in answer.strip().lower():
                try:
                    await message.click(r, c)
                    log("CAPTCHA", f"✅ Klik tombol: '{btn_text}'")
                    return True
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    try:
                        await message.click(r, c)
                        log("CAPTCHA", f"✅ Klik tombol: '{btn_text}' (retry)")
                        return True
                    except:
                        pass
                except Exception as e:
                    log("CAPTCHA", f"❌ Gagal klik: {e}")

    log("CAPTCHA", f"⚠️ Tombol '{answer}' tidak ditemukan")
    return False


def parse_rare_from_result(text):
    """Parse hasil mancing, cari nomor urut ikan rare."""
    rare_numbers = []
    total = 0

    total_match = re.search(r'Ditangkap\s*\((\d+)\s*ikan\)', text)
    if total_match:
        total = int(total_match.group(1))

    for line in text.split('\n'):
        line = line.strip()
        match = re.match(r'^(\d+)\.\s+(.+)', line)
        if not match:
            continue
        number = int(match.group(1))
        content = match.group(2)
        for emoji in RARE_EMOJIS:
            if emoji in content:
                rare_numbers.append(number)
                log("PARSE", f"🎯 Rare #{number}: {content[:40]}")
                break

    return total, rare_numbers


def calc_inventory_numbers(total_catch, rare_result_nums):
    """Hitung nomor inventory: inv = (total - urutan) + 1"""
    return [(total_catch - n) + 1 for n in rare_result_nums]


async def click_next(msg):
    """Klik tombol Next di inventory."""
    if not msg.reply_markup or not hasattr(msg.reply_markup, 'inline_keyboard'):
        return False
    for r, row in enumerate(msg.reply_markup.inline_keyboard):
        for c, btn in enumerate(row):
            txt = (btn.text or "").lower()
            if "next" in txt or "➡" in txt or "▶" in txt:
                try:
                    await msg.click(r, c)
                    return True
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    try:
                        await msg.click(r, c)
                        return True
                    except:
                        return False
                except:
                    return False
    return False


def find_confirm_button(msg):
    if not msg.reply_markup or not hasattr(msg.reply_markup, 'inline_keyboard'):
        return None
    for r, row in enumerate(msg.reply_markup.inline_keyboard):
        for c, btn in enumerate(row):
            txt = (btn.text or "").lower().strip()
            if any(cancel in txt for cancel in CANCEL_KEYWORDS):
                continue
            if any(kw.lower() in txt for kw in CONFIRM_KEYWORDS):
                return r, c, btn.text
    return None


async def safe_send(client, bot, text):
    try:
        await client.send_message(bot, text)
        log("SEND", f"📤 {text[:60]}")
    except FloodWait as e:
        log("SEND", f"⏳ FloodWait {e.value}s...")
        await asyncio.sleep(e.value)
        await client.send_message(bot, text)
    except Exception as e:
        log("SEND", f"❌ Gagal kirim: {e}")


async def instant_click(msg, r, c):
    try:
        await msg.click(r, c)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await msg.click(r, c)
            return True
        except:
            return False
    except:
        return False


async def proceed_to_favorite_and_sell(client):
    """Kirim /favorite lalu /jual semua."""
    if state["rare_inventory_nums"]:
        numbers_str = " ".join(str(n) for n in state["rare_inventory_nums"])
        log("FAV", f"⭐ Kirim /favorite {numbers_str}")
        await safe_send(client, FISHING_BOT, f"/favorite {numbers_str}")
        await asyncio.sleep(2)
    else:
        log("FAV", "📭 Tidak ada rare, skip favorite")

    await safe_send(client, FISHING_BOT, "/jual semua")
    state["waiting_sell"] = True


# ━━━ CLIENT ━━━
app = Client("automancing", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)


# ━━━ HANDLER ━━━
def is_fishing_bot(_, __, msg):
    if not msg.from_user:
        return False
    if msg.from_user.username:
        return msg.from_user.username.lower() == FISHING_BOT.lower()
    return False

fishing_bot_filter = filters.create(is_fishing_bot)


@app.on_message(filters.private & fishing_bot_filter)
async def handle_fishing_bot(client, message):
    text = message.text or message.caption or ""

    # ━━ Captcha ━━
    if is_captcha(text, message):
        await solve_captcha(client, message)
        return

    # ━━ Hasil mancing selesai ━━
    if "SESI MANCING SELESAI" in text or "Yang Ditangkap" in text:
        state["waiting_result"] = False
        log("MANCING", "🎣 Sesi selesai! Parsing...")

        total, rare_nums = parse_rare_from_result(text)
        state["total_catch"] = total

        if rare_nums:
            inv_nums = calc_inventory_numbers(total, rare_nums)
            state["rare_inventory_nums"] = inv_nums
            log("MANCING", f"⭐ Urutan: {rare_nums} → Inventory: {inv_nums}")
        else:
            state["rare_inventory_nums"] = []
            log("MANCING", "📭 Tidak ada ikan rare")

        await asyncio.sleep(2)
        state["scanning_pages"] = True
        await safe_send(client, FISHING_BOT, "/inventory")
        return

    # ━━ Scan inventory halaman per halaman ━━
    if state["scanning_pages"] and "Slot terisi" in text:
        page_match = re.search(r'Halaman[:\s]+(\d+)/(\d+)', text)

        if page_match:
            current_page = int(page_match.group(1))
            total_pages = int(page_match.group(2))
            log("INV", f"📄 Halaman {current_page}/{total_pages}")

            if current_page < total_pages:
                await asyncio.sleep(1)
                clicked = await click_next(message)
                if clicked:
                    log("INV", f"➡️ Next → halaman {current_page + 1}")
                else:
                    log("INV", "⚠️ Gagal klik Next, lanjut...")
                    state["scanning_pages"] = False
                    await proceed_to_favorite_and_sell(client)
            else:
                log("INV", "✅ Semua halaman selesai di-scan")
                state["scanning_pages"] = False
                await proceed_to_favorite_and_sell(client)
        else:
            state["scanning_pages"] = False
            await proceed_to_favorite_and_sell(client)
        return

    # ━━ Konfirmasi jual ━━
    if state["waiting_sell"] and ("KONFIRMASI PENJUALAN" in text or "Jual semua ikan" in text):
        result = find_confirm_button(message)
        if result:
            r, c, label = result
            success = await instant_click(message, r, c)
            if success:
                log("JUAL", f"✅ TERJUAL! '{label}'")
                state["waiting_sell"] = False
        return

    # ━━ Favorite berhasil ━━
    if "favorit" in text.lower() and "berhasil" in text.lower():
        log("FAV", "⭐ Favorite berhasil!")
        return


# ━━━ LOOP MANCING ━━━
async def mancing_loop(client):
    while True:
        log("LOOP", "🎣 Mulai mancing...")
        state["waiting_result"] = True
        await safe_send(client, FISHING_BOT, "/mancing")
        await asyncio.sleep(MANCING_INTERVAL + 60)
        log("LOOP", "🔄 Sesi berikutnya...")


# ━━━ STARTUP ━━━
async def main():
    missing = []
    if not API_ID: missing.append("API_ID")
    if not API_HASH: missing.append("API_HASH")
    if not SESSION_STRING: missing.append("SESSION_STRING")
    if not ANTHROPIC_API_KEY: missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"❌ Missing: {', '.join(missing)}", flush=True)
        return

    print("=" * 50, flush=True)
    print("  🎣 AUTO MANCING v1.3 (Pyrofork + Claude AI)", flush=True)
    print(f"  🤖 Bot: @{FISHING_BOT}", flush=True)
    print(f"  ⏳ Interval: {MANCING_INTERVAL}s", flush=True)
    print(f"  ⭐ Rare: {' '.join(RARE_EMOJIS)}", flush=True)
    print(f"  🧠 Claude AI: {'✅' if ANTHROPIC_API_KEY else '❌'}", flush=True)
    print("=" * 50, flush=True)

    await app.start()

    try:
        bot = await app.get_users(FISHING_BOT)
        log("INIT", f"✅ Bot: @{bot.username} (ID: {bot.id})")
    except Exception as e:
        log("INIT", f"⚠️ Bot: {e}")

    log("INIT", "━" * 40)
    log("INIT", "🟢 AUTO MANCING ACTIVE!")
    log("INIT", "   🧠 Claude AI siap jawab captcha otomatis!")
    log("INIT", "   Tinggal ditinggal tidur 😴")
    log("INIT", "━" * 40)

    asyncio.create_task(mancing_loop(app))

    await idle()
    await app.stop()


if __name__ == "__main__":
    app.run(main())
