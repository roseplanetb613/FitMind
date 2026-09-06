# -*- coding: utf-8 -*-
"""
build_name_zh.py — 食物名中文词表生成
=====================================
读取 data/foods_core.json，按「术语组合」策略翻译食品名：
  1. 逗号/括号/斜杠分段；段优先整体匹配（PHRASES），未中则按词匹配（TERMS）
  2. 翻译原则：确认的才翻；拿不准/品牌名保留英文（错译比不译更有害）
  3. 制备态词前置（raw→生，cooked→熟/烹制），主词段按原文顺序拼接
  4. 输出 data/name_zh.json（键=小写英文名），并输出覆盖率报告

频率驱动的词典（词表来自 name_survey.py top 段 + 常见食物）。
用法：python scripts/build_name_zh.py
"""
import json
import re
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# ---------------- 整体段优先匹配（高频复合段） ----------------
PHRASES = {
    "separable lean and fat": "肥瘦兼有",
    "separable lean only": "纯瘦肉",
    "trimmed to 1/8' fat": "去脂至1/8寸",
    'trimmed to 1/8" fat': "去脂至1/8寸",
    'trimmed to 0" fat': "去零脂",
    "all grades": "全等级",
    "2% reduced fat milk": "2%低脂奶",
    "1% lowfat milk": "1%低脂奶",
    "nonfat milk": "脱脂奶",
    "whole milk": "全脂奶",
    "100% juice": "纯果汁",
    "100% whole wheat": "全麦",
    "apple cinnamon": "苹果肉桂",
    "apple cider": "苹果酒",
    "barbecue sauce": "烧烤酱",
    "bbq sauce": "烧烤酱",
    "chicken base": "鸡精汤底",
    "beef broth": "牛肉汤",
    "chicken broth": "鸡汤",
    "vegetable broth": "蔬菜汤",
    "cream of mushroom": "蘑菇奶油汤",
    "with salt": "含盐",
    "without salt": "无盐",
    "no salt added": "未加盐",
    "low sodium": "低钠",
    "reduced sodium": "减钠",
    "no sugar added": "无添加糖",
    "sugar free": "无糖",
    "high fiber": "高纤维",
    "high protein": "高蛋白",
    "net carbs": "净碳水",
    "added sugar": "添加糖",
}

