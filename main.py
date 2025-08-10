# STEPN Weather Bot v2025-08-10-3 （ランドマーク対応強化＋関西弁コメント10種×条件）
import os
import re
import random
from datetime import datetime, timedelta, timezone

# .env（無くても動くようにtry）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import aiohttp
import discord
from discord import app_commands

# ---- Config ----
BOT_VERSION = "2025-08-10-3"
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JST = timezone(timedelta(hours=9))
USER_AGENT = f"STEPN-Weather-Bot/{BOT_VERSION} (contact: your-email@example.com)"

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # メンション文面を読むのに必要

class WeatherBot(discord.Client):
    def __init__(self):
        super().__init__(intents=INTENTS)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

client = WeatherBot()

# ---------- Geocoding ----------
async def geocode(session: aiohttp.ClientSession, query: str):
    """
    地名・ランドマーク名から候補を取得し、都市レベル（admin1 & countryあり）を優先し population 降順で1件返す。
    見つからん時は別名・市付け・ローマ字など段階的に試す。
    """
    def pick_best(results: list[dict]) -> dict | None:
        if not results:
            return None
        cities = [r for r in results if r.get("admin1") and r.get("country")]
        if cities:
            cities.sort(key=lambda r: (r.get("population") or 0), reverse=True)
            return cities[0]
        return results[0]

    async def search(name: str):
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {"name": name, "count": 10, "language": "ja", "format": "json"}
        headers = {"User-Agent": USER_AGENT}
        async with session.get(url, params=params, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("results", []) or []

    # よく使う略称や主要ランドマークの別名（必要に応じて増やせる）
    alias = {
        "USJ": "ユニバーサル・スタジオ・ジャパン",
        "ユニバ": "ユニバーサル・スタジオ・ジャパン",
        "ディズニー": "東京ディズニーランド",
        "ディズニーランド": "東京ディズニーランド",
        "ディズニーシー": "東京ディズニーシー",
        "TDL": "東京ディズニーランド",
        "TDS": "東京ディズニーシー",
        "梅田": "大阪市 梅田",
        "なんば": "大阪市 なんば",
        "心斎橋": "大阪市 心斎橋",
        "通天閣": "大阪市 通天閣",
        "天王寺": "大阪市 天王寺",
        "スカイツリー": "東京スカイツリー",
        "東京駅": "東京駅",
        "浅草": "台東区 浅草",
        "秋葉原": "千代田区 秋葉原",
        "横浜中華街": "横浜市 中区",
    }
    romaji = {
        "大阪": "Osaka",
        "京都": "Kyoto",
        "札幌": "Sapporo",
        "名古屋": "Nagoya",
        "福岡": "Fukuoka",
        "神戸": "Kobe",
        "横浜": "Yokohama",
        "仙台": "Sendai",
        "千葉": "Chiba",
        "川崎": "Kawasaki",
        "さいたま": "Saitama",
        "那覇": "Naha",
        "広島": "Hiroshima",
        "金沢": "Kanazawa",
    }

    trials = [query]
    if query in alias:
        trials.append(alias[query])
    # 短い地名は「市」付けも試す（大阪→大阪市など）
    if not query.endswith(("市", "区", "町", "村")) and len(query) <= 4:
        trials.append(query + "市")
    if query in romaji:
        trials.append(romaji[query])

    # 重複除去
    seen, uniq_trials = set(), []
    for t in trials:
        if t not in seen:
            uniq_trials.append(t)
            seen.add(t)

    for q in uniq_trials:
        results = await search(q)
        chosen = pick_best(results)
        if chosen:
            return {
                "name": chosen.get("name"),
                "latitude": chosen.get("latitude"),
                "longitude": chosen.get("longitude"),
                "country": chosen.get("country"),
                "admin1": chosen.get("admin1"),
                "timezone": chosen.get("timezone"),
            }
    return None

# ---------- Forecast ----------
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
    0:"☀️",1:"🌤️",2:"⛅",3:"☁️",45:"🌫️",48:"🌫️",
    51:"🌦️",53:"🌦️",55:"🌧️",61:"🌦️",63:"🌧️",65:"🌧️",66:"🌧️",67:"🌧️",
    71:"🌨️",73:"🌨️",75:"❄️",77:"❄️",80:"🌧️",81:"🌧️",82:"⛈️",
    85:"🌨️",86:"🌨️",95:"⛈️",96:"⛈️",99:"⛈️"
}
def pick_emoji(code:int)->str: return WEATHER_EMOJI.get(code,"🌡️")

def build_embed(place: dict, rows: list[dict]) -> discord.Embed:
    loc = place['name']; admin = place.get('admin1') or ''; country = place.get('country') or ''
    title = f"{loc}（{admin + '・' if admin else ''}{country}）".strip("（）")
    embed = discord.Embed(title=f"直近3時間の天気 | {title}", color=0x4C7CF3)
    lines=[]
    for r in rows:
        t=r['time']; emoji=pick_emoji(r['weathercode'])
        lines.append(f"**{t.strftime('%H:%M')}** {emoji}  気温 **{r['temp']:.1f}°C**  降水確率 **{r['pop']}%**  降水量 **{r['precip']:.1f}mm**")
    embed.description="\n".join(lines)
    ts=datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    embed.set_footer(text=f"更新: {ts} JST • Powered by Open-Meteo")
    return embed

