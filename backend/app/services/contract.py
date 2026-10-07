"""运维合同业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "contract"
REQUIRED_FIELDS = ["合同编号", "合同名称", "签约甲方"]
OPTIONAL_FIELDS = ["签约乙方", "合同金额", "起止日期", "续签条款"]
STATUS_ORDER = ["草稿中", "已签订", "履行中", "已到期", "已续签", "已终止"]
ACTION_RULES = {"确认签订": "已签订", "开始履行": "履行中", "终止合同": "已终止"}
RENEW_ACTION = "办理续签"
# 已续签、已终止都是定案状态：终止与续签互斥，也不再接受任何流转动作
TERMINAL_STATUSES = {"已续签", "已终止"}
# 履约状态驱动对账待处理清单：只有在履约和已到期的合同才进入待处理
PENDING_STATUSES = {"履行中", "已到期"}
# 附件上传失败允许重试一次
MAX_ATTACHMENT_RETRIES = 1


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _split_range(raw: Any) -> tuple[date | None, date | None]:
    """起止日期支持「YYYY-MM-DD」或「YYYY-MM-DD~YYYY-MM-DD」（也认 ～、至、—）两种写法。"""
    text = str(raw or "").strip()
    if not text:
        return None, None
    for sep in ("~", "～", "至", "—"):
        if sep in text:
            head, _, tail = text.partition(sep)
            return _parse_date(head), _parse_date(tail)
    return _parse_date(text), None


def _derive_status(row: dict[str, Any], today: date) -> str | None:
    """按起始日期推导履约状态；日期无法解析时返回 None，保持原状态。"""
    start, end = _split_range(row.get("起止日期"))
    if start is None:
        return None
    if start > today:
        return "已签订"
    if end is not None and end < today:
        return "已到期"
    return "履行中"


class ContractService:
    def __init__(self) -> None:
        self.backfill_legacy()

    def backfill_legacy(self, today: date | None = None) -> None:
        """存量合同按起始日期回填履约状态；草稿与已定案（已续签/已终止）的沿用既有判定。"""
        today = today or date.today()
        for row in store.rows(MODULE):
            status = str(row.get("status") or "")
            if status and status not in TERMINAL_STATUSES and status != "草稿中":
                derived = _derive_status(row, today)
                if derived is not None:
                    row["status"] = derived
            self._sync(row)

    @staticmethod
    def _sync(row: dict[str, Any]) -> None:
        """列表展示的「合同状态」与单条详情的 status 始终同源，待处理口径跟着履约状态走。"""
        status = str(row.get("status") or STATUS_ORDER[0])
        row["status"] = status
        row["合同状态"] = status
        row["pending"] = status in PENDING_STATUSES

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("合同编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def status_summary(self, rows: list[dict[str, Any]] | None = None) -> dict[str, int]:
        """按状态统计条数，导出清单与统计卡片共用同一口径，已终止的不会算进在履约。"""
        source = rows if rows is not None else store.rows(MODULE)
        summary = {status: 0 for status in STATUS_ORDER}
        for row in source:
            status = str(row.get("status") or "")
            summary[status] = summary.get(status, 0) + 1
        return summary

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            if field in values:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["abnormal"] = False
        self._sync(entry)
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"运维合同 {entry_id} 不存在或已归档"
        if action == RENEW_ACTION:
            return self._renew(entry, values or {})
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于运维合同可执行范围"
        status = str(entry.get("status") or "")
        if status in TERMINAL_STATUSES:
            return None, f"运维合同{status}，定案状态不能再执行「{action}」"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        self._sync(entry)
        return entry, f"运维合同已流转为「{target}」"

    def _renew(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        status = str(entry.get("status") or "")
        if status == "已终止":
            return None, "运维合同已终止，终止与续签互斥，不能再办理续签"
        # 同一份合同重复提交续签只落一次：直接回首次结果，不再重复落账
        if status == "已续签" or entry.get("续签记录"):
            self._sync(entry)
            return entry, "该合同已办理过续签，重复提交只保留首次结果"
        clause = str(values.get("续签条款") or entry.get("续签条款") or "").strip()
        if not clause:
            return None, f"续签条款为空：请先补充续签条款再提交续签，当前合同仍为「{status}」"
        if str(values.get("附件上传状态") or "成功").strip() == "失败":
            retries = int(entry.get("续签附件重试次数") or 0)
            if retries < MAX_ATTACHMENT_RETRIES:
                entry["续签附件重试次数"] = retries + 1
                return None, "附件上传失败，本次续签未生效，可重试一次"
            return None, "附件上传重试仍失败，续签未生效，请检查附件后重新提交"
        entry["续签条款"] = clause
        if values.get("附件"):
            entry["附件"] = values.get("附件")
        entry.setdefault("续签记录", []).append({
            "续签日期": date.today().isoformat(),
            "续签条款": clause,
            "附件": values.get("附件"),
        })
        entry["status"] = "已续签"
        self._sync(entry)
        return entry, "运维合同已办理续签"
