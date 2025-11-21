#!/usr/bin/env python3
"""
Script to update a specific IP state in the QE-server-pool.

This script uses the Couchbase SDK 4.x to connect to the test suite database
and update the state of a specific IP address in the server pool with
durability=MAJORITY.

Usage:
    python release_server_pool_ip.py --ip <ip_address> --state <state>
"""

import logging
import sys
from optparse import OptionParser

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.durability import DurabilityLevel
from couchbase.options import ClusterOptions


# Constants from testDispatcher_sdk4.py
TEST_SUITE_DB = '172.23.216.60'
TEST_SUITE_DB_USER_NAME = 'Administrator'
TEST_SUITE_DB_PASSWORD = "esabhcuoc"
QE_SERVER_POOL_BUCKET = 'QE-server-pool'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s: %(funcName)s:L%(lineno)d: %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)


def setup_couchbase_connection():
    """Setup connection to Couchbase cluster."""
    try:
        auth = ClusterOptions(
            PasswordAuthenticator(TEST_SUITE_DB_USER_NAME,
                                  TEST_SUITE_DB_PASSWORD))
        cluster = Cluster('couchbase://{}'.format(TEST_SUITE_DB), auth)
        bucket = cluster.bucket(QE_SERVER_POOL_BUCKET)
        collection = bucket.default_collection()

        log.info(f"Successfully connected to Couchbase cluster at "
                 f"{TEST_SUITE_DB}")
        return cluster, bucket, collection
    except Exception as e:
        log.error(f"Failed to connect to Couchbase: {str(e)}")
        raise


def check_ip_exists(collection, ip_address):
    """Check if the IP address exists in the server pool."""
    try:
        result = collection.get(ip_address)
        if result.success:
            log.info(f"Found IP {ip_address} in server pool")
            return result.value
        else:
            log.error(f"IP {ip_address} not found in server pool")
            return None
    except Exception as e:
        log.error(f"Error checking IP {ip_address}: {str(e)}")
        return None


def release_ip_with_durability(collection, ip_address, state):
    """
    Release an IP address and mark it with the specified state

    Args:
        collection: Couchbase collection object
        ip_address: IP address to release
        state: State to set for the IP

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # First check if the IP exists
        doc = check_ip_exists(collection, ip_address)
        if not doc:
            return False

        log.info(f"Current state of IP {ip_address}: {doc['state']}")

        # Update the document with new state and durability=MAJORITY
        doc['state'] = state
        doc['prevUser'] = doc.get('username', '')
        doc['username'] = ''

        # Use upsert with durability=MAJORITY
        result = collection.upsert(
            ip_address,
            doc,
            durability=DurabilityLevel.MAJORITY)

        if result.success:
            log.info(f"Successfully updated IP {ip_address} state='{state}'")
            return True
        else:
            log.error(f"Failed to update IP {ip_address}")
            return False

    except Exception as e:
        log.error(f"Error updating IP {ip_address}: {str(e)}")
        return False


def main():
    """Main function to parse arguments and release IP."""
    # Define valid states once
    valid_states = ['available', 'booked', 'failedInstall', 'sshFailed']

    parser = OptionParser(usage='%prog --ip <ip_address> --state <state>')
    parser.add_option('--ip', dest='ip_address',
                      help='IP address to release from server pool')
    parser.add_option('--state', dest='state',
                      help=f'State to set for the IP (valid values: '
                           f'{", ".join(valid_states)})')
    parser.add_option('--log-level', dest='log_level', default='INFO',
                      help='Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)')

    options, args = parser.parse_args()

    # Set log level
    log_level = getattr(logging, str(options.log_level).upper())
    logging.getLogger().setLevel(log_level)

    # Validate required arguments
    if not options.ip_address:
        log.error("IP address is required. Use --ip <ip_address>")
        parser.print_help()
        sys.exit(1)

    if not options.state:
        log.error("State is required. Use --state <state>")
        parser.print_help()
        sys.exit(1)

    # Validate state values
    if options.state not in valid_states:
        log.error(f"Invalid state '{options.state}'. "
                  f"Valid states are: {', '.join(valid_states)}")
        parser.print_help()
        sys.exit(1)

    # Initialize return_code to 1 (failure)
    return_code = 1
    try:
        # Setup Couchbase connection
        cluster, bucket, collection = setup_couchbase_connection()

        # Update the IP state
        success = release_ip_with_durability(collection, options.ip_address,
                                             options.state)

        if success:
            log.info("IP update process completed successfully")
            return_code = 0
        else:
            log.error("IP update process failed")

    except Exception as e:
        log.error(f"Unexpected error: {str(e)}")

    finally:
        # Always close the SDK connection
        try:
            if 'cluster' in locals():
                cluster.close()
                log.info("SDK connection closed successfully")
        except Exception as e:
            log.warning(f"Error closing SDK connection: {str(e)}")

    sys.exit(return_code)


if __name__ == "__main__":
    main()