# ---------- KANSAI comments (10 patterns each) ----------
def build_comment(rows: list[dict]) -> str:
    """直近3時間の条件から関西弁で一言アドバイス（条件ごとに10パターンをランダム）"""
    max_temp = max(r['temp'] for r in rows)
    max_pop  = max(r['pop'] for r in rows)
    sum_prec = sum(r['precip'] for r in rows)
    thunder  = any(r['weathercode'] in (95,96,99) for r in rows)

    thunder_comments = [
        "雷ゴロゴロや、外出は気ぃつけや！",
        "雷雨来るかもやで。気ぃ引き締めてな。",
        "雷注意や、今日は空見上げる暇ないで。",
        "ゴロゴロ音したら即退避や！",
        "雷雲近づいとるわ、傘あっても危険やで。",
        "今日は稲妻ショーかもしれん。安全第一や！",
        "雷雨の予感、外やとほんま危ないで。",
        "雷鳴ったら即建物に入るんやで！",
        "ピカッと来たらドン！や、油断せんといてな。",
        "雷注意報レベルや、外は最小限でな。",
    ]
    rain_comments = [
        "傘持ってった方がええな。濡れんようにね。",
        "雨来そうや、傘忘れたら後悔するで。",
        "今日はカッパの出番かもな。",
        "折りたたみ傘は必須やで。",
        "降る前に帰るんが賢いで。",
        "雨靴あったら履いときや。",
        "濡れると風邪ひくで、用心しいや。",
        "洗濯物は部屋干し推奨やな。",
        "降水確率高めや、濡れる覚悟しときや。",
        "雨の日コーデで行こか。",
    ]
    hot_comments = [
        "暑なりそうや。水分しっかり取っていこ。",
        "今日は真夏日やな、日焼け止め忘れずに！",
        "汗だく覚悟で行動やな。",
        "熱中症注意や、帽子もあるとええで。",
        "冷たい飲み物必須やな。",
        "日陰探して歩いた方がええで。",
        "外出は涼しい時間帯がええな。",
        "エアコン効いたとこで休憩しぃや。",
        "クールタオル持ってくとええで。",
        "今日はアイスがうまい日やな。",
    ]
    cold_comments = [
        "だいぶ冷えるで。あったかい格好でな。",
        "今日は手袋必須やな。",
        "マフラー忘れたら凍えるで。",
        "カイロ持ってくとええで。",
        "外は冷蔵庫みたいやな。",
        "厚着しとかな後悔するで。",
        "風邪ひかんようにな。",
        "暖房の効いたとこで休憩しいや。",
        "耳あてが恋しい寒さやな。",
        "寒さに負けんようにしっかり着込むんやで。",
    ]
    mild_comments = [
        "今日はわりと過ごしやすそうや。",
        "快適な気温やな、外歩くのもええ感じや。",
        "お出かけ日和やで。",
        "風が気持ちええ日やな。",
        "特に対策いらんくらいの天気やで。",
        "今日はのんびり散歩日和やな。",
        "空気が心地ええ日や。",
        "軽装で十分やな。",
        "気分ええ一日になりそうや。",
        "こういう日は外で過ごすのが正解やな。",
    ]

    if thunder:
        return random.choice(thunder_comments)
    if max_pop >= 60 or sum_prec >= 1.0:
        return random.choice(rain_comments)
    if max_temp >= 30:
        return random.choice(hot_comments)
    if max_temp <= 5:
        return random.choice(cold_comments)
    return random.choice(mild_comments)

# ---------- Core ----------
async def get_next_3_hours(session: aiohttp.ClientSession, place_query: str):
    geo = await geocode(session, place_query)
    if not geo:
        return None, None, "場所が見つからへんかったで。別の表記でもう一回試してな。"
    data = await fetch_forecast(session, geo["latitude"], geo["longitude"], geo["timezone"])
    if not data or "hourly" not in data:
        return geo, None, "天気データの取得に失敗したわ。"
    times = data["hourly"]["time"]
    temps = data["hourly"]["temperature_2m"]
    pops  = data["hourly"].get("precipitation_probability", [0]*len(times))
    precs = data["hourly"].get("precipitation", [0.0]*len(times))
    codes = data["hourly"].get("weathercode", [0]*len(times))

    now = datetime.now(JST)
    rows=[]
    for i, ts in enumerate(times):
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)
        except Exception:
            continue
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

# ---------- Message handling ----------
MENTION_PATTERN = re.compile(r"<@!?(\d+)>")

def extract_query_from_message(content: str, bot_id: int) -> str | None:
    m = MENTION_PATTERN.search(content)
    if not m: return None
    if int(m.group(1)) != bot_id: return None
    rest = MENTION_PATTERN.sub("", content, count=1).strip()
    return rest or None

@client.event
async def on_ready():
    print(f"Bot version: {BOT_VERSION}")
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print("------")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    query = extract_query_from_message(message.content, client.user.id)
    if not query:
        return
    async with message.channel.typing():
        async with aiohttp.ClientSession() as session:
            place, rows, err = await get_next_3_hours(session, query)
            if err:
                await message.reply(err, mention_author=False); return
            embed = build_embed(place, rows)
            comment = build_comment(rows)
            await message.reply(content=comment, embed=embed, mention_author=False)

@client.tree.command(name="weather", description="地名・ランドマーク名から直近3時間の天気を表示します")
@app_commands.describe(location="地名/ランドマーク（例：大阪, USJ, 東京ディズニーランド）")
async def weather(interaction: discord.Interaction, location: str):
    await interaction.response.defer(thinking=True)
    async with aiohttp.ClientSession() as session:
        place, rows, err = await get_next_3_hours(session, location)
        if err:
            await interaction.followup.send(err, ephemeral=True); return
        embed = build_embed(place, rows)
        comment = build_comment(rows)
        await interaction.followup.send(content=comment, embed=embed)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")
    client.run(BOT_TOKEN)

if __name__ == "__main__":
    main()
