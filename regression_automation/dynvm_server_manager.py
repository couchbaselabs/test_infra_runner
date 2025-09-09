# coding=utf-8
import urllib

import sys
import argparse
import json
import time
import datetime
import configparser
import threading
import logging
from couchbase.cluster import (ClusterOptions, Cluster)
from couchbase.auth import PasswordAuthenticator
import XenAPI
from couchbase.exceptions import DocumentNotFoundException 
from flask import Flask, request
from paramiko import SSHClient, AutoAddPolicy

"""
  --------------------------------------
  *** Dynamic VMs Server Manager API ***
  --------------------------------------
  Get available VMs count:
     http://127.0.0.1:5000/getavailablecount/<os>
  List of VMs:
      http://127.0.0.1:5000/showall
  Provisioning of VMs:
    Single VM: http://127.0.0.1:5000/getservers/<vmname>?os=centos
    Multiple VMs:
    http://127.0.0.1:5000/getservers/<vmnameprefix>?os=centos&count=<count>&format=<[short]|detailed>
    To change the default number of CPUs or RAM, add the below request parameters.
        cpus=<cpucount - 4 or 8 or 16>
        mem=<bytes in size>
        format=<short|detailed> → short (default): gives the response as a json array with IPs
         in similar current serverpool manager or detailed means the response is a json object
         with VM names.
    To check that an SSH connection can be made with each VM, add the checkvms=true parameter

   Termination of VMs:
       Single VM: http://127.0.0.1:5000/releaseservers/<vmname>?os=centos
       Multiple VMs: http://127.0.0.1:5000/releaseservers/<vmnameprefix>?os=centos&count=<count>
"""

log = logging.getLogger("dynvmservice")
ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
log.addHandler(ch)
print("*** Dynamic VMs ***")
app = Flask(__name__)

CONFIG_FILE = '.dynvmservice.ini'
MAX_EXPIRY_MINUTES = 1440
TIMEOUT_SECS = 600
RELEASE_URL = 'http://127.0.0.1:5000/releaseservers/'

reserved_count_by_label = {}

@app.route('/showall/<string:os>')
@app.route("/showall")
def showall_service(os=None):
    if request.args.get('labels'):
        labels = request.args.get('labels').split(",")
    else:
        labels = None

    if request.args.get("ignorelabels"):
        ignore_labels = request.args.get("ignorelabels").lower() == "true"
    else:
        ignore_labels = False

    count, all_xen_hosts = get_all_xen_hosts_count(os, labels, ignore_labels)
    log.info("--> count: {}".format(count))
    all_vms = {}
    for xen_host in all_xen_hosts:
        xen_host_ref = int(xen_host["name"][7:])
        log.info("Getting xen_host_ref=" + str(xen_host_ref))
        all_vms[xen_host_ref] = perform_service(xen_host_ref, service_name='listvms', os=os)
    return json.dumps(all_vms, indent=2, sort_keys=True)


@app.route('/getavailablecount/<string:os>')
@app.route('/getavailablecount')
def getavailable_count_service(os='centos'):
    """
    Calculate the available count:
        Get Total CPUs, Total Memory
        Get Free CPUs, Free Memory
        Get all the VMs - CPUs and Memory allocated
        Get each OS template - CPUs and Memory
        Available count1 = (Free CPUs - VMs CPUs)/OS_Template_CPUs
        Available count2 = (Free Memory - VMs Memory)/OS_Template_Memory
        Return min(count1,count2)
    """

    if request.args.get('labels'):
        labels = request.args.get('labels').split(",")
    else:
        labels = None

    if request.args.get("alllabels"):
        get_all_labels = request.args.get("alllabels").lower() == "true"
    else:
        get_all_labels = False

    if request.args.get("ignorelabels"):
        ignore_labels = request.args.get("ignorelabels").lower() == "true"
    else:
        ignore_labels = False
    
    if get_all_labels:
        all_labels = set()
        _, xen_hosts = get_all_xen_hosts_count(os, ignore_labels=True)
        for xen_host in xen_hosts:
            if "host.labels" in xen_host and xen_host["host.labels"] is not None:
                for label in xen_host["host.labels"]:
                    all_labels.add(label)
        response = []
        for label in all_labels:
            count, _, _ = get_all_available_count(os, [label])
            response.append({
                "label": label,
                "count": count
            })
        count, _, _ = get_all_available_count(os)
        response.append({
            "label": "default",
            "count": count
        })
        return json.dumps(response)

    reserved_count = get_reserved(labels)
    count, available_counts, xen_hosts = get_all_available_count(os, labels, ignore_labels)
    log.info("DEBUG 1")
    log.info("{},{},{},{}".format(count, available_counts, xen_hosts, reserved_count))
    # Subtract reserved_count and return 0 if negative
    count = max(count - reserved_count, 0)
    log.info("Less reserved count: {},{},{},{}".format(count, available_counts, xen_hosts,
                                                 reserved_count))
    return str(count)

def check_vm(os_name, host):
    config = read_config()
    if os_name == "windows":
        username = config.get("common", "vm.windows.username")
        password = config.get("common", "vm.windows.password")
    else:
        username = config.get("common", "vm.linux.username")
        password = config.get("common", "vm.linux.password")
    try:
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        client.connect(
            host,
            username=username,
            password=password,
            timeout=10
        )
    except Exception as e:
        log.warning("ssh error: " + str(e))
        return False
    return True

def host_is_overprovisioned(xhostref, os_name, additional_capacity=0):
    try:
        provisioned_vms = len(perform_service(xhostref, "listvms", os_name))
        xhost = get_xen_host(xhostref, os_name)
        return provisioned_vms + additional_capacity > xhost["host.vms.max." + os_name]
    except KeyError:
        return False


def create_vms_single_host(checkvms, xhostref, os_name, vm_names,
                                       cpus, maxmemory, expiry_minutes,
                                       output_format, pools=None, networkid=None, labels=None):
    global reserved_count

    if host_is_overprovisioned(xhostref, os_name, len(vm_names)):
        raise Exception("Host is overprovisioned, skipping")

    vms_info = perform_service(xhostref, 'createvm', os_name, vm_names,
                                        cpus, maxmemory, expiry_minutes,
                                        output_format, pools, networkid)
    if isinstance(vms_info, str):
        raise Exception(vms_info)

    # vms_info key is either vm_name or vm_name_error, value is ip, "" or error
    # success ips are where key does not end with _error and ip != ""

    success_ips = []
    success_vm_names = []

    for [vm_name, ip] in vms_info.items():
        if vm_name.endswith("_error") or ip == "":
            continue
        delete_vm = False
        deleted_reason = None
        if checkvms and not check_vm(os_name, ip):
            deleted_reason = "VM check failed for {}".format(ip)
            log.info(deleted_reason)
            delete_vm = True
        if host_is_overprovisioned(xhostref, os_name):
            deleted_reason = "Host {} is overprovisioned".format(xhostref)
            log.info(deleted_reason)
            delete_vm = True
        if delete_vm:
            vms_info[vm_name] = ""
            vms_info[vm_name + "_error"] = deleted_reason
            # need to increase reserved_count because it was 
            # decreased when the vm was created successfully
            increase_reserved(1, labels)
            # delete vm because ssh check failed or host overprovisioned
            try:
                perform_service(xhostref, 'deletevm', os_name, vm_name, 1)
            except Exception:
                log.error("couldn't delete vm, will be deleted after expiry")
                pass
            continue
        success_ips.append(ip)
        success_vm_names.append(vm_name)

    return success_ips, success_vm_names, vms_info


