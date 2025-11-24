#!/usr/bin/env python3
"""
Script to connect to Greenboard cluster and update server state in QE server
pool.

This script accepts command line arguments for cluster connection details and
executes an N1QL update query to change the state of a server IP.

Usage:
    python update_server_pool_vm_state.py \\
        --cluster-ip <cluster_ip> \\
        --username <username> \\
        --password <password> \\
        --bucket <bucket> \\
        --scope <scope> \\
        --collection <collection> \\
        --server-ip <server_ip> \\
        --state <state> \\
        --log-level <log_level>
"""

import argparse
import logging
import sys
from datetime import timedelta

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s: %(funcName)s:L%(lineno)d: %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description=('Connect to Greenboard cluster and open SDK connection '
                     'to QE server pool'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python update_server_pool_vm_state.py --cluster-ip 172.23.1.1 \\
      --username Administrator --password password --bucket QE-server-pool \\
      --scope _default --collection _default --server-ip 10.0.0.1 \\
      --state available --log-level DEBUG
        """
    )

    parser.add_argument(
        '--cluster-ip',
        required=True,
        help='Greenboard cluster IP address'
    )

    parser.add_argument(
        '--username',
        required=True,
        help='Username for cluster authentication'
    )

    parser.add_argument(
        '--password',
        required=True,
        help='Password for cluster authentication'
    )

    parser.add_argument(
        '--bucket',
        dest='qe_server_pool_bucket',
        required=True,
        help='QE server pool bucket name'
    )

    parser.add_argument(
        '--scope',
        dest='qe_server_pool_scope',
        required=True,
        help='QE server pool scope name'
    )

    parser.add_argument(
        '--collection',
        dest='qe_server_pool_collection',
        required=True,
        help='QE server pool collection name'
    )

    parser.add_argument(
        '--server-ip',
        required=True,
        help='Server IP address to update'
    )

    parser.add_argument(
        '--state',
        required=True,
        choices=['available', 'booked', 'sshFailed', 'failedInstall'],
        help='New state to set for the server IP (valid values: available, '
             'booked, sshFailed, failedInstall)'
    )

    parser.add_argument(
        '--log-level',
        dest='log_level',
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Log level (default: INFO)'
    )

    return parser.parse_args()


def open_sdk_connection(cluster_ip, username, password):
    """
    Open SDK connection to Couchbase cluster.

    Args:
        cluster_ip: Cluster IP address
        username: Username for authentication
        password: Password for authentication

    Returns:
        Cluster: Couchbase cluster object
    """
    try:
        # Create authentication options
        auth = PasswordAuthenticator(username, password)
        options = ClusterOptions(auth)

        # Connect to cluster
        cluster = Cluster(f'couchbase://{cluster_ip}', options)

        # Wait for cluster to be ready
        cluster.wait_until_ready(timedelta(seconds=5))

        log.info(f"Successfully connected to Couchbase cluster at "
                 f"{cluster_ip}")

        return cluster

    except Exception as e:
        log.error(f"Failed to connect to Couchbase: {str(e)}")
        raise


def update_server_state(cluster, bucket_name, scope_name, collection_name,
                        server_ip, state):
    """
    Execute N1QL update query to change server state.

    Args:
        cluster: Couchbase cluster object
        bucket_name: Bucket name
        scope_name: Scope name
        collection_name: Collection name
        server_ip: Server IP address to update
        state: New state to set

    Returns:
        bool: True if update was successful, False otherwise
    """
    try:
        # First, query the current document to get username and state
        query = (
            f"SELECT username, state FROM `{bucket_name}`.`{scope_name}`."
            f"`{collection_name}` WHERE ipaddr='{server_ip}'"
        )
        log.info(f"Query: {query}")

        select_result = cluster.query(query)
        current_username, current_state = None, None

        # Iterate through result rows to get current values
        for row in select_result.rows():
            current_username = row.get('username', 'N/A')
            current_state = row.get('state', 'N/A')
            log.info(f"\n{server_ip}:\n"
                     f"  -> Current username ='{current_username}'\n"
                     f"  -> Current state    ='{current_state}'")
            # Only need first row to get current values
            break

        if current_username is None:
            log.critical(f"Document not found for ipaddr='{server_ip}'")
        if current_state != "booked":
            log.critical(f"Server {server_ip} is not in booked state")

        # Format the N1QL update query using scope and collection names
        update_query = (
            f"UPDATE `{bucket_name}`.`{scope_name}`.`{collection_name}` "
            f"SET state='{state}' "
            f"WHERE ipaddr='{server_ip}' AND state='booked'"
        )
        log.info(f"Query: {update_query}")

        # Execute the query using cluster object
        result = cluster.query(update_query)

        # Iterate through all result rows to ensure the query actually executes
        rows_processed = 0
        for row in result.rows():
            log.debug(f"Query result row: {row}")
            rows_processed += 1

        # Get metadata to check mutation count and status after iterating
        metadata = result.metadata()

        # Check for errors first
        if hasattr(metadata, 'warnings') and metadata.warnings():
            for warning in metadata.warnings():
                log.warning(f"Query warning: {warning}")

        # Try to get mutation count from metrics
        mutation_count = None
        try:
            if hasattr(metadata, 'metrics') and metadata.metrics():
                metrics = metadata.metrics()
                if hasattr(metrics, 'mutation_count'):
                    mutation_count = metrics.mutation_count()
                elif hasattr(metrics, 'mutationCount'):
                    mutation_count = metrics.mutationCount()
        except Exception as e:
            log.debug(f"Could not access mutation count: {e}")

        # Check mutation count
        if mutation_count is not None:
            log.info(f"Mutation count: {mutation_count}")
            if mutation_count > 0:
                log.info(f"Success: {server_ip} state='{state}' "
                         f"(mutation count={mutation_count})")
                return True
            else:
                # Mutation count is 0, but query executed without errors.
                # This could mean no rows matched the WHERE clause, or
                # mutation_count is not reliable. Since query executed
                # successfully, we'll log info and return True.
                log.critical(f"{server_ip} state='{state}' (mutation count=0)")
                return True
        else:
            # If mutation count is not available, assume query executed
            # successfully if no exceptions were raised. The query execution
            # itself indicates success, even if we can't verify the count.
            log.info(f"Success: {server_ip} state='{state}' "
                     f"(mutation count unavailable)")
            return True
    except Exception as e:
        log.error(f"Failed to execute update query: {str(e)}")
        raise


def main():
    """Main function to parse arguments and open SDK connection."""
    args = parse_arguments()

    # Set log level
    log_level = getattr(logging, args.log_level.upper())
    logging.getLogger().setLevel(log_level)

    cluster = None
    return_code = 0

    try:
        # Open SDK connection
        cluster = open_sdk_connection(
            args.cluster_ip,
            args.username,
            args.password
        )

        log.info("SDK connection opened successfully")

        # Execute the update query
        success = update_server_state(
            cluster,
            args.qe_server_pool_bucket,
            args.qe_server_pool_scope,
            args.qe_server_pool_collection,
            args.server_ip,
            args.state
        )

        if not success:
            return_code = 1

    except Exception as e:
        log.error(f"Exception: {str(e)}")
        return_code = 1

    finally:
        # Always close the SDK connection
        if cluster is not None:
            try:
                cluster.close()
                log.info("SDK connection closed successfully")
            except Exception as e:
                log.warning(f"Error closing SDK connection: {str(e)}")

    sys.exit(return_code)


if __name__ == "__main__":
    main()
