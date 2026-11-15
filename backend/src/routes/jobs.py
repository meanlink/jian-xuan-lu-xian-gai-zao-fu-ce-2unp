from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import Job
from src.schemas.job import JobInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import jobs, trials
from src.services.audit import record
from src.utils.serialization import job_dict
router = APIRouter(prefix='/jobs')


def blind_job_dict(j):
    """Blind jobs expose 甲乙 results but never the mirror dataset id or trial params."""
    d = job_dict(j)
    if j.mode == 'blind':
        d.pop('dataset_id', None)
        d['params'] = {}
    return d


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    return [blind_job_dict(j) for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(100))]


@router.post('', status_code=201)
def create(data: JobInput, db=Depends(get_db), u=Depends(can_write)):
    if data.trial_id is not None:
        t = trials.lock(db, data.trial_id)
        if not t:
            raise HTTPException(404, '盲态试验不存在')
        created = trials.create_blind_jobs(db, t, u)
        db.commit()
        for j in created:
            jobs.enqueue(j.id)
        return [blind_job_dict(j) for j in created]
    if data.dataset_id is None:
        raise HTTPException(422, '普通分析必须选择数据批次')
    j = jobs.create(db, data, u)
    record(db, u.email, 'job.create', {'id': j.id, 'params': j.params})
    db.commit()
    jobs.enqueue(j.id)
    return blind_job_dict(j)


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    j = db.get(Job, id)
    if not j:
        raise HTTPException(404, '分析任务不存在')
    return blind_job_dict(j)


@router.post('/{id}/approve')
def approve(id: int, db=Depends(get_db), u=Depends(admin_only)):
    j = db.get(Job, id)
    if not j:
        raise HTTPException(404, '分析任务不存在')
    if j.mode == 'blind':
        raise HTTPException(409, '盲态结果只报告甲乙差异，禁止批准')
    if j.status != 'completed':
        raise HTTPException(409, '仅已完成结果可以批准')
    j.approved = True
    record(db, u.email, 'job.approve', {'id': id})
    db.commit()
    return blind_job_dict(j)
