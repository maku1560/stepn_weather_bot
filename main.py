# STEPN Weather Bot v2025-08-10-11
# 方言は“最初から用意”方式：変換せず、方言ごとのコメントパックを直接使用
# 直近3時間の天気 + 方言別コメント(天気×気温 主文 3バリ) + 時間帯追いコメント + AA
# 安全化: 未定義は方言内/標準にフォールバック、例外時も必ず返答

import os
import re
import random
from datetime import datetime, timedelta, timezone

# .env（無くても動く）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import aiohttp
import discord
from discord import app_commands

# ---- Config ----
BOT_VERSION = "2025-08-10-11"
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JST = timezone(timedelta(hours=9))
USER_AGENT = f"STEPN-Weather-Bot/{BOT_VERSION} (contact: your-email@example.com)"

INTENTS = discord.Intents.default()
INTENTS.message_content = True

class WeatherBot(discord.Client):
    def __init__(self):
        super().__init__(intents=INTENTS)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

client = WeatherBot()

# ---------- Geocoding ----------
async def geocode(session: aiohttp.ClientSession, query: str):
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
    if not query.endswith(("市", "区", "町", "村")) and len(query) <= 4:
        trials.append(query + "市")
    if query in romaji:
        trials.append(romaji[query])

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

# ---------- Emoji ----------
WEATHER_EMOJI = {
    0:"☀️",1:"🌤️",2:"⛅",3:"☁️",45:"🌫️",48:"🌫️",
    51:"🌦️",53:"🌦️",55:"🌧️",61:"🌦️",63:"🌧️",65:"🌧️",66:"🌧️",67:"🌧️",
    71:"🌨️",73:"🌨️",75:"❄️",77:"❄️",80:"🌧️",81:"🌧️",82:"⛈️",
    85:"🌨️",86:"🌨️",95:"⛈️",96:"⛈️",99:"⛈️"
}
def pick_emoji(code:int)->str: return WEATHER_EMOJI.get(code,"🌡️")

# ---- 分類（天気×気温×時間帯） ----
def categorize_weather(rows):
    codes = [r["weathercode"] for r in rows]
    if any(c in (95,96,99) for c in codes):
        return "thunder"  # 雷（追いコメントのみで扱う：主文は rain と似せる）
    if any(c in (71,73,75,77,85,86) for c in codes):
        return "snow"
    if any(c in (51,53,55,61,63,65,66,67,80,81,82) for c in codes):
        return "rain"
    if all(c in (0,1,2) for c in codes):
        return "sunny"
    return "cloudy"

def categorize_temp(rows):
    m = max(r["temp"] for r in rows)
    if m >= 30: return "hot"
    if m >= 20: return "warm"
    if m >= 10: return "cool"
    return "cold"

def categorize_time(rows):
    h = rows[0]["time"].hour
    if 5 <= h <= 9:  return "morning"
    if 10 <= h <= 15: return "day"
    if 16 <= h <= 18: return "evening"
    return "night"

# ---------- AA ----------
AA = ["|ω・)", "(/ω＼)", "( ´ ▽ ` )", "(￣▽￣;)", "(｀・ω・´)", "( ˘ω˘ )", "(｡･ω･｡)", "(；・∀・)", "(・∀・)", "(>_<)"]
def maybe_aa(p=0.75):
    return (" " + random.choice(AA)) if random.random() < p else ""

def ensure_aa(s: str) -> str:
    if re.search(r"\(|\||／", s):  # 既にAAらしき記号がある
        return s
    return s + maybe_aa()

# ---------- 方言キー ----------
DIALECTS = ["kanto","kansai","tohoku","chugoku","kyushu"]

def pick_dialect_key(place: dict) -> str:
    pref = (place.get("admin1") or "").strip()
    if pref in ["大阪府","京都府","兵庫県","滋賀県","奈良県","和歌山県"]:
        return "kansai"
    if pref in ["青森県","岩手県","宮城県","秋田県","山形県","福島県"]:
        return "tohoku"
    if pref in ["広島県","岡山県","山口県","鳥取県","島根県"]:
        return "chugoku"
    if pref in ["福岡県","佐賀県","長崎県","熊本県","大分県","宮崎県","鹿児島県"]:
        return "kyushu"
    # それ以外は中立（関東）
    return "kanto"

