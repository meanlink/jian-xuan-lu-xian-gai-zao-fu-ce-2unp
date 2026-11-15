"""盲态试验：批次封存、盲态计算、揭盲。

业务流程：
1. 分析员或保管员创建试验，参数在此冻结。
2. 盲态测时数据按 `blind_code/team/arm/seconds` 采集（每码一条甲、一条乙），
   通过正常数据接入进入系统，但不含任何原/新路线字样。
3. 保管员逐批登记：把一个盲态批次纳入试验，并封存该批甲/乙与原/新路线的
   对应。该对应从此只有保管员能在揭盲时解开。
4. 盲态计算只读盲码与甲乙，只报告甲比乙的差异，禁止批准与分发。
   普通复测与原始业务的接入、计算、批准、分发完全不受影响。
5. 揭盲：先独立提交冻结纳入批次与参数，再按每批自己的封存方向分别还原，
   合并为正式结论。

并发与恢复：
- 盲态任务携带 trial_round 围栏。登记新批次或完成揭盲都会递增轮次，旧盲态
  计算因此被判废，既不能把新导入数据混入已封存结论，也不能覆盖正式结论。
- 揭盲幂等：已完成后重试沿用同一正式结论，不新建记录；计算阶段失败整体回滚，
  封存范围与参数保留，重试从原进度继续，期间新导入的数据不会混入。
"""
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import Dataset, Job, Trial, TrialBatch
from src.services import business
from src.services.audit import record
from src.utils.serialization import checksum
from src.utils.sealing import ARMS, digest, open_sealed, seal, validate_arms

BLIND_FIELDS = ('blind_code', 'team', 'arm', 'seconds')


def _params(raw):
    allowed = {p['key']: p for p in business.metadata().get('parameters', [])}
    params = {k: p['default'] for k, p in allowed.items()}
    params.update(raw or {})
    for key, p in allowed.items():
        v = params[key]
        if p['type'] == 'number':
            try:
                v = float(v)
                if not (p.get('min', -1e9) <= v <= p.get('max', 1e9)):
                    raise ValueError()
                params[key] = v
            except (ValueError, TypeError):
                raise HTTPException(422, f'{p["label"]} 超出范围或非数值')
        elif str(v) not in [str(o['value']) for o in p.get('options', [])]:
            raise HTTPException(422, f'{p["label"]} 不是有效选项')
    return params


def validate_blind_records(records):
    """Blind intake: only pseudonymous 甲/乙 observations, never route names."""
    codes = set()
    for index, row in enumerate(records, 1):
        missing = [k for k in BLIND_FIELDS if k not in row]
        if missing:
            raise HTTPException(422, f'第 {index} 行缺少盲态字段：{", ".join(missing)}')
        if set(row) != set(BLIND_FIELDS):
            raise HTTPException(422, f'第 {index} 行含盲态批次不允许的字段，只接受盲码、小组、甲/乙与秒数')
        arm, code = str(row['arm']), str(row['blind_code'])
        if arm not in ARMS:
            raise HTTPException(422, f'第 {index} 行臂别只能是甲或乙')
        if (code, arm) in codes:
            raise HTTPException(422, f'第 {index} 行盲码 {code} 的 {arm} 重复')
        codes.add((code, arm))
        try:
            seconds = float(row['seconds'])
        except (TypeError, ValueError):
            raise HTTPException(422, f'第 {index} 行测时必须为有限数值')
        if seconds <= 0 or seconds != seconds or seconds in (float('inf'), float('-inf')):
            raise HTTPException(422, f'第 {index} 行测时必须为正有限数')
    if len(codes) < 6:
        raise HTTPException(422, '盲态批次至少需要 3 组完整甲乙配对')
    return records


def create(db, user, data):
    params = _params(data.params)
    t = Trial(name=data.name.strip(), params=params, params_checksum=checksum(params),
              status='blind', round_no=0, created_by=user.id, created_at=time.time())
    db.add(t)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '该盲态试验名称已存在，请修改名称')
    record(db, user.email, 'trial.create', {'id': t.id, 'params': params})
    return t


def lock(db, trial_id):
    """Row-lock the trial so registration, blind computation and unblinding serialize."""
    return db.scalar(select(Trial).where(Trial.id == trial_id).with_for_update())