# ---------------- 词级词典 ----------------
TERMS = {
    # 肉/家禽 __ 动物词（置于"肉"语境）
    "beef": "牛肉", "pork": "猪肉", "chicken": "鸡肉", "turkey": "火鸡肉",
    "lamb": "羊羔肉", "veal": "小牛肉", "duck": "鸭肉", "goose": "鹅肉",
    "quail": "鹌鹑肉", "rabbit": "兔肉", "venison": "鹿肉", "bison": "野牛肉",
    "guineafowl": "珍珠鸡肉", "buffalo": "水牛肉", "goat": "山羊肉",
    "ham": "火腿", "bacon": "培根", "sausage": "香肠", "sausages": "香肠",
    "salami": "萨拉米", "pepperoni": "意大利辣香肠", "hot dog": "热狗",
    "wieners": "法兰克福肠", "bologna": "博洛尼亚香肠", "bratwurst": "德式香肠",
    "chorizo": "西班牙辣肠", "jerky": "肉干", "pemmican": "干肉饼",
    # 部位/形态
    "loin": "里脊", "tenderloin": "牛柳", "ribeye": "肋眼", "rib": "肋排", "ribs": "肋排",
    "sirloin": "西冷", "flank": "牛腩侧", "skirt": "横膈膜肉", "brisket": "牛胸肉",
    "round": "圆腿肉", "chuck": "牛肩肉", "shoulder": "肩肉", "shank": "腱子肉",
    "breast": "胸肉", "thigh": "腿肉", "drumstick": "鸡腿", "wing": "鸡翅",
    "wings": "鸡翅", "leg": "腿", "legs": "腿", "feet": "爪", "giblets": "内脏杂件",
    "liver": "肝脏", "heart": "心脏", "kidney": "肾脏", "tongue": "舌",
    "stomach": "胃", "brain": "脑", "tripe": "牛肚", "chop": "排骨", "chops": "排骨",
    "steak": "牛排", "cutlet": "肉排", "cutlets": "肉排", "roast": "烤肉块",
    "stew": "炖肉", "ground": "绞肉", "minced": "碎肉", "patty": "肉饼", "nuggets": "鸡块",
    "links": "香肠串", "seasoned": "调味", "marinated": "腌渍", "battered": "裹面糊",
    "breaded": "裹面包糠", "boneless": "去骨", "bone-in": "带骨", "skinless": "去皮",
    "with skin": "带皮", "lean": "瘦", "extra lean": "极瘦", "fatty": "肥",
    "dark meat": "深色肉", "white meat": "白肉",
    # 鱼类/海鲜
    "fish": "鱼", "salmon": "三文鱼", "tuna": "金枪鱼", "trout": "鳟鱼",
    "cod": "鳕鱼", "halibut": "大比目鱼", "tilapia": "罗非鱼", "sardine": "沙丁鱼",
    "anchovy": "鳀鱼", "mackerel": "鲭鱼", "herring": "鲱鱼", "catfish": "鲶鱼",
    "bass": "鲈鱼", "snapper": "鲷鱼", "flounder": "比目鱼", "sole": "舌鳎",
    "perch": "河鲈", "carp": "鲤鱼", "caviar": "鱼子酱", "roe": "鱼卵",
    "oyster": "牡蛎", "clam": "蛤蜊", "mussel": "贻贝", "scallop": "扇贝",
    "shrimp": "虾", "prawns": "大虾", "crab": "螃蟹", "lobster": "龙虾",
    "squid": "鱿鱼", "octopus": "章鱼", "crawfish": "小龙虾", "abalone": "鲍鱼",
    "surimi": "蟹肉棒", "roe": "鱼卵",
    # 乳制品
    "milk": "奶", "cheese": "奶酪", "cheddar": "切达", "mozzarella": "马苏里拉",
    "parmesan": "帕玛森", "swiss": "瑞士奶酪", "provolone": "波罗伏洛", "feta": "菲达",
    "gouda": "高达", "blue cheese": "蓝纹奶酪", "brie": "布里", "camembert": "卡门贝尔",
    "american cheese": "美式奶酪", "cream cheese": "奶油奶酪", "cottage cheese": "茅屋奶酪",
    "yogurt": "酸奶", "butter": "黄油", "margarine": "人造黄油", "cream": "奶油",
    "sour cream": "酸奶油", "buttermilk": "酪乳", "condensed milk": "炼乳",
    "evaporated milk": "淡奶", "whey": "乳清", "casein": "酪蛋白",
    "breyers": "布莱尔斯", "half and half": "半淡奶油",
    # 蛋类
    "egg": "鸡蛋", "eggs": "鸡蛋", "egg white": "蛋白", "egg yolk": "蛋黄",
    "omelet": "蛋卷", "quiche": "乳蛋饼", "meringue": "蛋白霜",
    # 蔬果
    "apple": "苹果", "apples": "苹果", "banana": "香蕉", "orange": "橙子",
    "strawberry": "草莓", "grape": "葡萄", "grapes": "葡萄", "pear": "梨",
    "peach": "桃子", "plum": "李子", "cherry": "樱桃", "cherries": "樱桃",
    "blueberry": "蓝莓", "raspberry": "树莓", "blackberry": "黑莓", "cranberry": "蔓越莓",
    "watermelon": "西瓜", "cantaloupe": "哈密瓜", "honeydew": "蜜瓜", "melon": "甜瓜",
    "pineapple": "菠萝", "mango": "芒果", "kiwi": "猕猴桃", "avocado": "牛油果",
    "papaya": "木瓜", "pomegranate": "石榴", "lemon": "柠檬", "lime": "青柠",
    "coconut": "椰子", "olive": "橄榄", "fig": "无花果", "date": "椰枣", "dates": "椰枣",
    "raisin": "葡萄干", "prune": "西梅干", "apricot": "杏", "grapefruit": "西柚",
    "guava": "番石榴", "lychee": "荔枝", "tangerine": "柑橘", "clementine": "蜜柑",
    "raisins": "葡萄干", "fruit": "水果", "juice": "果汁", "cider": "果汁酒",
    "tomato": "番茄", "tomatoes": "番茄", "potato": "土豆", "potatoes": "土豆",
    "carrot": "胡萝卜", "carrots": "胡萝卜", "onion": "洋葱", "onions": "洋葱",
    "broccoli": "西兰花", "cauliflower": "花菜", "cabbage": "卷心菜",
    "lettuce": "生菜", "spinach": "菠菜", "cucumber": "黄瓜", "pepper": "辣椒",
    "bell pepper": "灯笼椒", "celery": "芹菜", "zucchini": "西葫芦",
    "squash": "南瓜", "pumpkin": "南瓜", "sweet potato": "红薯", "yam": "山药",
    "corn": "玉米", "pea": "豌豆", "peas": "豌豆", "green bean": "四季豆",
    "green beans": "四季豆", "asparagus": "芦笋", "mushroom": "蘑菇", "mushrooms": "蘑菇",
    "artichoke": "洋蓟", "brussels sprout": "抱子甘蓝", "brussels sprouts": "抱子甘蓝",
    "garlic": "大蒜", "ginger": "姜", "turnip": "芜菁", "radish": "萝卜",
    "beet": "甜菜", "beets": "甜菜", "eggplant": "茄子", "leek": "韭葱",
    "parsnip": "欧防风", "okra": "秋葵", "kale": "羽衣甘蓝", "collard": "芥蓝菜",
    "mustard greens": "芥菜", "chard": "瑞士甜菜", "endive": "菊苣", "arugula": "芝麻菜",
    "watercress": "西洋菜", "seaweed": "海藻", "spirulina": "螺旋藻",
    "vegetable": "蔬菜", "veggie": "蔬菜", "vegetables": "蔬菜",
    "mixed vegetables": "什锦蔬菜",
    # 谷物/烘焙/主食
    "bread": "面包", "bagel": "贝果", "muffin": "松饼", "croissant": "牛角包",
    "tortilla": "墨西哥饼", "pita": "皮塔饼", "roll": "面包卷", "bun": "圆面包",
    "pancake": "煎饼", "pancakes": "煎饼", "waffle": "华夫饼", "toast": "吐司",
    "cracker": "苏打饼干", "crackers": "苏打饼干", "pretzel": "碱水饼",
    "cookie": "曲奇", "cookies": "曲奇", "biscuit": "饼干", "cake": "蛋糕",
    "brownie": "布朗尼", "donut": "甜甜圈", "pie": "派", "pastry": "酥皮点心",
    "croissant": "牛角包", "granola": "格兰诺拉", "cereal": "麦片", "oatmeal": "燕麦粥",
    "oats": "燕麦", "oat": "燕麦", "wheat": "小麦", "barley": "大麦",
    "rice": "米饭", "brown rice": "糙米", "pasta": "意面", "noodle": "面条",
    "noodles": "面条", "spaghetti": "意面", "macaroni": "通心粉", "lasagna": "千层面",
    "flour": "面粉", "cornmeal": "玉米粉", "semolina": "粗粒小麦粉", "rye": "黑麦",
    "quinoa": "藜麦", "couscous": "库斯库斯", "buckwheat": "荞麦", "millet": "小米",
    "amaranth": "苋米", "grain": "谷物", "grains": "谷物", "bran": "麸皮",
    "germ": "胚芽", "puff": "膨化米", "popcorn": "爆米花", "chip": "薯片",
    "chips": "薯片", "crisps": "薯片", "tortilla chips": "墨西哥玉米片",
    # 豆类/坚果/种子
    "bean": "豆", "beans": "豆", "soybean": "大豆", "black bean": "黑豆",
    "pinto bean": "斑豆", "garbanzo": "鹰嘴豆", "chickpea": "鹰嘴豆",
    "kidney bean": "芸豆", "lentil": "扁豆", "edamame": "毛豆", "hummus": "鹰嘴豆泥",
    "tofu": "豆腐", "tempeh": "天贝", "miso": "味噌", "nut": "坚果", "nuts": "坚果",
    "almond": "杏仁", "almonds": "杏仁", "peanut": "花生", "peanuts": "花生",
    "walnut": "核桃", "cashew": "腰果", "pistachio": "开心果", "pecan": "山核桃",
    "hazelnut": "榛子", "macadamia": "夏威夷果", "chestnut": "板栗", "sesame": "芝麻",
    "sunflower seed": "葵花籽", "flaxseed": "亚麻籽", "chia": "奇亚籽",
    "pumpkin seed": "南瓜籽", "seed": "种子", "seeds": "种子", "paste": "糊",
    "butter": "泥",
    # 饮品类（常用段整体）
    "coffee": "咖啡", "tea": "茶", "soda": "汽水", "cola": "可乐",
    "water": "水", "mineral water": "矿泉水", "beer": "啤酒", "wine": "葡萄酒",
    "whiskey": "威士忌", "rum": "朗姆酒", "brandy": "白兰地", "cocktail": "鸡尾酒",
    "smoothie": "果昔", "shake": "奶昔", "milk shake": "奶昔", "lemonade": "柠檬水",
    "cider": "果汁酒", "energy drink": "能量饮料", "sports drink": "运动饮料",
    "drink": "饮品", "beverage": "饮品", "beverages": "饮品", "powder": "粉剂",
    # 调味/油/糖/其他
    "sauce": "酱", "sauces": "酱", "ketchup": "番茄酱", "mustard": "芥末酱",
    "mayonnaise": "蛋黄酱", "relish": "调味酱菜", "gravy": "肉汁", "vinaigrette": "油醋汁",
    "dressing": "沙拉酱", "dip": "蘸酱", "salsa": "莎莎酱", "pesto": "青酱",
    "honey": "蜂蜜", "syrup": "糖浆", "molasses": "糖蜜", "jam": "果酱",
    "jelly": "果冻", "preserves": "蜜饯", "oil": "油", "olive oil": "橄榄油",
    "coconut oil": "椰子油", "canola oil": "菜籽油", "sunflower oil": "葵花籽油",
    "vegetable oil": "植物油", "vinegar": "醋", "salt": "盐", "sugar": "糖",
    "powdered sugar": "糖粉", "brown sugar": "红糖", "splenda": "代糖",
    "sweetener": "甜味剂", "spice": "香料", "spices": "香料", "seasoning": "调味料",
    "mix": "混合粉", "baking powder": "泡打粉", "baking soda": "小苏打",
    "yeast": "酵母", "gelatin": "明胶", "marshmallow": "棉花糖", "candy": "糖果",
    "chocolate": "巧克力", "caramel": "焦糖", "nougat": "牛轧糖", "toffee": "太妃糖",
    "gum": "口香糖", "ice cream": "冰淇淋", "sorbet": "雪葩", "gelato": "意式冰淇淋",
    "yogurt": "酸奶", "pudding": "布丁", "custard": "卡仕达", "batter": "面糊",
    "soup": "汤", "stew": "炖菜", "chili": "辣豆酱", "burrito": "卷饼",
    "taco": "塔可", "casserole": "焗菜", "fries": "薯条", "saltine": "咸饼干",
    "pizza": "披萨", "sandwich": "三明治", "wrap": "卷", "burger": "汉堡",
    "meatball": "肉丸", "meatballs": "肉丸", "pie crust": "派皮", "tortillas": "墨西哥饼",
    # 制备态/属性（置于名前）
    "cooked": "熟", "raw": "生", "roasted": "烤制", "boiled": "水煮", "broiled": "炙烤",
    "grilled": "烧烤", "fried": "油炸", "baked": "烘烤", "steamed": "蒸制",
    "braised": "焖炖", "sauteed": "嫩煎", "stir-fried": "爆炒", "smoked": "烟熏",
    "cured": "腌制", "fermented": "发酵", "dried": "干制", "dehydrated": "脱水",
    "frozen": "冷冻", "canned": "罐装", "pickled": "醋渍", "salted": "盐渍",
    "unsalted": "无盐", "fresh": "新鲜", "chilled": "冷藏", "prepared": "即食",
    "processed": "加工", "instant": "速食", "ready-to-eat": "即食", "ready to eat": "即食",
    "refrigerated": "冷藏", "heat processed": "热加工", "pasteurized": "巴氏杀菌",
    "homogenized": "均质", "seasoned": "调味", "plain": "原味", "original": "原味",
    "regular": "常规", "light": "轻", "lite": "轻", "heavy": "重", "thick": "浓稠",
    "thin": "稀", "large": "大", "small": "小", "medium": "中", "extra": "特",
    "whole": "整粒", "whole grain": "全谷物", "rolled": "压片", "flaked": "片状",
    "puffed": "膨化", "shredded": "切丝", "grated": "擦丝", "diced": "切丁",
    "chopped": "切碎", "sliced": "片状", "crushed": "压碎", "minced": "切末",
    "blanched": "焯水", "pitted": "去核", "seeded": "去籽", "chunky": "大块",
    "creamy": "浓郁", "mild": "温和", "sharp": "浓郁", "sharp cheddar": "浓郁切达",
    "aged": "陈年", "american": "美式", "italian": "意式", "mexican": "墨式",
    "asian": "亚洲风味", "chinese": "中式", "japanese": "日式", "indian": "印式",
    "french": "法式", "greek": "希腊式", "moroccan": "摩洛哥式", "thai": "泰式",
    "organic": "有机", "natural": "天然", "artificial": "人工", "vegan": "纯素",
    "vegetarian": "素食", "gluten-free": "无麸质", "gluten free": "无麸质",
    "lactose-free": "无乳糖", "dairy-free": "无乳制品", "fat-free": "脱脂",
    "fat free": "脱脂", "low fat": "低脂", "lowfat": "低脂", "reduced fat": "减脂",
    "flavored": "风味", "unflavored": "无味", "vanilla": "香草", "strawberry": "草莓",
    "chocolate": "巧克力", "lemon": "柠檬", "orange": "橙味", "raspberry": "树莓",
    "apple": "苹果味", "cherry": "樱桃味", "coffee": "咖啡味", "mint": "薄荷",
    "cinnamon": "肉桂", "cocoa": "可可", "maple": "枫糖", "butterscotch": "奶油太妃糖味",
    "oatmeal": "燕麦味", "cheese-flavored": "奶酪味", "garlic": "蒜味",
    "barbecue": "烧烤味", "bbq": "烧烤味", "teriyaki": "照烧", "ranch": "牧场酱味",
    "honey": "蜂蜜味", "peanut butter": "花生酱", "tuna": "金枪鱼", "chicken": "鸡肉味",
    # 常用尾词/其他
    "snack": "零食", "snacks": "零食", "meal": "餐", "entree": "主菜",
    "side": "配菜", "appetizer": "开胃菜", "breakfast": "早餐", "lunch": "午餐",
    "dinner": "晚餐", "dessert": "甜点", "babyfood": "婴幼儿辅食", "formula": "奶粉",
    "infant": "婴儿", "toddler": "幼儿", "adult": "成人", "animal": "动物用",
    "pet": "宠物", "generic": "通用", "prescription": "处方",
    "liquid": "液体", "solid": "固体", "concentrate": "浓缩", "reconstituted": "复原",
    "dry": "干", "wet": "湿", "premium": "优质", "imitation": "仿制", "substitute": "替代品",
    "meatless": "无肉", "plant-based": "植物基", "grain-based": "谷物基底",
    "dairy-based": "乳基底", "soy-based": "大豆基底", "coconut-based": "椰浆基底",
}

