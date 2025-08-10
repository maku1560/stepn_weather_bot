# STEPN Weather Bot v2025-08-10-10
# 機能: 直近3時間の天気 + 矛盾なしコメント(天気×気温×時間帯) + 強風追記
#       方言スキン（中立→方言化） + AA顔文字（頻度UP） + 柔らか語尾
# 安全化: comments欠落時でも落ちないフォールバック、エラー文も方言＆AA

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
BOT_VERSION = "2025-08-10-10"
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
        return "thunder"
    if any(c in (71,73,75,77,85,86) for c in codes):
        return "snow"
    if any(c in (51,53,55,61,63,65,66,67,80,81,82) for c in codes):
        return "rain"
    if all(c in (0,1,2) for c in codes):
        return "clear"
    return "cloudy"

def categorize_temp(rows):
    m = max(r["temp"] for r in rows)
    if m < 10: return "cold"
    if m < 20: return "cool"
    if m < 28: return "warm"
    return "hot"

def categorize_time(rows):
    h = rows[0]["time"].hour
    if 5 <= h <= 9:  return "morning"
    if 10 <= h <= 15: return "noon"
    if 16 <= h <= 18: return "evening"
    return "night"

# ---------- 矛盾なしコメント辞書（各3本・ベース関西→後で中立化→方言化） ----------
comments = {
    "clear": {
        "cold": {
            "morning": [
                "快晴やけど朝は冷えるで、手袋あると安心や",
                "空はキレイやのに空気はキンと冷たいな、暖かくしていこ",
                "日差しはあるけど体は冷える、重ね着しとこ",
            ],
            "noon": [
                "晴れやけど空気は冷たい、日向を選んで歩こ",
                "日差しで体感は少しマシや、上着はまだ要るで",
                "風邪ひかんように、太陽の下で温まってこ",
            ],
            "evening": [
                "夕方からまた冷え戻るで、帰りの一枚は忘れずにな",
                "日没で一気に寒なる、早めに帰ろ",
                "放射冷却で冷えやすい、油断禁物や",
            ],
            "night": [
                "夜は放射冷却でグッと冷える、厚着しときや",
                "星空は綺麗やけど寒いで、寄り道少なめでな",
                "帰りは手先から冷える、ポケットにカイロ仕込んどこ",
            ],
        },
        "cool": {
            "morning": [
                "ひんやり晴れの朝や、軽い上着でちょうどええな",
                "空気スッとして気持ちええ、朝活はかどるで",
                "陽を浴びて体起こしていこ",
            ],
            "noon": [
                "晴れて歩きやすい、外回りは今がチャンスや",
                "洗濯も乾きやすいで、用事片付けよ",
                "日向と日陰で体感差あるから、服装は調整できると楽や",
            ],
            "evening": [
                "夕方は少し冷え戻るで、一枚あると安心や",
                "西日がまぶしい時間や、眩しさ対策もありやな",
                "帰り道は風が出るかも、マフラー軽く巻いとこ",
            ],
            "night": [
                "夜はひんやり、帰りは早めに切り上げよ",
                "星がよう見える夜や、でも体は冷えるで",
                "静かな夜やな、足元と車にだけ注意や",
            ],
        },
        "warm": {
            "morning": [
                "ぽかぽか晴れ、気持ちよくスタート切れそうや",
                "朝から外出も快適、軽装でいけるで",
                "日差し柔らかいわ、散歩にぴったりや",
            ],
            "noon": [
                "昼は過ごしやすい晴れ、外の用事どんどん片付けよ",
                "洗濯＆布団干し日和や、チャンスやで",
                "公園日和やな、日陰でぼーっとするのもアリや",
            ],
            "evening": [
                "夕方は空がきれい、気温も穏やかでええ感じや",
                "日没前は涼しなって快適や、寄り道も悪くないで",
                "夕焼け狙いで写真もええな",
            ],
            "night": [
                "夜も穏やか、軽めの羽織で十分や",
                "星空見に行くのもアリやな、帰り道だけ注意やで",
                "快適な夜や、用事は今のうちに片付けよ",
            ],
        },
        "hot": {
            "morning": [
                "朝から暑いで、水分ちょいちょい補給な",
                "日差し強めや、日焼け止め忘れんといて",
                "通勤は日陰ルートがおすすめや",
            ],
            "noon": [
                "ギラギラ晴れ、日陰休憩＆水分はマストやで",
                "熱中症注意、帽子あると楽や",
                "冷たい飲み物を相棒にいこか",
            ],
            "evening": [
                "夕方でも暑さ残るで、無理せんとゆっくり帰ろ",
                "西日の熱がきつい、サングラスあると楽やな",
                "日没後に少しマシ、用事はそのタイミングで",
            ],
            "night": [
                "夜も蒸し暑いな、風通しのええ服で快適に",
                "寝苦しさ対策で水分＆冷房うまく使お",
                "遅い時間でも暑い、無理は禁物やで",
            ],
        },
    },
    "cloudy": {
        "cold": {
            "morning": [
                "曇りで余計に寒い、厚手の上着でいこか",
                "日差しがない分ひんやりや、手袋もありやで",
                "体起きにくい冷えや、ゆっくりウォームアップな",
            ],
            "noon": [
                "曇り空＆冷たい空気、屋内メインでもええかもな",
                "日陰みたいな体感や、首元温めると楽やで",
                "風が出たらさらに冷える、マフラー準備しとこ",
            ],
            "evening": [
                "夕方は冷え込むで、帰りは足早にいこ",
                "曇天で暗なるの早い、ライトと足元注意な",
                "寄り道少なめで体温逃がさんように",
            ],
            "night": [
                "夜は底冷えや、温かい飲みもんでほっこりしよ",
                "曇りやと星は見えんけど、防寒はしっかりな",
                "冷える夜やで、帰宅ルートは安全第一で",
            ],
        },
        "cool": {
            "morning": [
                "ひんやり曇り、軽い羽織で快適に動けるで",
                "運動にはちょうどええ体感や、無理なくいこ",
                "朝は視界暗め、横断は余裕見てな",
            ],
            "noon": [
                "日差し弱めで動きやすい、用事は今のうちに",
                "過ごしやすい体感や、散歩も作業も捗るわ",
                "洗濯は乾き遅め、部屋干しも視野やな",
            ],
            "evening": [
                "夕方からは肌寒くなる、帰りの一枚は忘れずに",
                "暗くなるの早いで、反射素材あると安心や",
                "寄り道しすぎず、安全に帰ろか",
            ],
            "night": [
                "夜はひんやり、風邪ひかんよう温かくしてな",
                "視界が暗い分ゆっくり歩こ",
                "帰宅は足元注意、滑りやすい路面もあるで",
            ],
        },
        "warm": {
            "morning": [
                "温度はちょうどええ、曇りで眩しくないのが助かるな",
                "軽装でOK、動きやすい朝やで",
                "外に出る準備はかどる気温や",
            ],
            "noon": [
                "作業日和や、屋外でも負担少なめ",
                "曇りで熱こもりにくい、効率よく動けるで",
                "洗濯は少し乾き遅いから余裕見とこ",
            ],
            "evening": [
                "夕方は気温ちょうどええ、帰り道も快適や",
                "日没前に済ませたい用事は今やな",
                "ちょい風あればさらに心地ええで",
            ],
            "night": [
                "夜も穏やか、薄手の羽織で十分や",
                "視界は暗め、足元と車にだけ注意しよ",
                "帰りにちょっと寄り道もアリやな",
            ],
        },
        "hot": {
            "morning": [
                "朝から蒸し暑い、無理せんペースでいこ",
                "曇ってても紫外線は来る、対策はしとこ",
                "日陰は少しマシや、ルート工夫しよ",
            ],
            "noon": [
                "雲多めでも暑さは続く、水分＆休憩な",
                "湿気で体力削られる、涼しい所で小休止や",
                "クールタオルあると助かるで",
            ],
            "evening": [
                "夕方も蒸し暑さ残る、用事は分割していこ",
                "西日弱い分マシやけど、体力配分は注意な",
                "風通しのええ道を選ぶと楽やで",
            ],
            "night": [
                "夜もむわっと暑い、寝る前に水分と室温調整や",
                "帰宅後はしっかりクールダウンしよ",
                "遅い時間の外出は短めに、無理は禁物や",
            ],
        },
    },
    "rain": {
        "cold": {
            "morning": [
                "冷たい雨や、傘とあったか装備でいこ",
                "手がかじかむで、手袋あると助かるわ",
                "靴も防水が安心や、足元気ぃつけてな",
            ],
            "noon": [
                "昼も冷たい雨、屋内メインで動くのが賢いで",
                "濡れると体温奪われる、無茶せんとこ",
                "合羽があると快適度だいぶ違うで",
            ],
            "evening": [
                "夕方は暗くて足元危ない、寄り道少なめで",
                "体も冷えてくる時間や、早めの撤収推奨やで",
                "横殴りなら傘＋フードが安心や",
            ],
            "night": [
                "夜の雨は視界悪い、帰りはゆっくり安全第一",
                "冷たい雨やから帰宅後はしっかり温もろ",
                "路面すべりやすいで、気ぃつけや",
            ],
        },
        "cool": {
            "morning": [
                "ひんやり雨、傘は必須やで",
                "通勤は水たまり回避でいこ",
                "レインカバーあると荷物が助かるな",
            ],
            "noon": [
                "昼もパラつくかも、合間見て動こうか",
                "濡れたら体冷える、タオル一枚あると安心",
                "屋内に逃げ場を作っとくと楽やで",
            ],
            "evening": [
                "夕方は交通混みがち、余裕を持って動こ",
                "ライトの反射で見えにくい、横断は慎重にな",
                "雨脚強まったら計画変更もアリや",
            ],
            "night": [
                "夜は視界さらに悪い、傘＋反射材で安全に",
                "寄り道せず帰宅優先やな",
                "濡れた靴は翌朝に響くで、帰ったら乾燥や",
            ],
        },
        "warm": {
            "morning": [
                "暖かい雨で蒸し気味、通気のええ服が楽や",
                "傘＋タオルで快適度キープしよ",
                "ムレ対策で速乾素材がええで",
            ],
            "noon": [
                "雨でも気温は高め、こまめに水分も忘れずに",
                "屋内と屋外の出入りでムワッとする、体調に気ぃつけて",
                "荷物は撥水だと安心や",
            ],
            "evening": [
                "夕方は雨足に注意、強まったら予定見直しも",
                "湿気で体力奪われる、無理せんと帰ろ",
                "足元のすべりと車の水はね要注意や",
            ],
            "night": [
                "夜の雨は静かやけど油断禁物、視界悪いで",
                "帰りは傘きっちり差してな",
                "濡れたら体冷える、帰宅後は温かい飲みもんで回復や",
            ],
        },
        "hot": {
            "morning": [
                "暑い＋雨＝ムシムシや、汗＆雨対策ダブルでいこ",
                "レインウェアは通気性重視でな",
                "日差し弱い分マシやけど蒸すで、無理は禁物",
            ],
            "noon": [
                "蒸し暑い雨、休憩挟んで体力温存や",
                "汗冷えしやすい、タオルと替えシャツあると最高",
                "合間の小雨タイミングを活用しよ",
            ],
            "evening": [
                "夕方も蒸し蒸し、帰りはゆっくりペースでな",
                "路面の水たまり要注意や、靴選び大事やで",
                "湿気で疲れ出やすい、早めの撤収ありや",
            ],
            "night": [
                "夜の蒸し雨、寝る前は室温と湿度の調整しよ",
                "帰宅後はシャワーでリセットすると楽や",
                "傘は忘れず乾かしてな、明日も使えるように",
            ],
        },
    },
    "snow": {
        "cold": {
            "morning": [
                "雪で路面ツルツル、歩幅小さめでいこ",
                "冷たい朝や、手袋＆厚手の靴下で武装や",
                "転倒注意、時間には余裕持ってな",
            ],
            "noon": [
                "昼も冷えた雪空、屋外は短時間で済ませよ",
                "溶けかけ雪が滑る、足元よう見てな",
                "体温奪われやすい、温かい飲みもん休憩入れよ",
            ],
            "evening": [
                "夕方の凍結に注意や、早めの帰宅が吉",
                "日没で一段と危険、階段・横断特に慎重に",
                "雪かき後は冷えるで、しっかり防寒な",
            ],
            "night": [
                "夜は凍みる寒さ、外は最小限でいこ",
                "視界が悪い雪夜や、反射材あると安心やで",
                "帰宅したら温もって身体いたわろ",
            ],
        },
        "cool": {
            "morning": [
                "小雪まじりでひんやり、足元だけは気ぃつけて",
                "路面濡れて冷たい、滑らんようにね",
                "手先冷えやすい、ポケットにカイロ忍ばせとこ",
            ],
            "noon": [
                "昼は小休止の雪、用事はまとめて片付けよ",
                "視界が白っぽい、横断は慎重に",
                "濡れた靴は冷えるで、替え靴下あると安心",
            ],
            "evening": [
                "夕方は凍結の準備段階、寄り道控えめで",
                "気温下がると滑る、帰宅時間は余裕持って",
                "手袋と耳当てあると助かるで",
            ],
            "night": [
                "夜は再凍結が怖い、最短ルートで帰ろ",
                "静かな雪夜やけど足元は油断せんといて",
                "道路の端が凍りやすい、踏まんようにな",
            ],
        },
        "warm": {
            "morning": [
                "みぞれ気味でベチャつく、撥水の靴が安心や",
                "解けかけで滑るで、歩幅は小さめに",
                "濡れ冷えしやすい、体温管理な",
            ],
            "noon": [
                "昼は解けて足元グチャ、ルート選び大事やで",
                "水はね注意、裾が汚れやすいで",
                "用事は短時間でササッと済ませよ",
            ],
            "evening": [
                "夕方にまた冷え戻る、解け水が凍る前に帰ろ",
                "濡れた靴は冷えるで、予備持てたらベスト",
                "視界悪いとこは無理せんことや",
            ],
            "night": [
                "夜はみぞれも凍るで、外出はほどほどに",
                "帰ったら靴とコートをしっかり乾かそ",
                "冷え込みに備えて温かいもので体力回復や",
            ],
        },
        "hot": {
            "morning": ["珍しいコンディションや、足元最優先でな"]*3,
            "noon": ["無理せんで、安全第一や"]*3,
            "evening": ["帰りは早めに動こ"]*3,
            "night": ["外は必要最低限でな"]*3,
        },
    },
    "thunder": {
        "cold": {
            "morning": [
                "雷の可能性＋冷え込み、外は最小限でいこ",
                "寒さと雷はダブルでキツい、建物の中で待機が吉",
                "音がしたら近くの屋内へ、無理は禁物や",
            ],
            "noon": [
                "雷注意、用事は屋内中心で組み直そ",
                "稲光見えたら即退避、長居はせんことや",
                "金属製の長い物は持ち歩かんほうが安心やで",
            ],
            "evening": [
                "夕方は暗さも相まって危険度↑、早め撤収や",
                "雷鳴近いなら屋外活動は中止やで",
                "傘より建物、身を守るんが先や",
            ],
            "night": [
                "夜の雷は視界最悪、外出は控えめに",
                "音が続くなら窓から離れて過ごそ",
                "停電対策でライトの場所も確認しとこ",
            ],
        },
        "cool": {
            "morning": [
                "雷の気配や、外に出るときは空チェックな",
                "雨風強まる前に要件済ませとこ",
                "無理に外での作業はせんほうがええで",
            ],
            "noon": [
                "雷注意、屋内メインに切り替えよ",
                "稲光見えたら即撤収、身の安全優先や",
                "移動は短距離で、長居は避けよ",
            ],
            "evening": [
                "夕方は危険度上がる、建物の近くで行動や",
                "ゴロゴロ聞こえたら予定変更やで",
                "帰り道は寄り道せんと一直線でな",
            ],
            "night": [
                "夜は視界悪い＋雷、外は最小限にしよ",
                "安全第一、窓と電源周りも注意や",
                "空が荒れとる日は早めに休むが勝ちや",
            ],
        },
        "warm": {
            "morning": [
                "暖かいけど雷は危険、屋外は控えめに",
                "朝のうちに室内でできること進めよ",
                "傘よりまず避難場所、意識しとこ",
            ],
            "noon": [
                "雷鳴ったら屋内退避、用事は一時中断やで",
                "雲の動き速い、空の色チェックな",
                "金属や高い場所は避けるんやで",
            ],
            "evening": [
                "夕方は雷雨の一発来やすい、時間ずらすのもアリ",
                "暗くなる前に移動を終わらせよ",
                "安全なルートを優先して帰ろ",
            ],
            "night": [
                "夜の雷はほんま危ない、外出は必要最小限で",
                "光ったら数える間もなくゴロッと来る距離かも、屋内へ",
                "念のため懐中電灯も用意しとこ",
            ],
        },
        "hot": {
            "morning": [
                "暑くても雷は危険、外より屋内優先や",
                "水分は屋内で取りつつ様子見しよ",
                "空が黒かったら予定変更やで",
            ],
            "noon": [
                "雷雨注意、モールや駅ビルに逃げ場作っとこ",
                "稲光見えたら外出中断！",
                "暑さよりまず安全第一や",
            ],
            "evening": [
                "夕方の前線通過は荒れやすい、早帰り推奨や",
                "無理せず予定は後日に回そ",
                "寄り道はやめて一直線で帰宅や",
            ],
            "night": [
                "夜間の雷はリスク高め、家で大人しくしよ",
                "停電の備えだけして、早めに休むのが吉や",
                "窓とベランダは締めとこな",
            ],
        },
    },
}

