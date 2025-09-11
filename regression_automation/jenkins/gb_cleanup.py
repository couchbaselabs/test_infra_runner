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

def mark_deleted_and_collect(entries, job_name, build_no, delete_all, changed):
    for entry in entries:
        if isinstance(entry, dict) and entry.get("displayName") == job_name:
            if delete_all:
                if not entry.get("deleted", False):
                    entry["deleted"] = True
                    changed.append(entry.get("build_id"))
            elif build_no is not None and entry.get("build_id") == build_no:
                if not entry.get("deleted", False):
                    entry["deleted"] = True
                    changed.append(entry.get("build_id"))
                entry["olderBuild"] = True

def find_best(entries, job_name):
    candidates = [
        e for e in entries
        if isinstance(e, dict)
        and e.get("displayName") == job_name
        and not e.get("deleted", False)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda e: (e.get("failCount", float("inf")), -e.get("timestamp", 0)))
    return candidates[0]


def exec_update(obj, job_name, build_no=None, delete_all=False, changed=None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = exec_update(v, job_name, build_no, delete_all, changed)
    elif isinstance(obj, list):
        mark_deleted_and_collect(obj, job_name, build_no, delete_all, changed)

        if not delete_all and build_no is not None and len(obj) > 1:
            best = find_best(obj, job_name)
            if best:
                for entry in obj:
                    if isinstance(entry, dict) and entry.get("displayName") == job_name:
                        if entry is best:
                            entry["olderBuild"] = False
                        else:
                            entry["olderBuild"] = True
    return obj

def delete_job(version, job_name, builds, delete_all=False):
    cluster = Cluster(CB_HOST, ClusterOptions(
        PasswordAuthenticator(CB_USERNAME, CB_PASSWORD)
    ))
    cb = cluster.bucket(CB_BUCKET)
    collection = cb.scope("_default").collection("_default")

    exit_code = 0
    doc_id = f"{version}_server"
    doc = collection.get(doc_id, QueryOptions(timeout=timedelta(seconds=120))).content_as[dict]

    for build_no in builds:
        changed = []
        doc = exec_update(doc, job_name, int(build_no), delete_all, changed)

        if not changed:
            print(f"No matching entries found for job_name={job_name}, build_no={build_no}")
            exit_code = 1

        collection.upsert(doc_id, doc, UpsertOptions(timeout=timedelta(seconds=120)))
        print(f"Updated {len(changed)} entries for job '{job_name}', build(s): {changed}")

    return exit_code

if __name__ == "__main__":
    version = sys.argv[1]
    job_name = sys.argv[2]
    build_no = sys.argv[3] if len(sys.argv) > 3 else None
    delete_all = (len(sys.argv) > 4 and sys.argv[4].lower() == "true")

    if build_no is None and not delete_all:
        delete_all = True

    build_nums = build_no.split(',') if build_no else []
    exit_code = delete_job(version, job_name, build_nums, delete_all)
    sys.exit(exit_code)
