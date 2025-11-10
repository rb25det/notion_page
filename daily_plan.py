import os
import copy
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from notion_client import Client
from dotenv import load_dotenv

# =============================================================================
# 設定 / 定数
# =============================================================================

MONTHLY_TASK_TITLE = "Monthly TASK"
REPLACE_TEXT = "XXXX"  # テンプレート中の置換対象
TEMPLATE_PAGE_ID = "235337f925e580578bc8c08d97a868b0"  # 既存のテンプレページ（ブロックの束）
PAGE_SIZE = 100  # Notion APIの1回あたりの上限

# =============================================================================
# 初期化
# =============================================================================

def init_client() -> Tuple[Client, str]:
    load_dotenv()
    token = os.getenv("NOTION_TOKEN")
    parent_id = os.getenv("PARENT_PAGE_ID")
    if not token or not parent_id:
        raise RuntimeError("NOTION_TOKEN / PARENT_PAGE_ID が .env に未設定です。")

    return Client(auth=token), parent_id

# =============================================================================
# 日付ユーティリティ
# =============================================================================

def format_mmdd(d: datetime) -> str:
    return d.strftime("%m%d")

def monday_of(date_: datetime) -> datetime:
    # 月曜始まり
    return date_ - timedelta(days=date_.weekday())

def week_title_and_range(base_date: datetime) -> Tuple[str, datetime, datetime]:
    mon = monday_of(base_date)
    sun = mon + timedelta(days=6)
    return f"{format_mmdd(mon)}-{format_mmdd(sun)}", mon, sun

# =============================================================================
# Notion API ヘルパー（ページネーション/サニタイズ）
# =============================================================================

def paginate_children(notion: Client, block_id: str) -> List[Dict[str, Any]]:
    """任意のブロック配下の全子ブロックを取得（ページネーション対応）"""
    results: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        resp = notion.blocks.children.list(
            block_id=block_id, page_size=PAGE_SIZE, start_cursor=cursor
        )
        results.extend(resp.get("results", []))
        cursor = resp.get("next_cursor")
        if not resp.get("has_more"):
            break
    return results