# ---------- コメントエンジン（方言別プリセット） ----------
class CommentEngine:
    def __init__(self):
        # 主文：方言→天気→気温→バリエ3
        self.base = self._build_bases()
        # 追いコメント：方言→時間帯→候補
        self.tails = self._build_tails()
        # 雷用の追加尾
        self.thunder_tails = {
            "kanto": ["雷に注意して、無理せずね", "稲光が見えたら屋内に避難しよう", "安全第一で、計画は柔軟に"],
            "kansai": ["雷は要注意や、無理せんときや", "ピカッと来たら屋内に避難しよ", "安全第一で、予定は柔軟にいこ"],
            "tohoku": ["雷気をつけっぺな", "光ったら屋内さ入ろう", "無理せず安全第一だべ"],
            "chugoku": ["雷に気ぃつけんさい", "光ったら屋内へ避難しんさい", "安全第一で無理はせんのんよ"],
            "kyushu": ["雷には気をつけんね", "ピカッと来たら屋内に避難しとき", "安全第一たい"],
        }

    def get(self, dialect: str, weather: str, temp: str, timeband: str, has_thunder: bool) -> str:
        d = dialect if dialect in DIALECTS else "kanto"
        # 1) 主文（方言内→kantoの順にフォールバック）
        text = self._pick_base(d, weather, temp)
        # 2) 追いコメント（時間帯）
        tail = self._pick_tail(d, timeband)
        s = f"{text} {tail}".strip()
        # 3) 雷なら一言追加
        if has_thunder:
            s += " " + random.choice(self.thunder_tails[d])
        # 4) AA（重複防止）
        return ensure_aa(s)

    # ---- 内部 ----
    def _pick_base(self, dialect: str, weather: str, temp: str) -> str:
        # フォールバック順：その方言→kanto→最後は汎用文
        for d in [dialect, "kanto"]:
            block = self.base.get(d, {}).get(weather, {}).get(temp, [])
            if block:
                return random.choice(block)
        # 最終フォールバック（絶対に矛盾しない汎用）
        generic = {
            "sunny": "晴れてるよ、体調に合わせて無理なく過ごそう",
            "cloudy": "雲が多め、のんびりいこう",
            "rain": "雨の気配あり、傘があると安心だよ",
            "snow": "雪の可能性あり、足元に注意してね",
        }
        return generic.get(weather, "今日は穏やかにいこう")

    def _pick_tail(self, dialect: str, timeband: str) -> str:
        for d in [dialect, "kanto"]:
            block = self.tails.get(d, {}).get(timeband, [])
            if block:
                return random.choice(block)
        # 最終フォールバック
        return {"morning":"朝はゆっくり準備しよう",
                "day":"日中はこまめに休憩ね",
                "evening":"夕方は早めに切り上げよう",
                "night":"夜は安全第一でね"}.get(timeband, "")

    def _build_bases(self):
        # 短め・自然・矛盾なし。各3バリ。
        # weather: sunny/cloudy/rain/snow
        # temp: hot/warm/cool/cold
        return {
            "kanto": {
                "sunny": {
                    "hot": [
                        "強い日差しで暑いね、水分と日陰休憩を忘れずに",
                        "かなり暑いよ、帽子と日焼け対策もしっかりね",
                        "暑さが厳しいから、無理せず涼しい場所を使おう",
                    ],
                    "warm": [
                        "過ごしやすい晴れ、外の用事がはかどりそう",
                        "穏やかな陽気だね、洗濯や散歩にちょうどいい",
                        "心地よい晴れ、動くなら今がいいタイミング",
                    ],
                    "cool": [
                        "ひんやり晴れ、薄手の上着があると安心",
                        "空気は涼しめ、体を冷やしすぎないようにね",
                        "日差しはあるけど体感は低め、服装で調整しよう",
                    ],
                    "cold": [
                        "快晴でも冷えるよ、手袋やマフラーが役立つ",
                        "空気が冷たい、重ね着で温かくしていこう",
                        "放射冷却で冷え込みやすい、油断しないでね",
                    ],
                },
                "cloudy": {
                    "hot": [
                        "雲は多いけど蒸し暑い、こまめに水分を",
                        "蒸し暑さが残るね、風通しの良い服装で",
                        "日差しは弱めでも暑いよ、無理はしないで",
                    ],
                    "warm": [
                        "雲多めで動きやすい、屋外作業も負担少なめ",
                        "熱がこもりにくくて快適、今のうちに用事を進めよう",
                        "穏やかな曇り、外回りもしやすいね",
                    ],
                    "cool": [
                        "ひんやり曇り、羽織が一枚あるとちょうどいい",
                        "体感は低め、首元を温めると楽だよ",
                        "日差しがない分冷える、長居はほどほどに",
                    ],
                    "cold": [
                        "曇りで冷えるね、厚手の上着でしっかり防寒",
                        "底冷えしそう、温かい飲み物で体を守ろう",
                        "暗くなると一段と寒い、早めに帰るのが安心",
                    ],
                },
                "rain": {
                    "hot": [
                        "蒸し暑い雨、通気性の良いレインウェアが楽だよ",
                        "汗と雨で冷えやすい、タオルや替えのシャツがあると安心",
                        "小雨の合間をうまく使って動こう",
                    ],
                    "warm": [
                        "雨でも気温は高め、ムレ対策をしていこう",
                        "荷物は撥水だと安心、傘は忘れずにね",
                        "出入りでムワッとするから、体調に気をつけて",
                    ],
                    "cool": [
                        "ひんやり雨、傘は必須だよ",
                        "濡れると体が冷える、タオルを一枚持っておこう",
                        "屋内に逃げ場を作っておくと楽だよ",
                    ],
                    "cold": [
                        "冷たい雨、手袋や防水の靴が役立つよ",
                        "体温を奪われやすい、無理せずにいこう",
                        "風が強いときは合羽があると快適さが違う",
                    ],
                },
                "snow": {
                    "hot": [
                        "珍しい雪の条件だね、足元最優先でいこう",
                        "気温は高めでも雪には注意、無理はしないで",
                        "状況が変わりやすい、慎重に動こう",
                    ],
                    "warm": [
                        "みぞれ気味で足元が悪い、水はねに注意しよう",
                        "解けかけで滑りやすい、歩幅は小さめに",
                        "濡れ冷えしやすいから、体温管理を忘れずに",
                    ],
                    "cool": [
                        "小雪でひんやり、足元に気をつけよう",
                        "視界が白っぽい、横断は慎重にね",
                        "濡れた靴は冷える、替えの靴下があると安心",
                    ],
                    "cold": [
                        "凍結が心配、時間に余裕を持って動こう",
                        "転倒に注意、手袋と滑りにくい靴で",
                        "日没後は危険度アップ、寄り道は控えめに",
                    ],
                },
            },

            "kansai": {
                "sunny": {
                    "hot": [
                        "えらい暑いわ、水分と日陰休憩は忘れんときや",
                        "ギラギラやで、帽子と日焼け止めもしっかりな",
                        "無理せんと、涼しいとこ上手に使ってこ",
                    ],
                    "warm": [
                        "ええ晴れや、外の用事サクッと片付けよか",
                        "心地よい陽気やな、洗濯日和やで",
                        "動くなら今がええタイミングや",
                    ],
                    "cool": [
                        "ひんやり晴れや、薄手の上着あると安心やで",
                        "日差しはあっても体感は低め、服で調整しよ",
                        "朝晩は冷えるし、油断せんといてな",
                    ],
                    "cold": [
                        "快晴でも冷えるわ、手袋やマフラー用意しとき",
                        "空気きんと冷たい、重ね着でぬくぬくいこ",
                        "放射冷却で冷え込みやすいさかい、要注意や",
                    ],
                },
                "cloudy": {
                    "hot": [
                        "曇ってても蒸し暑いわ、こまめに水分な",
                        "日差し弱めでも暑いで、無理は禁物や",
                        "風通しのええ服でいこか",
                    ],
                    "warm": [
                        "雲多めで動きやすい、今のうちに用事やってまお",
                        "熱こもりにくくて快適や、外回りもしやすいで",
                        "ええ感じの曇りやな、サクサク動けそうや",
                    ],
                    "cool": [
                        "ひんやり曇り、羽織一枚あると楽やで",
                        "日差しないぶん冷える、首元あっためよ",
                        "長居はほどほどにしとこ",
                    ],
                    "cold": [
                        "曇りで底冷えするで、厚めの上着でいこ",
                        "温かい飲みもんで体守っとこな",
                        "暗なると一段と寒い、早めに帰るんが安心や",
                    ],
                },
                "rain": {
                    "hot": [
                        "蒸し雨や、通気性ええレインウェアが楽やで",
                        "汗と雨で冷えやすいし、タオルと替えシャツ用意な",
                        "小雨の合間うまく使って動こ",
                    ],
                    "warm": [
                        "雨でも気温は高め、ムレ対策忘れんといて",
                        "荷物は撥水が安心や、傘も忘れずにな",
                        "出入りでムワッと来るし、体調気ぃつけてな",
                    ],
                    "cool": [
                        "ひんやり雨や、傘は必須やで",
                        "濡れると体冷えるし、タオル持っとくと楽や",
                        "屋内の逃げ場つくっとくと安心やで",
                    ],
                    "cold": [
                        "冷たい雨や、手袋と防水靴が役立つで",
                        "体温もってかれやすい、無理せんようにな",
                        "風あれば合羽がだいぶ違うわ",
                    ],
                },
                "snow": {
                    "hot": [
                        "珍しい雪の条件や、足元最優先でいこな",
                        "気温は高めでも雪は要注意やで",
                        "状況変わりやすいし、慎重に動こ",
                    ],
                    "warm": [
                        "みぞれ気味で足元グズグズや、水はね注意な",
                        "解けかけで滑るで、歩幅は小さめにな",
                        "濡れ冷えしやすいし、体温管理してこ",
                    ],
                    "cool": [
                        "小雪でひんやり、足元気ぃつけてな",
                        "視界白っぽいし、横断は慎重にやで",
                        "靴濡れると冷えるわ、替え靴下あると安心や",
                    ],
                    "cold": [
                        "凍結こわいし、時間に余裕持って動こ",
                        "転倒注意や、滑りにくい靴と手袋をな",
                        "日没後は危険度上がるし、寄り道控えめで",
                    ],
                },
            },

            "tohoku": {
                "sunny": {
                    "hot": [
                        "暑いっちゃね、水分と日陰で休みながらいぐべ",
                        "日差しつよいがら、帽子と日焼け対策大事だべ",
                        "無理すんなよ、涼しいとこ使っていこう",
                    ],
                    "warm": [
                        "穏やかな晴れだべ、用事進めるなら今だな",
                        "過ごしやすくていい日だね、洗濯日和だべ",
                        "動くにはちょうどいい陽気だっちゃ",
                    ],
                    "cool": [
                        "ひんやり晴れだな、羽織一枚あるといいべ",
                        "日差しあっても体感は低めだべ、服で調整しよ",
                        "朝晩さむいかも、油断すんなよ",
                    ],
                    "cold": [
                        "快晴でも冷えるっちゃ、手袋やマフラー用意だべ",
                        "空気つめてぇ、重ね着してあったまろう",
                        "放射冷却で冷え込みやすいから気をつけてな",
                    ],
                },
                "cloudy": {
                    "hot": [
                        "曇ってても蒸すべ、こまめに水分な",
                        "日差し弱めでも暑いっちゃ、無理すんなよ",
                        "風通し良い服装がいいべ",
                    ],
                    "warm": [
                        "雲多めで動きやすい日だな、今のうちに片付けよう",
                        "熱こもりにくくて楽だべ、外回りもしやすい",
                        "穏やかな曇りで、作業はかどるっちゃ",
                    ],
                    "cool": [
                        "ひんやり曇り、羽織一枚あると助かるべ",
                        "日差しないぶん体感低いな、首元あっためよ",
                        "長居はほどほどだべな",
                    ],
                    "cold": [
                        "曇りで底冷えするべ、防寒しっかりな",
                        "温かい飲み物で体あっためていぐべ",
                        "暗くなるといっそう寒いがら、早めに帰ろう",
                    ],
                },
                "rain": {
                    "hot": [
                        "蒸し雨だべ、通気性いいレインが楽だな",
                        "汗と雨で冷えやすいっちゃ、タオルと替えシャツ持ってくべ",
                        "小雨の合間みて動くといいべ",
                    ],
                    "warm": [
                        "雨でも気温高め、ムレ対策しとくと楽だべ",
                        "荷物は撥水が安心だな、傘忘れんなよ",
                        "出入りでムワッとするから、体調気をつけてな",
                    ],
                    "cool": [
                        "ひんやり雨、傘は必須だべ",
                        "濡れると冷えるがら、タオル一枚持ってくといい",
                        "屋内の逃げ場つくっておくと安心だべ",
                    ],
                    "cold": [
                        "冷たい雨だべ、手袋と防水の靴が役立つな",
                        "体温奪われやすいっちゃ、無理しないでいこう",
                        "風ある日は合羽があると違うべ",
                    ],
                },
                "snow": {
                    "hot": [
                        "めずらしい雪の条件だべ、足元最優先でな",
                        "気温高めでも雪は要注意だっちゃ",
                        "状況変わりやすいから慎重にいぐべ",
                    ],
                    "warm": [
                        "みぞれ気味で足元わるい、はねに気をつけてな",
                        "解けかけで滑りやすいっちゃ、歩幅ちいさめに",
                        "濡れ冷えしやすいから体温管理しっかりな",
                    ],
                    "cool": [
                        "小雪でひんやり、足元注意だべ",
                        "視界白っぽいから横断は慎重にいこう",
                        "靴が濡れると冷えるっちゃ、替え靴下あると安心だべ",
                    ],
                    "cold": [
                        "凍結こわいべ、時間に余裕持って動こう",
                        "転倒注意だっちゃ、滑りにくい靴でな",
                        "日没後は危険度上がるから寄り道控えめに",
                    ],
                },
            },

            "chugoku": {
                "sunny": {
                    "hot": [
                        "よう暑いで、こまめに水分と日陰で休みんさい",
                        "日差しがきついけぇ、帽子と日焼け対策しんさい",
                        "無理はせんほうがええ、涼しいとこ使いんさい",
                    ],
                    "warm": [
                        "ええ晴れじゃ、用事はさくさく進むで",
                        "過ごしやすい陽気じゃけぇ、洗濯日和じゃの",
                        "動くなら今がちょうどええ時間よ",
                    ],
                    "cool": [
                        "ひんやり晴れじゃ、羽織が一枚あると安心よ",
                        "日差しあっても体感は低いけぇ、服で調整しんさい",
                        "朝晩は冷えるけぇ、油断せんようにな",
                    ],
                    "cold": [
                        "快晴でも冷えるで、手袋やマフラー持っときんさい",
                        "空気がつめたいけぇ、重ね着で温もろうや",
                        "放射冷却で冷え込みやすいけぇ気ぃつけんさい",
                    ],
                },
                "cloudy": {
                    "hot": [
                        "曇っとっても蒸し暑いで、こまめに水分な",
                        "日差し弱うても暑いけぇ、無理せんように",
                        "風通しのええ服装で行きんさい",
                    ],
                    "warm": [
                        "雲多めで動きやすいわ、今のうちに片付けんさい",
                        "熱こもりにくうて楽じゃわ、外回りもしやすいで",
                        "穏やかな曇りで作業はかどるのう",
                    ],
                    "cool": [
                        "ひんやり曇りじゃ、羽織一枚あると助かるで",
                        "日差しないぶん体感低いけぇ、首元温めんさい",
                        "長居はほどほどがええで",
                    ],
                    "cold": [
                        "曇りで底冷えするわ、防寒しっかりしんさい",
                        "温かい飲みもんで体を温めんさい",
                        "暗うなると一段と寒いけぇ、早めに帰りんさい",
                    ],
                },
                "rain": {
                    "hot": [
                        "蒸し雨じゃ、通気性ええレインが楽よ",
                        "汗と雨で冷えやすいけぇ、タオルと替えシャツあると安心じゃ",
                        "小雨の合間を上手に使いんさい",
                    ],
                    "warm": [
                        "雨でも気温は高めじゃ、ムレ対策しんさい",
                        "荷物は撥水が安心よ、傘も忘れんさんな",
                        "出入りでムワッとするけぇ、体調に気ぃつけんさい",
                    ],
                    "cool": [
                        "ひんやり雨じゃ、傘は必須よ",
                        "濡れると冷えるけぇ、タオル一枚持っとくとええ",
                        "屋内の逃げ場つくっとくと安心じゃ",
                    ],
                    "cold": [
                        "冷たい雨じゃ、手袋と防水靴が役立つで",
                        "体温もっていかれやすいけぇ、無理せんのがええ",
                        "風ある日は合羽があると違うけぇの",
                    ],
                },
                "snow": {
                    "hot": [
                        "珍しい雪の条件じゃ、足元最優先で行きんさい",
                        "気温高めでも雪は要注意よ",
                        "状況変わりやすいけぇ、慎重にな",
                    ],
                    "warm": [
                        "みぞれ気味で足元悪いけぇ、水はね注意しんさい",
                        "解けかけで滑りやすいけぇ、歩幅は小さめにな",
                        "濡れ冷えしやすいけぇ、体温管理しんさい",
                    ],
                    "cool": [
                        "小雪まじりでひんやりじゃ、足元気ぃつけんさい",
                        "視界白うなるけぇ、横断は慎重にの",
                        "靴が濡れると冷えるけぇ、替え靴下あると安心よ",
                    ],
                    "cold": [
                        "凍結が怖いけぇ、時間に余裕持って動きんさい",
                        "転倒注意じゃ、滑りにくい靴と手袋でな",
                        "日没後は危険度上がるけぇ、寄り道控えめがええ",
                    ],
                },
            },

            "kyushu": {
                "sunny": {
                    "hot": [
                        "めっちゃ暑かね、水分と日陰休憩ば忘れんごとね",
                        "日差し強かけん、帽子と日焼け対策もしとったがよかよ",
                        "無理せんで、涼しか所うまく使うとよかばい",
                    ],
                    "warm": [
                        "気持ちよか晴れたい、用事がはかどるね",
                        "過ごしやすか陽気やけん、洗濯日和たい",
                        "動くなら今がちょうどよかね",
                    ],
                    "cool": [
                        "ひんやり晴れやけん、羽織一枚あると安心たい",
                        "日差しあっても体感は低めやけん、服で調整しよ",
                        "朝晩は冷えるけん、油断せんごと",
                    ],
                    "cold": [
                        "快晴でも冷えるばい、手袋やマフラーが役立つたい",
                        "空気がつめたか、重ね着して温もろうね",
                        "放射冷却で冷え込みやすかけん、気をつけんね",
                    ],
                },
                "cloudy": {
                    "hot": [
                        "曇っとっても蒸し暑か、こまめに水分ばい",
                        "日差し弱うても暑かけん、無理は禁物たい",
                        "風通しのよか服装でいこ",
                    ],
                    "warm": [
                        "雲多めで動きやすか、今のうちに片付けよ",
                        "熱こもりにくくて楽たい、外回りもしやすかね",
                        "穏やかな曇りで作業はかどるばい",
                    ],
                    "cool": [
                        "ひんやり曇りやけん、羽織一枚あるとよかよ",
                        "日差しないぶん体感低か、首元温めると楽たい",
                        "長居はほどほどにしとこ",
                    ],
                    "cold": [
                        "曇りで底冷えするばい、防寒しっかりね",
                        "温かい飲み物で体ば温めていこう",
                        "暗うなると一段と寒かけん、早めに帰ろ",
                    ],
                },
                "rain": {
                    "hot": [
                        "蒸し雨たい、通気性のよかレインが楽ばい",
                        "汗と雨で冷えやすかけん、タオルと替えシャツあると安心たい",
                        "小雨の合間ば上手に使おうね",
                    ],
                    "warm": [
                        "雨でも気温は高めたい、ムレ対策しとくとよかよ",
                        "荷物は撥水が安心やけん、傘も忘れんごと",
                        "出入りでムワッとするけん、体調に気をつけてね",
                    ],
                    "cool": [
                        "ひんやり雨やけん、傘は必須たい",
                        "濡れると冷えるけん、タオル一枚あると助かるばい",
                        "屋内の逃げ場つくっとくと安心たい",
                    ],
                    "cold": [
                        "冷たい雨ばい、手袋と防水靴が役立つよ",
                        "体温持っていかれやすかけん、無理せんごと",
                        "風ある日は合羽があると違うたい",
                    ],
                },
                "snow": {
                    "hot": [
                        "珍しか雪の条件やけん、足元最優先たい",
                        "気温高めでも雪は要注意ばい",
                        "状況変わりやすかけん、慎重に動こ",
                    ],
                    "warm": [
                        "みぞれ気味で足元わるかね、水はね注意たい",
                        "解けかけで滑りやすかけん、歩幅は小さめで",
                        "濡れ冷えしやすかけん、体温管理しとこう",
                    ],
                    "cool": [
                        "小雪でひんやりたい、足元気をつけんね",
                        "視界が白っぽかけん、横断は慎重に",
                        "靴が濡れると冷えるたい、替え靴下あると安心ばい",
                    ],
                    "cold": [
                        "凍結が怖か、時間に余裕持って動こうね",
                        "転倒注意やけん、滑りにくい靴と手袋で",
                        "日没後は危険度上がるけん、寄り道控えめがよか",
                    ],
                },
            },
        }

    def _build_tails(self):
        return {
            "kanto": {
                "morning": [
                    "朝のうちに動けることを進めよう",
                    "通勤前に準備を整えておこう",
                    "朝は無理せず体を慣らしていこう",
                ],
                "day": [
                    "日中はこまめに休憩ね",
                    "外の用事は今のうちに片付けよう",
                    "昼は水分と小休止を意識して",
                ],
                "evening": [
                    "夕方は早めに切り上げると安心だよ",
                    "日没前に移動を終えよう",
                    "夕方は混みやすいから余裕を持ってね",
                ],
                "night": [
                    "夜は見通しが悪いから安全第一で",
                    "帰り道は足元と車に注意してね",
                    "夜は冷えやすいから早めに帰ろう",
                ],
            },
            "kansai": {
                "morning": [
                    "朝のうちにササッと進めよか",
                    "通勤前に準備だけ整えとこ",
                    "朝は無理せんと体慣らしていこ",
                ],
                "day": [
                    "日中はこまめに休憩な",
                    "外の用事は今のうちに片付けよ",
                    "昼は水分と小休止わすれんといて",
                ],
                "evening": [
                    "夕方は早めに切り上げるんが安心やで",
                    "日没前に移動は終わらせとこ",
                    "夕方は混みがちやし、余裕持ってな",
                ],
                "night": [
                    "夜は見通し悪いし安全第一でな",
                    "帰り道は足元と車に気ぃつけてや",
                    "夜は冷えやすいし、早めに帰ろか",
                ],
            },
            "tohoku": {
                "morning": [
                    "朝のうちにできること進めっぺ",
                    "通勤前に準備ととのえような",
                    "朝は無理しねで体慣らしていこう",
                ],
                "day": [
                    "日中はこまめに休憩すっぺ",
                    "外の用事は今のうちに片付けよう",
                    "昼は水分と小休止いれっぺ",
                ],
                "evening": [
                    "夕方は早めに切り上げると安心だべ",
                    "日没前に移動終わらせような",
                    "夕方は混みがちだがら余裕持ってな",
                ],
                "night": [
                    "夜は見通し悪いから安全第一だべ",
                    "帰り道は足元と車さ注意してな",
                    "夜は冷えやすいがら早めに帰ろう",
                ],
            },
            "chugoku": {
                "morning": [
                    "朝のうちにできること進めんさい",
                    "通勤前に準備を整えんさい",
                    "朝は無理せんと体慣らしていこ",
                ],
                "day": [
                    "日中はこまめに休憩しんさい",
                    "外の用事は今のうちに片付けんさい",
                    "昼は水分と小休止を意識しんさい",
                ],
                "evening": [
                    "夕方は早めに切り上げるんが安心よ",
                    "日没前に移動を終えんさい",
                    "夕方は混みやすいけぇ余裕を持っての",
                ],
                "night": [
                    "夜は見通し悪いけぇ安全第一で",
                    "帰り道は足元と車に気ぃつけんさい",
                    "夜は冷えやすいけぇ早めに帰りんさい",
                ],
            },
            "kyushu": {
                "morning": [
                    "朝のうちにできること進めとこ",
                    "通勤前に準備ば整えとこう",
                    "朝は無理せんで体慣らしていこ",
                ],
                "day": [
                    "日中はこまめに休憩しとこうね",
                    "外の用事は今のうちに片付けよう",
                    "昼は水分と小休止、忘れんごとね",
                ],
                "evening": [
                    "夕方は早めに切り上げた方が安心たい",
                    "日没前に移動は済ませとこう",
                    "夕方は混みやすかけん、余裕持って動こ",
                ],
                "night": [
                    "夜は見通し悪かけん安全第一たい",
                    "帰り道は足元と車に気をつけんね",
                    "夜は冷えやすかけん早めに帰ろ",
                ],
            },
        }

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
        return None, None, "場所が見つかりませんでした。別の表記でもう一度試してね。"
    data = await fetch_forecast(session, geo["latitude"], geo["longitude"], geo["timezone"])
    if not data or "hourly" not in data:
        return geo, None, "天気データの取得に失敗しました。時間をおいて再度お試しください。"

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
        return geo, None, "直近3時間のデータが見つかりませんでした。"
    return geo, rows, None

