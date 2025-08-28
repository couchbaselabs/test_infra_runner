import os
import logging
import sys
from time import sleep
from contextlib import contextmanager

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator

# Set up Python path like testrunner.py does
current_dir = os.path.dirname(__file__)
parent_dir = os.path.abspath(os.path.join(current_dir, '..'))
lib_dir = os.path.join(parent_dir, 'lib')

# Add paths like other working scripts do
sys.path = [parent_dir, lib_dir] + sys.path

from TestInput import TestInputServer, TestInputSingleton, TestInput
from remote.remote_util import RemoteMachineShellConnection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

cleanup_commands = [
    # Kill memcached processes
    "pkill -9 memcached",

    # IPv4 iptables cleanup
    "iptables -F ; iptables -t nat -F ; iptables -t mangle -F ; iptables -X",
    # IPv6 ip6tables cleanup
    "ip6tables -t nat -F ; ip6tables -t mangle -F ; ip6tables -X",

    # Abort any ongoing dpkg operations
    "dpkg --configure -a",

    # Force-remove broken package if partially installed
    "dpkg --remove --force-remove-reinstreq couchbase-server",
    "dpkg --purge couchbase-server",

    # Clean up dpkg state files (only if really stuck — be cautious)
    # "rm -f /var/lib/dpkg/lock* /var/cache/apt/archives/lock /var/lib/apt/lists/lock",

    # Remove possibly broken updates or lock files
    # "rm -f /var/lib/dpkg/updates/*",

    # Reconfigure dpkg database in case it's inconsistent
    # Clears the available packages list
    "dpkg --clear-avail",
    # Clean the local repo of retrieved package files
    "apt-get clean",

    # Clean and rebuild package list
    "rm -rf /var/lib/apt/lists/*",
    "rm -rf /var/lib/apt/lists/partial/*",
    "apt-get update",

    # Cleanup unused packages
    "apt-get autoremove -y",
    "apt-get autoclean",

    # Clean up Journal logs
    "journalctl --rotate ; journalctl --vacuum-time=1s",

    # Remove couchbase data and temp files
    "rm -rf /opt/couchbase /data/* /tmp/*",
]


# Initialize TestInputSingleton to avoid errors in RemoteMachineShellConnection
def setup_test_input():
    """Setup minimal TestInput to avoid RemoteMachineShellConnection errors"""
    if not TestInputSingleton.input:
        TestInputSingleton.input = TestInput()
        TestInputSingleton.input.clients = []  # Initialize missing clients attribute
        TestInputSingleton.input.test_params = {}


# Patch TestInput class to always have clients attribute
original_init = TestInput.__init__
def patched_init(self):
    original_init(self)
    if not hasattr(self, 'clients'):
        self.clients = []

TestInput.__init__ = patched_init
setup_test_input()


def validate_environment():
    """Validate required environment variables"""
    required_vars = ['CB_USERNAME', 'CB_PASSWORD', 'CB_IP', 'CB_BUCKET_NAME']
    missing_vars = []

    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}")


@contextmanager
def ssh_connection(server):
    """Context manager for SSH connections to ensure proper cleanup"""
    ssh_sess = None
    try:
        ssh_sess = RemoteMachineShellConnection(
            server, exit_on_failure=False, max_attempts_connect=1,
            ssh_timeout=5)
        yield ssh_sess
    except Exception as e:
        logger.error(f"Failed to establish SSH connection to {server.ip}: {e}")
        raise
    finally:
        if ssh_sess:
            try:
                ssh_sess.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting SSH session: {e}")


def cleanup_node(node_info):
    global cleanup_commands

    logger.info(f"Cleaning up node: {node_info['ipaddr']}"
                f" in poolId={node_info['poolId']}")

    server = TestInputServer()
    server.ip = node_info['ipaddr']
    server.ssh_username = "root"
    server.ssh_password = "couchbase"

    try:
        with ssh_connection(server) as ssh_sess:
            # Execute cleanup commands in sequence
            for cmd in cleanup_commands:
                try:
                    ssh_sess.execute_command(cmd)
                    sleep(1)
                except Exception as e:
                    # Log and continue with next command even if one fails
                    logger.warning(f"Command '{cmd}' failed: {e}")

            # Stop couchbase server
            ssh_sess.execute_command(
                "service couchbase-server stop ; "
                "apt remove -y couchbase-server ; "
                "dpkg --purge couchbase-server")

        return "ok"

    except Exception as e:
        # Check if this is a connection-related error
        error_str = str(e).lower()
        if any(keyword in error_str for keyword in [
            'connection', 'timeout', 'refused', 'unreachable',
            'network', 'ssh', 'authentication'
        ]):
            return "connection_issue"
        else:
            return "failure"


