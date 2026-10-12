"""
预警引擎（v1.1）。

数据流：
1. AlertRule.create(...) 入库
2. AlertEngine.run_all(conn) 遍历所有 active 规则 → 评估 → 触发 → 写 alert_event
3. 通知渠道（邮件/lark）由 AlertEngine.on_event 钩子调用（v1.1 默认 web 渠道，仅写表）

支持的 metric：
- pe / pb        → finance_core
- change_pct     → daily_kline 最新一日
"""
from __future__ import annotations
import uuid as _uuid
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict, Iterable
from datetime import date, datetime
import duckdb


# 已知 metric 集合（白名单）
VALID_METRICS = {"pe", "pb", "change_pct", "roe", "gross_margin", "volume_ratio"}
VALID_OPERATORS = {">", "<", ">=", "<=", "==", "!="}
VALID_TARGET_TYPES = {"stock", "group"}
VALID_CHANNELS = {"web", "email", "lark"}


@dataclass
class AlertRule:
    """预警规则"""
    rule_id: str
    name: str
    target_type: str        # 'stock' / 'group'
    target_value: str        # 股票代码 / 分组名
    metric: str
    operator: str            # > / <
    threshold: float
    channel: str = "web"
    is_active: bool = True
    created_at: Optional[str] = None

    # ============ CRUD ============
    @staticmethod
    def create(
        conn: duckdb.DuckDBPyConnection,
        rule_id: Optional[str] = None,
        name: str = "",
        target_type: str = "stock",
        target_value: str = "",
        metric: str = "pe",
        operator: str = ">",
        threshold: float = 0.0,
        channel: str = "web",
        is_active: bool = True,
    ) -> "AlertRule":
        # 入参校验
        if metric not in VALID_METRICS:
            raise ValueError(f"未知 metric: {metric}。可用: {sorted(VALID_METRICS)}")
        if operator not in VALID_OPERATORS:
            raise ValueError(f"未知 operator: {operator}。可用: {sorted(VALID_OPERATORS)}")
        if target_type not in VALID_TARGET_TYPES:
            raise ValueError(f"未知 target_type: {target_type}")
        if channel not in VALID_CHANNELS:
            raise ValueError(f"未知 channel: {channel}")

        rid = rule_id or _uuid.uuid4().hex[:12]
        existing = conn.execute("SELECT 1 FROM alert_rule WHERE rule_id=?", [rid]).fetchone()
        if existing:
            raise ValueError(f"规则 id 已存在: {rid}")

        conn.execute(
            """
            INSERT INTO alert_rule (rule_id, name, target_type, target_value,
                                    metric, operator, threshold, channel, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [rid, name, target_type, target_value, metric, operator, threshold, channel, is_active],
        )
        return AlertRule.get(conn, rid)

    @staticmethod
    def get(conn: duckdb.DuckDBPyConnection, rule_id: str) -> Optional["AlertRule"]:
        row = conn.execute(
            "SELECT rule_id, name, target_type, target_value, metric, operator, threshold, channel, is_active, created_at "
            "FROM alert_rule WHERE rule_id=?",
            [rule_id],
        ).fetchone()
        if not row:
            return None
        return AlertRule(
            rule_id=row[0], name=row[1] or "", target_type=row[2] or "",
            target_value=row[3] or "", metric=row[4] or "", operator=row[5] or "",
            threshold=row[6] or 0.0, channel=row[7] or "web",
            is_active=bool(row[8]), created_at=str(row[9]) if row[9] else None,
        )

    @staticmethod
    def list_all(conn: duckdb.DuckDBPyConnection) -> List["AlertRule"]:
        rows = conn.execute("SELECT rule_id FROM alert_rule ORDER BY created_at").fetchall()
        return [AlertRule.get(conn, r[0]) for r in rows]

    @staticmethod
    def list_active(conn: duckdb.DuckDBPyConnection) -> List["AlertRule"]:
        rows = conn.execute(
            "SELECT rule_id FROM alert_rule WHERE is_active=true ORDER BY created_at"
        ).fetchall()
        return [AlertRule.get(conn, r[0]) for r in rows]

    @staticmethod
    def delete(conn: duckdb.DuckDBPyConnection, rule_id: str) -> bool:
        cur = conn.execute("DELETE FROM alert_rule WHERE rule_id=?", [rule_id])
        return cur.rowcount > 0


@dataclass
class AlertEvent:
    """触发事件"""
    event_id: str
    rule_id: str
    stock_code: str
    metric: str
    value: float
    triggered_at: Optional[str] = None
    message: str = ""
    sent_status: str = "pending"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "rule_id": self.rule_id,
            "stock_code": self.stock_code,
            "metric": self.metric,
            "value": self.value,
            "triggered_at": self.triggered_at,
            "message": self.message,
            "sent_status": self.sent_status,
        }


# ============ 评估 + 触发 ============

# metric 到当前值的 SQL 模板
_METRIC_SQL = {
    "pe":          "SELECT stock_code, pe FROM finance_core WHERE stock_code=? AND report_date IS NOT NULL ORDER BY report_date DESC LIMIT 1",
    "pb":          "SELECT stock_code, pb FROM finance_core WHERE stock_code=? AND report_date IS NOT NULL ORDER BY report_date DESC LIMIT 1",
    "roe":         "SELECT stock_code, roe FROM finance_core WHERE stock_code=? AND report_date IS NOT NULL ORDER BY report_date DESC LIMIT 1",
    "gross_margin":"SELECT stock_code, gross_margin FROM finance_core WHERE stock_code=? AND report_date IS NOT NULL ORDER BY report_date DESC LIMIT 1",
    "change_pct":  "SELECT stock_code, change_pct FROM daily_kline WHERE stock_code=? AND k_type=1 ORDER BY trade_date DESC LIMIT 1",
    "volume_ratio":"SELECT stock_code, volume_ratio_20d FROM factors_daily WHERE stock_code=? ORDER BY trade_date DESC LIMIT 1",
}


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":  return value >  threshold
    if operator == "<":  return value <  threshold
    if operator == ">=": return value >= threshold
    if operator == "<=": return value <= threshold
    if operator == "==": return value == threshold
    if operator == "!=": return value != threshold
    raise ValueError(f"未知 operator: {operator}")


class AlertEngine:
    """预警评估引擎"""

    @staticmethod
    def _resolve_target_stock_codes(conn, rule: AlertRule) -> Optional[List[str]]:
        """根据 target_type / target_value 返回要监控的股票代码列表"""
        if rule.target_type == "stock":
            return [rule.target_value]
        if rule.target_type == "group":
            # 从 watchlist / watchlist_group 解析
            rows = conn.execute(
                "SELECT stock_code FROM watchlist WHERE group_name=?",
                [rule.target_value],
            ).fetchall()
            return [r[0] for r in rows] if rows else None
        return None

    @staticmethod
    def evaluate_rule(conn: duckdb.DuckDBPyConnection, rule: AlertRule) -> List[AlertEvent]:
        """评估单条规则，返回触发事件列表（不入库）"""
        if rule.metric not in _METRIC_SQL:
            return []

        stock_codes = AlertEngine._resolve_target_stock_codes(conn, rule)
        if not stock_codes:
            return []

        sql = _METRIC_SQL[rule.metric]
        events: List[AlertEvent] = []
        for stock_code in stock_codes:
            row = conn.execute(sql, [stock_code]).fetchone()
            if not row:
                continue
            value = row[1]
            if value is None:
                continue
            if _compare(value, rule.operator, rule.threshold):
                ev = AlertEvent(
                    event_id=_uuid.uuid4().hex[:12],
                    rule_id=rule.rule_id,
                    stock_code=stock_code,
                    metric=rule.metric,
                    value=float(value),
                    triggered_at=datetime.now().isoformat(timespec="seconds"),
                    message=f"{rule.name}：{stock_code} {rule.metric}{rule.operator}{rule.threshold}（实际 {value}）",
                    sent_status="pending",
                )
                events.append(ev)
        return events

    @staticmethod
    def _persist_events(conn, events: List[AlertEvent]) -> None:
        for ev in events:
            conn.execute(
                """
                INSERT INTO alert_event (event_id, rule_id, stock_code, metric,
                                         value, triggered_at, message, sent_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [ev.event_id, ev.rule_id, ev.stock_code, ev.metric,
                 ev.value, ev.triggered_at, ev.message, ev.sent_status],
            )

    @staticmethod
    def run_all(conn: duckdb.DuckDBPyConnection) -> List[AlertEvent]:
        """评估所有 active 规则，写入 alert_event，返回所有事件"""
        all_events: List[AlertEvent] = []
        for rule in AlertRule.list_active(conn):
            evs = AlertEngine.evaluate_rule(conn, rule)
            all_events.extend(evs)
        if all_events:
            AlertEngine._persist_events(conn, all_events)
        return all_events