def generate_vm_names(name, count):
    if count == 0:
        return []
    elif count == 1:
        return [name]
    else:
        return [name + str(i) for i in range(1, count + 1)]


def increase_reserved(count, labels):
    global reserved_count_by_label
    if labels:
        for label in labels:
            if label in reserved_count_by_label:
                reserved_count_by_label[label] += count
            else:
                reserved_count_by_label[label] = count
    else:
        label = None
        if label in reserved_count_by_label:
            reserved_count_by_label[label] += count
        else:
            reserved_count_by_label[label] = count



def decrease_reserved(count, labels):
    global reserved_count_by_label
    if labels:
        for label in labels:
            if label in reserved_count_by_label:
                reserved_count_by_label[label] = max(0, reserved_count_by_label[label] - count)
    else:
        label = None
        if label in reserved_count_by_label:
            reserved_count_by_label[label] = max(0, reserved_count_by_label[label] - count)


def get_reserved(labels):
    reserved = 0
    if labels:
        for label in labels:
            if label in reserved_count_by_label:
                reserved = max(reserved, reserved_count_by_label[label])
    else:
        label = None
        if label in reserved_count_by_label:
            reserved = reserved_count_by_label[label]
    return reserved



# /getservers/username?count=number&os=centos&ver=6&expiresin=30&checkvms=true
@app.route('/getservers/<string:username>')
def getservers_service(username):
    if request.args.get('count'):
        vm_count = int(request.args.get('count'))
    else:
        vm_count = 1
    os_name = request.args.get('os')
    if request.args.get('cpus'):
        cpus_count = request.args.get('cpus')
    else:
        cpus_count = "default"

    if request.args.get('mem'):
        mem = request.args.get('mem')
    else:
        mem = "default"

    if request.args.get('expiresin'):
        exp = int(request.args.get('expiresin'))
    else:
        exp = MAX_EXPIRY_MINUTES

    if request.args.get('format'):
        output_format = request.args.get('format')
    else:
        output_format = "servermanager"

    if request.args.get('checkvms'):
        checkvms = request.args.get('checkvms').lower() == "true"
    else:
        checkvms = False

    if request.args.get('allornone'):
        all_or_none = request.args.get('allornone').lower() == "true"
    else:
        all_or_none = True

    if request.args.get("pools"):
        pools = request.args.get("pools").split(",")
    else:
        pools = None

    if request.args.get("networkid"):
        networkid = request.args.get("networkid")
    else:
        networkid = None 

    if request.args.get('labels'):
        labels = request.args.get('labels').split(",")
    else:
        labels = None

    if request.args.get("ignorelabels"):
        ignore_labels = request.args.get("ignorelabels").lower() == "true"
    else:
        ignore_labels = False

    xhostref = None
    if request.args.get('xhostref'):
        xhostref = request.args.get('xhostref')
    increase_reserved(vm_count, labels)

    vm_names = generate_vm_names(username, vm_count)

    # if xhostref is specified we do not move on to another host
    if xhostref:
        log.info("-->  VMs on given xenhost" + xhostref)
        try:
            ips, success_vm_names, vms_info = create_vms_single_host(checkvms, xhostref, os_name, vm_names, cpus_count, mem, exp, output_format, pools=pools, networkid=networkid, labels=labels)
            decrease_reserved(vm_count - len(ips), labels)
        except Exception as e:
            decrease_reserved(vm_count, labels)
            return str(e), 499
        else:
            if len(ips) != vm_count and all_or_none:
                log.warning("deleting all created vms due to failure")
                try:
                    release_servers(username, os_name, vm_count)
                except Exception:
                    pass
                return "all vms couldn't be created successfully", 499
            elif output_format == 'detailed':
                return json.dumps(vms_info)
            else:
                return json.dumps(ips)

    # TBD consider cpus/mem later
    count, available_counts, xen_hosts_available_refs = get_all_available_count(os_name, labels, ignore_labels)
    # Subtract reserved_count and return 0 if negative
    count = max(count - (get_reserved(labels) - vm_count), 0)
    log.info("{}, {}".format(available_counts, xen_hosts_available_refs))
    if vm_count > count:
        decrease_reserved(vm_count, labels)
        return "Error: No capacity is available! {} reserved_count={}".format(str(available_counts), get_reserved(labels))
    
    log.info("--> Distributing VMs among multiple xen hosts")
    merged_vms_list = []
    merged_vms_info = {}
    need_vm_names = set(vm_names)
    for index in range(0, len(available_counts)):
        need_vms = len(need_vm_names)
        if need_vms == 0:
            break
        per_xen_host_vms = available_counts[index]
        if per_xen_host_vms > 0:
            free_xenhost_ref = int(xen_hosts_available_refs[index].split(':')[0])
            if need_vms <= per_xen_host_vms:
                per_xen_host_vms = need_vms
            log.info("Creating {} out of {} VMs on xenhost{}".format(per_xen_host_vms, need_vms, free_xenhost_ref))
            try:
                per_xen_host_res, success_vm_names, vms_info = create_vms_single_host(checkvms, free_xenhost_ref, os_name, list(need_vm_names)[:per_xen_host_vms], cpus_count, mem, exp, output_format, pools=pools, networkid=networkid, labels=labels)
            except Exception as e:
                log.debug(str(e))
                continue
            else:
                merged_vms_info.update(vms_info)
                for ip in per_xen_host_res:
                    merged_vms_list.append(ip)
                log.info(per_xen_host_res)
                # Remove successful vms from set of names
                for vm_name in success_vm_names:
                    need_vm_names.remove(vm_name)

    decrease_reserved(vm_count - len(merged_vms_list), labels)

    if (len(merged_vms_list) != vm_count) and all_or_none:
        log.warning("deleting all created vms due to failure")
        try:
            release_servers(username, os_name, vm_count)
        except Exception:
            pass
        return "all vms couldn't be created successfully", 499

    # remove error entries if all or none when vms are successful
    if all_or_none:
        for key in list(merged_vms_info.keys()):
            if key.endswith("_error"):
                log.info("Removing error {} from response as vms created successfully".format(merged_vms_info[key]))
                del merged_vms_info[key]

    if output_format == 'detailed':
        return json.dumps(merged_vms_info, indent=2, sort_keys=True)
    else:
        return json.dumps(merged_vms_list)