def main():
    # Validate environment variables
    try:
        validate_environment()
    except ValueError as e:
        logger.error(f"Environment validation failed: {e}")
        return [], []

    username = os.getenv('CB_USERNAME')
    password = os.getenv('CB_PASSWORD')
    ip = os.getenv('CB_IP')
    bucket_name = os.getenv('CB_BUCKET_NAME')

    pool_ids_to_monitor = ["regression", "12hrreg", "magmareg",
                           "upgrade", "magmaUpgrade", "12hour",
                           "magmanew", "elastic-fts", "failover"]

    sdk_conn = None
    try:
        sdk_conn = Cluster(f'couchbase://{ip}',
                           authenticator=PasswordAuthenticator(username,
                                                               password))
        # bucket = sdk_conn.bucket(bucket_name)
        # collection = bucket.default_collection()

        result = sdk_conn.query(
            f"SELECT * FROM `{bucket_name}` WHERE state='failedInstall'")

        ssh_failed_nodes = []
        unknown_failure_nodes = []
        cleaned_up_nodes = []

        for row in result.rows():
            row = row["QE-server-pool"]
            cleanup_status = "fail"

            if isinstance(row["poolId"], list):
                for t_pool_id in row["poolId"]:
                    if t_pool_id in pool_ids_to_monitor:
                        cleanup_status = cleanup_node(row)
                        break
            else:
                if row["poolId"] in pool_ids_to_monitor:
                    cleanup_status = cleanup_node(row)

            if cleanup_status == "ok":
                # Mark the node as available
                logger.info(f"Marking node {row['ipaddr']} as available")
                update_query = (f"UPDATE `{bucket_name}` SET state='available' "
                                f"WHERE ipaddr='{row['ipaddr']}' "
                                f"AND state='failedInstall'")
                logger.info(update_query)
                try:
                    update_result = sdk_conn.query(update_query)
                    for update_row in update_result.rows():
                        logger.info(update_row)
                    logger.info(f"Success: {row['ipaddr']} state changed to available")
                    cleaned_up_nodes.append(row['ipaddr'])
                except Exception as e:
                    logger.error(f"Fail: {row['ipaddr']}, Exception: {e}")
                    unknown_failure_nodes.append(row['ipaddr'])
            elif cleanup_status == "connection_issue":
                update_result = sdk_conn.query(
                    f"UPDATE `{bucket_name}` SET state='sshFailed'"
                    f" WHERE ipaddr='{row['ipaddr']}'"
                    f" AND state='failedInstall'")
                for update_row in update_result.rows():
                    pass
            else:
                unknown_failure_nodes.append(row['ipaddr'])

        result = sdk_conn.query(
            f"SELECT * FROM `{bucket_name}` WHERE state='sshFailed'")
        for row in result.rows():
            ssh_failed_nodes.append(row[bucket_name]['ipaddr'])

        return cleaned_up_nodes, ssh_failed_nodes, unknown_failure_nodes

    except Exception as e:
        logger.error(f"Error in main execution: {e}")
        return [], []
    finally:
        if sdk_conn:
            try:
                sdk_conn.close()
            except Exception as e:
                logger.warning(f"Error closing SDK connection: {e}")


if __name__ == "__main__":
    exit_code = 0
    cleaned_up_nodes, unreachable, unknown_failures = main()

    if cleaned_up_nodes:
        op = "---------- Cleaned up nodes -----------"
        for ip in cleaned_up_nodes:
            op += f"\n {ip}"
        logger.info(f"\n {op}")

    if unreachable:
        op = "---------- Unreachable nodes -----------"
        for ip in unreachable:
            op += f"\n {ip}"
        logger.critical(f"\n {op}")
        exit_code = 1

    if unknown_failures:
        op = "---------- Cleanup failed nodes -----------"
        for ip in unknown_failures:  # Fixed: was using unreachable instead of unknown_failures
            op += f"\n {ip}"
        logger.critical(f"\n {op}")
        exit_code = 1

    sys.exit(exit_code)
