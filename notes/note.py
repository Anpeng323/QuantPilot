"""笔记 CRUD"""
import uuid
import json
from datetime import datetime
from etl.db import get_conn


def list_notes(stock_code: str = "", limit: int = 50) -> list:
    sql = "SELECT note_id, stock_code, title, content_md, tags, created_at, updated_at, synced_to_vector FROM note"
    params = []
    if stock_code:
        sql += " WHERE stock_code = ?"
        params.append(stock_code)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    return [{
        "note_id": r[0],
        "stock_code": r[1] or "",
        "title": r[2] or "",
        "content_md": r[3] or "",
        "tags": json.loads(r[4]) if r[4] else [],
        "created_at": str(r[5]) if r[5] else "",
        "updated_at": str(r[6]) if r[6] else "",
        "synced_to_vector": bool(r[7]),
    } for r in rows]


def get_note(note_id: str) -> dict:
    conn = get_conn()
    row = conn.execute("""
        SELECT note_id, stock_code, title, content_md, tags, created_at, updated_at, synced_to_vector
        FROM note WHERE note_id=?
    """, [note_id]).fetchone()
    if not row:
        return {}
    return {
        "note_id": row[0], "stock_code": row[1] or "", "title": row[2] or "",
        "content_md": row[3] or "",
        "tags": json.loads(row[4]) if row[4] else [],
        "created_at": str(row[5]) if row[5] else "",
        "updated_at": str(row[6]) if row[6] else "",
        "synced_to_vector": bool(row[7]),
    }


def create_note(stock_code: str, title: str, content_md: str, tags: list = None) -> dict:
    note_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    conn = get_conn()
    conn.execute("""
        INSERT INTO note (note_id, stock_code, title, content_md, tags, created_at, updated_at, synced_to_vector)
        VALUES (?, ?, ?, ?, ?, ?, ?, false)
    """, [note_id, stock_code or "", title, content_md, tags_json, now, now])
    return {"note_id": note_id, "stock_code": stock_code, "title": title, "status": "created"}


def update_note(note_id: str, title: str = None, content_md: str = None,
                tags: list = None) -> dict:
    """更新笔记。修改后需重新同步向量库。"""
    fields, sets = [], []
    params = []
    if title is not None:
        sets.append("title=?"); params.append(title)
    if content_md is not None:
        sets.append("content_md=?"); params.append(content_md)
        sets.append("synced_to_vector=false")  # 内容改了 → 需重同步
    if tags is not None:
        sets.append("tags=?"); params.append(json.dumps(tags, ensure_ascii=False))
    sets.append("updated_at=?"); params.append(datetime.now().isoformat())
    params.append(note_id)
    conn = get_conn()
    conn.execute(f"UPDATE note SET {', '.join(sets)} WHERE note_id=?", params)
    return {"note_id": note_id, "status": "updated"}


def delete_note(note_id: str) -> dict:
    """删除笔记 + 同步向量库"""
    from notes.sync import remove_note_from_vector
    n = get_note(note_id)
    if not n:
        return {"error": f"找不到笔记 {note_id}"}
    conn = get_conn()
    conn.execute("DELETE FROM note WHERE note_id=?", [note_id])
    if n.get("synced_to_vector"):
        remove_note_from_vector(note_id)
    return {"note_id": note_id, "status": "deleted"}
