from datetime import timedelta
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, QueryOptions, UpsertOptions
import sys
import os
import json

CB_HOST = "couchbase://172.23.120.87"
CB_BUCKET = "greenboard"
CB_USERNAME = "Administrator"
CB_PASSWORD = "esabhcuoc"

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

def find_all_job_occurrences(obj, job_name):
    occurrences = []
    
    if isinstance(obj, dict):
        if job_name in obj:
            occurrences.append(obj[job_name])
        for value in obj.values():
            occurrences.extend(find_all_job_occurrences(value, job_name))
    elif isinstance(obj, list):
        for item in obj:
            occurrences.extend(find_all_job_occurrences(item, job_name))
    
    return occurrences

def delete_pending_job(version, job_name):
    cluster = Cluster(CB_HOST, ClusterOptions(
        PasswordAuthenticator(CB_USERNAME, CB_PASSWORD)
    ))
    cb = cluster.bucket(CB_BUCKET)
    collection = cb.scope("_default").collection("_default")

    doc_id = "existing_builds_server"
    doc = collection.get(doc_id, QueryOptions(timeout=timedelta(seconds=120))).content_as[dict]
    
    version_number = version.split('-')[0]
    
    job_occurrences = find_all_job_occurrences(doc, job_name)
    
    if not job_occurrences:
        print(f"Job '{job_name}' not found in existing_builds_server document")
        return 1
    
    version_found = False
    for i, job_entry in enumerate(job_occurrences):
        if "jobs_in" in job_entry and version_number in job_entry["jobs_in"]:
            job_entry["jobs_in"].remove(version_number)
            print(f"Removed version '{version_number}' for job '{job_name}'")
            version_found = True
    
    if not version_found:
        print(f"Version '{version_number}' not found for job '{job_name}'")
        return 1
    
    collection.upsert(doc_id, doc, UpsertOptions(timeout=timedelta(seconds=120)))
    print(f"Updated existing_builds_server document in cluster")
    
    return 0

if __name__ == "__main__":
    version = sys.argv[1]
    job_name = sys.argv[2]
    build_no = sys.argv[3] if len(sys.argv) > 3 else None
    delete_all = (len(sys.argv) > 4 and sys.argv[4].lower() == "true")
    is_pending = (len(sys.argv) > 5 and sys.argv[5].lower() == "true")

    if is_pending:
        exit_code = delete_pending_job(version, job_name)
    else:
        if build_no is None and not delete_all:
            delete_all = True

        build_nums = build_no.split(',') if build_no else []
        exit_code = delete_job(version, job_name, build_nums, delete_all)
    
    sys.exit(exit_code)