def sanitize_block_for_create(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    blocks.children.append/create に渡せる形に整形。
    読み取り専用フィールドを取り除き、最小限のスキーマにする。
    """
    b = copy.deepcopy(block)

    # 代表的な読み取り専用フィールドを除去
    for k in [
        "id", "created_time", "last_edited_time", "archived", "has_children",
        "object", "parent", "created_by", "last_edited_by", "in_trash"
    ]:
        b.pop(k, None)

    block_type = b.get("type")
    if not block_type or block_type not in b:
        return b  # 想定外はそのまま返す（後段でスキップされうる）

    # 子ブロックの中にさらに children を含む場合は再帰的に sanitize
    if "children" in b.get(block_type, {}):
        raw_children = b[block_type].get("children", [])
        b[block_type]["children"] = [sanitize_block_for_create(c) for c in raw_children]

    # rich_text などは基本そのままでOK
    return b

def sanitize_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    for blk in blocks:
        cleaned_blk = sanitize_block_for_create(blk)
        # typeが無い/不完全なブロックは落とす
        if "type" in cleaned_blk and cleaned_blk.get(cleaned_blk["type"]):
            cleaned.append(cleaned_blk)
    return cleaned

def clone_block_with_children(notion: Client, block: Dict[str, Any]) -> Dict[str, Any]:
    """
    指定ブロックをサニタイズしつつ、has_children=True の場合は再帰的に子を取得して構築。
    """
    sanitized = sanitize_block_for_create(block)
    block_type = sanitized.get("type")
    block_payload = sanitized.get(block_type) if block_type else None
    if not block_type or not isinstance(block_payload, dict):
        return sanitized

    if block.get("has_children"):
        child_blocks = paginate_children(notion, block["id"])
        block_payload["children"] = clone_blocks_with_children(notion, child_blocks)

    return sanitized

def clone_blocks_with_children(notion: Client, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cloned = []
    for blk in blocks:
        cloned_blk = clone_block_with_children(notion, blk)
        block_type = cloned_blk.get("type")
        if block_type and cloned_blk.get(block_type):
            cloned.append(cloned_blk)
    return cloned

def append_block_tree(
    notion: Client,
    parent_id: str,
    blocks: List[Dict[str, Any]],
) -> None:
    """
    ブロックツリーを Notion 上に再帰的に構築する。
    children を含むブロックは、一旦 children を除いた形で append し、
    付与された ID を用いて子ブロックを追加する。
    """
    if not blocks:
        return

    batch: List[Dict[str, Any]] = []
    child_lists: List[List[Dict[str, Any]]] = []

    for blk in blocks:
        blk_copy = copy.deepcopy(blk)
        block_type = blk_copy.get("type")
        payload = blk_copy.get(block_type)
        children = []
        if isinstance(payload, dict) and "children" in payload:
            children = payload.pop("children") or []
        batch.append(blk_copy)
        child_lists.append(children)

        if len(batch) == PAGE_SIZE:
            resp = notion.blocks.children.append(block_id=parent_id, children=batch)
            created_blocks = resp.get("results", [])
            for created, child_nodes in zip(created_blocks, child_lists):
                if child_nodes:
                    append_block_tree(notion, created["id"], child_nodes)
            batch = []
            child_lists = []

    if batch:
        resp = notion.blocks.children.append(block_id=parent_id, children=batch)
        created_blocks = resp.get("results", [])
        for created, child_nodes in zip(created_blocks, child_lists):
            if child_nodes:
                append_block_tree(notion, created["id"], child_nodes)

# =============================================================================
# ページ/ブロック取得・生成ロジック
# =============================================================================

def find_child_page_by_title(notion: Client, parent_id: str, title: str) -> Optional[str]:
    """親ページ直下の child_page を走査して一致タイトルのページIDを返す"""
    # 親ページの直下ブロック（child_page）をページネートで探索
    children = []
    cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=parent_id, page_size=PAGE_SIZE, start_cursor=cursor
        )
        children.extend(resp.get("results", []))
        cursor = resp.get("next_cursor")
        if not resp.get("has_more"):
            break

    for block in children:
        if block.get("type") == "child_page":
            if block["child_page"].get("title") == title:
                return block["id"]
    return None

def build_monthly_task_toggle_from_last_week(
    notion: Client, last_page_id: Optional[str]
) -> Dict[str, Any]:
    """
    前週ページから Monthly TASK トグルを見つけ、中身を複製して返す。
    見つからなければ空のトグルを返す。
    """
    if not last_page_id:
        return empty_monthly_task_toggle()

    blocks = paginate_children(notion, last_page_id)

    for blk in blocks:
        if blk["type"] != "toggle":
            continue
        rich_text = blk["toggle"].get("rich_text", [])
        title_texts = [rt.get("text", {}).get("content", "").strip() for rt in rich_text if rt.get("type") == "text"]
        if any(t == MONTHLY_TASK_TITLE for t in title_texts):
            # このトグルの子を取得し、サニタイズして貼り付け準備
            toggle_id = blk["id"]
            children = paginate_children(notion, toggle_id)
            copied_children = clone_blocks_with_children(notion, children)

            return {
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": MONTHLY_TASK_TITLE}}],
                    "children": copied_children
                }
            }

    # 見つからなかった場合は空
    return empty_monthly_task_toggle()

def empty_monthly_task_toggle() -> Dict[str, Any]:
    return {
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": MONTHLY_TASK_TITLE}}],
            "children": []
        }
    }

def load_template_blocks(notion: Client, template_page_id: str) -> List[Dict[str, Any]]:
    """テンプレページ直下のブロック群をフル取得（ページネーション対応＆サニタイズ）"""
    raw = paginate_children(notion, template_page_id)
    return sanitize_blocks(raw)

def materialize_week_blocks_from_template(
    template_blocks: List[Dict[str, Any]],
    week_monday: datetime
) -> List[Dict[str, Any]]:
    """
    テンプレブロック群を 7 日分に展開。
    各ブロックの rich_text 内の REPLACE_TEXT を mmdd で置換した複製を作る。
    """
    all_blocks: List[Dict[str, Any]] = []
    for i in range(7):
        day = week_monday + timedelta(days=i)
        day_str = format_mmdd(day)

        for src in template_blocks:
            blk = copy.deepcopy(src)
            btype = blk["type"]
            obj = blk[btype]

            # 代表的なtext系プロパティ（paragraph, heading_x, to_do, toggle, callout, quote, etc.）
            if "rich_text" in obj:
                for rt in obj["rich_text"]:
                    if rt.get("type") == "text":
                        rt["text"]["content"] = rt["text"]["content"].replace(REPLACE_TEXT, day_str)

            # 子を持つテンプレ（例：toggleやcallout内にchildren）も同様に置換（浅い階層に限定）
            if "children" in obj:
                for child in obj["children"]:
                    ctype = child.get("type")
                    if ctype and ctype in child and "rich_text" in child[ctype]:
                        for rt in child[ctype]["rich_text"]:
                            if rt.get("type") == "text":
                                rt["text"]["content"] = rt["text"]["content"].replace(REPLACE_TEXT, day_str)

            all_blocks.append(blk)

    return all_blocks

def create_week_page(
    notion: Client,
    parent_page_id: str,
    title: str,
    monthly_task_toggle: Dict[str, Any],
    content_blocks: List[Dict[str, Any]],
) -> str:
    """
    週次ページを作成し、ブロックを 100件単位で分割して追加。
    先頭に Monthly TASK トグルを配置。
    戻り値: 作成ページID
    """
    resp = notion.pages.create(
        parent={"page_id": parent_page_id},
        properties={"title": [{"type": "text", "text": {"content": title}}]},
        children=[],
    )
    page_id = resp["id"]
    print(f"✅ 今週ページ作成 → {resp['url']}")

    all_blocks = [monthly_task_toggle] + content_blocks
    append_block_tree(notion, page_id, all_blocks)

    return page_id

# =============================================================================
# メインフロー
# =============================================================================

def main() -> None:
    notion, parent_id = init_client()

    # 今週/前週タイトル
    today = datetime.today()
    this_title, this_mon, _ = week_title_and_range(today)
    last_title, _, _ = week_title_and_range(this_mon - timedelta(days=1))
    print(f"今週: {this_title} / 前週: {last_title}")

    # 前週ページ取得 & Monthly TASK 構築
    last_page_id = find_child_page_by_title(notion, parent_id, last_title)
    monthly_toggle = build_monthly_task_toggle_from_last_week(notion, last_page_id)
    if last_page_id:
        print("✅ 前週のMonthly TASKをコピー（または空で生成）")
    else:
        print("ℹ️ 前週ページが見つからないため、空のMonthly TASKを作成")

    # テンプレ読み込み & 7日×展開
    template_blocks = load_template_blocks(notion, TEMPLATE_PAGE_ID)
    week_blocks = materialize_week_blocks_from_template(template_blocks, this_mon)

    # ページ作成
    create_week_page(
        notion=notion,
        parent_page_id=parent_id,
        title=this_title,
        monthly_task_toggle=monthly_toggle,
        content_blocks=week_blocks,
    )

if __name__ == "__main__":
    main()