# ---------- 方言スキン ----------
DIALECT_PACKS = {
    "kansai":   {"intense":["めっちゃ","ようさん","だいぶ"], "end":["や","で","やで","やな","やわ"]},
    # ★東京はやわらか語尾を厚めに
    "tokyo":    {"intense":["すごく","かなり","けっこう"],   "end":["だよ","だね","だよね","かな","かもね","だなあ","だねぇ"]},
    "nagoya":   {"intense":["でら","どえりゃあ","ようけ"],   "end":["だで","だがね","だわ"]},
    "hokkaido": {"intense":["なまら","わや","たっけ"],       "end":["だべさ","だっしょ","でないかい"]},
    "tohoku":   {"intense":["いっぺぇ","だいぶ","わんつか"],  "end":["だべ","だっちゃ","だな"]},
    "hiroshima":{"intense":["ぶち","たいぎいくらい","ぎょうさん"], "end":["じゃけぇ","しんさい","なんよ"]},
    "hakata":   {"intense":["ばり","とっとーと","ようけ"],     "end":["っちゃ","ばい","たい"]},
    "okinawa":  {"intense":["とても","かなり","ちゅらい"],     "end":["さー","ねー","よー"]},
}

def pick_dialect_key(place: dict) -> str:
    pref = (place.get("admin1") or "").strip()
    if pref in ["大阪府","京都府","兵庫県","滋賀県","奈良県","和歌山県"]:
        return "kansai"
    if pref in ["東京都","神奈川県","千葉県","埼玉県"]:
        return "tokyo"
    if pref in ["愛知県","岐阜県","三重県"]:
        return "nagoya"
    if pref == "北海道":
        return "hokkaido"
    if pref in ["青森県","岩手県","宮城県","秋田県","山形県","福島県"]:
        return "tohoku"
    if pref in ["広島県","岡山県","山口県","鳥取県","島根県"]:
        return "hiroshima"
    if pref in ["福岡県","佐賀県","長崎県","熊本県","大分県","宮崎県","鹿児島県"]:
        return "hakata"
    if pref == "沖縄県":
        return "okinawa"
    # 未知は“中立”の東京
    return "tokyo"

