function OnUpdate(doc, meta, xattrs) {
    var meta = {"id": meta.id};
    var failed=false
    try{
    couchbase.mutateIn(dst_bucket, meta, [couchbase.MutateInSpec.upsert("path",1 , { "xattrs": true })]);
    }
    catch (e) {
        failed=true
    }
    if (!failed){
        dst_bucket[meta.id]="success"
    }
}

function OnDelete(meta, options) {
    log("Doc deleted/expired", meta.id);
}