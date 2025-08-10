# STEPN Weather Bot (Discord) - GPT-5 sample
# 使い方：メンションと一緒に地名を送ると、直近3時間の天気を返します。
# 例: @Bot 東京 / @Bot Osaka / @Bot 札幌
# Slashコマンド: /weather location:<地名>
#
# 無料の Open-Meteo API を使用（APIキー不要）
# - ジオコーディング: https://geocoding-api.open-meteo.com/v1/search
# - 天気: https://api.open-meteo.com/v1/forecast
#
# 必要設定：環境変数 DISCORD_BOT_TOKEN にトークンを設定
# Discord側で "MESSAGE CONTENT INTENT" を有効にしてください。

import os

from dotenv import load_dotenv
load_dotenv()

import re
import asyncio
from datetime import datetime, timedelta, timezone
import aiohttp
import discord
from discord import app_commands

# ---- Config ----
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JST = timezone(timedelta(hours=9))
USER_AGENT = "STEPN-Weather-Bot/1.0 (contact: your-email@example.com)"

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # メッセージ本文を読むために必要

class WeatherBot(discord.Client):
    def __init__(self):
        super().__init__(intents=INTENTS)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # スラッシュコマンドを同期
        await self.tree.sync()

client = WeatherBot()

# ---- Utilities ----
async def geocode(session: aiohttp.ClientSession, query: str):
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": query, "count": 1, "language": "ja", "format": "json"}
    headers = {"User-Agent": USER_AGENT}
    async with session.get(url, params=params, headers=headers, timeout=15) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        results = data.get("results", [])
        if not results:
            return None
        r = results[0]
        return {
            "name": r.get("name"),
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
            "country": r.get("country"),
            "admin1": r.get("admin1"),
            "timezone": r.get("timezone"),
        }

async def fetch_forecast(session: aiohttp.ClientSession, lat: float, lon: float, tz: str):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,precipitation,weathercode",
        "timezone": tz or "Asia/Tokyo"
    }
    headers = {"User-Agent": USER_AGENT}
    async with session.get(url, params=params, headers=headers, timeout=15) as resp:
        if resp.status != 200:
            return None
        return await resp.json()

WEATHER_EMOJI = {
    # Open-Meteo weather codes (simplified)
    0: "☀️",  # Clear sky
    1: "🌤️",  # Mainly clear
    2: "⛅",   # Partly cloudy
    3: "☁️",   # Overcast
    45: "🌫️",  # Fog
    48: "🌫️",  # Depositing rime fog
    51: "🌦️",  # Drizzle
    53: "🌦️",
    55: "🌧️",
    61: "🌦️",  # Rain
    63: "🌧️",
    65: "🌧️",
    66: "🌧️",
    67: "🌧️",
    71: "🌨️",  # Snow fall
    73: "🌨️",
    75: "❄️",
    77: "❄️",
    80: "🌧️",  # Rain showers
    81: "🌧️",
    82: "⛈️",
    85: "🌨️",  # Snow showers
    86: "🌨️",
    95: "⛈️",  # Thunderstorm
    96: "⛈️",
    99: "⛈️"
}

def pick_emoji(code: int) -> str:
    return WEATHER_EMOJI.get(code, "🌡️")

def build_embed(place: dict, rows: list[dict]) -> discord.Embed:
    title = f"{place['name']}（{place.get('admin1') or ''}{'・' if place.get('admin1') else ''}{place.get('country') or ''}）"
    embed = discord.Embed(title=f"直近3時間の天気 | {title}", color=0x4C7CF3)
    lines = []
    for r in rows:
        t = r['time']
        emoji = pick_emoji(r['weathercode'])
        lines.append(
            f"**{t.strftime('%H:%M')}** {emoji}  気温 **{r['temp']:.1f}°C**  "
            f"降水確率 **{r['pop']}%**  降水量 **{r['precip']:.1f}mm**"
        )
    embed.description = "\n".join(lines)
    ts = datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    embed.set_footer(text=f"更新: {ts} JST • Powered by Open‑Meteo")
    return embed

async def get_next_3_hours(session: aiohttp.ClientSession, place_query: str):
    geo = await geocode(session, place_query)
    if not geo:
        return None, None, "場所が見つからへんかったで。別の表記でもう一回試してな。例：東京/大阪/札幌/京都市 など"

    data = await fetch_forecast(session, geo["latitude"], geo["longitude"], geo["timezone"])
    if not data or "hourly" not in data:
        return geo, None, "天気データの取得に失敗したわ。ちょっと時間をおいて再試行してな。"

    times = data["hourly"]["time"]
    temps = data["hourly"]["temperature_2m"]
    pops  = data["hourly"].get("precipitation_probability", [0]*len(times))
    precs = data["hourly"].get("precipitation", [0.0]*len(times))
    codes = data["hourly"].get("weathercode", [0]*len(times))

    # 現在時刻以降の3件を抽出（APIは現地タイムゾーンで返す）
    now = datetime.now(JST)
    rows = []
    for i, ts in enumerate(times):
        # tsはISO8601文字列
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)
        except Exception:
            # 念のため
            t = now
        if t >= now and len(rows) < 3:
            rows.append({
                "time": t,
                "temp": float(temps[i]),
                "pop": int(pops[i]) if i < len(pops) and pops[i] is not None else 0,
                "precip": float(precs[i]) if i < len(precs) and precs[i] is not None else 0.0,
                "weathercode": int(codes[i]) if i < len(codes) and codes[i] is not None else 0
            })
        if len(rows) == 3:
            break

    if not rows:
        return geo, None, "直近3時間分のデータが見つからんかったわ。"

    return geo, rows, None

MENTION_PATTERN = re.compile(r"<@!?(\d+)>")

def extract_query_from_message(content: str, bot_id: int) -> str | None:
    # メンション形式の直後の文字列を地名として使う
    # 例: "<@1234567890> 東京駅" -> "東京駅"
    m = MENTION_PATTERN.search(content)
    if not m:
        return None
    mentioned_id = int(m.group(1))
    if mentioned_id != bot_id:
        return None
    # メンション部分を削除して残りをトリム
    rest = MENTION_PATTERN.sub("", content, count=1).strip()
    return rest or None

@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print("------")

@client.event
async def on_message(message: discord.Message):
    # Bot自身の発言は無視
    if message.author.bot:
        return

    # メンション＋地名で応答
    query = extract_query_from_message(message.content, client.user.id)
    if not query:
        return

    async with message.channel.typing():
        async with aiohttp.ClientSession() as session:
            place, rows, err = await get_next_3_hours(session, query)
            if err:
                await message.reply(err, mention_author=False)
                return
            embed = build_embed(place, rows)
            await message.reply(embed=embed, mention_author=False)

# ---- Slash command ----
@client.tree.command(name="weather", description="地名を指定して直近3時間の天気を表示します")
@app_commands.describe(location="地名（例：東京, Osaka, 札幌）")
async def weather(interaction: discord.Interaction, location: str):
    await interaction.response.defer(thinking=True)
    async with aiohttp.ClientSession() as session:
        place, rows, err = await get_next_3_hours(session, location)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        embed = build_embed(place, rows)
        await interaction.followup.send(embed=embed)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")
    client.run(BOT_TOKEN)

if __name__ == "__main__":
    main()
