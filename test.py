from notion_client import Client
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

# 環境変数から認証情報とURLを読み込む
load_dotenv()
token = os.getenv("NOTION_TOKEN")
parent_url = os.getenv("PARENT_PAGE_URL")

notion = Client(auth=token)

# URLからページIDを抽出（ハイフンなし形式に）
def extract_page_id(url):
    return url.split("/")[-1].replace("-", "")

parent_id = extract_page_id(parent_url)

# 今週の月曜〜日曜の日付を取得
today = datetime.today()
start_of_week = today - timedelta(days=today.weekday())  # 月曜
end_of_week = start_of_week + timedelta(days=6)          # 日曜

# 表示用に「0708」みたいな形式で
def format_day(d): return d.strftime("%m%d")

# ページタイトル：例）0708-0714
title = f"{format_day(start_of_week)}-{format_day(end_of_week)}"

# 各日付のブロック（callout + 予定・TODO・メモ）を作る関数
def create_day_blocks(date):
    return [
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": format_day(date)},
                        "annotations": {
                            "color": "blue_background"
                        }
                    }
                ]
            }
        },
        *[
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": label}
                        }
                    ]
                }
            }
            for label in ["予定", "TODO", "メモ"]
        ]
    ]

# 7日分のブロックをまとめて作る
children_blocks = []
for i in range(7):
    day = start_of_week + timedelta(days=i)
    children_blocks.extend(create_day_blocks(day))

# Notionページを作成
response = notion.pages.create(
    parent={"page_id": parent_id},
    properties={
        "title": [{"type": "text", "text": {"content": title}}]
    },
    children=children_blocks
)

print("✅ テンプレページ作成成功！→", response["url"])