def register_batch(db, user, trial, data, arms):
    """Custodian-only. Validates the blind batch and seals this batch's direction."""
    try:
        arms = validate_arms(arms)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    blind = db.get(Dataset, data.dataset_id)
    if not blind:
        raise HTTPException(404, '盲态数据批次不存在')
    if blind.kind != 'blind':
        raise HTTPException(422, '只能登记盲态测时批次；原始前后测批次请先按盲码重新采集')
    if trial.status != 'blind':
        raise HTTPException(409, '试验已进入揭盲，不能再登记新批次')
    if trial.sealed_at:
        raise HTTPException(409, '试验范围已封存，不能再纳入新批次')
    if db.scalar(select(TrialBatch).where(TrialBatch.trial_id == trial.id,
                                          TrialBatch.dataset_id == blind.id)):
        raise HTTPException(409, '该数据批次已纳入本试验')
    validate_blind_records(blind.records)
    try:
        sealed = seal(arms, trial_id=trial.id, dataset_id=blind.id,
                      checksum=blind.checksum, rows=len(blind.records))
        seal_digest = digest(arms)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    tb = TrialBatch(trial_id=trial.id, dataset_id=blind.id, dataset_name=blind.name,
                    blind_name=blind.name, dataset_checksum=blind.checksum,
                    row_count=len(blind.records), mapping_sealed=sealed, seal_digest=seal_digest,
                    registered_by=user.email, registered_at=time.time())
    db.add(tb)
    # Fence out blind jobs queued before this batch became part of the scope.
    trial.round_no = (trial.round_no or 0) + 1
    db.flush()
    record(db, user.email, 'trial.batch.sealed',
           {'trial': trial.id, 'dataset': blind.id, 'rows': len(blind.records), 'seal': seal_digest})
    return tb


def batches(db, trial):
    return list(db.scalars(select(TrialBatch).where(TrialBatch.trial_id == trial.id)
                           .order_by(TrialBatch.id)))


def _latest_completed(db, trial, batch):
    return db.scalar(select(Job).where(Job.trial_id == trial.id, Job.mode == 'blind',
                                       Job.dataset_id == batch.dataset_id, Job.status == 'completed')
                     .order_by(Job.id.desc()))


def blind_jobs(db, trial):
    return list(db.scalars(select(Job).where(Job.trial_id == trial.id, Job.mode == 'blind')
                           .order_by(Job.id.desc())))


def create_blind_jobs(db, trial, user):
    """One blind job per registered batch not already covered by a completed run.

    Completed batches are skipped, so a retry after an interrupted run never
    duplicates the whole trial's computation.
    """
    scope = batches(db, trial)
    if not scope:
        raise HTTPException(409, '盲态试验尚未登记任何批次')
    if trial.status != 'blind':
        raise HTTPException(409, '试验已进入揭盲，不能发起新的盲态计算')
    covered = set(db.scalars(select(Job.dataset_id).where(
        Job.trial_id == trial.id, Job.mode == 'blind', Job.status == 'completed')))
    created = []
    for b in scope:
        if b.dataset_id in covered:
            continue
        j = Job(name=f'{trial.name} · 盲态 {b.dataset_name}', dataset_id=b.dataset_id,
                params=dict(trial.params), created_by=user.id, created_at=time.time(),
                status='queued', mode='blind', trial_id=trial.id, trial_round=trial.round_no)
        db.add(j)
        created.append(j)
    db.flush()
    for j in created:
        record(db, user.email, 'job.blind.create',
               {'id': j.id, 'trial': trial.id, 'batch': j.dataset_id, 'round': j.trial_round})
    return created


def _seal_scope(db, trial):
    """Pin the exact batch set and params once; later registrations stay out."""
    if trial.sealed_at:
        return trial.sealed_batches
    trial.sealed_batches = [{'batch': b.id, 'dataset_id': b.dataset_id, 'checksum': b.dataset_checksum,
                             'rows': b.row_count, 'seal': b.seal_digest} for b in batches(db, trial)]
    trial.sealed_params = dict(trial.params)
    trial.sealed_at = time.time()
    db.flush()
    record(db, 'trial', 'trial.scope.sealed', {'trial': trial.id, 'batches': len(trial.sealed_batches)})
    return trial.sealed_batches


def prepare_unblind(db, trial):
    """Validate and freeze the scope in its own committed step.

    Freezing before the (possibly long) formal computation means data imported
    or registered during the unblinding cannot join the conclusion, and an
    interrupted unblinding resumes against the same pinned scope instead of
    re-snapshotting a wider one. Returns False when the trial was already
    unblinded, so a retry reuses the existing formal conclusion.
    """
    if trial.status == 'unblinded' and trial.formal_result:
        return False
    sealed = _seal_scope(db, trial)
    if not sealed:
        raise HTTPException(409, '盲态试验尚未登记任何批次')
    current = {b.id: b for b in batches(db, trial)}
    if any(s['batch'] not in current for s in sealed):
        raise HTTPException(409, '封存范围中的批次已缺失，无法揭盲')
    for snap in sealed:
        b = current[snap['batch']]
        if b.dataset_checksum != snap['checksum']:
            raise HTTPException(409, f'批次 {b.dataset_name} 与封存指纹不一致，已停止揭盲')
        try:
            open_sealed(b.mapping_sealed, trial_id=trial.id, dataset_id=b.dataset_id,
                        checksum=b.dataset_checksum, rows=b.row_count)
        except ValueError:
            # Never echo the sealed value or the decryption context.
            raise HTTPException(409, f'批次 {b.dataset_name} 的封存映射校验失败，请联系保管员核对')
        job = _latest_completed(db, trial, b)
        if not job or not job.result:
            # Raising rolls back the just-taken snapshot too, so the custodian can
            # still finish the blind run and the scope is pinned on the real attempt.
            raise HTTPException(409, f'批次 {b.dataset_name} 尚无可用的盲态结论，请先完成盲态计算')
    trial.status = 'unblinding'
    trial.unblind_started_at = trial.unblind_started_at or time.time()
    db.flush()
    return True


