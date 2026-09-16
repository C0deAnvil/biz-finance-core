import mimetypes
import re
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from config import settings
from database import get_db
from dependencies import get_current_user, require_roles
from models import Attachment, PurchaseOrder, Role, SalesOrder, User
from responses import api_response
from services.core import _role_user_ids, create_notification
from services.time_utils import now_local

router = APIRouter(prefix='/files', tags=['附件'])

MAX_FILE_SIZE = 10 * 1024 * 1024
SAFE_BUSINESS_PATTERN = re.compile(r'^[a-z0-9_\-]+$')
SAFE_ATTACHMENT_PATTERN = re.compile(r'^[A-Za-z0-9\u4e00-\u9fa5_\-]{1,50}$')
FILE_RULES = {
    '.jpg': {
        'mime_types': {'image/jpeg', 'image/pjpeg'},
        'signatures': [b'\xff\xd8\xff'],
    },
    '.jpeg': {
        'mime_types': {'image/jpeg', 'image/pjpeg'},
        'signatures': [b'\xff\xd8\xff'],
    },
    '.png': {
        'mime_types': {'image/png'},
        'signatures': [b'\x89PNG\r\n\x1a\n'],
    },
    '.pdf': {
        'mime_types': {'application/pdf'},
        'signatures': [b'%PDF-'],
    },
    '.xls': {
        'mime_types': {'application/vnd.ms-excel', 'application/octet-stream'},
        'signatures': [b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'],
    },
    '.xlsx': {
        'mime_types': {
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/zip',
            'application/octet-stream',
        },
        'signatures': [b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'],
        'required_zip_entries': {'[Content_Types].xml', 'xl/workbook.xml'},
    },
}


def _clean_business_type(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or not SAFE_BUSINESS_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail='业务类型不合法')
    return normalized


def _clean_attachment_type(value: str) -> str:
    normalized = value.strip()
    if not normalized or not SAFE_ATTACHMENT_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail='附件类型不合法')
    return normalized


def _validate_file_bytes(suffix: str, content: bytes):
    rule = FILE_RULES.get(suffix)
    if rule is None:
        raise HTTPException(status_code=400, detail='不支持的文件类型')
    if not content:
        raise HTTPException(status_code=400, detail='上传文件不能为空')
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail='上传文件不能超过 10MB')
    if not any(content.startswith(signature) for signature in rule['signatures']):
        raise HTTPException(status_code=400, detail='文件内容与扩展名不匹配')
    required_zip_entries = rule.get('required_zip_entries')
    if required_zip_entries:
        try:
            from io import BytesIO

            with zipfile.ZipFile(BytesIO(content)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail='Excel 文件已损坏或格式无效') from exc
        if not required_zip_entries.issubset(names):
            raise HTTPException(status_code=400, detail='Excel 文件内容不合法')


def _validate_mime_type(suffix: str, content_type: str | None, filename: str):
    rule = FILE_RULES[suffix]
    guessed_type = mimetypes.guess_type(filename)[0]
    checked_types = {item for item in {content_type, guessed_type} if item}
    if checked_types and checked_types.isdisjoint(rule['mime_types']):
        raise HTTPException(status_code=400, detail='文件 MIME 类型与扩展名不匹配')
    return content_type or guessed_type


def _notify_order_attachment_uploaded(
    db: Session,
    *,
    business_type: str,
    business_id: int,
    attachment_type: str,
    filename: str,
    current_user: User,
) -> None:
    if business_type not in {'purchase_order', 'sales_order'}:
        return
    if not any(keyword in attachment_type for keyword in ('合同', '发票')):
        return

    if business_type == 'purchase_order':
        order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == business_id)).scalar_one_or_none()
        order_no = order.order_no if order else f'#{business_id}'
        title = '采购订单附件已上传'
        module = 'purchase'
        base_recipient_ids = [order.purchaser_user_id] if order and order.purchaser_user_id else []
        watcher_role_codes = ('finance', 'manager')
    else:
        order = db.execute(select(SalesOrder).where(SalesOrder.id == business_id)).scalar_one_or_none()
        order_no = order.order_no if order else f'#{business_id}'
        title = '客户订单附件已上传'
        module = 'sales'
        base_recipient_ids = [order.sales_user_id] if order and order.sales_user_id else []
        watcher_role_codes = ('finance', 'manager', 'chairman')

    recipient_user_ids = list(dict.fromkeys([*base_recipient_ids, *_role_user_ids(db, *watcher_role_codes)]))
    create_notification(
        db,
        title=title,
        content=f"{order_no} 已上传{attachment_type}附件：{filename}，请财务和经理按需查看。",
        module=module,
        recipient_user_ids=recipient_user_ids,
        created_by_id=current_user.id,
        category='info',
    )


