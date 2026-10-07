"""运维合同业务规则：状态流转、字段校验、续签互斥与对账待处理口径都收在这里。

口径约定（列表与详情共用同一份判定，杜绝两处对不上）：
- 行内只有一个权威状态字段 ``status``；展示用的「合同状态」列在出参时由它同步回填。
- 状态序列：草稿中 → 已签订 → 履行中 → 已到期；「已终止」「已续签」为终态。
- 续签幂等：同一份合同重复提交续签只落一条续签记录，状态不再重复跳转。
- 终态互斥：已终止的合同不能续签，已续签的合同不能再终止。
- 对账待处理只认「履行中」，已终止/已到期/已续签一律不算，导出条数也按这个口径。
- 存量合同在服务首次装载时按起始日期回填一次状态，过往的续签/终止判定保持不动。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "contract"

# 登记时必须提供的字段；缺失即拒绝保存，并逐条写明原因。
REQUIRED_FIELDS = ["合同编号", "签约甲方"]
# 登记时一并保存的业务字段（金额、起止日期、续签条款允许后补）。
SAVED_FIELDS = [
    "合同编号",
    "合同名称",
    "签约甲方",
    "签约乙方",
    "合同金额",
    "起止日期",
    "续签条款",
]

STATUS_DRAFT = "草稿中"
STATUS_SIGNED = "已签订"
STATUS_ACTIVE = "履行中"
STATUS_EXPIRED = "已到期"
STATUS_TERMINATED = "已终止"
STATUS_RENEWED = "已续签"

# 状态展示顺序；前四个是可被日期驱动的常规流转，后两个是终态。
STATUS_ORDER = [
    STATUS_DRAFT,
    STATUS_SIGNED,
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_TERMINATED,
    STATUS_RENEWED,
]
# 终态：不再接受任何动作。
FINAL_STATUSES = {STATUS_TERMINATED, STATUS_RENEWED}
# 会进入对账待处理清单的状态——只认履行中。
RECONCILIATION_STATUSES = {STATUS_ACTIVE}

# 各动作允许的前置状态（None 表示不限制），不在表里的跳转一律拒绝。
ACTION_RULES: dict[str, dict[str, Any]] = {
    "确认签订": {"target": STATUS_SIGNED, "allowed": {STATUS_DRAFT}, "done": "已确认签订"},
    "开始履行": {"target": STATUS_ACTIVE, "allowed": {STATUS_DRAFT, STATUS_SIGNED}, "done": "已开始履行"},
    "终止合同": {
        "target": STATUS_TERMINATED,
        # 续签之后不允许再终止，保证终止/续签互斥。
        "allowed": {STATUS_DRAFT, STATUS_SIGNED, STATUS_ACTIVE, STATUS_EXPIRED},
        "done": "已终止",
    },
}
RENEW_ACTION = "申请续签"


def _parse_period(text: Any) -> tuple[date | None, date | None]:
    """解析「起止日期」字段，兼容 ``2026-01-01~2026-12-31`` 等常见写法。

    起止分隔符支持 ``~``、``～``、``-``、``—``、``至``；只填一个日期时视为起始日。
    解析不出来的部分返回 ``None``，由调用方决定如何兜底。
    """
    raw = str(text or "").strip()
    if not raw:
        return None, None

    for sep in ("~", "～", "至", "—", "--"):
        if sep in raw:
            left, _, right = raw.partition(sep)
            return _parse_date(left.strip()), _parse_date(right.strip())

    # 单个日期：可能是单日，也可能是 "2026-01-01 - 2026-12-31" 被空格拆开。
    tokens = raw.replace(" - ", "~").split("~")
    if len(tokens) == 2:
        return _parse_date(tokens[0].strip()), _parse_date(tokens[1].strip())
    return _parse_date(raw), None


def _parse_date(text: str) -> date | None:
    text = str(text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class ContractService:
    def __init__(self) -> None:
        self._backfilled = False
        # 附件上传失败计数：entry_id -> 已失败次数，用来模拟“首次失败、重试成功”。
        self._upload_failures: dict[int, int] = {}
        # 模块在路由导入时即实例化，存量合同在这里就按起始日期回填一次，
        # 保证 /api/overview 等不经过本服务的入口拿到的 pending 也是新口径。
        self._ensure_backfilled()

    # ------------------------------------------------------------------ 读取

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        self._ensure_backfilled()
        rows = [self._sync_row(dict(row)) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("合同编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        self._ensure_backfilled()
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._sync_row(dict(entry))

    def reconciliation_pending(self) -> list[dict[str, Any]]:
        """对账待处理清单：由履约状态驱动，只包含履行中的合同。"""
        self._ensure_backfilled()
        pending: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            synced = self._sync_row(dict(row))
            if synced.get("status") in RECONCILIATION_STATUSES:
                pending.append(synced)
        pending.sort(
            key=lambda row: (_parse_period(row.get("起止日期"))[1] or date.max, int(row["id"]))
        )
        return pending

    def summary(self, today: date | None = None) -> dict[str, int]:
        """列表顶部统计卡片：口径与导出、对账完全一致。"""
        self._ensure_backfilled()
        today = today or date.today()
        rows = [self._sync_row(dict(row)) for row in store.rows(MODULE)]
        month_end = _month_end(today)
        return {
            "履行中合同": sum(1 for row in rows if row["status"] == STATUS_ACTIVE),
            "本月到期合同": sum(1 for row in rows if self._expires_this_month(row, today, month_end)),
            "待签订合同": sum(1 for row in rows if row["status"] == STATUS_DRAFT),
            "对账待处理": sum(1 for row in rows if row["status"] in RECONCILIATION_STATUSES),
        }

    # ------------------------------------------------------------------ 登记

    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        # 缺失必填：逐条写明，不许静默存成“已签订/已续签”。
        missing = [
            field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()
        ]
        if missing:
            reasons = []
            for field in missing:
                reasons.append(f"{field}缺失，无法登记合同")
            return None, reasons

        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in SAVED_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip():
                entry[field] = str(value).strip()
        entry["status"] = STATUS_DRAFT
        entry["pending"] = False
        entry["abnormal"] = False
        entry["renewals"] = []
        entry["attachments"] = []
        rows.append(entry)
        return self._sync_row(dict(entry)), []

    # ------------------------------------------------------------------ 动作

    def run_action(
        self,
        entry_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        self._ensure_backfilled()
        payload = payload or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"运维合同 {entry_id} 不存在或已归档"

        if action == RENEW_ACTION:
            return self._renew(entry, payload)

        rule = ACTION_RULES.get(action)
        if rule is None:
            return None, f"动作「{action}」不属于运维合同可执行范围"

        current = self._derive_status(entry)
        target = rule["target"]
        if current in FINAL_STATUSES:
            return None, f"合同当前为「{current}」，不能再执行「{action}」"
        if current not in rule["allowed"]:
            return None, f"当前状态为「{current}」，不允许执行「{action}」"

        entry["status"] = target
        entry["pending"] = target in RECONCILIATION_STATUSES
        entry["abnormal"] = False
        return self._sync_row(dict(entry)), f"运维合同{rule['done']}"

    def _renew(
        self, entry: dict[str, Any], payload: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        current = self._derive_status(entry)

        # 终态互斥：已终止不许续签，已续签不许重复续签。
        if current == STATUS_TERMINATED:
            return None, "合同已终止，终止与续签互斥，不能再申请续签"
        if current == STATUS_RENEWED:
            return None, "该合同已完成续签，同一份合同不能重复续签"

        clause = str(payload.get("续签条款") or entry.get("续签条款") or "").strip()
        if not clause:
            # 续签条款为空：给出空态说明，绝不落成已续签。
            return None, "续签条款为空，无法续签：请先补充续签条款后再提交"

        attachment_id = payload.get("attachment_id")
        if attachment_id is not None:
            known = {att.get("id") for att in entry.get("attachments", [])}
            if attachment_id not in known:
                return None, "续签附件尚未上传成功，请重新上传后再提交续签"
        else:
            return None, "续签附件未上传，不能落成已续签；请上传附件（失败可重试一次）"

        # 幂等：续签记录只落一次；同一合同并发/重复提交复用同一条。
        renewals = entry.setdefault("renewals", [])
        if renewals:
            entry["status"] = STATUS_RENEWED
            entry["pending"] = False
            return self._sync_row(dict(entry)), "该合同已续签，本次重复提交未重复落单"

        renewals.append(
            {
                "续签条款": clause,
                "附件": attachment_id,
                "续签日期": str(payload.get("续签日期") or date.today()),
            }
        )
        entry["续签条款"] = clause
        entry["status"] = STATUS_RENEWED
        entry["pending"] = False
        entry["abnormal"] = False
        return self._sync_row(dict(entry)), "运维合同已续签"

    # ------------------------------------------------------------------ 附件

    def upload_attachment(
        self,
        entry_id: int,
        filename: str,
        *,
        fail_times: int = 0,
    ) -> tuple[dict[str, Any] | None, str]:
        """登记续签附件。

        ``fail_times`` 表示该合同前 ``fail_times`` 次上传必然失败，用于复现
        “上传失败允许重试一次”的边界；重试成功后才返回附件编号。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"运维合同 {entry_id} 不存在或已归档"
        filename = str(filename or "").strip()
        if not filename:
            return None, "附件文件名为空，无法上传"

        attempts = self._upload_failures.get(entry_id, 0)
        if attempts < fail_times:
            self._upload_failures[entry_id] = attempts + 1
            return None, "附件上传失败（网络异常），请重试一次"

        attachments = entry.setdefault("attachments", [])
        attachment_id = f"ATT-{entry_id}-{len(attachments) + 1:03d}"
        attachments.append({"id": attachment_id, "filename": filename})
        return {"id": attachment_id, "filename": filename, "retries": attempts}, "附件上传成功"

    # ------------------------------------------------------------------ 状态判定

    def _derive_status(self, row: dict[str, Any], today: date | None = None) -> str:
        """按权威状态 + 起止日期推导当前应展示的状态。

        - 终态（已终止/已续签）保持原样，过往续签沿用既有判定，不被日期覆盖。
        - 履行中合同过了止期 → 已到期（日期驱动，对账清单随之剔除）。
        """
        today = today or date.today()
        status = str(row.get("status") or STATUS_DRAFT)
        if status in FINAL_STATUSES:
            return status
        _, end = _parse_period(row.get("起止日期"))
        if status == STATUS_ACTIVE and end is not None and end < today:
            return STATUS_EXPIRED
        return status

    def _sync_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """把权威状态同步到「合同状态」展示列与 pending 标记，列表/详情同源。"""
        status = self._derive_status(row)
        row["status"] = status
        row["合同状态"] = status
        row["pending"] = status in RECONCILIATION_STATUSES
        row.setdefault("续签条款", "")
        row.setdefault("renewals", [])
        row.setdefault("attachments", [])
        if not str(row.get("续签条款") or "").strip():
            row["续签条款"] = ""
        return row

    def _expires_this_month(
        self, row: dict[str, Any], today: date, month_end: date
    ) -> bool:
        if row["status"] in FINAL_STATUSES:
            return False
        _, end = _parse_period(row.get("起止日期"))
        return end is not None and today <= end <= month_end

    def _ensure_backfilled(self) -> None:
        """存量合同按起始日期回填一次状态；过往续签/终止判定保持不动。"""
        if self._backfilled:
            return
        self._backfilled = True
        today = date.today()
        for row in store.rows(MODULE):
            status = str(row.get("status") or "").strip()
            # 过往已经走到终态（含历史上已续签/已终止）的，沿用既有判定。
            if status in FINAL_STATUSES:
                self._sync_row(row)
                continue
            start, end = _parse_period(row.get("起止日期"))
            if start is None:
                self._sync_row(row)
                continue
            if end is not None and end < today:
                row["status"] = STATUS_EXPIRED
            elif start <= today:
                row["status"] = STATUS_ACTIVE
            else:
                row["status"] = STATUS_DRAFT
            self._sync_row(row)


def _month_end(today: date) -> date:
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    return next_month.fromordinal(next_month.toordinal() - 1)
