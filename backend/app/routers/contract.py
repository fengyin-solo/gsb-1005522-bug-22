"""运维合同接口：维护运维合同，覆盖确认签订、开始履行、终止合同、申请续签等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.contract import RENEW_ACTION, ContractService

router = APIRouter(prefix="/api/contract", tags=["运维合同"])

service = ContractService()

LIST_FIELDS = ["合同编号", "合同名称", "签约甲方", "签约乙方", "合同金额", "起止日期", "续签条款", "合同状态"]
STATUSES = ["草稿中", "已签订", "履行中", "已到期", "已终止", "已续签"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按合同编号检索"),
    status: str | None = Query(default=None, description="草稿中、已签订、履行中、已到期、已终止、已续签"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按合同编号与状态过滤运维合同列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 注意：/export、/reconciliation、/summary 必须排在 /{entry_id} 之前，
# 否则 FastAPI 会把 "export" 当成 entry_id 解析，返回 422，导出直接打不开。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出运维合同清单：全量明细附状态分组，条数与列表口径一致（不含已终止在履约）。"""
    items, total = service.list_entries(page=1, size=10000)
    summary = service.summary()
    by_status: dict[str, int] = {}
    for item in items:
        status = str(item.get("status"))
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "module": "contract",
        "total": total,
        "在履约数": summary["履行中合同"],
        "对账待处理数": summary["对账待处理"],
        "状态分组": by_status,
        "items": items,
    }


@router.get("/reconciliation")
def reconciliation_pending() -> dict[str, Any]:
    """对账待处理清单：由履约状态驱动，只有履行中的合同会出现。"""
    items = service.reconciliation_pending()
    return {"module": "contract", "total": len(items), "items": items}


@router.get("/summary")
def contract_summary() -> dict[str, int]:
    """统计卡片口径：履行中 / 本月到期 / 待签订 / 对账待处理。"""
    return service.summary()


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条运维合同明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"运维合同 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条运维合同，缺合同编号/签约甲方时逐条说明原因，而不是静默丢弃。"""
    entry, reasons = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message="；".join(reasons))
    return ActionResult(ok=True, message="运维合同已登记", entry=entry)


@router.post("/{entry_id}/attachments", response_model=ActionResult)
def upload_attachment(entry_id: int, payload: EntryPayload) -> ActionResult:
    """上传续签附件；首次失败时前端可原样重试一次，重试仍失败不许落成已续签。"""
    filename = str(payload.values.get("filename") or "").strip()
    fail_times = int(payload.values.get("fail_times") or 0)
    attachment, message = service.upload_attachment(entry_id, filename, fail_times=fail_times)
    if attachment is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=attachment)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条运维合同执行签订/履行/终止/续签；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip() or RENEW_ACTION
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