def _serialize_attachment(row: Attachment) -> dict:
    """附件序列化。照片墙需要 mime_type 判断是否图片，created_at 与上传人用于展示来源。"""
    return {
        'id': row.id,
        'business_type': row.business_type,
        'business_id': row.business_id,
        'attachment_type': row.attachment_type,
        'original_name': row.original_name,
        'relative_path': row.relative_path,
        'mime_type': row.mime_type,
        'verified_by_id': row.verified_by_id,
        'verified_by_name': row.verified_by.full_name if row.verified_by else None,
        'verified_at': row.verified_at.isoformat() if row.verified_at else None,
        'uploaded_by_id': row.uploaded_by_id,
        'uploaded_by_name': row.uploaded_by.full_name if row.uploaded_by else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


@router.post('/upload')
async def upload_file(
    business_type: str = Form(...),
    business_id: int = Form(...),
    attachment_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filename = file.filename or ''
    suffix = Path(filename).suffix.lower()
    if suffix not in FILE_RULES:
        raise HTTPException(status_code=400, detail='不支持的文件类型')

    clean_business_type = _clean_business_type(business_type)
    clean_attachment_type = _clean_attachment_type(attachment_type)
    content = await file.read()
    _validate_file_bytes(suffix, content)
    mime_type = _validate_mime_type(suffix, file.content_type, filename)

    target_dir = settings.upload_dir / clean_business_type / str(business_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f'{clean_business_type}_{business_id}_{clean_attachment_type}_{uuid.uuid4().hex}{suffix}'
    target_path = target_dir / stored_name
    target_path.write_bytes(content)
    await file.close()

    attachment = Attachment(
        business_type=clean_business_type,
        business_id=business_id,
        attachment_type=clean_attachment_type,
        original_name=filename,
        stored_name=stored_name,
        relative_path=str(target_path.relative_to(settings.upload_dir)),
        mime_type=mime_type,
        uploaded_by_id=current_user.id,
    )
    db.add(attachment)
    _notify_order_attachment_uploaded(
        db,
        business_type=clean_business_type,
        business_id=business_id,
        attachment_type=clean_attachment_type,
        filename=filename,
        current_user=current_user,
    )
    db.commit()
    return api_response(data={'id': attachment.id, 'relative_path': attachment.relative_path})


@router.post('/{attachment_id}/verify')
def verify_attachment(
    attachment_id: int,
    current_user: User = Depends(require_roles("finance", "finance_super", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    """财务核验附件(发票/合同等):记录核验人与核验时间。"""
    row = db.execute(select(Attachment).where(Attachment.id == attachment_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    row.verified_by_id = current_user.id
    row.verified_at = now_local()
    db.commit()
    return api_response(
        msg="附件已核验",
        data={
            "id": row.id,
            "verified_by_id": row.verified_by_id,
            "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        },
    )


@router.get('/{attachment_id}')
def get_attachment(attachment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    attachment = db.execute(select(Attachment).where(Attachment.id == attachment_id)).scalar_one_or_none()
    if attachment is None:
        raise HTTPException(status_code=404, detail='附件不存在')
    file_path = settings.upload_dir / attachment.relative_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail='文件已丢失')
    return FileResponse(path=file_path, filename=attachment.original_name, media_type=attachment.mime_type)


@router.get('/business/{business_type}/{business_id}')
def list_attachments(
    business_type: str,
    business_id: int,
    attachment_type: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(Attachment)
        .options(joinedload(Attachment.uploaded_by), joinedload(Attachment.verified_by))
        .where(Attachment.business_type == business_type, Attachment.business_id == business_id)
        .order_by(Attachment.created_at.desc())
    )
    if attachment_type:
        query = query.where(Attachment.attachment_type == attachment_type)
    rows = db.execute(query).scalars().all()
    return api_response(data=[_serialize_attachment(row) for row in rows])