def release_servers(username, os_name, vm_count):
    delete_vms_res = []
    vm_names = generate_vm_names(username, vm_count)
    for vm_name in vm_names:
        while True:
            xen_host_ref = get_vm_existed_xenhost_ref(vm_name, 1, None)
            if xen_host_ref != 0:
                log.info("VM to be deleted from xhost_ref=" + str(xen_host_ref))
                delete_per_xen_res = perform_service(xen_host_ref, 'deletevm', os_name, [vm_name])
                for deleted_vm_res in delete_per_xen_res:
                    delete_vms_res.append(deleted_vm_res)
            else:
                break
    return delete_vms_res

# /releaseservers/{username}
@app.route('/releaseservers/<string:username>/<string:target_state>')
def releaseservers_service_state(username, target_state):
    return releaseservers_service(username, target_state)

@app.route('/releaseservers/<string:username>')
def releaseservers_service(username, target_state=None):
    delete_prefixed_vms = False
    if request.args.get('count'):
        vm_count = int(request.args.get('count'))
    else:
        vm_count = 1
        if target_state == "available":
            delete_prefixed_vms=True
            vm_count=9 #TBD: how to get the matched prefixes

    os_name = request.args.get('os')
    delete_vms_res = release_servers(username, os_name, vm_count)
    if len(delete_vms_res) < 1:
        return "Error: VM " + username + " doesn't exist"
    else:
        return json.dumps(delete_vms_res, indent=2, sort_keys=True)

@app.route("/setexpiry/<string:username>/<int:expiresin>")
def setexpiry_service(username, expiresin):
    if request.args.get("count"):
        vm_count = int(request.args.get("count"))
        names = [username + str(i) for i in range(1, vm_count + 1)]
    else:
        names = [username]
    if request.args.get("from"):
        valid = ["now", "created"]
        from_reference = request.args.get("from")
        if from_reference not in valid:
            return "Error: from={} is not valid, must be one of: {}".format(from_reference, ",".join(valid)), 499
    else:
        from_reference = "now"
    cb_doc = CBDoc()
    updated_names = cb_doc.set_expired(names, expiresin, from_reference)
    return json.dumps(updated_names)


def perform_service(xen_host_ref=1, service_name='list_vms', os="centos", vm_names=[],
                    cpus="default", maxmemory="default",
                    expiry_minutes=MAX_EXPIRY_MINUTES, output_format="servermanager",
                    pools=None, networkid=None):
    xen_host = get_xen_host(xen_host_ref, os)
    if not xen_host:
        error = "Error: No XenHost available for the OS matching template!"
        log.error(error)
        return error
    # xen_host = get_all_xen_hosts(os)[0]
    url = "http://" + xen_host['host.name']
    log.info("Xen Server host: " + xen_host['host.name'])
    try:
        session = XenAPI.Session(url)
        session.xenapi.login_with_password(xen_host['host.user'], xen_host['host.password'])
    except XenAPI.Failure as f:
        error = "Failed to acquire a session: {}".format(f.details)
        log.error(error)
        return error
    if os is not None:
        log.info("Getting template " + os + '.template')
        try:
            template = xen_host[os + '.template']  # type: object
        except:
            log.info("Template is not found!")
            return []
    try:
        if service_name == 'createvm':
            network = networkid or xen_host["host.network.id"] or xen_host[os + ".template.network"]
            labels = xen_host["host.labels"]
            log.debug("Creating from {0} :{1}, cpus: {2}, memory: {3}".format(str(template),
                                                                              str(vm_names), cpus,
                                                                              maxmemory))
            new_vms, _ = create_vms(session, os, template, network, vm_names,
                                              cpus, maxmemory, expiry_minutes, pools, labels)
            log.info(new_vms)
            return new_vms
        elif service_name == 'deletevm':
            return delete_vms(session, vm_names)
        elif service_name == 'listvm':
            return list_given_vm_set_details(session, vm_names)
        elif service_name == 'listvms':
            return list_vms(session)
        elif service_name == 'getavailablecount':
            return get_available_count(session, os, xen_host)
        else:
            list_vms(session)
    except Exception as e:
        log.error(str(e))
        raise
    finally:
        session.logout()


def get_config(name):
    config = read_config()
    section_ref = 1
    all_config = []
    for section in config.sections():
        if section.startswith(name):
            section_config = {}
            for key in config.keys():
                section_config[key] = config.get(name + str(section_ref), key)
            all_config.append(section_config)
            section_ref += 1

    return all_config

def get_all_xen_hosts_count(os=None, labels=None, ignore_labels=False):
    config = read_config()
    xen_host_ref_count = 0
    all_xen_hosts = []
    log.info(config.sections())
    for section in config.sections():
        if section.startswith('xenhost'):
            try:
                xen_host_ref_count += 1
                xen_host_ref  = int(section[7:])
                xen_host = get_xen_values(config, xen_host_ref, os)

                if ignore_labels:
                    labels_match = True
                else:
                    if xen_host["host.labels"]:
                        if not labels:
                            labels_match = False
                        else:
                            xen_host_labels = set(xen_host["host.labels"])
                            labels = set(labels)
                            labels_match = len(xen_host_labels.intersection(labels)) > 0
                    else:
                        labels_match = not labels
                
                if xen_host and labels_match:
                    all_xen_hosts.append(xen_host)
                else:
                    xen_host_ref_count -= 1
            except Exception as e:
                xen_host_ref_count -= 1
                log.debug(e)

    return xen_host_ref_count, all_xen_hosts


def get_xen_host(xen_host_ref=1, os='centos'):
    config = read_config()
    return get_xen_values(config, xen_host_ref, os)


