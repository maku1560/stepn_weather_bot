# STEPN Weather Bot v2025-08-10-5
# 直近3時間の天気を返す + 天気×気温×時間帯の関西弁コメント（各軸5パターン）＋AA顔文字

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
BOT_VERSION = "2025-08-10-5"
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JST = timezone(timedelta(hours=9))
USER_AGENT = f"STEPN-Weather-Bot/{BOT_VERSION} (contact: your-email@example.com)"

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # メンションを読むのに必要

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
        "大阪": "Osaka", "京都": "Kyoto", "札幌": "Sapporo", "名古屋": "Nagoya",
        "福岡": "Fukuoka", "神戸": "Kobe", "横浜": "Yokohama", "仙台": "Sendai",
        "千葉": "Chiba", "川崎": "Kawasaki", "さいたま": "Saitama", "那覇": "Naha",
        "広島": "Hiroshima", "金沢": "Kanazawa",
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
            uniq_trials.append(t); seen.add(t)

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
        "latitude": lat, "longitude": lon,
        "hourly": (
            "temperature_2m,precipitation_probability,precipitation,weathercode,"
            "windspeed_10m"
        ),
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

def categorize_weather(rows: list[dict]) -> str:
    """weather: clear/cloudy/rain/snow/thunder/windy のいずれか"""
    codes = [r["weathercode"] for r in rows]
    winds = [r.get("wind", 0.0) for r in rows]
    if any(c in (95,96,99) for c in codes):
        return "thunder"
    if any(c in (71,73,75,77,85,86) for c in codes):
        return "snow"
    if any(c in (51,53,55,61,63,65,66,67,80,81,82) for c in codes):
        return "rain"
    if max(winds or [0.0]) >= 10.0:  # 10m/s以上で「強風」扱い
        return "windy"
    if all(c in (0,1,2) for c in codes):
        return "clear"
    return "cloudy"

def categorize_temp(rows: list[dict]) -> str:
    """temp: cold(<10) / cool(10-20) / warm(20-28) / hot(>=28)"""
    max_temp = max(r["temp"] for r in rows)
    if max_temp < 10:
        return "cold"
    if max_temp < 20:
        return "cool"
    if max_temp < 28:
        return "warm"
    return "hot"

def categorize_time(rows: list[dict]) -> str:
    """time band: morning(5-9) / noon(10-15) / evening(16-18) / night(19-4)"""
    # 先頭の時間帯で代表させる
    h = rows[0]["time"].hour
    if 5 <= h <= 9:
        return "morning"
    if 10 <= h <= 15:
        return "noon"
    if 16 <= h <= 18:
        return "evening"
    return "night"

# ---------- コメントパターン辞書（各軸5パターン＋AA混ぜ） ----------
AA = ["|ω・)", "(/ω＼)", "( ´ ▽ ` )", "(￣▽￣;)", "(｀・ω・´)", "( ˘ω˘ )", "(｡･ω･｡)", "(；・∀・)", "(・∀・)", "(>_<)"]
def maybe_aa(prob=0.6):
    return (" " + random.choice(AA)) if random.random() < prob else ""

WEATHER_TEXT = {
    "clear": [
        "ええ天気やな☀️",
        "日差したっぷりやで",
        "空、スカッと晴れとるわ",
        "今日は青空がご機嫌さんや",
        "洗濯日和ってやつやね",
    ],
    "cloudy": [
        "雲多めやな",
        "どんよりしとるけど雨まではいかんかな",
        "薄曇りって感じや",
        "空はグレーやけどまだ平和やで",
        "日差しは控えめやな",
    ],
    "rain": [
        "雨来そう（or降っとる）で☔",
        "空気しっとりや、傘あると安心やで",
        "路面濡れてるから足元注意や",
        "ザーッと来るかも、用心しときや",
        "にわか雨の匂いするなぁ",
    ],
    "snow": [
        "雪の気配や❄️",
        "白いの降っとるかもや",
        "路面滑りやすいで、ほんま注意な",
        "手先冷える雪空やで",
        "景色は綺麗やけど足元キケンや",
    ],
    "thunder": [
        "雷の可能性あるで⚡",
        "ゴロゴロ来るかも、外は気ぃつけや",
        "稲光あったら建物に避難やで",
        "雷雨注意、無理な外出はやめとこ",
        "空の機嫌が悪いわ、要警戒や",
    ],
    "windy": [
        "風つよいで🌬️",
        "突風ありそうや、帽子飛ぶで",
        "体感温度下がる風やな",
        "洗濯物は要クリップやで",
        "自転車の横風に注意や",
    ],
}

