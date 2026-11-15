from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import Trial
from src.schemas.trial import TrialInput, TrialBatchInput
from src.middlewares.auth import current_user, trial_write, custodian_only
from src.services import trials, jobs
from src.utils.serialization import trial_dict, trial_batch_dict
router = APIRouter(prefix='/trials')


def _trial(db, id):
    t = db.get(Trial, id)
    if not t:
        raise HTTPException(404, '盲态试验不存在')
    return t


def _detail(db, t):
    return {**trial_dict(t),
            'batches': [trial_batch_dict(b) for b in trials.batches(db, t)],
            'jobs': [{'id': j.id, 'name': j.name, 'status': j.status, 'error': j.error,
                      'mode': j.mode, 'created_at': j.created_at, 'finished_at': j.finished_at,
                      'result': j.result}
                     for j in trials.blind_jobs(db, t)],
            'formal': t.formal_result if t.status == 'unblinded' else None}


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    return [trial_dict(t) for t in db.scalars(select(Trial).order_by(Trial.id.desc()).limit(100))]


@router.post('', status_code=201)
def create(data: TrialInput, db=Depends(get_db), u=Depends(trial_write)):
    t = trials.create(db, u, data)
    db.commit()
    return trial_dict(t)


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    return _detail(db, _trial(db, id))


@router.post('/{id}/batches', status_code=201)
def register_batch(id: int, data: TrialBatchInput, db=Depends(get_db), u=Depends(custodian_only)):
    t = trials.lock(db, id)
    if not t:
        raise HTTPException(404, '盲态试验不存在')
    tb = trials.register_batch(db, u, t, data, data.arms)
    db.commit()
    # The response confirms the seal only; the mapping itself is never echoed.
    return {**trial_batch_dict(tb), 'seal_confirmed': True}


@router.post('/{id}/blind-jobs', status_code=201)
def blind_jobs(id: int, db=Depends(get_db), u=Depends(trial_write)):
    t = trials.lock(db, id)
    if not t:
        raise HTTPException(404, '盲态试验不存在')
    created = trials.create_blind_jobs(db, t, u)
    db.commit()
    for j in created:
        jobs.enqueue(j.id)
    return [{'id': j.id, 'name': j.name, 'status': j.status, 'mode': 'blind', 'trial_id': t.id}
            for j in created]


@router.post('/{id}/unblind')
def unblind(id: int, db=Depends(get_db), u=Depends(custodian_only)):
    t = trials.lock(db, id)
    if not t:
        raise HTTPException(404, '盲态试验不存在')
    prepared = trials.prepare_unblind(db, t)
    # Freeze scope/params in their own commit before the formal computation, so
    # concurrent imports cannot join it and an interruption resumes the same scope.
    db.commit()
    if not prepared:
        t = trials.lock(db, id)
        return {**trial_dict(t), 'formal': t.formal_result, 'created': False, 'resumed': True,
                'batches': [trial_batch_dict(b) for b in trials.batches(db, t)]}
    t, created = trials.compute_formal(db, u, t)
    db.commit()
    return {**trial_dict(t), 'formal': t.formal_result, 'created': created, 'resumed': not created,
            'batches': [trial_batch_dict(b) for b in trials.batches(db, t)]}