def get_xen_values(config, xen_host_ref, os):
    xen_host = {}
    try:
        xen_host['name'] = 'xenhost' + str(xen_host_ref)
        xen_host["host.name"] = config.get('xenhost' + str(xen_host_ref), 'host.name')
        xen_host["host.user"] = config.get('xenhost' + str(xen_host_ref), 'host.user')
        xen_host["host.password"] = config.get('xenhost' + str(xen_host_ref), 'host.password')
        xen_host["host.storage.name"] = config.get('xenhost' + str(xen_host_ref),
                                                   'host.storage.name')
        if config.has_option('xenhost' + str(xen_host_ref), 'host.network.id'):
            xen_host["host.network.id"] = config.get('xenhost' + str(xen_host_ref), 'host.network.id')
        else:
            xen_host["host.network.id"] = None
        if config.has_option('xenhost' + str(xen_host_ref), 'host.labels'):
            xen_host["host.labels"] = config.get('xenhost' + str(xen_host_ref), 'host.labels').split(",")
        else:
            xen_host["host.labels"] = None
        if os is not None:
            xen_host[os + ".template"] = config.get('xenhost' + str(xen_host_ref), os + '.template')
            if config.has_option('xenhost' + str(xen_host_ref), os + '.template' + '.network'):
                xen_host[os + ".template.network"] = config.get('xenhost' + str(xen_host_ref), os + '.template' + '.network')
            else:
                xen_host[os + ".template.network"] = None
            if config.has_option("xenhost" + str(xen_host_ref), "host.vms.max." + os):
                xen_host["host.vms.max." + os] = int(config.get("xenhost" + str(xen_host_ref), "host.vms.max." + os))
            if config.has_option("xenhost" + str(xen_host_ref),  os + ".vcpus"):
                xen_host[os + ".vcpus"] = int(config.get("xenhost" + str(xen_host_ref), os + ".vcpus"))
            else:
                if os.startswith('win'):
                    xen_host[os + ".vcpus"] = 12 #default vcpus 
                else:
                    xen_host[os + ".vcpus"] = 8
            if config.has_option("xenhost" + str(xen_host_ref),  os + ".memory"):
                xen_host[os + ".memory"] = int(config.get("xenhost" + str(xen_host_ref), os + ".memory"))
            else:
                if os.startswith('win'):
                    xen_host[os + ".memory"] = 6 #default memory 
                else:
                    xen_host[os + ".memory"] = 4
            if config.has_option("xenhost" + str(xen_host_ref),  os + ".disk"):
                xen_host[os + ".disk"] = int(config.get("xenhost" + str(xen_host_ref), os + ".disk"))
            else:
                if os.startswith('win'):
                    xen_host[os + ".disk"] = 71 #default disk 
                else:
                    xen_host[os + ".disk"] = 35
    except Exception as e:
        log.info("--> check for template and other values in the .ini file!")
        log.info(e)
        xen_host = None
    return xen_host


def read_config():
    config = configparser.RawConfigParser()
    config.read(CONFIG_FILE)
    log.info(config.sections())
    return config


def usage():
    print("""\
Usage Syntax: dynxenvms -h or options

Examples:
 python dynxenvms.py -h
 python dynxenvms.py -x xenserver -u root -p pwd -c vmname -n count"
""")
    sys.exit(0)


def set_log_level(log_level='debug'):
    if log_level and log_level.lower() == 'info':
        log.setLevel(logging.INFO)
    elif log_level and log_level.lower() == 'warning':
        log.setLevel(logging.WARNING)
    elif log_level and log_level.lower() == 'debug':
        log.setLevel(logging.DEBUG)
    elif log_level and log_level.lower() == 'critical':
        log.setLevel(logging.CRITICAL)
    elif log_level and log_level.lower() == 'fatal':
        log.setLevel(logging.FATAL)
    else:
        log.setLevel(logging.NOTSET)


def list_vms(session):
    vm_count = 0
    vms = session.xenapi.VM.get_all()
    log.info("Server has {} VM objects (this includes templates):".format(len(vms)))
    log.info("-----------------------------------------------------------")
    log.info("S.No.,VMname,PowerState,Vcpus,MaxMemory,Networkinfo,Description")
    log.info("-----------------------------------------------------------")

    vm_details = []

    for vm in vms:
        network_info = 'N/A'
        record = session.xenapi.VM.get_record(vm)
        if not (record["is_a_template"]) and not (record["is_control_domain"]):
            log.debug(record)
            vm_count = vm_count + 1
            name = record["name_label"]
            name_description = record["name_description"]
            power_state = record["power_state"]
            vcpus = record["VCPUs_max"]
            memory_static_max = record["memory_static_max"]
            if record["power_state"] != 'Halted':
                ip_ref = session.xenapi.VM_guest_metrics.get_record(record['guest_metrics'])
                network_info = ','.join([str(elem) for elem in ip_ref['networks'].values()])
            else:
                continue  # Listing only Running VMs

            vm_info = {'name': name, 'power_state': power_state, 'vcpus': vcpus,
                       'memory_static_max': memory_static_max, 'networkinfo': network_info,
                       'name_description': name_description}
            vm_details.append(vm_info)
            log.info(vm_info)

    log.info("Server has {} VM objects and {} templates.".format(vm_count, len(vms) - vm_count))
    log.debug(vm_details)
    return vm_details


def list_vm_details(session, vm_name):
    vm = session.xenapi.VM.get_by_name_label(vm_name)
    if len(vm) > 0:
        record = session.xenapi.VM.get_record(vm[0])
        name_description = record["name_description"]
        power_state = record["power_state"]
        vcpus = record["VCPUs_max"]
        memory_static_max = record["memory_static_max"]
        if record["power_state"] != 'Halted':
            ip_ref = session.xenapi.VM_guest_metrics.get_record(record['guest_metrics'])
            networkinfo = ','.join([str(elem) for elem in ip_ref['networks'].values()])
        else:
            networkinfo = 'N/A'
        log.info(
            vm_name + "," + power_state + "," + vcpus + "," + memory_static_max + ","
            + networkinfo + ", " + name_description)


def create_vms(session, os, template, network, vm_names, cpus="default",
               maxmemory="default", expiry_minutes=MAX_EXPIRY_MINUTES, pools=None, labels=None):
    new_vms_info = {}
    list_of_vms = []
    for vm_name in vm_names:
        vm_ip, vm_os, error = create_vm(session, os, template, network, vm_name, cpus, maxmemory,
                                        expiry_minutes, pools, labels)
        new_vms_info[vm_name] = vm_ip
        list_of_vms.append(vm_ip)
        if error:
            new_vms_info[vm_name + "_error"] = error
            list_of_vms.append(error)
        else:
            decrease_reserved(1, labels)
             
    return new_vms_info, list_of_vms