TEMP_TEXT = {
    "cold": [
        "めっちゃ冷える、厚着でな",
        "手袋とマフラー出番やで",
        "カイロあると心強いで",
        "外は冷蔵庫みたいや",
        "寒の戻り感あるわ",
    ],
    "cool": [
        "ひんやり気持ちええな",
        "軽めの上着あると安心や",
        "歩くにはちょうどええ体感やで",
        "空気がスッとして心地ええな",
        "汗かかん程度で快適や",
    ],
    "warm": [
        "ぽかぽかで過ごしやすい",
        "薄手で十分やな",
        "外に出るのが捗る気温やで",
        "散歩日和や、気持ちええわ",
        "ちょうど春〜初夏の感じや",
    ],
    "hot": [
        "暑いで💦 水分しっかりな",
        "日差しキツい、日焼け止め忘れんといて",
        "無理は禁物、日陰で休憩や",
        "アイスがうまい気温やな",
        "熱中症注意、帽子あるとええで",
    ],
}

TIME_TEXT = {
    "morning": [
        "朝は体起こすまでゆっくりいこ",
        "通勤時間は足元と信号に注意や",
        "朝活にはちょうどええかも",
        "寝ぼけて転ばへんようにな",
        "モーニング日差しで目覚めスッキリや",
    ],
    "noon": [
        "昼は動きやすい時間帯やな",
        "外回りは今のうちに済ませよ",
        "日差し真上やから日陰選んで歩こ",
        "ランチの行列は余裕持ってな",
        "体力使いすぎんようこまめに休憩や",
    ],
    "evening": [
        "夕方は冷え戻るから一枚あると安心や",
        "帰りは空の機嫌に注意しとこ",
        "日没前後は視界が落ちるで、気ぃつけて",
        "寄り道は控えめに安全第一や",
        "夕焼け見れたらラッキーやな",
    ],
    "night": [
        "夜道は暗いで、足元と車に注意な",
        "冷え込むから帰りは急ぎめで",
        "遅い時間は無理せんと帰ろ",
        "視界悪いから反射材あると安心や",
        "終電前には撤収やで",
    ],
}

def build_comment(rows: list[dict]) -> str:
    """天気×気温×時間帯の各軸から1つずつ選んで、AAもランダム添え。"""
    w = categorize_weather(rows)
    t = categorize_temp(rows)
    d = categorize_time(rows)

    w_txt = random.choice(WEATHER_TEXT[w])
    t_txt = random.choice(TEMP_TEXT[t])
    d_txt = random.choice(TIME_TEXT[d])

    # 文を自然に繋ぐ
    base = f"{w_txt}。{t_txt}。{d_txt}。"
    return base + maybe_aa()

# ---------- 表示 ----------
def build_embed(place: dict, rows: list[dict]) -> discord.Embed:
    loc = place['name']; admin = place.get('admin1') or ''; country = place.get('country') or ''
    title = f"{loc}（{admin + '・' if admin else ''}{country}）".strip("（）")
    embed = discord.Embed(title=f"直近3時間の天気 | {title}", color=0x4C7CF3)
    lines=[]
    for r in rows:
        t=r['time']; emoji=pick_emoji(r['weathercode'])
        wind = r.get("wind", 0.0)
        lines.append(
            f"**{t.strftime('%H:%M')}** {emoji}  気温 **{r['temp']:.1f}°C**  "
            f"降水確率 **{r['pop']}%**  降水量 **{r['precip']:.1f}mm**  風速 **{wind:.1f}m/s**"
        )
    embed.description="\n".join(lines)
    ts=datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    embed.set_footer(text=f"更新: {ts} JST • Powered by Open-Meteo")
    return embed

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
    winds = data["hourly"].get("windspeed_10m", [0.0]*len(times))

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
                "weathercode": int(codes[i]) if i < len(codes) and codes[i] is not None else 0,
                "wind": float(winds[i]) if i < len(winds) and winds[i] is not None else 0.0,
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
