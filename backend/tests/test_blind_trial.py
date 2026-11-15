"""盲态试验端到端验收（SQLite + TestClient，不依赖 Docker）。

运行：cd backend && DATABASE_URL=sqlite:////tmp/blind-test.db python tests/test_blind_trial.py
"""
import json
import os
import sys
import tempfile

DB = os.path.join(tempfile.mkdtemp(prefix='blind-trial-'), 'test.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + DB
os.environ['SEED_DEMO'] = 'false'
os.environ['JWT_SECRET'] = 'test-secret-only'
os.environ['BLIND_SEAL_SECRET'] = 'test-seal-secret-only'
# Tests run without Redis; keep the optional queue publish from stalling on connect.
os.environ['REDIS_URL'] = 'redis://127.0.0.1:1/0'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402
from migrations.initial import migrate  # noqa: E402
from src.config.database import Session  # noqa: E402
from src.models import User, Dataset, Job, Trial, TrialBatch  # noqa: E402
from src.services.business import seed_records, analyze  # noqa: E402
from src.services import trials as trial_service  # noqa: E402
from src.worker import execute  # noqa: E402
from src.utils.sealing import open_sealed  # noqa: E402
from src.utils.security import hash_password  # noqa: E402
from src.utils.serialization import checksum  # noqa: E402
import time  # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=''):
    (PASS if condition else FAIL).append(name)
    print(('PASS  ' if condition else 'FAIL  ') + name + (('  -> ' + str(detail)) if not condition and detail else ''))


def blind_rows(records, prefix, swapped=False):
    """Same timing pairs as pseudonymous 甲/乙 observations.

    swapped=True puts the new route in 甲, which is the direction a custodian
    would register for that batch.
    """
    out = []
    for i, r in enumerate(records, 1):
        code = f'{prefix}-{i:03}'
        a, b = ('after', 'before') if swapped else ('before', 'after')
        out.append({'blind_code': code, 'team': r['team'], 'arm': '甲', 'seconds': r[a]})
        out.append({'blind_code': code, 'team': r['team'], 'arm': '乙', 'seconds': r[b]})
    return out


ROWS = seed_records()


def boot():
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer', 'custodian'):
            db.add(User(email=role + '@t.local', name=role, role=role, password=hash_password('Demo123!')))
        for name, subset in (('批次一', ROWS[:40]), ('批次二', ROWS[40:])):
            db.add(Dataset(name=name, source=name + '.csv', kind='business', records=subset,
                           checksum=checksum(subset), created_at=time.time()))
        db.commit()


app.router.lifespan_context = lambda app: _lifespan(app)


class _Ctx:
    async def __aenter__(self): pass
    async def __aexit__(self, *a): pass


def _lifespan(app):
    return _Ctx()


client = TestClient(app)


def login(role):
    r = client.post('/auth/login', json={'email': role + '@t.local', 'password': 'Demo123!'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


def main():
    migrate()
    boot()
    admin, analyst, viewer, custodian = login('admin'), login('analyst'), login('viewer'), login('custodian')

    # ---------- normal retest still runs ----------
    ds = client.get('/datasets', headers=analyst).json()
    check('普通数据批次只列原始批次', len(ds) == 2 and all(d['kind'] == 'business' for d in ds))
    r = client.post('/jobs', headers=analyst, json={'name': '普通复测', 'dataset_id': ds[0]['id'], 'params': {}})
    check('普通复测任务照常创建', r.status_code == 201 and r.json()['mode'] == 'normal', r.text)
    normal_job = r.json()['id']
    execute(normal_job)
    normal = client.get(f'/jobs/{normal_job}', headers=analyst).json()
    check('普通复测结果照常生成', normal['status'] == 'completed' and '平均节省' in normal['result']['summary'])

    # ---------- blind intake carries no route direction ----------
    r = client.post('/datasets', headers=analyst,
                    json={'name': '盲态批次一', 'records': blind_rows(ROWS[:40], 'B1')})
    check('盲态批次可正常接入', r.status_code == 201 and r.json()['kind'] == 'blind', r.text)
    ds1 = r.json()
    r = client.post('/datasets', headers=analyst,
                    json={'name': '盲态批次二', 'records': blind_rows(ROWS[40:], 'B2', swapped=True)})
    ds2 = r.json()
    r = client.post('/datasets', headers=analyst, json={'name': '带路线的伪盲批次',
                   'records': blind_rows(ROWS[:5], 'X')[:10]})
    r = client.post('/datasets', headers=analyst, json={'name': '泄露路线', 'records': [
        {'blind_code': 'B-001', 'team': '一组', 'arm': '原路线', 'seconds': 400}]})
    check('盲态接入拒绝路线字样', r.status_code == 422, r.text)
    r = client.post('/datasets', headers=analyst, json={'name': '混入原始字段', 'records': [
        {'blind_code': 'B-001', 'team': '一组', 'arm': '甲', 'seconds': 400, 'before': 410, 'after': 400},
        {'blind_code': 'B-001', 'team': '一组', 'arm': '乙', 'seconds': 400}]})
    check('盲态接入拒绝混入原始前后测字段', r.status_code == 422, r.text)
    r = client.post('/jobs', headers=analyst, json={'name': '直算盲态批次', 'dataset_id': ds1['id'], 'params': {}})
    check('盲态批次不能走普通分析', r.status_code == 422, r.text)

    # ---------- create trial ----------
    r = client.post('/trials', headers=analyst, json={'name': '路线方向盲测', 'params': {'team': '全部', 'confidence': '0.95'}})
    check('分析员可创建盲态试验', r.status_code == 201, r.text)
    trial_id = r.json()['id']
    r = client.post('/trials', headers=analyst, json={'name': '路线方向盲测', 'params': {}})
    check('重名试验被拒绝', r.status_code == 409, r.text)

    # ---------- registration is custodian-only ----------
    for who, h in (('分析员', analyst), ('业务负责人', admin), ('观察员', viewer)):
        r = client.post(f'/trials/{trial_id}/batches', headers=h,
                        json={'dataset_id': ds1['id'], 'arms': {'甲': '原', '乙': '新'}})
        check(f'{who}登记映射被拒绝', r.status_code == 403, r.text)
    detail = client.get(f'/trials/{trial_id}', headers=analyst).json()
    check('越权登记未改变封存范围', detail['batches'] == [] and detail['status'] == 'blind')
    r = client.post(f'/trials/{trial_id}/batches', headers=custodian,
                    json={'dataset_id': ds1['id'], 'arms': {'甲': '原', '乙': '新'}})
    check('保管员登记批次一（甲=原）', r.status_code == 201, r.text)
    check('登记响应不回显映射', 'arms' not in json.dumps(r.json()) and '原' not in json.dumps(r.json()) and '新' not in json.dumps(r.json()), r.text)
    r = client.post(f'/trials/{trial_id}/batches', headers=custodian,
                    json={'dataset_id': ds2['id'], 'arms': {'甲': '新', '乙': '原'}})
    check('保管员登记批次二（方向相反）', r.status_code == 201, r.text)
    r = client.post(f'/trials/{trial_id}/batches', headers=custodian,
                    json={'dataset_id': ds1['id'], 'arms': {'甲': '新', '乙': '原'}})
    check('同一批次重复登记被拒绝', r.status_code == 409, r.text)
    r = client.post(f'/trials/{trial_id}/batches', headers=custodian,
                    json={'dataset_id': ds1['id'], 'arms': {'甲': '原', '甲2': '新'}})
    check('非法映射被拒绝', r.status_code == 422, r.text)
    r = client.post(f'/trials/{trial_id}/batches', headers=custodian,
                    json={'dataset_id': ds[0]['id'], 'arms': {'甲': '原', '乙': '新'}})
    check('原始前后测批次不能直接登记', r.status_code == 422, r.text)

    # ---------- mapping never readable from normal surfaces ----------
    body = client.get(f'/trials/{trial_id}', headers=analyst).text
    check('试验详情不含映射明文', 'arms' not in body and '"甲": "原"' not in body and '"甲":"原"' not in body, body[:400])
    detail = client.get(f'/trials/{trial_id}', headers=analyst).json()
    check('试验详情只暴露封存摘要', detail['batches'][0]['seal_digest'])
    listed = {d['id']: d for d in client.get('/datasets', headers=analyst).json()}
    check('盲态批次可读但只含盲码与甲乙', listed[ds1['id']]['kind'] == 'blind')
    mirror = client.get(f"/datasets/{ds1['id']}", headers=analyst).json()
    check('盲态批次行只有盲码与甲乙', all(set(x) == {'blind_code', 'team', 'arm', 'seconds'} for x in mirror['records'])
          and all(x['arm'] in ('甲', '乙') for x in mirror['records']))
    check('盲码不含路线或人员信息', all('OP-' not in x['blind_code'] and '原' not in x['blind_code'] for x in mirror['records']))

    # ---------- blind computation reports only 甲乙 ----------
    r = client.post(f'/trials/{trial_id}/blind-jobs', headers=analyst)
    check('分析员可发起盲态计算', r.status_code == 201 and len(r.json()) == 2, r.text)
    for job in r.json():
        execute(job['id'])
    detail = client.get(f'/trials/{trial_id}', headers=analyst).json()
    check('两批盲态计算完成', all(j['status'] == 'completed' for j in detail['jobs']), detail['jobs'])
    blind_job = detail['jobs'][0]
    blind_text = json.dumps(blind_job['result'], ensure_ascii=False)
    check('盲态结果不出现原/新路线方向', '原路线' not in blind_text and '新路线' not in blind_text, blind_text[:400])
    check('盲态结果含甲乙差异', '甲比乙' in blind_text)
    check('盲态结果标记 blind', blind_job['result'].get('blind') is True)
    job_view = client.get(f"/jobs/{blind_job['id']}", headers=analyst).json()
    check('盲态任务接口不泄露内部批次与参数', job_view['params'] == {} and 'dataset_id' not in job_view, job_view)
    r = client.post(f'/trials/{trial_id}/blind-jobs', headers=admin)
    check('已完成的批次不再重复排队', r.status_code == 201 and r.json() == [], r.text)

    # ---------- no approve / no distribution ----------
    r = client.post(f"/jobs/{blind_job['id']}/approve", headers=admin)
    check('盲态结果禁止批准', r.status_code == 409, r.text)
    r = client.post('/reports', headers=admin,
                    json={'name': '盲态分发', 'job_id': blind_job['id'], 'destination': '业务组', 'interval_seconds': 86400})
    check('盲态结果禁止分发', r.status_code == 409, r.text)
    with Session() as db:
        check('盲态任务未被标记批准', db.get(Job, blind_job['id']).approved is False)
    jobs_visible = client.get('/jobs', headers=analyst).json()
    check('盲态任务不出现在普通批准入口', all(j['mode'] != 'blind' or not j['approved'] for j in jobs_visible))

    # ---------- authority: only custodian unblinds ----------
    for who, h in (('分析员', analyst), ('业务负责人', admin), ('观察员', viewer)):
        r = client.post(f'/trials/{trial_id}/unblind', headers=h)
        check(f'{who}揭盲被拒绝', r.status_code == 403, r.text)
    check('越权揭盲未改变状态', client.get(f'/trials/{trial_id}', headers=analyst).json()['status'] == 'blind')

    # ---------- stale blind run from an older round is fenced ----------
    with Session() as db:
        stale = Job(name='过期盲态', dataset_id=ds1['id'], params={}, created_by=1, created_at=time.time(),
                    status='queued', mode='blind', trial_id=trial_id, trial_round=0)
        db.add(stale)
        db.commit()
        stale_id = stale.id
    execute(stale_id)
    with Session() as db:
        s = db.get(Job, stale_id)
        check('旧轮次盲态计算被判废', s.status == 'failed' and '作废' in s.error, s.error)
        check('旧轮次结果未写入', s.result == {})

    # ---------- unblinding: per-batch directions restored independently ----------
    r = client.post(f'/trials/{trial_id}/unblind', headers=custodian)
    check('保管员揭盲成功', r.status_code == 200, r.text)
    sealed = r.json()
    check('揭盲返回正式结论', sealed['status'] == 'unblinded' and sealed['formal']['blind'] is False)
    check('正式结论纳入两批', len(sealed['formal']['per_batch']) == 2)
    directions = [b['direction'] for b in sealed['formal']['per_batch']]
    check('各批方向分别还原（未统一翻转）', directions == ['甲=原，乙=新', '甲=新，乙=原'], directions)
    formal = sealed['formal']
    formal_checksum = sealed['formal_checksum']
    expected = analyze(ROWS, {'team': '全部', 'confidence': '0.95'})
    check('还原后总均值与原始方向一致', abs(formal['metrics'][0]['value'] - expected['metrics'][0]['value']) < 1e-6,
          (formal['metrics'][0], expected['metrics'][0]))
    check('还原后结论语义为原路线节省', '原路线' in formal['summary'] and '节省' in formal['summary'], formal['summary'])
    check('正式结论含逐人前后测', all({'operator', 'before', 'after'} <= set(r) for r in formal['table']['rows']))
    check('纳入口径与封存一致', len(formal['sealed_batches']) == 2 and formal['params'] == sealed['sealed_params'])

    # ---------- sealing: ciphertext only, tamper detected ----------
    with Session() as db:
        tb = db.query(TrialBatch).first()
        check('映射在库中为密文', tb.mapping_sealed and '原' not in tb.mapping_sealed and '甲' not in tb.mapping_sealed)
        decrypted = open_sealed(tb.mapping_sealed, trial_id=trial_id, dataset_id=tb.dataset_id,
                                checksum=tb.dataset_checksum, rows=tb.row_count)
        check('保管员侧可解封且方向正确', decrypted == {'甲': '原', '乙': '新'}, decrypted)
        try:
            open_sealed(tb.mapping_sealed, trial_id=trial_id, dataset_id=tb.dataset_id,
                        checksum='deadbeef', rows=tb.row_count)
            check('篡改批次指纹后解封失败', False)
        except ValueError:
            check('篡改批次指纹后解封失败', True)

    # ---------- frozen scope: later imports and runs stay out ----------
    new_rows = blind_rows(ROWS[40:], 'B3')
    new_ds = client.post('/datasets', headers=analyst, json={'name': '揭盲后新导入', 'records': new_rows}).json()
    r = client.post(f'/trials/{trial_id}/batches', headers=custodian,
                    json={'dataset_id': new_ds['id'], 'arms': {'甲': '原', '乙': '新'}})
    check('封存后不能再登记批次', r.status_code == 409, r.text)
    r = client.post(f'/trials/{trial_id}/blind-jobs', headers=analyst)
    check('揭盲后不能新发盲态计算', r.status_code == 409, r.text)
    check('封存范围不变', len(client.get(f'/trials/{trial_id}', headers=analyst).json()['formal']['sealed_batches']) == 2)
    with Session() as db:
        check('正式结论仍只含封存批次', db.get(Trial, trial_id).formal_checksum == formal_checksum)

    # ---------- idempotent retry ----------
    before_jobs = len(client.get('/jobs', headers=analyst).json())
    r = client.post(f'/trials/{trial_id}/unblind', headers=custodian)
    check('重复揭盲幂等成功', r.status_code == 200 and r.json()['resumed'] is True, r.text)
    check('重复揭盲沿用正式结论', r.json()['formal_checksum'] == formal_checksum)
    check('重复揭盲不另建记录', before_jobs == len(client.get('/jobs', headers=analyst).json()))
    with Session() as db:
        check('数据库中仍只有一次试验', db.query(Trial).count() == 1)

    # ---------- a blind run finishing after unblinding cannot overwrite ----------
    with Session() as db:
        late = Job(name='揭盲后才跑完的盲态', dataset_id=ds1['id'], params={}, created_by=1,
                   created_at=time.time(), status='queued', mode='blind', trial_id=trial_id, trial_round=0)
        db.add(late)
        db.commit()
        late_id = late.id
    execute(late_id)
    with Session() as db:
        s = db.get(Job, late_id)
        check('揭盲后完成的旧盲态计算被判废', s.status == 'failed' and s.result == {}, (s.status, s.error))
        check('正式结论未被旧盲态覆盖', db.get(Trial, trial_id).formal_checksum == formal_checksum)

    # ---------- interrupted unblinding rolls back and resumes ----------
    r = client.post('/trials', headers=analyst, json={'name': '中断恢复试验', 'params': {}})
    t2_id = r.json()['id']
    for dsid, mp in ((ds1['id'], {'甲': '原', '乙': '新'}), (ds2['id'], {'甲': '新', '乙': '原'})):
        assert client.post(f'/trials/{t2_id}/batches', headers=custodian,
                           json={'dataset_id': dsid, 'arms': mp}).status_code == 201
    for job in client.post(f'/trials/{t2_id}/blind-jobs', headers=analyst).json():
        execute(job['id'])
    original_analyze = trial_service.business.analyze

    def broken(*a, **kw):
        raise RuntimeError('模拟揭盲中断')
    trial_service.business.analyze = broken
    try:
        client.post(f'/trials/{t2_id}/unblind', headers=custodian)
        check('中断的揭盲返回失败', False)
    except RuntimeError:
        check('中断的揭盲返回失败', True)
    finally:
        trial_service.business.analyze = original_analyze
    with Session() as db:
        t2 = db.get(Trial, t2_id)
        # Scope is frozen in its own commit; the failed computation leaves no formal result.
        check('中断后封存范围已冻结', t2.status == 'unblinding' and t2.sealed_at is not None
              and len(t2.sealed_batches) == 2, (t2.status, len(t2.sealed_batches)))
        check('中断后未留下正式结论', t2.formal_result == {})
        check('中断后未留下正式结论任务',
              db.query(Job).filter(Job.trial_id == t2_id, Job.mode == 'formal').count() == 0)
        check('中断后未留下部分批次结论',
              all(b.formal_result == {} for b in db.query(TrialBatch).filter(TrialBatch.trial_id == t2_id)))
    # Data imported while the trial is mid-unblinding must not join the resumed run.
    mid_import = client.post('/datasets', headers=analyst,
                             json={'name': '中断期间新导入', 'records': blind_rows(ROWS[:10], 'M')}).json()
    r = client.post(f'/trials/{t2_id}/batches', headers=custodian,
                    json={'dataset_id': mid_import['id'], 'arms': {'甲': '原', '乙': '新'}})
    check('中断期间不能扩大封存范围', r.status_code == 409, r.text)
    r = client.post(f'/trials/{t2_id}/unblind', headers=custodian)
    check('中断后重试揭盲成功', r.status_code == 200 and r.json()['created'] is True, r.text)
    check('重试沿用原封存范围', len(r.json()['sealed_batches']) == 2)
    with Session() as db:
        check('重试后只有一个正式结论记录',
              db.query(Job).filter(Job.trial_id == t2_id, Job.mode == 'formal').count() == 1)
        check('重试后仍只有两次试验', db.query(Trial).count() == 2)

    # ---------- concurrency: new import + late blind run during unblinding ----------
    r = client.post('/trials', headers=analyst, json={'name': '并发试验', 'params': {}})
    t3_id = r.json()['id']
    client.post(f'/trials/{t3_id}/batches', headers=custodian,
                json={'dataset_id': ds1['id'], 'arms': {'甲': '原', '乙': '新'}})
    for job in client.post(f'/trials/{t3_id}/blind-jobs', headers=analyst).json():
        execute(job['id'])
    with Session() as db:
        late = Job(name='并发盲态', dataset_id=ds1['id'], params={}, created_by=1, created_at=time.time(),
                   status='queued', mode='blind', trial_id=t3_id, trial_round=db.get(Trial, t3_id).round_no)
        db.add(late)
        db.commit()
        late_id = late.id
    r = client.post(f'/trials/{t3_id}/unblind', headers=custodian)
    check('并发场景揭盲成功', r.status_code == 200, r.text)
    t3_formal = r.json()['formal_checksum']
    execute(late_id)
    with Session() as db:
        check('并发旧盲态任务不写入结果', db.get(Job, late_id).result == {})
        check('并发旧盲态任务未污染正式结论', db.get(Trial, t3_id).formal_checksum == t3_formal)
        check('封存范围为一个批次', len(db.get(Trial, t3_id).sealed_batches) == 1)
    r = client.post(f'/trials/{t3_id}/batches', headers=custodian,
                    json={'dataset_id': new_ds['id'], 'arms': {'甲': '原', '乙': '新'}})
    check('并发新导入不能进入已封存结论', r.status_code == 409)
    with Session() as db:
        check('正式结论仍只含封存批次', len(db.get(Trial, t3_id).sealed_batches) == 1)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + ', '.join(FAIL))
        sys.exit(1)


if __name__ == '__main__':
    main()