def create_vm(session, os_name, template, network, new_vm_name, cpus="default", maxmemory="default",
              expiry_minutes=MAX_EXPIRY_MINUTES, pools=None, labels=None):
    error = ''
    vm_os_name = ''
    vm_ip_addr = ''
    prov_start_time = time.time()
    try:
        log.info("\n--- Creating VM: " + new_vm_name + " using " + template)
        pifs = session.xenapi.PIF.get_all_records()
        log.debug(pifs)
        lowest = None
        if network:
            for pifRef in pifs.keys():
                if network in pifs[pifRef]['device']:
                    lowest = pifRef
                    log.debug("using custom network: {} {}".format(network, pifs[lowest]['device']))
                    break
        if lowest is None:
            for pifRef in pifs.keys():
                if (lowest is None) or (pifs[pifRef]['device'] < pifs[lowest]['device']):
                    lowest = pifRef
        log.debug("Choosing PIF with device: {}".format(pifs[lowest]['device']))
        ref = lowest
        mac = pifs[ref]['MAC']
        device = pifs[ref]['device']
        mode = pifs[ref]['ip_configuration_mode']
        ip_addr = pifs[ref]['IP']
        net_mask = pifs[ref]['IP']
        gateway = pifs[ref]['gateway']
        dns_server = pifs[ref]['DNS']
        log.debug("{},{},{},{},{},{},{}".format(mac, device, mode, ip_addr, net_mask, gateway,
                                                dns_server))
        # List all the VM objects
        vms = session.xenapi.VM.get_all_records()
        log.debug("Server has {} VM objects (this includes templates)".format(len(vms)))

        templates = []
        all_templates = []
        for vm in vms:
            record = vms[vm]
            res_type = "VM"
            if record["is_a_template"]:
                res_type = "Template"
                all_templates.append(vm)
                # Look for a given template
                if record["name_label"] == template:
                    templates.append(vm)
                    log.debug(" Found %8s with name_label = %s" % (res_type, record["name_label"]))

        log.debug("Server has {} Templates and {} VM objects.".format(len(all_templates),
                                                                      len(vms) - len(
                                                                          all_templates)))

        log.debug("Choosing a {} template to clone".format(template))
        if not templates:
            log.error("Could not find any {} templates. Exiting.".format(template))
            sys.exit(1)

        template_ref = templates[0]
        log.debug("  Selected template: {}".format(session.xenapi.VM.get_name_label(template_ref)))

        # Retries when 169.x address received
        ipaddr_max_retries = 3
        retry_count = 1
        is_local_ip = True
        vm_ip_addr = ""
        while is_local_ip and retry_count != ipaddr_max_retries:
            log.info("Installing new VM from the template - attempt #{}".format(retry_count))
            vm = session.xenapi.VM.clone(template_ref, new_vm_name)

            network = session.xenapi.PIF.get_network(lowest)
            log.debug("Chosen PIF is connected to network: {}".format(
                session.xenapi.network.get_name_label(network)))
            vifs = session.xenapi.VIF.get_all()
            log.debug(("Number of VIFs=" + str(len(vifs))))
            for i in range(len(vifs)):
                vmref = session.xenapi.VIF.get_VM(vifs[i])
                a_vm_name = session.xenapi.VM.get_name_label(vmref)
                log.debug(str(i) + "." + session.xenapi.network.get_name_label(
                    session.xenapi.VIF.get_network(vifs[i])) + " " + a_vm_name)
                if a_vm_name == new_vm_name:
                    session.xenapi.VIF.move(vifs[i], network)

            log.debug("Adding non-interactive to the kernel commandline")
            session.xenapi.VM.set_PV_args(vm, "non-interactive")
            log.debug("Choosing an SR to instantiate the VM's disks")
            pool = session.xenapi.pool.get_all()[0]
            default_sr = session.xenapi.pool.get_default_SR(pool)
            #default_sr = session.xenapi.SR.get_record(default_sr)
            #log.debug("Choosing SR: {} (uuid {})".format(default_sr['name_label'], default_sr['uuid']))
            log.debug("Asking server to provision storage from the template specification")
            description = new_vm_name + " from " + template + " on " + str(datetime.datetime.utcnow())
            session.xenapi.VM.set_name_description(vm, description)
            if cpus != "default":
                log.info("Setting cpus to " + cpus)
                session.xenapi.VM.set_VCPUs_max(vm, int(cpus))
                session.xenapi.VM.set_VCPUs_at_startup(vm, int(cpus))
            if maxmemory != "default":
                log.info("Setting memory to " + maxmemory)
                session.xenapi.VM.set_memory(vm, maxmemory)  # 8GB="8589934592" or 4GB="4294967296"
            session.xenapi.VM.provision(vm)
            log.info("Starting VM")
            session.xenapi.VM.start(vm, False, True)
            log.debug("  VM is booting")

            log.debug("Waiting for the installation to complete")

            # Get the OS Name and IPs
            log.info("Getting the OS Name and IP...")
            config = read_config()
            vm_network_timeout_secs = int(config.get("common", "vm.network.timeout.secs"))
            if vm_network_timeout_secs > 0:
                TIMEOUT_SECS = vm_network_timeout_secs

            log.info("Max wait time in secs for VM OS address is {0}".format(str(TIMEOUT_SECS)))
            if "win" not in template:
                maxtime = time.time() + TIMEOUT_SECS
                while read_os_name(session, vm) is None and time.time() < maxtime:
                    time.sleep(1)
                vm_os_name = read_os_name(session, vm)
                log.info("VM OS name: {}".format(vm_os_name))
            else:
                # TBD: Wait for network to refresh on Windows VM
                time.sleep(60)

            log.info("Max wait time in secs for IP address is " + str(TIMEOUT_SECS))
            maxtime = time.time() + TIMEOUT_SECS
            # Wait until IP is not None or 169.xx (when no IPs available, this is default) and timeout
            # is not reached.
            while (read_ip_address(session, vm) is None or read_ip_address(session, vm).startswith(
                    '169')) and \
                    time.time() < maxtime:
                time.sleep(1)
            vm_ip_addr = read_ip_address(session, vm)
            log.info("VM IP: {}".format(vm_ip_addr))

            if vm_ip_addr is None or vm_ip_addr.startswith('169'):
                log.info("No Network IP available. Deleting this VM ... ")
                record = session.xenapi.VM.get_record(vm)
                power_state = record["power_state"]
                if power_state != 'Halted':
                    session.xenapi.VM.hard_shutdown(vm)
                delete_all_disks(session, vm)
                session.xenapi.VM.destroy(vm)
                time.sleep(5)
                is_local_ip = True
                retry_count += 1
            else:
                is_local_ip = False

        if is_local_ip:
            # empty string represents no/invalid IP
            vm_ip_addr = ""
            raise Exception("Couldn't get IP within timeout")

        log.info("Final VM IP: {}".format(vm_ip_addr))
        # Measure time taken for VM provisioning
        prov_end_time = time.time()
        create_duration = round(prov_end_time - prov_start_time)

        # Get other details of VM
        record = session.xenapi.VM.get_record(vm)
        uuid = record["uuid"]
        vcpus = record["VCPUs_max"]
        memory_static_max = record["memory_static_max"]

        # print_all_disks(session, vm)
        disks = get_disks_size(session, vm)
        log.debug(disks)
        disks_info = ','.join([str(elem) for elem in disks])
        log.debug(disks_info)

        xen_host_description = "unknown"
        host_records = session.xenapi.host.get_all_records()
        log.debug(host_records)
        for host_key in host_records.keys():
            xen_host_description = host_records[host_key]['name_label']

        vm_max_expiry_minutes = int(config.get("common", "vm.expiry.minutes"))
        if expiry_minutes > vm_max_expiry_minutes:
            log.info("Max allowed expiry in minutes is " + str(vm_max_expiry_minutes))
            expiry_minutes = vm_max_expiry_minutes

        log.info("Starting the timer for expiry of " + str(expiry_minutes) + " minutes.")
        t = threading.Timer(interval=expiry_minutes * 60, function=release_servers,
                            args=[new_vm_name, os_name, 1])
        t.setName(new_vm_name + "__" + uuid)
        t.start()

        # Save as doc in CB
        state = "available"
        username = new_vm_name
        pool = "dynamicpool"
        doc_value = {"ipaddr": vm_ip_addr, "origin": xen_host_description, "os": os_name,
                     "state": state, "poolId": pool, "prevUser": "", "username": username,
                     "ver": "12", "memory": memory_static_max, "os_version": vm_os_name,
                     "name": new_vm_name, "created_time": prov_end_time,
                     "create_duration_secs": create_duration, "cpu": vcpus, "disk": disks_info, "expired_time": prov_end_time + (expiry_minutes * 60), "labels": labels or []}
        # doc_value["mac_address"] = mac_address
        doc_key = uuid

        cb_doc = CBDoc()
        cb_doc.save_dynvm_doc(doc_key, doc_value)

        if pools:
            # static pool uses centos for centos7
            if os_name == "centos7":
                os_name = "centos"
            static_doc_value = {
                "ipaddr": vm_ip_addr,
                "origin": xen_host_description,
                "os": os_name,
                "state": "available",
                "poolId": pools,
                "prevUser": "",
                "username": "",
                "ver": 12,
                "memory": memory_static_max,
                "os_version": vm_os_name
            }
            cb_doc.add_to_static_pool(static_doc_value)

    except Exception as e:
        error = str(e)
        log.error(error)
        # Clean up any partially created resources
        try:
            delete_vm(session, new_vm_name)
        except Exception:
            pass

    return vm_ip_addr, vm_os_name, error