# ---------- Message handling ----------
MENTION_PATTERN = re.compile(r"<@!?(\d+)>")

def extract_query_from_message(content: str, bot_id: int) -> str | None:
    m = MENTION_PATTERN.search(content)
    if not m: return None
    if int(m.group(1)) != bot_id: return None
    rest = MENTION_PATTERN.sub("", content, count=1).strip()
    return rest or None

engine = CommentEngine()

def build_comment(rows: list[dict], place: dict) -> str:
    weather_key = categorize_weather(rows)    # sunny/cloudy/rain/snow
    temp_key    = categorize_temp(rows)       # hot/warm/cool/cold
    time_key    = categorize_time(rows)       # morning/day/evening/night
    dialect     = pick_dialect_key(place)     # kanto/kansai/tohoku/chugoku/kyushu
    has_thunder = any(r["weathercode"] in (95,96,99) for r in rows)
    return engine.get(dialect, weather_key, temp_key, time_key, has_thunder)

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
                await message.reply(ensure_aa(err), mention_author=False); return
            embed = build_embed(place, rows)
            try:
                comment = build_comment(rows, place)
            except Exception as e:
                print(f"[WARN] comment build failed: {e}")
                comment = ensure_aa("今日は無理せず、安全第一でいこう")
            await message.reply(content=comment, embed=embed, mention_author=False)

@client.tree.command(name="weather", description="地名・ランドマーク名から直近3時間の天気を表示します")
@app_commands.describe(location="地名/ランドマーク（例：大阪, USJ, 東京ディズニーランド）")
async def weather(interaction: discord.Interaction, location: str):
    await interaction.response.defer(thinking=True)
    async with aiohttp.ClientSession() as session:
        place, rows, err = await get_next_3_hours(session, location)
        if err:
            await interaction.followup.send(ensure_aa(err), ephemeral=True); return
        embed = build_embed(place, rows)
        try:
            comment = build_comment(rows, place)
        except Exception as e:
            print(f"[WARN] comment build failed: {e}")
            comment = ensure_aa("今日は無理せず、安全第一でいこう")
        await interaction.followup.send(content=comment, embed=embed)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")
    client.run(BOT_TOKEN)

if __name__ == "__main__":
    main()