# ---------- 関西→標準 正規化 → 方言スキン適用 + 柔らか砂糖 ----------
KANSAI_TO_NEUTRAL = [
    (r"無理せんと", "無理せず"),
    (r"気ぃつけ", "気をつけ"),
    (r"しよな", "しようね"),
    (r"しよか", "しようか"),
    (r"しよ", "しよう"),
    (r"やろ", "だろう"),
    (r"せんとこ", "しないでおこう"),
    (r"せんと", "しないと"),
    (r"帰ろ(?!う)", "帰ろう"),
]

SOFT_TAILS = ["よね", "かな", "ね〜", "なあ", "かも", "かもね", "よ〜", "ねぇ"]

def neutralize(text: str) -> str:
    def drop_kansai_tail(s: str) -> str:
        return re.sub(r"(やで|やな|やわ|や|で)$", "", s)
    sents = [t.strip() for t in re.split(r"。+", text) if t.strip()]
    out = []
    for s in sents:
        s = drop_kansai_tail(s)
        for pat, rep in KANSAI_TO_NEUTRAL:
            s = re.sub(pat, rep)
        out.append(s)
    return "。".join(out) + "。"

def add_soft_tail(text: str, prob=0.6):
    if random.random() >= prob:
        return text
    sents = [s.strip() for s in re.split(r"。+", text) if s.strip()]
    if not sents:
        return text
    i = random.randrange(len(sents))
    # 既に終助詞があれば足さない
    if re.search(r"(よ|ね|な|かも|たい|ばい|じゃけぇ|さー|よー|ねー)$", sents[i]):
        return "。".join(sents) + "。"
    sents[i] = sents[i] + random.choice(SOFT_TAILS)
    return "。".join(sents) + "。"