def read_os_name(session, a_vm):
    vgm = session.xenapi.VM.get_guest_metrics(a_vm)
    try:
        os = session.xenapi.VM_guest_metrics.get_os_version(vgm)
        if "name" in os.keys():
            return os["name"]
        return None
    except:
        return None


def read_ip_address(session, a_vm):
    vgm = session.xenapi.VM.get_guest_metrics(a_vm)
    try:
        os = session.xenapi.VM_guest_metrics.get_networks(vgm)
        log.debug(os.keys())
        if "0/ip" in os.keys():
            return os["0/ip"]
        elif "1/ip" in os.keys():
            return os["1/ip"]
        return None
    except:
        return None


def call_release_url(vm_name, os_name, uuid):
    import ssl
    try:
        log.debug("uuid = " + uuid)
        ssl._create_default_https_context = ssl._create_unverified_context
        urllib.request.urlopen(RELEASE_URL + vm_name + "?os=" + os_name)
    except Exception as e:
        log.info(e)


def delete_vms(session, vm_names):
    log.info("Deleting VMs...{}".format(str(vm_names)))

    vm_info = {}
    for vm_name in vm_names:
        delete_vm(session, vm_name)
        vm_info[vm_name] = "deleted"

    return vm_info  # return json.dumps(vm_info, indent=2, sort_keys=True)


def delete_vm(session, vm_name):
    log.info("Deleting VM: " + vm_name)
    delete_start_time = time.time()
    vm = session.xenapi.VM.get_by_name_label(vm_name)
    log.info("Number of VMs found with name - " + vm_name + " : " + str(len(vm)))
    for j in range(len(vm)):
        record = session.xenapi.VM.get_record(vm[j])
        power_state = record["power_state"]
        if power_state != 'Halted':
            # session.xenapi.VM.shutdown(vm[j])
            session.xenapi.VM.hard_shutdown(vm[j])

        # print_all_disks(session, vm[j])
        delete_all_disks(session, vm[j])

        session.xenapi.VM.destroy(vm[j])

        delete_end_time = time.time()
        delete_duration = round(delete_end_time - delete_start_time)

        # delete from CB
        uuid = record["uuid"]
        doc_key = uuid
        delete_vm_from_db(doc_key, delete_duration)
        

def delete_vm_from_db(doc_key, delete_duration):
    cbdoc = CBDoc()
    doc_result = cbdoc.get_doc(doc_key)
    if doc_result:
        doc_value = doc_result.value
        doc_value["state"] = 'deleted'
        current_time = time.time()
        doc_value["deleted_time"] = current_time
        if doc_value["created_time"]:
            doc_value["live_duration_secs"] = round(current_time - doc_value["created_time"])
        doc_value["delete_duration_secs"] = delete_duration
        cbdoc.save_dynvm_doc(doc_key, doc_value)
        cbdoc.remove_from_static_pool(doc_value["ipaddr"])


def read_vm_ip_address(session, a_vm):
    vgm = session.xenapi.VM.get_guest_metrics(a_vm)
    try:
        os = session.xenapi.VM_guest_metrics.get_networks(vgm)
        if "0/ip" in os.keys():
            return os["0/ip"]
        return None
    except:
        return None


def get_vm_existed_xenhost_ref(vm_name, count, os="centos"):
    _, xen_hosts = get_all_xen_hosts_count(os, ignore_labels=True)

    if count > 1:
        vm_name = vm_name + "1"
    is_found = False
    xen_host_index = 0
    for xen_host in xen_hosts:
        xname = xen_host['name']
        log.debug(xname + ' --> index: ' + xname[7:])
        xen_host_index = int(xname[7:])
        try:
            xsession = get_xen_session(xen_host_index, os)
            vm = xsession.xenapi.VM.get_by_name_label(vm_name)
            xsession.logout()
        except Exception:
            continue
        if len(vm) > 0:
            log.info("Number of VMs found with name - " + vm_name + " : " + str(len(vm)))
            is_found = True
            break
    if not is_found:
        xen_host_index = 0
    return xen_host_index


def get_all_available_count(os="centos", labels=None, ignore_labels=False):
    num_xen_hosts, xen_hosts = get_all_xen_hosts_count(os, labels, ignore_labels)
    log.info("Number of xen hosts: {}, {}".format(num_xen_hosts, xen_hosts))
    count = 0
    available_counts = []
    xen_hosts_available_refs = []
    for xen_host in xen_hosts:
        xname = xen_host['name']
        log.info(xname +' --> index: ' + xname[7:])
        xen_host_index = int(xname[7:])
        try:
            xsession = get_xen_session(xen_host_index, os)
            xcount = get_available_count(xsession, os, xen_host)
            # Number of vms provisioned on the host
            provisioned_vms, _, _ = get_vms_usage(xsession)
            # Max number of vms that should be provisioned if set
            max_vms = xen_host.get("host.vms.max." + os)
            if max_vms is not None:
                # Available count based on max configured, 0 if overprovisioned
                available = max(max_vms - provisioned_vms, 0)
                # Lowest count (max configured or resource constrained)
                xcount = min(available, xcount)
        except Exception as e:
            log.warning(str(e))
            continue
        else:
            available_counts.append(xcount)
            xen_hosts_available_refs.append(str(xen_host_index) + ":" + str(xcount))
            count += xcount
            xsession.logout()
    return count, available_counts, xen_hosts_available_refs


