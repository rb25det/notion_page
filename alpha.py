import os
import copy
from datetime import datetime, timedelta
from notion_client import Client
from dotenv import load_dotenv

# --- 環境変数とクライアント設定 ---
load_dotenv()
notion = Client(auth=os.getenv("NOTION_TOKEN"))
PARENT_PAGE_ID = os.getenv("PARENT_PAGE_ID")

# --- 日付処理ヘルパー ---
def format_day(d): return d.strftime("%m%d")

def get_week_range_str(base_date):
    monday = base_date - timedelta(days=base_date.weekday())
    sunday = monday + timedelta(days=6)
    return f"{format_day(monday)}-{format_day(sunday)}", monday, sunday

# --- 1. 今週・前週のタイトル計算 ---
today = datetime.today()
this_week_title, this_monday, _ = get_week_range_str(today)
last_week_title, _, _ = get_week_range_str(this_monday - timedelta(days=1))

print(f"今週: {this_week_title} / 前週: {last_week_title}")

# --- 2. 前週ページのIDを取得 ---
def find_child_page_by_title(parent_id, title):
    children = notion.blocks.children.list(parent_id, page_size=100)["results"]
    for block in children:
        if block["type"] == "child_page" and block["child_page"]["title"] == title:
            return block["id"]
    return None

last_page_id = find_child_page_by_title(PARENT_PAGE_ID, last_week_title)

# --- 3. 前週のMonthly TASKブロックを探して中身をコピー ---
monthly_task_toggle_block = None

if last_page_id:
    blocks = notion.blocks.children.list(last_page_id, page_size=100)["results"]
    for block in blocks:
        if block["type"] == "toggle":
            rich_texts = block["toggle"].get("rich_text", [])
            for rt in rich_texts:
                if rt["type"] == "text" and rt["text"]["content"].strip() == "Monthly TASK":
                    toggle_id = block["id"]
                    # 中身を取得
                    children = notion.blocks.children.list(toggle_id, page_size=100)["results"]
                    # deepcopyして構造を保持
                    copied_children = [copy.deepcopy(b) for b in children]
                    # トグルブロックを生成
                    monthly_task_toggle_block = {
                        "object": "block",
                        "type": "toggle",
                        "toggle": {
                            "rich_text": [
                                {"type": "text", "text": {"content": "Monthly TASK"}}
                            ],
                            "children": copied_children
                        }
                    }
                    print("✅ 前週のMonthly TASKをコピーしました")
                    break

if not monthly_task_toggle_block:
    # 空のMonthly TASK
    monthly_task_toggle_block = {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [
                {"type": "text", "text": {"content": "Monthly TASK"}}
            ],
            "children": []
        }
    }
    print("ℹ️ 前週にMonthly TASKが見つからなかったので空で作成")

# --- 4. テンプレートブロックを使って1週間分のブロック生成 ---
TEMPLATE_PAGE_ID = "235337f925e580578bc8c08d97a868b0"
REPLACE_TEXT = "XXXX"

template_blocks = notion.blocks.children.list(
    block_id=TEMPLATE_PAGE_ID, page_size=100
)["results"]

# 今週の7日分の日付
dates = [this_monday + timedelta(days=i) for i in range(7)]

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

# --- 5. ページ作成（Monthly TASKを先頭に） ---
first_100 = [monthly_task_toggle_block] + all_blocks[:99]
response = notion.pages.create(
    parent={"page_id": PARENT_PAGE_ID},
    properties={
        "title": [{"type": "text", "text": {"content": this_week_title}}]
    },
    children=first_100
)

page_id = response["id"]
print(f"✅ 今週ページ作成完了 → {response['url']}")

# --- 6. 残りのブロックを追加（必要に応じて） ---
remaining = all_blocks[99:]
for i in range(0, len(remaining), 100):
    chunk = remaining[i:i+100]
    notion.blocks.children.append(block_id=page_id, children=chunk)
    print(f"🔧 追記: ブロック {i+1}〜{i+len(chunk)}")
