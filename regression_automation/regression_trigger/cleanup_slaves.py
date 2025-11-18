import paramiko
import os
import re
import datetime
import requests
import xml.etree.ElementTree as ET
import concurrent.futures
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
logging.getLogger("paramiko").setLevel(logging.WARNING)


def get_env(var_name):
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(f"Missing environment variable: {var_name}")
    return value

DIRECTORIES = ["/tmp", "/root"]
FILE_EXTENSIONS = (".rpm", ".deb")
JENKINS = {
    'http://qa.sc.couchbase.com/':{
        "username": get_env("JENKINS_USER_QA"),
        "password": get_env("JENKINS_PASS_QA"),
    },
    'http://qe-jenkins1.sc.couchbase.com/':{
        "username": get_env("JENKINS_USER_QE"),
        "password": get_env("JENKINS_PASS_QE"),
    }
}
SLAVE_USERNAME = get_env("SLAVE_USERNAME")
SLAVE_PASSWORD = get_env("SLAVE_PASSWORD")

"""
Instead of using sftp the following bash script can also be used to clean /tmp on multiple hosts:

# 1. Delete build packages older than 7 days
find /tmp -type f \( -name "*.rpm" -o -name "*.deb" \) -mtime +7 -delete

# 2. For remaining build packages, keep latest 2, delete the rest
files=$(find /tmp -type f \( -name "*.rpm" -o -name "*.deb" \) -mtime -7 -printf "%T@ %p\n" | sort -nr | awk '{print $2}')

count=0
for f in $files; do
    count=$((count + 1))
    if [ $count -gt 2 ]; then
        rm -f "$f"
    fi
done
"""
def clean_directories_on_slaves(slaves, username, password, days_threshold, keep_recent):
    def clean_directories_on_host(host, count, length_slaves, username, password, days_threshold, keep_recent):
        logger.info(f"{count}/{length_slaves} Cleaning up directories on {host}")
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, username=username, password=password, timeout=10)
            sftp = ssh.open_sftp()

            now = datetime.datetime.now(datetime.timezone.utc)
            cutoff_time = now - datetime.timedelta(days=days_threshold)

            pattern = r"\.(" + "|".join(ext.lstrip('.') for ext in FILE_EXTENSIONS) + r")(\.\d+)?$"

            for directory in DIRECTORIES:
                files = {}

                for entry in sftp.listdir_attr(directory):
                    filename = entry.filename
                    if not re.search(pattern, filename):
                        continue

                    filepath = os.path.join(directory, filename)

                    # In all slaves currently, / is mounted with option 'relatime' or 'strictatime'.
                    # If it is mounted with option 'noatime', use entry.st_mtime
                    mtime = datetime.datetime.fromtimestamp(entry.st_atime, datetime.timezone.utc)

                    files[filepath] = mtime

                # Delete copy files (.deb.1, .rpm.1, .rpm.2, etc.)
                copy_file_pattern = r"\.(" + "|".join(ext.lstrip('.') for ext in FILE_EXTENSIONS) + r")(\.\d+)$"
                copy_files = [f for f in files if re.search(copy_file_pattern, f)]
                for f in copy_files:
                    try:
                        sftp.remove(f)
                        logger.info(f"{count}/{length_slaves} Deleted COPY file {f} on host {host}")
                    except Exception as e:
                        logger.error(f"{count}/{length_slaves} Failed to delete COPY file {f} on host {host}: {e}")
                    finally:
                        files.pop(f)

                # Delete old files
                old_files = [f for f in files if files[f] < cutoff_time]
                for f in old_files:
                    try:
                        sftp.remove(f)
                        logger.info(f"{count}/{length_slaves} Deleted OLD file {f} on host {host}")
                    except Exception as e:
                        logger.error(f"{count}/{length_slaves} Failed to delete OLD file {f} on host {host}: {e}")

                # Keep n newest, delete the rest
                recent_files = sorted(
                    [f for f in files if files[f] >= cutoff_time],
                    key=lambda x: files[x],
                    reverse=True
                )

                if len(recent_files) <= keep_recent:
                    logger.info(f"{count}/{length_slaves} Number of recent files ({len(recent_files)}) is less than or equal "
                          f"to keep_recent ({keep_recent}) on host {host}."
                          " No files will be deleted.")
                else:
                    for f in recent_files[keep_recent:]:
                        try:
                            sftp.remove(f)
                            logger.info(f"{count}/{length_slaves} Deleted extra recent file on host {host}: {f}")
                        except Exception as e:
                            logger.error(f"{count}/{length_slaves} Failed to delete extra recent file {f} on host {host}: {e}")

                if len(recent_files[:keep_recent]) > 0 and len(recent_files) > keep_recent:
                    logger.info(f"{count}/{length_slaves} Files left in {directory} after cleanup on host {host}: {recent_files[:keep_recent]}")
                elif len(recent_files) > 0:
                    logger.info(f"{count}/{length_slaves} Files left in {directory} after cleanup on host {host}: {recent_files}")
                else:
                    logger.info(f"{count}/{length_slaves} No files left in {directory} after cleanup on host {host}")

            sftp.close()
            ssh.close()
            logger.info(f"{count}/{length_slaves} Cleanup done on {host}")

        except Exception as e:
            logger.error(f"{count}/{length_slaves} Error on {host}: {e}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(clean_directories_on_host, ip_addr, count+1, len(slaves), username, password, days_threshold, keep_recent) \
                    for count, ip_addr in enumerate(slaves)]
        for future in concurrent.futures.as_completed(futures):
            future.result()

def get_jenkins_slaves(jenkins_url):
    url = f"{jenkins_url}computer/api/json?tree=computer[displayName,assignedLabels[name]]"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        slaves = []
        for node in data.get("computer", []):
            display_name = node.get("displayName")
            labels = [lbl.get("name") for lbl in node.get("assignedLabels", []) if "name" in lbl]
            slaves.append({
                "name": display_name,
                "labels": labels
            })
        return slaves
    except requests.RequestException as e:
        logger.error(f"HTTP error occurred: {e}")
    except ValueError as e:
        logger.error(f"JSON decode error: {e}")
    return []

def get_jenkins_slaves_ip(jenkins_url, count, slave_name, total, username, password, res_dic):
    url = f'{jenkins_url}/computer/{slave_name}/config.xml'
    try:
        response = requests.get(url, auth=(username, password))
        response.raise_for_status()

        root = ET.fromstring(response.text)
        host_elem = root.find('.//host')
        if host_elem is not None:
            res_dic[count] = host_elem.text
            logger.info(f'{count+1}/{total} IP for {slave_name}: {host_elem.text}')
        else:
            logger.critical(f'{count+1}/{total} Could not retrieve IP for {slave_name}')
            return None

    except requests.RequestException as e:
        logger.error(f'HTTP error for {slave_name}: {e}')
    except ET.ParseError as e:
        logger.error(f'XML parse error for {slave_name}: {e}')

def get_all_slave_ips(jenkins_url, username, password, labels_filter=['P0']):
    slaves = get_jenkins_slaves(jenkins_url)
    logger.info(f"Found {len(slaves)} slaves on Jenkins {jenkins_url}")
    logger.info(f"Applying label filter: {labels_filter} and filtering slaves for cleanup")
    filtered_slaves = [s['name'] for s in slaves]
    if len(labels_filter) > 0:
        filtered_slaves = [s['name'] for s in slaves if any(lbl in s['labels'] for lbl in labels_filter)]
    logger.info(f"Found {len(filtered_slaves)} slaves on Jenkins {jenkins_url} after filtering")
    all_ips = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(get_jenkins_slaves_ip, jenkins_url, count+1, name, len(slaves), username, password, all_ips) for count, name in enumerate(filtered_slaves)]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    return list(all_ips.values())