def get_xen_session(xen_host_ref=1, os="centos"):
    xen_host = get_xen_host(xen_host_ref, os)
    if not xen_host:
        return None
    url = "http://" + xen_host['host.name']
    log.info("\nXen Server host: " + xen_host['host.name'] + "\n")
    try:
        session = XenAPI.Session(url)
        session.xenapi.login_with_password(xen_host['host.user'], xen_host['host.password'])
    except XenAPI.Failure as f:
        error = "Failed to acquire a session: {}".format(f.details)
        log.error(error)
        return error
    return session


def get_available_count(session, os="centos", xen_host=None):
    if xen_host is None:
        psize, valloc, fsize = get_host_disks(session, 'SCSIid')
    else:
        psize, valloc, fsize = get_host_disks(session, xen_host['host.storage.name'])
    xen_cpu_count_free, xen_memory_free_gb, xen_cpu_count_total, xen_memory_total_gb = \
        get_host_usage(session)
    log.info(
        'Host free cpus={},free memory={},total cpus={},total memory={}'.format(xen_cpu_count_free, xen_memory_free_gb, xen_cpu_count_total, xen_memory_total_gb))
    # TBD: Get the sizes dynamically from template if possible
    required_cpus = xen_host[os + ".vcpus"]
    required_memory_gb = xen_host[os + ".memory"]
    required_disk_gb = xen_host[os + ".disk"]

    log.info("required_cpus={},required_memory={}".format(required_cpus, required_memory_gb))
    cpus_count = int(xen_cpu_count_free / required_cpus)
    memory_count = int(xen_memory_free_gb / required_memory_gb)
    fsize = fsize - int(0.1*fsize) # TBD: Leaving buffer space as seen issue with xenhost
    log.info("Marking free disk size={}".format(fsize))
    disk_count = int((fsize / (1024 * 1024 * 1024)) / required_disk_gb)
    if disk_count > 0:
        disk_count -= 1 # reserve count as 1 base copy occupies to avoid insufficient
    # space
    log.info("cpus_count={}, memory_count={}, disk_count={}".format(cpus_count, memory_count,
                                                                    disk_count))
    counts = [cpus_count, memory_count, disk_count]
    counts.sort()
    available_count = counts[0]

    return available_count


def get_host_usage(session):
    vm_count, vm_cpus, vm_memory = get_vms_usage(session)
    host_ref = session.xenapi.session.get_this_host(session.handle)
    xen_host_record = session.xenapi.host.get_record(host_ref)
    log.debug(xen_host_record)
    xen_cpu_count_total = int(xen_host_record['cpu_info']['cpu_count'])
    xen_cpu_count_free = xen_cpu_count_total - vm_cpus
    if xen_cpu_count_free < 0:
        xen_cpu_count_free = 0
    xen_host_metrics_ref = session.xenapi.host.get_metrics(host_ref)
    metrics = session.xenapi.host_metrics.get_record(xen_host_metrics_ref)
    xen_memory_free_gb = int(int(metrics['memory_free']) / (1024 * 1024 * 1024))
    xen_memory_total_gb = int(int(metrics['memory_total']) / (1024 * 1024 * 1024))
    return xen_cpu_count_free, xen_memory_free_gb, xen_cpu_count_total, xen_memory_total_gb


def get_vms_usage(session):
    vms = session.xenapi.VM.get_all()
    vm_count = 0
    vcpus = 0
    memory_static_max = 0
    for vm in vms:
        record = session.xenapi.VM.get_record(vm)
        if not (record["is_a_template"]) and not (record["is_control_domain"]) and (
                record["power_state"] != 'Halted'):
            vm_count = vm_count + 1
            vcpus = vcpus + int(record["VCPUs_max"])
            memory_static_max = memory_static_max + int(
                int(record["memory_static_max"]) / (1024 * 1024 * 1024))
    log.info("vm_count={},vcpus={},memory={}".format(vm_count, vcpus, memory_static_max))
    return vm_count, vcpus, memory_static_max


def get_host_details(session):
    xen_cpu_count_free, xen_memory_free_gb, xen_cpu_count_total, xen_memory_total_gb = \
        get_host_usage(session)
    log.info("{},{},{},{}".format(xen_cpu_count_free, xen_memory_free_gb, xen_cpu_count_total,
                                  xen_memory_total_gb))


def print_host_details(session):
    try:
        host_records = session.xenapi.host.get_all_records()
        log.info(host_records)
        for host_key in host_records.keys():
            xen_cpu_info = host_records[host_key]['cpu_info']
            xen_cpu_count = xen_cpu_info['cpu_count']
            xen_host_name = host_records[host_key]['hostname']
            xen_host_ip = host_records[host_key]['address']
            host_ref = session.xenapi.session.get_this_host(session.handle)
            xen_host_metrics_ref = session.xenapi.host.get_metrics(host_ref)
            metrics = session.xenapi.host_metrics.get_record(xen_host_metrics_ref)
            xen_memory_free_gb = int(int(metrics['memory_free']) / (1024 * 1024 * 1024))
            xen_memory_total_gb = int(int(metrics['memory_total']) / (1024 * 1024 * 1024))

            xen_vms = host_records[host_key]['resident_VMs']
            log.info(xen_cpu_info)
            log.info("Host Name:{}, IP:{}".format(xen_host_name, xen_host_ip))
            log.info("Number of CPUs:" + str(xen_cpu_count))
            log.info("Number of VMs:" + str(len(xen_vms)))
            log.info("Total memory (GB) :" + str(xen_memory_total_gb))
            log.info("Free memory (GB):" + str(xen_memory_free_gb))

    except Exception as e:
        log.info(e)


def print_all_disks(session, vm):
    vbds = session.xenapi.VM.get_VBDs(vm)
    index = 1
    for vbd in vbds:
        vdi = session.xenapi.VBD.get_VDI(vbd)
        if vdi:
            log.info("Disk..." + str(index))
            log.info(vdi)
            index = index + 1
            try:
                # session.xenapi.VDI.destroy(vdi)
                vdi_record = session.xenapi.VDI.get_record(vdi)
                log.info(vdi_record)  # virtual_size
            except Exception as e:
                log.error(e)
                pass


def get_host_disks(session, device_id):
    pbds_records = session.xenapi.PBD.get_all_records()
    log.debug(pbds_records)
    pbds = session.xenapi.PBD.get_all()
    psize = 0
    valloc = 0
    fsize = 0
    log.info("Check for storage name: {}".format(device_id))
    for pbd in pbds:
        try:
            if pbd != 'OpaqueRef:NULL':
                pbd_record = session.xenapi.PBD.get_record(pbd)
                log.debug(pbd_record)
                try:
                    #device_config = pbd_record['device_config'][device_id] # ignore the return val
                    # matched disk device id.
                    sr = session.xenapi.PBD.get_SR(pbd)
                    log.debug(sr)

                    storage_name_label = session.xenapi.SR.get_name_label(sr)
                    storage_name_desc = session.xenapi.SR.get_name_description(sr)

                    if storage_name_label.lower() != device_id.lower():
                        continue
                    log.info("storage_name:{}, storage_description={}".format(storage_name_label,
                                                                              storage_name_desc))
                    psize = session.xenapi.SR.get_physical_size(sr)
                    #valloc = session.xenapi.SR.get_virtual_allocation(sr)
                    valloc = session.xenapi.SR.get_physical_utilisation(sr)
                    fsize = int(psize) - int(valloc)
                    log.info(psize)
                    log.info(valloc)
                    log.info(fsize)
                    break
                except:
                    pass

        except Exception as e:
            log.error(e)
            pass
    return psize, valloc, fsize


