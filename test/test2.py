from notion_client import Client
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import copy

# --- Notion認証 ---
load_dotenv()
notion = Client(auth=os.getenv("NOTION_TOKEN"))

# --- 固定設定 ---

REPLACE_TEXT = "XXXX"  # テンプレで置き換え対象になっている日付文字

# --- 日付範囲（今週の月曜～日曜） ---
today = datetime.today()
start_of_week = today - timedelta(days=today.weekday())
dates = [start_of_week + timedelta(days=i) for i in range(7)]

def format_day(d): return d.strftime("%m%d")

# ページタイトル（例：0708-0714）
title = f"{format_day(dates[0])}-{format_day(dates[-1])}"

# --- 1日分のテンプレートブロックを取得 ---
template_blocks = notion.blocks.children.list(
    block_id=TEMPLATE_PAGE_ID,
    page_size=100
)["results"]

print(f"✅ テンプレブロック数（1日分）: {len(template_blocks)}")

# --- 全ブロックを構築（7日分をdeepcopyしてプレースホルダー置換） ---
all_blocks = []

for day in dates:
    day_str = format_day(day)

    for block in template_blocks:
        block_copy = copy.deepcopy(block)
        block_type = block_copy["type"]
        block_obj = block_copy[block_type]

        if "rich_text" in block_obj:
            for rt in block_obj["rich_text"]:
                if rt["type"] == "text":
                    rt["text"]["content"] = rt["text"]["content"].replace(REPLACE_TEXT, day_str)

        all_blocks.append(block_copy)

print(f"✅ 最終的なブロック数（7日分）: {len(all_blocks)}")

# --- 分割して Notion ページに追加（API制限：100件まで） ---
first_100 = all_blocks[:100]
response = notion.pages.create(
    parent={"page_id": PARENT_PAGE_ID},
    properties={
        "title": [{"type": "text", "text": {"content": title}}]
    },
    children=first_100
)

page_id = response["id"]
print(f"✅ ページ作成 → {response['url']}")

# --- 残りを追加（100件ずつ） ---
remaining = all_blocks[100:]
for i in range(0, len(remaining), 100):
    chunk = remaining[i:i+100]
    notion.blocks.children.append(
        block_id=page_id,
        children=chunk
    )
    print(f"🔧 追記: ブロック {i+1}〜{i+len(chunk)}")