def compute_formal(db, user, trial):
    """Restore direction per batch from that batch's own sealed arms, then merge."""
    sealed = trial.sealed_batches
    current = {b.id: b for b in batches(db, trial)}
    restored_rows, per_batch = [], []
    for snap in sealed:
        b = current[snap['batch']]
        arms = open_sealed(b.mapping_sealed, trial_id=trial.id, dataset_id=b.dataset_id,
                           checksum=b.dataset_checksum, rows=b.row_count)
        job = _latest_completed(db, trial, b)
        if not job or not job.result:
            raise HTTPException(409, f'批次 {b.dataset_name} 尚无可用的盲态结论，请先完成盲态计算')
        try:
            restored = _restore(job.result, arms, b)
        except (KeyError, TypeError, ValueError):
            raise HTTPException(409, f'批次 {b.dataset_name} 的盲态结论不可用，请重新计算后再揭盲')
        b.formal_result = {'blind_job': job.id, 'direction': restored['direction'],
                           'rows': restored['records']}
        db.flush()
        per_batch.append({k: restored[k] for k in ('batch', 'dataset_name', 'blind_name', 'rows', 'direction')})
        restored_rows.extend({'operator': f'批{b.id}·{r["operator"]}', 'team': r['team'],
                              'before': r['before'], 'after': r['after']} for r in restored['records'])
    formal = business.analyze(restored_rows, trial.sealed_params)
    restored_summary = formal['summary'].replace('平均节省', '原路线相对新路线平均节省', 1)
    formal.update({'blind': False, 'trial_id': trial.id, 'trial_name': trial.name,
                   'params': trial.sealed_params, 'params_checksum': trial.params_checksum,
                   'sealed_batches': sealed, 'per_batch': per_batch,
                   'summary': f'揭盲后 {len(restored_rows)} 人完整配对，方向按各批封存映射分别还原；' + restored_summary,
                   'findings': ['方向按每批各自封存映射还原，未对整批统一翻转。'] + formal['findings'],
                   'method': formal['method'] + ' 揭盲阶段仅在服务端按各批封存映射还原原/新方向；盲态结果本身不含方向。',
                   'warnings': ['正式结论仅来自封存批次与封存参数；揭盲后新导入的数据需另建试验。']})
    trial.formal_result = formal
    trial.formal_checksum = checksum(formal)
    formal_job = db.get(Job, trial.formal_job_id) if trial.formal_job_id else None
    if formal_job:
        formal_job.result = formal
        formal_job.status = 'completed'
        formal_job.finished_at = time.time()
    else:
        formal_job = Job(name=f'{trial.name} · 正式结论', dataset_id=None, params=dict(trial.sealed_params),
                         status='completed', mode='formal', trial_id=trial.id,
                         result=formal, created_by=user.id, created_at=time.time(), finished_at=time.time())
        db.add(formal_job)
        db.flush()
        trial.formal_job_id = formal_job.id
    trial.status = 'unblinded'
    trial.unblinded_at = time.time()
    trial.unblinded_by = user.email
    trial.round_no = (trial.round_no or 0) + 1
    db.flush()
    record(db, user.email, 'trial.unblinded',
           {'trial': trial.id, 'batches': [s['batch'] for s in sealed], 'checksum': trial.formal_checksum})
    return trial, True


def blind_analysis(db, trial, dataset_id):
    """Worker blind branch: arm rows of one batch only; reports 甲/乙 difference."""
    if trial.status != 'blind':
        raise ValueError('试验已进入揭盲，盲态计算停止')
    batch = db.scalar(select(TrialBatch).where(TrialBatch.trial_id == trial.id,
                                               TrialBatch.dataset_id == dataset_id))
    if not batch:
        raise ValueError('该批次不属于此试验的盲态范围')
    data = db.get(Dataset, batch.dataset_id)
    if not data or data.checksum != batch.dataset_checksum:
        raise ValueError('盲态批次内容与封存指纹不一致，请保管员核对')
    return business.analyze_blind(data.records, trial.params)


def _restore(blind_result, arms, batch):
    """Restore original/new naming for one batch from that batch's own sealed arms."""
    flip = arms['甲'] == '新'
    rows = []
    for row in blind_result['table']['rows']:
        a, b = row['arm_a'], row['arm_b']
        rows.append({'operator': row['blind_code'], 'team': row['team'],
                     'before': b if flip else a, 'after': a if flip else b})
    return {'batch': batch.id, 'dataset_name': batch.dataset_name, 'blind_name': batch.blind_name,
            'rows': len(rows), 'direction': '甲=新，乙=原' if flip else '甲=原，乙=新',
            'records': rows}