def get_disks_size(session, vm):
    vbds = session.xenapi.VM.get_VBDs(vm)
    disks = []
    for vbd in vbds:
        if vbd != 'OpaqueRef:NULL':
            vdi = session.xenapi.VBD.get_VDI(vbd)
            if vdi:
                try:
                    if vdi != 'OpaqueRef:NULL':
                        vdi_record = session.xenapi.VDI.get_record(vdi)
                        disks.append(vdi_record['virtual_size'])
                except Exception as e:
                    log.error(e)
                    pass
    return disks


def delete_all_disks(session, vm):
    vbds = session.xenapi.VM.get_VBDs(vm)
    index = 1
    for vbd in vbds:
        vdi = session.xenapi.VBD.get_VDI(vbd)
        if vdi != 'OpaqueRef:NULL':
            log.info("Delete Disk..." + str(index))
            log.debug(vdi)
            index = index + 1
            try:
                vdi_record = session.xenapi.VDI.get_record(vdi)
                log.debug(vdi_record)
                session.xenapi.VDI.destroy(vdi)
            except Exception as e:
                log.error(e)
                pass


class CBDoc:
    def __init__(self):
        config = read_config()
        self.cb_server = config.get("couchbase", "couchbase.server")
        self.cb_bucket = config.get("couchbase", "couchbase.bucket")
        self.cb_username = config.get("couchbase", "couchbase.username")
        self.cb_userpassword = config.get("couchbase", "couchbase.userpassword")
        self.static_cb_server = config.get("couchbase", "static.server")
        self.static_cb_bucket = config.get("couchbase", "static.bucket")
        self.static_cb_username = config.get("couchbase", "static.username")
        self.static_cb_userpassword = config.get("couchbase", "static.userpassword")
        try:
            #self.cb_auth = PasswordAuthenticator(self.cb_username, self.cb_userpassword)
            #options = ClusterOptions(self.cb_auth)
            self.cb_cluster = Cluster('couchbase://' + self.cb_server)
            self.cb_auth = PasswordAuthenticator(self.cb_username, self.cb_userpassword)
            self.cb_cluster.authenticate(self.cb_auth)
            self.cb = self.cb_cluster.open_bucket(self.cb_bucket)
            self.static_cb_cluster = Cluster('couchbase://' + self.static_cb_server)
            self.static_cb_auth = PasswordAuthenticator(self.static_cb_username, self.static_cb_userpassword)
            self.static_cb_cluster.authenticate(self.static_cb_auth)
            self.static_cb = self.static_cb_cluster.open_bucket(self.static_cb_bucket)
        except Exception as e:
            log.error('Connection Failed: %s ' % self.cb_server)
            log.error(e)

    def get_doc(self, doc_key, retries=3):
        while retries > 0:
            try:
                return self.cb.get(doc_key)
            except Exception as e:
                log.error('Error while getting doc %s !' % doc_key)
                log.error(e)
            time.sleep(5)
            retries -= 1

    def save_dynvm_doc(self, doc_key, doc_value, retries=3):
        while retries > 0:
            try:
                log.info(doc_value)
                self.cb.upsert(doc_key, doc_value)
                log.info("%s added/updated successfully" % doc_key)
                break
            except Exception as e:
                log.error('Document with key: %s saving error' % doc_key)
                log.error(e)
            time.sleep(5)
            retries -= 1

    def remove_from_static_pool(self, ip, retries=3):
        while retries > 0:
            try:
                static_doc_value = self.static_cb.get(ip).value
                self.static_cb.remove(ip)
                log.info("{} removed from static pools: {}".format(ip, ",".join(static_doc_value["poolId"])))
                break
            except DocumentNotFoundException:
                break
            except Exception as e:
                log.error("Error removing {} from static pools".format(ip))
                log.error(e)
            time.sleep(5)
            retries -= 1
    
    def add_to_static_pool(self, doc_value, retries=3):
        ip = doc_value["ipaddr"]
        pools_str = ",".join(doc_value["poolId"])
        while retries > 0:
            try:
                self.static_cb.upsert(ip, doc_value)
                log.info("{} added to static pools: {}".format(ip, pools_str))
                break
            except Exception as e:
                log.error("Error adding {} to static pools: {}".format(ip, pools_str))
                log.error(e)
            time.sleep(5)
            retries -= 1

    def get_expired(self):
        try:
            return list(self.cb.n1ql_query("SELECT os, username, META().id FROM `QE-dynserver-pool` WHERE ipaddr != '' AND state = 'available' AND expired_time is not missing AND expired_time < {}".format(time.time())))
        except Exception as e:
            log.error("Error getting expired vms: {}".format(str(e)))
            return []

    def set_expired(self, names, expired_time_mins, from_reference):
        try:
            if from_reference == "now":
                set_expired_to = time.time() + (expired_time_mins * 60)
            elif from_reference == "created":
                set_expired_to = "(created_time + {})".format(expired_time_mins * 60)
            else:
                log.error("Error setting expired_time for")
                raise Exception("Invalid from_reference")
            query = "UPDATE `QE-dynserver-pool` SET expired_time = {} WHERE ipaddr != '' AND state = 'available' AND name in {} RETURNING raw name".format(set_expired_to, names)
            updated_names = list(self.cb.n1ql_query(query))
            return updated_names
        except Exception as e:
            log.error("Error setting expired_time for {}", names)
            raise e


def list_given_vm_set_details(session, vm_names):
    for vm_name in vm_names:
        list_vm_details(session, vm_name)


def parse_arguments():
    log.debug("Parsing arguments")
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", default=".dynvmservice.ini", help="Configuration file")
    parser.add_argument("-l", "--log-level", dest="loglevel", default="INFO",
                        help="e.g -l info,warning,error")
    options = parser.parse_args()
    return options

def expire_vms():
    cb_doc = CBDoc()
    while True:
        expired = cb_doc.get_expired()
        for vm in expired:
            try:
                res = release_servers(vm["username"], vm["os"], 1)
                if len(res) == 0:
                    # vm record in db but vm already deleted, cleanup record
                    delete_vm_from_db(vm["id"], 0)
                log.info("deleted expired vm: {}".format(vm["username"]))
            except Exception:
                pass
        time.sleep(60)

def main():
    # options = parse_arguments()
    set_log_level()
    expiry_thread = threading.Thread(target=expire_vms, daemon=True)
    expiry_thread.start()
    app.run(host='0.0.0.0', debug=False)

if __name__ == "__main__":
    main()
