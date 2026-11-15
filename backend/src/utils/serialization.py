import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'kind':d.kind, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','mode','trial_id','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def trial_dict(t):
    return {'id':t.id,'name':t.name,'params':t.params,'params_checksum':t.params_checksum,'status':t.status,
            'round':t.round_no,'created_at':t.created_at,'sealed_at':t.sealed_at,
            'unblind_started_at':t.unblind_started_at,'unblinded_at':t.unblinded_at,'unblinded_by':t.unblinded_by,
            'sealed_batches':t.sealed_batches,'sealed_params':t.sealed_params,'formal_checksum':t.formal_checksum}
def trial_batch_dict(b):
    # mapping_sealed is ciphertext; the per-batch direction is never returned in any form.
    return {'id':b.id,'trial_id':b.trial_id,'dataset_id':b.dataset_id,'dataset_name':b.dataset_name,
            'blind_name':b.blind_name,'rows':b.row_count,'checksum':b.dataset_checksum,'seal_digest':b.seal_digest,
            'registered_by':b.registered_by,'registered_at':b.registered_at,
            'formal_ready':bool(b.formal_result)}
