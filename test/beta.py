import os
import copy
from datetime import datetime, timedelta
from notion_client import Client
from dotenv import load_dotenv

# --- 環境設定 ---
load_dotenv()
notion = Client(auth=os.getenv("NOTION_TOKEN"))
PARENT_PAGE_ID = os.getenv("PARENT_PAGE_ID")
TEMPLATE_PAGE_ID = os.getenv("TEMPLATE_PAGE_ID")

# --- 日付関連 ---
def format_day(d): return d.strftime("%m%d")
def get_week_range_str(base_date):
    monday = base_date - timedelta(days=base_date.weekday())
    sunday = monday + timedelta(days=6)
    return f"{format_day(monday)}-{format_day(sunday)}", monday, sunday
def get_month_str(d): return d.strftime("%Y-%m")

today = datetime.today()
this_week_title, this_monday, _ = get_week_range_str(today)
last_week_title, _, _ = get_week_range_str(this_monday - timedelta(days=1))
this_month_title = get_month_str(this_monday)

# --- 子ページ検索 ---
def find_child_page_by_title(parent_id, title):
    children = notion.blocks.children.list(parent_id, page_size=100)["results"]
    for block in children:
        if block["type"] == "child_page" and block["child_page"]["title"] == title:
            return block["id"]
    return None

# --- toggle内のページ検索 ---
def find_page_inside_toggle(toggle_block_id, title):
    children = notion.blocks.children.list(toggle_block_id)["results"]
    for block in children:
        if block["type"] == "child_page" and block["child_page"]["title"] == title:
            return block["id"]
    return None

# --- toggleブロック検索（例："月別"） ---
def find_toggle_block_by_text(parent_id, text):
    blocks = notion.blocks.children.list(parent_id)["results"]
    for block in blocks:
        if block["type"] == "toggle":
            for rt in block["toggle"].get("rich_text", []):
                if rt["type"] == "text" and rt["text"]["content"] == text:
                    return block["id"]
    return None

# --- toggleブロックがなければ作成 ---
def ensure_toggle_block(parent_id, toggle_text):
    toggle_id = find_toggle_block_by_text(parent_id, toggle_text)
    if toggle_id:
        return toggle_id
    block = {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": toggle_text}}],
            "children": []
        }
    }
    res = notion.blocks.children.append(parent_id, children=[block])
    return res["results"][0]["id"]

# --- 月次ページの作成 ---
def create_month_page_under_toggle(toggle_id, month_title):
    page = notion.pages.create(
        parent={"type": "block_id", "block_id": toggle_id},
        properties={"title": [{"type": "text", "text": {"content": month_title}}]},
        children=[
            {
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": "Monthly TASK"}}],
                    "children": []
                }
            }
        ]
    )
    return page["id"]

# --- 前週のMonthly TASKトグルをコピー ---
def copy_monthly_task_from_page(page_id):
    blocks = notion.blocks.children.list(page_id, page_size=100)["results"]
    for block in blocks:
        if block["type"] == "toggle":
            for rt in block["toggle"].get("rich_text", []):
                if rt["type"] == "text" and rt["text"]["content"].strip() == "Monthly TASK":
                    toggle_id = block["id"]
                    children = notion.blocks.children.list(toggle_id)["results"]
                    copied = [copy.deepcopy(b) for b in children]
                    return {
                        "object": "block",
                        "type": "toggle",
                        "toggle": {
                            "rich_text": [{"type": "text", "text": {"content": "Monthly TASK"}}],
                            "children": copied
                        }
                    }
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": "Monthly TASK"}}],
            "children": []
        }
    }

# --- Weeklyテンプレから日付差し替え ---
def generate_week_blocks(template_id, monday):
    dates = [monday + timedelta(days=i) for i in range(7)]
    template_blocks = notion.blocks.children.list(template_id, page_size=100)["results"]
    result = []
    for d in dates:
        day_str = format_day(d)
        for b in template_blocks:
            new_b = copy.deepcopy(b)
            t = new_b[new_b["type"]]
            if "rich_text" in t:
                for rt in t["rich_text"]:
                    if rt["type"] == "text":
                        rt["text"]["content"] = rt["text"]["content"].replace("XXXX", day_str)
            result.append(new_b)
    return result

# --- ✅ 実行開始！ ---
# 1. 前週からMonthly TASKコピー
last_page_id = find_child_page_by_title(PARENT_PAGE_ID, last_week_title)
monthly_task_block = copy_monthly_task_from_page(last_page_id) if last_page_id else {
    "object": "block",
    "type": "toggle",
    "toggle": {
        "rich_text": [{"type": "text", "text": {"content": "Monthly TASK"}}],
        "children": []
    }
}

# 2. 今週ページ作成（先頭にMonthly TASK）
week_blocks = [monthly_task_block] + generate_week_blocks(TEMPLATE_PAGE_ID, this_monday)
first_100 = week_blocks[:100]
page = notion.pages.create(
    parent={"page_id": PARENT_PAGE_ID},
    properties={"title": [{"type": "text", "text": {"content": this_week_title}}]},
    children=first_100
)
page_id = page["id"]
print(f"✅ 今週ページ作成: {this_week_title}")

# 3. 残りのブロックを追記
remaining = week_blocks[100:]
for i in range(0, len(remaining), 100):
    chunk = remaining[i:i+100]
    notion.blocks.children.append(page_id, children=chunk)

# 4. 月別トグル内に月次ページがなければ作成
monthly_toggle_id = ensure_toggle_block(PARENT_PAGE_ID, "月別")
month_page_id = find_page_inside_toggle(monthly_toggle_id, this_month_title)
if not month_page_id:
    month_page_id = create_month_page_under_toggle(monthly_toggle_id, this_month_title)
    print(f"📄 月次ページ作成: {this_month_title}")

# 5. 月次ページに週次ページへのリンク追加
notion.blocks.children.append(month_page_id, children=[
    {
        "object": "block",
        "type": "link_to_page",
        "link_to_page": {"type": "page_id", "page_id": page_id}
    }
])
print(f"🔗 週次ページ {this_week_title} を月次ページに追加")
