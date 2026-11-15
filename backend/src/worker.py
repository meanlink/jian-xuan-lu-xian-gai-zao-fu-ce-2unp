import time, json, logging
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report, Trial
from src.services.business import analyze
from src.services.trials import blind_analysis, lock as lock_trial
from src.services.reports import deliver
from src.services.audit import record
from src.utils.errors import safe_error
logging.basicConfig(level=logging.INFO)


def _discard_stale(job_id, reason):
    """A blind job that lost the race is marked failed; it never writes a result."""
    with Session() as db:
        j=db.get(Job,job_id)
        if j and j.status in ('queued','running'):
            j.status='failed';j.error=reason;j.finished_at=time.time()
            record(db,'worker','job.blind.stale',{'id':job_id,'reason':reason})
            db.commit()


def execute_blind(job_id):
    """Blind computation fenced against imports and unblinding.

    The trial row is locked while the result is committed. If a custodian
    registered a new batch (round bump) or started/finished unblinding in the
    meantime, the result is discarded rather than written: new data can never
    leak into a sealed conclusion, and a stale blind run can never overwrite
    the formal conclusion.
    """
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        t=db.get(Trial,j.trial_id)
        if not t or t.status!='blind':
            _discard_stale(job_id,'试验已进入揭盲，旧盲态计算作废');return
        if t.round_no!=j.trial_round:
            _discard_stale(job_id,'试验范围已更新，旧盲态计算作废，请重新发起');return
        j.status='running';db.commit()
    try:
        with Session() as db:
            t=lock_trial(db,j.trial_id)
            if not t or t.status!='blind' or t.round_no!=j.trial_round:
                db.rollback();_discard_stale(job_id,'试验范围或阶段已更新，本次盲态计算作废，请重新发起');return
            result=blind_analysis(db,t,j.dataset_id)
            json.dumps(result,allow_nan=False)
            j=db.get(Job,job_id)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id,'mode':'blind','trial':j.trial_id})
            db.commit()
    except Exception as e:
        logging.exception('Blind analysis failed')
        _discard_stale(job_id,safe_error(e)[:990])


def execute(job_id):
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        if j.mode=='blind':
            db.rollback()
            return execute_blind(job_id)
        j.status='running'; db.commit()
        try:
            d=db.get(Dataset,j.dataset_id)
            result=analyze(d.records,j.params)
            json.dumps(result,allow_nan=False)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status='failed';j.error=safe_error(e)[:990];j.finished_at=time.time()
            record(db,'worker','job.failed',{'id':j.id,'error':j.error})
        db.commit()


def tick():
    with Session() as db:
        queued=list(db.scalars(select(Job.id).where(Job.status=='queued').limit(3)))
    for job_id in queued:execute(job_id)
    with Session() as db:
        for r in db.scalars(select(Report).where(Report.enabled==True,Report.next_run<=time.time())):
            d=deliver(db,r)
            if d:record(db,'worker','report.delivered',{'id':r.id,'delivery':d.id})
        db.commit()


def main():
    r=Redis.from_url(REDIS_URL,socket_timeout=3)
    # This Compose stack has a single worker. A crash must not strand its jobs.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            j.status='queued';record(db,'worker','job.recovered',{'id':j.id})
        db.commit()
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item=r.blpop('analysis:queue',timeout=1)
            if item:execute(int(item[1]))
            tick()
        except Exception:
            logging.exception('Worker retry');time.sleep(2)


if __name__=='__main__':main()
