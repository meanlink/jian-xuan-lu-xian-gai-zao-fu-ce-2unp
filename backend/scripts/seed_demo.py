import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, Trial
from src.services.business import metadata, seed_records, analyze
from src.services import trials as trial_service
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum
DEMO_USERS=[('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员'),('custodian','沈岚 · 盲态保管员')]
DEMO_TRIAL='路线方向盲测 · 首轮'
DEMO_BLIND='盲态测时 · 首轮'


def seed_blind_records():
    """Same timing pairs, but pseudonymous and arm-labelled: no route names."""
    rows=[]
    for i,r in enumerate(seed_records()):
        code=f'B-{i+1:03}'
        rows.append({'blind_code':code,'team':r['team'],'arm':'甲','seconds':r['before']})
        rows.append({'blind_code':code,'team':r['team'],'arm':'乙','seconds':r['after']})
    return rows
def seed():
    migrate()
    with Session() as db:
        for role,name in DEMO_USERS:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',kind='business',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        if not db.scalar(select(Trial).where(Trial.name==DEMO_TRIAL)):
            # Demo starts in blind state: the batch holds only blind codes and 甲/乙,
            # and the direction is sealed. Unblinding stays a deliberate custodian action.
            custodian=db.scalar(select(User).where(User.role=='custodian'))
            analyst=db.scalar(select(User).where(User.role=='analyst'))
            blind_rows=seed_blind_records()
            blind=db.scalar(select(Dataset).where(Dataset.name==DEMO_BLIND))
            if not blind:
                blind=Dataset(name=DEMO_BLIND,source='blind-intake-202611.csv',kind='blind',records=blind_rows,checksum=checksum(blind_rows),created_at=time.time())
                db.add(blind); db.flush()
            t=trial_service.create(db,analyst,type('D',(),{'name':DEMO_TRIAL,'params':{}})())
            from src.schemas.trial import TrialBatchInput
            trial_service.register_batch(db,custodian,t,TrialBatchInput(dataset_id=blind.id,arms={'甲':'原','乙':'新'}),{'甲':'原','乙':'新'})
            trial_service.create_blind_jobs(db,t,analyst)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
