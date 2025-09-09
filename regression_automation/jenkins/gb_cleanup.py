from datetime import timedelta
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, QueryOptions, UpsertOptions
import sys
import os

CB_HOST = os.getenv("CB_HOST")
CB_BUCKET = "greenboard"
CB_USERNAME = os.getenv("CB_USERNAME")
CB_PASSWORD = os.getenv("CB_PASSWORD")

def exec_update(obj, job_name, build_no=None, delete_all=False, changed=None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = exec_update(v, job_name, build_no, delete_all, changed)
    elif isinstance(obj, list):
        for entry in obj:
            if isinstance(entry, dict):
                if entry.get("displayName") == job_name:
                    if delete_all:
                        if not entry.get("deleted", False):
                            entry["deleted"] = True
                            changed.append(entry.get("build_id"))
                    elif build_no is not None and entry.get("build_id") == build_no:
                        if not entry.get("deleted", False):
                            entry["deleted"] = True
                            changed.append(entry.get("build_id"))
            exec_update(entry, job_name, build_no, delete_all, changed)
    return obj

def delete_job(version, job_name, build_no=None, delete_all=False):
    cluster = Cluster(CB_HOST, ClusterOptions(
        PasswordAuthenticator(CB_USERNAME, CB_PASSWORD)
    ))
    cb = cluster.bucket(CB_BUCKET)
    collection = cb.scope("_default").collection("_default")

    doc_id = f"{version}_server"
    doc = collection.get(doc_id, QueryOptions(timeout=timedelta(seconds=120))).content_as[dict]

    changed = []
    doc = exec_update(doc, job_name, build_no, delete_all, changed)

    if not changed:
        print(f"No matching entries found for job_name={job_name}, build_no={build_no}")
        sys.exit(1)

    collection.upsert(doc_id, doc, UpsertOptions(timeout=timedelta(seconds=120)))
    print(f"Updated {len(changed)} entries for job '{job_name}', build(s): {changed}")
    sys.exit(0)


if __name__ == "__main__":
    version = sys.argv[1]
    job_name = sys.argv[2]
    build_no = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else None
    delete_all = (len(sys.argv) > 4 and sys.argv[4].lower() == "true")

    if build_no is None and not delete_all:
        delete_all = True

    delete_job(version, job_name, build_no, delete_all)