def get_connectable_ips(ip_list):
    def check_ssh_connection(ip, count, total, username, password, res_dict):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(ip, username=username, password=password, timeout=60)
            stdin, stdout, stderr = ssh.exec_command('echo SSH Connection Successful')
            output = stdout.read().decode()
            if 'SSH Connection Successful' in output:
                logger.info(f'{count+1}/{total} SSH connection to {ip} is successful.')
                res_dict[count] = ip
            else:
                logger.critical(f'{count+1}/{total} SSH connection to {ip} failed. No expected output.')
            ssh.close()
        except Exception as e:
            logger.error(f'{count+1}/{total} SSH connection to {ip} failed. Error: {str(e)}')

    connectable_ips = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(check_ssh_connection, ip, count+1, len(ip_list), SLAVE_USERNAME, SLAVE_PASSWORD, connectable_ips) for count, ip in enumerate(ip_list)]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    return list(connectable_ips.values())

def generate_ini(ip_list, output_file='slaves.ini'):
    with open(output_file, 'w') as f:
        f.write('[global]\n')
        f.write(f'username:{SLAVE_USERNAME}\n')
        f.write(f'password:{SLAVE_PASSWORD}\n\n')

        f.write('[membase]\n')
        f.write('rest_username:Administrator\n')
        f.write('rest_password:password\n\n')

        f.write('[servers]\n')
        for i in range(1, len(ip_list) + 1):
            f.write(f'{i}:_{i}\n')
        f.write('\n')

        for i, ip in enumerate(ip_list, 1):
            f.write(f'[_{i}]\n')
            f.write(f'ip:{ip}\n\n')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Cleanup old build packages on Jenkins slave nodes.")
    parser.add_argument("--days_threshold", default=7, type=int, help="Days threshold for cleanup")
    parser.add_argument("--keep_recent", default=2, type=int, help="Number of recent builds to keep")
    parser.add_argument('--ini_file_size', type=int, required=True, help='Number of slaves in ini file process')
    parser.add_argument('--label_filter', type=str, default='', help='Comma separated labels to filter slaves from Jenkins')

    args = parser.parse_args()

    label_filter = []
    if args.label_filter:
        label_filter = [lbl.strip() for lbl in args.label_filter.split(',') if lbl.strip()]

    all_ips = []
    for jenkins_url in JENKINS:
        logger.info(f'Fetching slave IPs from Jenkins {jenkins_url}')
        all_ips = all_ips + get_all_slave_ips(jenkins_url, 
                                              JENKINS[jenkins_url]["username"], 
                                              JENKINS[jenkins_url]["password"], 
                                              label_filter)

    logger.info(f'Checking connectivity to all slave IPs.')
    connectable_ips = get_connectable_ips(all_ips)

    logger.info(f'Total Number of slaves : {len(all_ips)}')
    logger.info(f'Number of reachable slaves : {len(connectable_ips)}')

    logger.info(f'Starting cleanup on reachable slaves.')
    clean_directories_on_slaves(connectable_ips, SLAVE_USERNAME, SLAVE_PASSWORD,
                                days_threshold=args.days_threshold, keep_recent=args.keep_recent)
    logger.info('Cleanup process completed on all reachable slaves.')

    logger.info('Generating ini files')
    for i in range(0, len(connectable_ips), args.ini_file_size):
        generate_ini(connectable_ips[i: i+args.ini_file_size], f'slaves_{i // args.ini_file_size}.ini')
        logger.info(f'INI file generated: slaves_{i // args.ini_file_size}.ini')
