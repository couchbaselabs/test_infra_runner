import os
import logging
import sys
from time import sleep

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


def cleanup_node(node_info):
    logger.info(f"Cleaning up node: {node_info['ipaddr']}"
                f" in poolId={node_info['poolId']}")
    server = TestInputServer()
    server.ip = node_info['ipaddr']
    server.ssh_username = "root"
    server.ssh_password = "couchbase"

    try:
        ssh_sess = RemoteMachineShellConnection(server)
        ssh_sess.execute_command("pkill -9 memcached")
        ssh_sess.execute_command("iptables -F ; ip6tables -F ; iptables -t nat -F ; iptables -t mangle -F ; iptables -X")
        ssh_sess.execute_command("ip6tables -t nat -F ; ip6tables -t mangle -F ; ip6tables -X ")
        sleep(2)
        ssh_sess.execute_command("service couchbase-server stop")
        ssh_sess.execute_command("journalctl --rotate ; journalctl --vacuum-time=1s")
        ssh_sess.execute_command("apt-get clean ; rm -rf /var/lib/apt/lists/* ")
        ssh_sess.execute_command("rm -rf /opt/couchbase /data/* /tmp/*")
        ssh_sess.disconnect()
        return True
    except Exception as e:
        logger.error(f"Error: {e}")
    return False


def main():
    username = os.getenv('CB_USERNAME')
    password = os.getenv('CB_PASSWORD')
    ip = os.getenv('CB_IP')
    bucket_name = os.getenv('CB_BUCKET_NAME')

    pool_ids_to_monitor = ["regression", "12hrreg", "magmareg",
                           "upgrade", "magmaUpgrade", "12hour",
                           "magmanew", "elastic-fts", "failover"]

    sdk_conn = Cluster(f'couchbase://{ip}',
                       authenticator=PasswordAuthenticator(username, password))
    # bucket = sdk_conn.bucket(bucket_name)
    # collection = bucket.default_collection()
    result = sdk_conn.query(
        f"SELECT * FROM `{bucket_name}` WHERE state='failedInstall'")
    for row in result.rows():
        row = row["QE-server-pool"]
        cleanup_okay = False
        if isinstance(row["poolId"], list):
            for t_pool_id in row["poolId"]:
                if t_pool_id in pool_ids_to_monitor:
                    cleanup_okay = cleanup_node(row)
                    break
        else:
            if row["poolId"] in pool_ids_to_monitor:
                cleanup_okay = cleanup_node(row)

        if cleanup_okay:
            # Mark the node as available
            logger.info(f"Marking node {row['ipaddr']} as available")
            update_query = (f"update `{bucket_name}` set state='available' "
                            f"where ipaddr='{row['ipaddr']}' "
                            f"and state='failedInstall'")
            logger.info(update_query)
            try:
                update_result = sdk_conn.query(update_query)
                for update_row in update_result.rows():
                    logger.info(update_row)
                logger.info(f"Success: {row['ipaddr']} state to available")
            except Exception as e:
                logger.error(f"Fail: {row['ipaddr']}, Exception: {e}")

    sdk_conn.close()


if __name__ == "__main__":
    main()