def dialectize(text: str, key: str) -> str:
    # 1) 中立化
    text = neutralize(text)
    # 2) スキン適用
    pack = DIALECT_PACKS.get(key, DIALECT_PACKS["tokyo"])
    for base in ["めっちゃ","すごく","かなり","けっこう","だいぶ"]:
        text = re.sub(re.escape(base), random.choice(pack["intense"]), text)

    # 末尾付与は断定文だけ（依頼・勧誘には付けない）
    def tweak_sent(s):
        s = s.strip()
        if not s:
            return s
        if re.search(r"(しよう|よう|ろう|てね|ください|してね|して|しまおう|しなきゃ|しましょう|くださいね|しようね|しまおうね)$", s):
            return s
        if re.search(r"[!?！？]$", s):
            return s
        end = random.choice(pack["end"])
        s = re.sub(r"(です|だ|ね|よ|わ)$", "", s)
        return s + end

    sentences = [tweak_sent(s) for s in re.split(r"。+", text) if s.strip()]
    out = "。".join(sentences) + "。"
    # 3) やわらか砂糖
    return add_soft_tail(out, prob=0.6)

# ---------- AA ----------
AA = ["|ω・)", "(/ω＼)", "( ´ ▽ ` )", "(￣▽￣;)", "(｀・ω・´)", "( ˘ω˘ )", "(｡･ω･｡)", "(；・∀・)", "(・∀・)", "(>_<)"]
def maybe_aa(p=0.85):  # ★頻度UP
    return (" " + random.choice(AA)) if random.random() < p else ""