# 某些词是多义词，按语境修正（"butter" 已在 TERMS 二次定义，保留后义"泥"用于 peanut butter 场景）
TERMS.pop("butter")  # 争议词：牛奶黄油 vs 花生泥，统一译"黄油"，复合词由 PHRASES 处理
TERMS["butter"] = "黄油"
TERMS["peanut butter"] = "花生酱"

# 第二批：等级/产地/常见复合词（name_survey 高频段补充）
TERMS.update({
    "choice": "精选", "select": "优选", "prime": "特级", "standard": "标准级",
    "utility": "次级", "commercial": "商用级", "canner": "罐装级", "cutter": "切割级",
    "imported": "进口", "domestic": "国产", "farmed": "养殖", "wild": "野生",
    "atlantic": "大西洋", "pacific": "太平洋", "gulf": "海湾",
    "new zealand": "新西兰", "australian": "澳洲", "canadian": "加拿大",
    "greek": "希腊式", "jumbo": "特大", "mini": "迷你", "bite size": "一口大小",
    "english muffin": "英式松饼", "taco shell": "塔可皮", "mixed nuts": "什锦坚果",
    "assorted": "什锦", "american style": "美式", "italian style": "意式",
    "mexican style": "墨式", "oriental": "东方风味", "soul food": "南方风味",
    "baltimore": "巴尔的摩", "boston": "波士顿", "california": "加州",
    "florida": "佛罗里达", "georgia": "佐治亚", "idaho": "爱达荷",
    "maine": "缅因", "maryland": "马里兰", "minnesota": "明尼苏达",
    "new york": "纽约", "texas": "德克萨斯", "virginia": "弗吉尼亚",
    "washington": "华盛顿", "hawaiian": "夏威夷", "puerto rican": "波多黎各",
    "cuban": "古巴式", "cajun": "卡津", "creole": "克里奥尔", "southern": "南方式",
    "kosher": "洁食", "halal": "清真", "unleavened": "无酵", "sourdough": "酸面团",
    "yeasted": "酵头", "stuffed": "填充", "stuffed with": "填充", "stuffed with cheese": "奶酪馅",
    "pork rinds": "猪皮脆", "grits": "玉米糁", "hominy": "碱化玉米",
    "polenta": "玉米糊", "jambalaya": "什锦饭", "gumbo": "秋葵浓汤",
    "enchilada": "恩奇拉达", "taquito": "玉米卷", "chimichanga": "炸卷饼",
    "soft serve": "软冰淇淋", "sherbet": "雪贝", "topping": "淋酱", "syrup topping": "糖浆淋酱",
    "granita": "格兰尼塔", "frappe": "奶昔冰饮", "milkshake": "奶昔",
    "tonic water": "汤力水", "sparkling water": "苏打水", "seltzer": "苏打水",
    "club soda": "苏打水", "root beer": "根汁汽水", "energy": "能量",
    "nutritional yeast": "营养酵母", "soy sauce": "酱油", "worcestershire": "伍斯特酱",
    "hoisin": "海鲜酱", "sriracha": "是拉差酱", "tartar": "塔塔酱", "aioli": "蒜味蛋黄酱",
    "hollandaise": "荷兰酱", "marinara": "意式番茄酱", "alfredo": "白酱",
    "gorgonzola": "戈尔贡佐拉", "parmesan cheese": "帕玛森奶酪", "ricotta": "里科塔",
    "munster": "芒斯特", "colby": "科尔比", "monterey jack": "蒙特雷杰克",
    "pepper jack": "胡椒杰克", "jack cheese": "杰克奶酪", "american cheese": "美式奶酪",
    "string cheese": "奶酪条", "queso": "芝士酱", "tempura": "天妇罗",
    "sushi": "寿司", "sashimi": "刺身", "bibimbap": "石锅拌饭", "pad thai": "泰式炒河粉",
})