# ---------- 安全にコメントを拾う（フォールバック） ----------
def pick_safe_comment(w: str, t: str, d: str) -> str:
    weather_order = {
        "thunder": ["thunder","rain","cloudy","clear"],
        "snow":    ["snow","cloudy","clear"],
        "rain":    ["rain","cloudy","clear"],
        "cloudy":  ["cloudy","clear"],
        "clear":   ["clear","cloudy"],
    }
    temp_keys  = ["cold","cool","warm","hot"]
    time_keys  = ["morning","noon","evening","night"]

    for wkey in weather_order.get(w, ["cloudy","clear"]):
        wdict = comments.get(wkey)
        if not wdict:
            continue
        for tkey in [t] + [k for k in temp_keys if k != t]:
            tdict = wdict.get(tkey)
            if not tdict:
                continue
            for dkey in [d] + [k for k in time_keys if k != d]:
                lst = tdict.get(dkey)
                if lst:
                    return random.choice(lst)
    return "今日はわりと無難なコンディション。安全第一でいこう。"

# ---------- コメント生成（安全化） ----------
def build_comment_base(rows: list[dict]) -> str:
    try:
        w = categorize_weather(rows)
        t = categorize_temp(rows)
        d = categorize_time(rows)
        base = pick_safe_comment(w, t, d)
    except Exception as e:
        print(f"[WARN] comment build failed: {e}")
        max_temp = max(r['temp'] for r in rows)
        thunder  = any(r['weathercode'] in (95,96,99) for r in rows)
        rainlike = any(r['weathercode'] in (51,53,55,61,63,65,66,67,80,81,82) for r in rows)
        if thunder:
            base = "雷の可能性がある。外出は気をつけてね"
        elif rainlike:
            base = "雨が来そう。傘があると安心だよ"
        elif max_temp >= 28:
            base = "暑いから水分しっかりね"
        elif max_temp < 10:
            base = "冷えるから暖かくしていこう"
        else:
            base = "今日は過ごしやすそうだよ"

    max_wind = max(r.get("wind", 0.0) for r in rows)
    if max_wind >= 15:
        base += " 風が強すぎるから、帽子や傘は要注意。"
    elif max_wind >= 10:
        base += " 風が強めだから、洗濯物と自転車は気をつけてね。"
    return base

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
                dialect = pick_dialect_key(place or {"admin1": None})
                await message.reply(dialectize(err, dialect) + maybe_aa(), mention_author=False)
                return

            embed = build_embed(place, rows)
            try:
                base = build_comment_base(rows)
                dialect = pick_dialect_key(place)
                comment = dialectize(base, dialect)
                # 最低1個はAA
                if not re.search(r"\(|\||／", comment):
                    comment += maybe_aa()
            except Exception as e:
                print(f"[WARN] dialectize failed: {e}")
                comment = base + maybe_aa()
            await message.reply(content=comment, embed=embed, mention_author=False)

@client.tree.command(name="weather", description="地名・ランドマーク名から直近3時間の天気を表示します")
@app_commands.describe(location="地名/ランドマーク（例：大阪, USJ, 東京ディズニーランド）")
async def weather(interaction: discord.Interaction, location: str):
    await interaction.response.defer(thinking=True)
    async with aiohttp.ClientSession() as session:
        place, rows, err = await get_next_3_hours(session, location)
        if err:
            dialect = pick_dialect_key(place or {"admin1": None})
            await interaction.followup.send(dialectize(err, dialect) + maybe_aa(), ephemeral=True)
            return

        embed = build_embed(place, rows)
        try:
            base = build_comment_base(rows)
            dialect = pick_dialect_key(place)
            comment = dialectize(base, dialect)
            if not re.search(r"\(|\||／", comment):
                comment += maybe_aa()
        except Exception as e:
            print(f"[WARN] dialectize failed: {e}")
            comment = base + maybe_aa()
        await interaction.followup.send(content=comment, embed=embed)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")
    client.run(BOT_TOKEN)

if __name__ == "__main__":
    main()