# 制备态词（译文前置："生牛肉"而非"牛肉生"）
PREP = {"熟", "生", "烤制", "水煮", "炙烤", "烧烤", "油炸", "烘烤", "蒸制", "焖炖",
        "嫩煎", "爆炒", "烟熏", "腌制", "发酵", "干制", "脱水", "冷冻", "罐装",
        "醋渍", "盐渍", "无盐", "新鲜", "冷藏", "即食", "加工", "速食", "巴氏杀菌",
        "均质", "调味", "原味", "含盐", "无盐", "去零脂", "去脂至1/8寸", "低钠",
        "减钠", "未加盐", "无添加糖", "无糖"}


def translate_segment(seg: str) -> tuple[list[str], list[str]]:
    """返回 (命中词典的译文列表, 未译词列表)。非字母段（数字/非英文）不参与翻译。"""
    s = seg.strip().lower()
    if not s:
        return [], []
    if s in PHRASES:
        return [PHRASES[s]], []
    toks = re.findall(r"[a-z][a-z\-']*", s)
    if not toks:
        return [], []
    hits, miss = [], []
    i, n = 0, len(toks)
    while i < n:
        matched = False
        for L in (3, 2, 1):          # 最长组合优先
            if i + L <= n:
                ph = " ".join(toks[i:i + L])
                if ph in TERMS:
                    hits.append(TERMS[ph])
                    i += L
                    matched = True
                    break
        if not matched:
            miss.append(toks[i])
            i += 1
    return hits, miss


def translate(name: str) -> tuple[str, int]:
    """返回 (中文名, 状态)。状态: 2=全译 1=部分译(附英文) 0=保持原文。"""
    segs = [s for s in re.split(r"[,()/]", name) if s.strip()]
    hits, miss = [], []
    for seg in segs:
        h, m = translate_segment(seg)
        hits += h
        miss += m
    if not hits:
        return name, 0
    # 制备态词前置，其余按出现顺序
    prep, rest = [], []
    for h in hits:
        (prep if h in PREP else rest).append(h)
    out = "".join(prep + rest)
    if miss:
        return f"{out}({' '.join(miss)})", 1
    return out, 2


def main():
    foods = json.load(open(DATA / "foods_core.json", encoding="utf-8"))["foods"]
    seen = {}
    stats = Counter()
    for f in foods:
        key = f["name"].strip().lower()
        if not key or key in seen:
            continue
        zh, status = translate(f["name"])
        seen[key] = zh
        stats[status] += 1
        if status == 0:
            stats["kept_english"] += 1

    with open(DATA / "name_zh.json", "w", encoding="utf-8") as fh:
        json.dump(seen, fh, ensure_ascii=False, indent=1)

    n = len(seen)
    print(f"唯一名: {n}（已全覆盖输出 name_zh.json）")
    print(f"全译: {stats[2]} ({stats[2]/n:.1%})  部分译: {stats[1]} ({stats[1]/n:.1%})  "
          f"保持英文: {stats[0]} ({stats[0]/n:.1%})")
    # 预览
    for k in list(seen)[:8]:
        print(f"  {k} -> {seen[k]}")
    # 未翻译样本
    kept = [(k, k) for k, v in seen.items() if v == k][:10]
    print("保持英文样例:", [a for a, _ in kept])


if __name__ == "__main__":
    main()