import unittest
import time
import copy
import logger
import logging
import re

from couchbase_helper.cluster import Cluster
from membase.api.rest_client import RestConnection, Bucket
from membase.api.exception import ServerUnavailableException
from remote.remote_util import RemoteMachineShellConnection
from remote.remote_util import RemoteUtilHelper
from testconstants import STANDARD_BUCKET_PORT
from couchbase_helper.document import View
from membase.helper.cluster_helper import ClusterOperationHelper
from couchbase_helper.stats_tools import StatsCommon
from membase.helper.bucket_helper import BucketOperationHelper
from memcached.helper.data_helper import MemcachedClientHelper
from TestInput import TestInputSingleton
from scripts.collect_server_info import cbcollectRunner
from scripts import collect_data_files
from tasks.future import TimeoutError

from couchbase_helper.documentgenerator import BlobGenerator
from lib.membase.api.exception import XDCRException
from security.auditmain import audit


class RenameNodeException(XDCRException):

    """Exception thrown when converting ip to hostname failed
    """

    def __init__(self, msg=''):
        XDCRException.__init__(self, msg)


class RebalanceNotStopException(XDCRException):

    """Exception thrown when stopping rebalance failed
    """

    def __init__(self, msg=''):
        XDCRException.__init__(self, msg)


def raise_if(cond, ex):
    """Raise Exception if condition is True
    """
    if cond:
        raise ex


class TOPOLOGY:
    CHAIN = "chain"
    STAR = "star"
    RING = "ring"
    HYBRID = "hybrid"


class REPLICATION_DIRECTION:
    UNIDIRECTION = "unidirection"
    BIDIRECTION = "bidirection"


class REPLICATION_TYPE:
    CONTINUOUS = "continuous"


class REPLICATION_PROTOCOL:
    CAPI = "capi"
    XMEM = "xmem"


class INPUT:
    REPLICATION_DIRECTION = "rdirection"
    CLUSTER_TOPOLOGY = "ctopology"
    SEED_DATA = "sdata"
    SEED_DATA_MODE = "sdata_mode"
    SEED_DATA_OPERATION = "sdata_op"
    POLL_INTERVAL = "poll_interval"  # in seconds
    POLL_TIMEOUT = "poll_timeout"  # in seconds
    SEED_DATA_MODE_SYNC = "sync"


class OPS:
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    APPEND = "append"


class EVICTION_POLICY:
    VALUE_ONLY = "valueOnly"


class BUCKET_PRIORITY:
    HIGH = "high"


class BUCKET_NAME:
    DEFAULT = "default"


class OS:
    WINDOWS = "windows"
    LINUX = "linux"
    OSX = "osx"


class COMMAND:
    SHUTDOWN = "shutdown"
    REBOOT = "reboot"


class STATE:
    RUNNING = "running"


class REPL_PARAM:
    FAILURE_RESTART = "failureRestartInterval"
    CHECKPOINT_INTERVAL = "checkpointInterval"
    OPTIMISTIC_THRESHOLD = "optimisticReplicationThreshold"
    FILTER_EXP = "filterExpression"
    SOURCE_NOZZLES = "sourceNozzlePerNode"
    TARGET_NOZZLES = "targetNozzlePerNode"
    BATCH_COUNT = "workerBatchSize"
    BATCH_SIZE = "docBatchSizeKb"
    LOG_LEVEL = "logLevel"
    MAX_REPLICATION_LAG = "maxExpectedReplicationLag"
    TIMEOUT_PERC = "timeoutPercentageCap"
    PAUSE_REQUESTED = "pauseRequested"


class TEST_XDCR_PARAM:
    FAILURE_RESTART = "failure_restart_interval"
    CHECKPOINT_INTERVAL = "checkpoint_interval"
    OPTIMISTIC_THRESHOLD = "optimistic_threshold"
    FILTER_EXP = "filter_expression"
    SOURCE_NOZZLES = "source_nozzles"
    TARGET_NOZZLES = "target_nozzles"
    BATCH_COUNT = "batch_count"
    BATCH_SIZE = "batch_size"
    LOG_LEVEL = "log_level"
    MAX_REPLICATION_LAG = "max_replication_lag"
    TIMEOUT_PERC = "timeout_percentage"

    @staticmethod
    def get_test_to_create_repl_param_map():
        return {
            TEST_XDCR_PARAM.FAILURE_RESTART: REPL_PARAM.FAILURE_RESTART,
            TEST_XDCR_PARAM.CHECKPOINT_INTERVAL: REPL_PARAM.CHECKPOINT_INTERVAL,
            TEST_XDCR_PARAM.OPTIMISTIC_THRESHOLD: REPL_PARAM.OPTIMISTIC_THRESHOLD,
            TEST_XDCR_PARAM.FILTER_EXP: REPL_PARAM.FILTER_EXP,
            TEST_XDCR_PARAM.SOURCE_NOZZLES: REPL_PARAM.SOURCE_NOZZLES,
            TEST_XDCR_PARAM.TARGET_NOZZLES: REPL_PARAM.TARGET_NOZZLES,
            TEST_XDCR_PARAM.BATCH_COUNT: REPL_PARAM.BATCH_COUNT,
            TEST_XDCR_PARAM.BATCH_SIZE: REPL_PARAM.BATCH_SIZE,
            TEST_XDCR_PARAM.MAX_REPLICATION_LAG: REPL_PARAM.MAX_REPLICATION_LAG,
            TEST_XDCR_PARAM.TIMEOUT_PERC: REPL_PARAM.TIMEOUT_PERC,
            TEST_XDCR_PARAM.LOG_LEVEL: REPL_PARAM.LOG_LEVEL
        }


class XDCR_PARAM:
    # Per-replication params (input)
    XDCR_FAILURE_RESTART = "xdcrFailureRestartInterval"
    XDCR_CHECKPOINT_INTERVAL = "xdcrCheckpointInterval"
    XDCR_OPTIMISTIC_THRESHOLD = "xdcrOptimisticReplicationThreshold"
    XDCR_FILTER_EXP = "xdcrFilterExpression"
    XDCR_SOURCE_NOZZLES = "xdcrSourceNozzlePerNode"
    XDCR_TARGET_NOZZLES = "xdcrTargetNozzlePerNode"
    XDCR_BATCH_COUNT = "xdcrWorkerBatchSize"
    XDCR_BATCH_SIZE = "xdcrDocBatchSizeKb"
    XDCR_LOG_LEVEL = "xdcrLogLevel"
    XDCR_MAX_REPLICATION_LAG = "xdcrMaxExpectedReplicationLag"
    XDCR_TIMEOUT_PERC = "xdcrTimeoutPercentageCap"


class CHECK_AUDIT_EVENT:
    CHECK = False


class GO_XDCR:
    ENABLED = False

# Event Definition:
# https://github.com/couchbase/goxdcr/blob/master/etc/audit_descriptor.json


class GO_XDCR_AUDIT_EVENT_ID:
    CREATE_CLUSTER = 16384
    MOD_CLUSTER = 16385
    RM_CLUSTER = 16386
    CREATE_REPL = 16387
    PAUSE_REPL = 16388
    RESUME_REPL = 16389
    CAN_REPL = 16390
    DEFAULT_SETT = 16391
    IND_SETT = 16392


class ERLANG_XDCR_AUDIT_EVENT_ID:
    CREATE_CLUSTER = 8213
    MOD_CLUSTER = 8214
    RM_CLUSTER = 8215
    CREATE_REPL = 8216
    # EventID for Pause, Resume and Individual repl, settings.
    UPDATE_REPL = 8217
    CAN_REPL = 8218


class NodeHelper:
    _log = logger.Logger.get_logger()

    @staticmethod
    def disable_firewall(
            server, rep_direction=REPLICATION_DIRECTION.UNIDIRECTION):
        """Disable firewall to put restriction to replicate items in XDCR.
        @param server: server object to disable firewall
        @param rep_direction: replication direction unidirection/bidirection
        """
        shell = RemoteMachineShellConnection(server)
        o, r = shell.execute_command("iptables -F")
        shell.log_command_output(o, r)
        o, r = shell.execute_command(
            "/sbin/iptables -A INPUT -p tcp -i eth0 --dport 1000:65535 -j ACCEPT")
        shell.log_command_output(o, r)
        if rep_direction == REPLICATION_DIRECTION.BIDIRECTION:
            o, r = shell.execute_command(
                "/sbin/iptables -A OUTPUT -p tcp -o eth0 --dport 1000:65535 -j ACCEPT")
            shell.log_command_output(o, r)
        o, r = shell.execute_command(
            "/sbin/iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT")
        shell.log_command_output(o, r)
        # self.log.info("enabled firewall on {0}".format(server))
        o, r = shell.execute_command("/sbin/iptables --list")
        shell.log_command_output(o, r)
        shell.disconnect()

    @staticmethod
    def reboot_server(server, test_case, wait_timeout=60):
        """Reboot a server and wait for couchbase server to run.
        @param server: server object, which needs to be rebooted.
        @param test_case: test case object, since it has assert() function
                        which is used by wait_for_ns_servers_or_assert
                        to throw assertion.
        @param wait_timeout: timeout to whole reboot operation.
        """
        # self.log.info("Rebooting server '{0}'....".format(server.ip))
        shell = RemoteMachineShellConnection(server)
        if shell.extract_remote_info().type.lower() == OS.WINDOWS:
            o, r = shell.execute_command(
                "{0} -r -f -t 0".format(COMMAND.SHUTDOWN))
        elif shell.extract_remote_info().type.lower() == OS.LINUX:
            o, r = shell.execute_command(COMMAND.REBOOT)
        shell.log_command_output(o, r)
        # wait for restart and warmup on all server
        time.sleep(wait_timeout * 5)
        # disable firewall on these nodes
        NodeHelper.disable_firewall(server)
        # wait till server is ready after warmup
        ClusterOperationHelper.wait_for_ns_servers_or_assert(
            [server],
            test_case,
            wait_if_warmup=True)
        if GO_XDCR.ENABLED:
            RestConnection(server).enable_goxdcr()
        else:
            RestConnection(server).enable_xdcr_trace_logging()

    @staticmethod
    def enable_firewall(
            server, rep_direction=REPLICATION_DIRECTION.UNIDIRECTION):
        """Enable firewall
        @param server: server object to enable firewall
        @param rep_direction: replication direction unidirection/bidirection
        """
        is_bidirectional = rep_direction == REPLICATION_DIRECTION.BIDIRECTION
        RemoteUtilHelper.enable_firewall(
            server,
            bidirectional=is_bidirectional,
            xdcr=True)

    @staticmethod
    def do_a_warm_up(server):
        """Warmp up server
        """
        shell = RemoteMachineShellConnection(server)
        shell.stop_couchbase()
        time.sleep(5)
        shell.start_couchbase()
        shell.disconnect()
        if GO_XDCR.ENABLED:
            RestConnection(server).enable_goxdcr()
        else:
            RestConnection(server).enable_xdcr_trace_logging()

    @staticmethod
    def wait_service_started(server, wait_time=120):
        """Function will wait for Couchbase service to be in
        running phase.
        """
        shell = RemoteMachineShellConnection(server)
        os_type = shell.extract_remote_info().distribution_type
        if os_type.lower() == 'windows':
            cmd = "sc query CouchbaseServer | grep STATE"
        else:
            cmd = "service couchbase-server status"
        now = time.time()
        while time.time() - now < wait_time:
            output, _ = shell.execute_command(cmd)
            if str(output).lower().find("running") != -1:
                # self.log.info("Couchbase service is running")
                if GO_XDCR.ENABLED:
                    RestConnection(server).enable_goxdcr()
                else:
                    RestConnection(server).enable_xdcr_trace_logging()
                return
            time.sleep(10)
        raise Exception(
            "Couchbase service is not running after {0} seconds".format(
                wait_time))

    @staticmethod
    def wait_warmup_completed(warmupnodes, bucket_names=["default"]):
        if isinstance(bucket_names, str):
            bucket_names = [bucket_names]
        for server in warmupnodes:
            for bucket in bucket_names:
                mc = MemcachedClientHelper.direct_client(server, bucket)
                start = time.time()
                while time.time() - start < 150:
                    if mc.stats()["ep_warmup_thread"] == "complete":
                        NodeHelper._log.info(
                            "Warmed up: %s items " %
                            (mc.stats()["curr_items_tot"]))
                        time.sleep(10)
                        break
                    elif mc.stats()["ep_warmup_thread"] == "running":
                        NodeHelper._log.info(
                            "Still warming up .. curr_items_tot : %s" % (mc.stats()["curr_items_tot"]))
                        continue
                    else:
                        NodeHelper._log.info(
                            "Value of ep_warmup_thread does not exist, exiting from this server")
                        break
                if mc.stats()["ep_warmup_thread"] == "running":
                    NodeHelper._log.info(
                        "ERROR: ep_warmup_thread's status not complete")
                mc.close

    @staticmethod
    def wait_node_restarted(
            server, test_case, wait_time=120, wait_if_warmup=False,
            check_service=False):
        """Wait server to be re-started
        """
        now = time.time()
        if check_service:
            NodeHelper.wait_service_started(server, wait_time)
            wait_time = now + wait_time - time.time()
        num = 0
        while num < wait_time / 10:
            try:
                ClusterOperationHelper.wait_for_ns_servers_or_assert(
                    [server], test_case, wait_time=wait_time - num * 10,
                    wait_if_warmup=wait_if_warmup)
                break
            except ServerUnavailableException:
                num += 1
                time.sleep(10)

    @staticmethod
    def kill_erlang(server):
        """Kill erlang process running on server.
        """
        NodeHelper._log.info("Killing erlang on server: {0}".format(server))
        shell = RemoteMachineShellConnection(server)
        os_info = shell.extract_remote_info()
        shell.kill_erlang(os_info)
        shell.disconnect()

    @staticmethod
    def kill_memcached(server):
        """Kill memcached process running on server.
        """
        shell = RemoteMachineShellConnection(server)
        shell.kill_erlang()
        shell.disconnect()

    @staticmethod
    def rename_nodes(servers):
        """Rename server name from ip to their hostname
        @param servers: list of server objects.
        @return: dictionary whose key is server and value is hostname
        """
        hostnames = {}
        for server in servers:
            shell = RemoteMachineShellConnection(server)
            try:
                hostname = shell.get_full_hostname()
                rest = RestConnection(server)
                renamed, content = rest.rename_node(
                    hostname, username=server.rest_username,
                    password=server.rest_password)
                raise_if(
                    not renamed,
                    RenameNodeException(
                        "Server %s is not renamed! Hostname %s. Error %s" % (
                            server, hostname, content)
                    )
                )
                hostnames[server] = hostname
                server.hostname = hostname
            finally:
                shell.disconnect()
        return hostnames

    # Returns version like "x.x.x" after removing build number
    @staticmethod
    def get_cb_version(node):
        rest = RestConnection(node)
        version = rest.get_nodes_self().version
        return version[:version.rfind('-')]

    @staticmethod
    def set_wall_clock_time(node, date_str):
        shell = RemoteMachineShellConnection(node)
        # os_info = shell.extract_remote_info()
        # if os_info == OS.LINUX:
        # date command works on linux and windows cygwin as well.
        shell.execute_command(
            "sudo date -s '%s'" %
            time.ctime(
                date_str.tx_time))
        # elif os_info == OS.WINDOWS:
        #    raise "NEED To SETUP DATE COMMAND FOR WINDOWS"


class ValidateAuditEvent:

    @staticmethod
    def validate_audit_event(event_id, master_node, expected_results):
        if CHECK_AUDIT_EVENT.CHECK:
            audit_obj = audit(event_id, master_node)
            field_verified, value_verified = audit_obj.validateEvents(
                expected_results)
            raise_if(
                not field_verified,
                XDCRException("One of the fields is not matching"))
            raise_if(
                not value_verified,
                XDCRException("Values for one of the fields is not matching"))


class FloatingServers:

    """Keep Track of free servers, For Rebalance-in
    or swap-rebalance operations.
    """
    _serverlist = []


class XDCRRemoteClusterRef:

    """Class keep the information related to Remote Cluster References.
    """

    def __init__(self, src_cluster, dest_cluster, name, encryption=False):
        """
        @param src_cluster: source couchbase cluster object.
        @param dest_cluster: destination couchbase cluster object:
        @param name: remote cluster reference name.
        @param encryption: True to enable SSL encryption for replication else
                        False
        """
        self.__src_cluster = src_cluster
        self.__dest_cluster = dest_cluster
        self.__name = name
        self.__encryption = encryption
        self.__rest_info = {}

        # List of XDCRepication objects
        self.__replications = []

    def __str__(self):
        return "{0} -> {1}, Name: {2}".format(
            self.__src_cluster.get_name(), self.__dest_cluster.get_name(),
            self.__name)

    def get_src_cluster(self):
        return self.__src_cluster

    def get_dest_cluster(self):
        return self.__dest_cluster

    def get_name(self):
        return self.__name

    def get_replications(self):
        return self.__replications

    def get_rest_info(self):
        return self.__rest_info

    def __get_event_expected_results(self):
        if GO_XDCR.ENABLED:
            expected_results = {
                "real_userid:source": "internal",
                "real_userid:user": self.__src_cluster.get_master_node().rest_username,
                "cluster_name": self.__name,
                "cluster_hostname": "%s:%s" % (self.__dest_cluster.get_master_node().ip, self.__dest_cluster.get_master_node().port),
                "is_encrypted": str(self.__encryption).lower()
            }
        else:
            expected_results = {
                "uuid": self.__rest_info['uuid'],
                "name": self.__rest_info['name'],
                "username": self.__rest_info['username'],
                "hostname": self.__rest_info['hostname'],
                "demand_encryption": str(self.__encryption).lower(),
                "real_userid:source": "ns_server",
                "real_userid:user": self.__src_cluster.get_master_node().rest_username
            }
        return expected_results

    def __validate_create_event(self):
        if GO_XDCR.ENABLED:
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.CREATE_CLUSTER,
                self.__src_cluster.get_master_node(),
                self.__get_event_expected_results())
        else:
            ValidateAuditEvent.validate_audit_event(
                ERLANG_XDCR_AUDIT_EVENT_ID.CREATE_CLUSTER,
                self.__src_cluster.get_master_node(),
                self.__get_event_expected_results())

    def add(self):
        """create cluster reference- add remote cluster
        """
        rest_conn_src = RestConnection(self.__src_cluster.get_master_node())
        certificate = ""
        dest_master = self.__dest_cluster.get_master_node()
        if self.__encryption:
            rest_conn_dest = RestConnection(dest_master)
            certificate = rest_conn_dest.get_cluster_ceritificate()
        self.__rest_info = rest_conn_src.add_remote_cluster(
            dest_master.ip, dest_master.port,
            dest_master.rest_username,
            dest_master.rest_password, self.__name,
            demandEncryption=self.__encryption,
            certificate=certificate)

        self.__validate_create_event()

    def __validate_modify_event(self):
        if GO_XDCR.ENABLED:
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.MOD_CLUSTER,
                self.__src_cluster.get_master_node(),
                self.__get_event_expected_results())
        else:
            ValidateAuditEvent.validate_audit_event(
                ERLANG_XDCR_AUDIT_EVENT_ID.MOD_CLUSTER,
                self.__src_cluster.get_master_node(),
                self.__get_event_expected_results())

    def modify(self, encryption=True):
        """Modify cluster reference to enable SSL encryption
        """
        dest_master = self.__dest_cluster.get_master_node()
        rest_conn_src = RestConnection(self.__src_cluster.get_master_node())
        certificate = ""
        if encryption:
            rest_conn_dest = RestConnection(dest_master)
            certificate = rest_conn_dest.get_cluster_ceritificate()
            self.__rest_info = rest_conn_src.modify_remote_cluster(
                dest_master.ip, dest_master.port,
                dest_master.rest_username,
                dest_master.rest_password, self.__name,
                demandEncryption=encryption,
                certificate=certificate)
        self.__encryption = encryption

        self.__validate_modify_event()

    def __validate_remove_event(self):
        if GO_XDCR.ENABLED:
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.RM_CLUSTER,
                self.__src_cluster.get_master_node(),
                self.__get_event_expected_results())
        else:
            ValidateAuditEvent.validate_audit_event(
                ERLANG_XDCR_AUDIT_EVENT_ID.RM_CLUSTER,
                self.__src_cluster.get_master_node(),
                self.__get_event_expected_results())

    def remove(self):
        RestConnection(
            self.__src_cluster.get_master_node()).remove_remote_cluster(
            self.__name)
        self.__validate_remove_event()

    def create_replication(
            self, fromBucket,
            rep_type=REPLICATION_PROTOCOL.XMEM,
            toBucket=None
    ):
        """Create replication objects, but replication will not get
        started here.
        """
        self.__replications.append(
            XDCReplication(
                self,
                fromBucket,
                rep_type,
                toBucket))

    def clear_all_replications(self):
        self.__replications = []

    def start_all_replications(self):
        """Start all created replication
        """
        [repl.start() for repl in self.__replications]

    def pause_all_replications(self):
        """Pause all created replication
        """
        [repl.pause() for repl in self.__replications]

    def resume_all_replications(self):
        """Resume all created replication
        """
        [repl.resume() for repl in self.__replications]

    def stop_all_replications(self):
        rest = RestConnection(self.__src_cluster.get_master_node())
        rest_all_repls = rest.get_replications()
        for repl in self.__replications:
            if rest.is_goxdcr_enabled():
                rest.stop_replication("controller/cancelXDCR/%s" % repl.get_repl_id())
            else:
                for rest_all_repl in rest_all_repls:
                    if repl.get_repl_id() == rest_all_repl['id']:
                        repl.cancel(rest, rest_all_repl)
        self.clear_all_replications()


class XDCReplication:

    def __init__(self, remote_cluster_ref, from_bucket, rep_type, to_bucket):
        """
        @param remote_cluster_ref: XDCRRemoteClusterRef object
        @param from_bucket: Source bucket (Bucket object)
        @param rep_type: replication protocol REPLICATION_PROTOCOL.CAPI/XMEM
        @param to_bucket: Destination bucket (Bucket object)
        """
        self.__input = TestInputSingleton.input
        self.__remote_cluster_ref = remote_cluster_ref
        self.__from_bucket = from_bucket
        self.__to_bucket = to_bucket or from_bucket
        self.__src_cluster = self.__remote_cluster_ref.get_src_cluster()
        self.__dest_cluster = self.__remote_cluster_ref.get_dest_cluster()
        self.__src_cluster_name = self.__src_cluster.get_name()
        self.__rep_type = rep_type
        self.__test_xdcr_params = {}
        self.__updated_params = {}

        self.__parse_test_xdcr_params()
        self.log = logger.Logger.get_logger()

        # Response from REST API
        self.__rep_id = None

    def __str__(self):
        return "Replication {0}:{1} -> {2}:{3}".format(
            self.__src_cluster.get_name(),
            self.__from_bucket.name, self.__dest_cluster.get_name(),
            self.__to_bucket.name)

    # get per replication params specified as from_bucket@cluster_name=
    # eg. default@C1="xdcrFilterExpression:loadOne,xdcrCheckpointInterval:60,
    # xdcrFailureRestartInterval:20"
    def __parse_test_xdcr_params(self):
        param_str = self.__input.param(
            "%s@%s" %
            (self.__from_bucket, self.__src_cluster_name), None)
        if param_str:
            argument_split = re.split('[:,]', param_str)
            self.__test_xdcr_params.update(
                dict(zip(argument_split[::2], argument_split[1::2]))
            )

    def __convert_test_to_xdcr_params(self):
        xdcr_params = {}
        xdcr_param_map = TEST_XDCR_PARAM.get_test_to_create_repl_param_map()
        for test_param, value in self.__test_xdcr_params.iteritems():
            xdcr_params[xdcr_param_map[test_param]] = value
        return xdcr_params

    def get_filter_exp(self):
        if TEST_XDCR_PARAM.FILTER_EXP in self.__test_xdcr_params:
            return self.__test_xdcr_params[TEST_XDCR_PARAM.FILTER_EXP]
        return None

    def get_src_bucket(self):
        return self.__from_bucket

    def get_dest_bucket(self):
        return self.__to_bucket

    def get_src_cluster(self):
        return self.__src_cluster

    def get_dest_cluster(self):
        return self.__dest_cluster

    def get_repl_id(self):
        return self.__rep_id

    def __get_event_expected_results(self):
        expected_results = {}
        if GO_XDCR.ENABLED:
            expected_results = {
                "real_userid:source": "internal",
                "real_userid:user": self.__src_cluster.get_master_node().rest_username,
                "local_cluster_name": "",  # FIXME
                "source_bucket_name": self.__from_bucket.name,
                "remote_cluster_name": self.__remote_cluster_ref.get_name(),
                "target_bucket_name": self.__to_bucket.name
            }
        return expected_results

    def __validate_update_repl_event(self):
        expected_results = {
            "settings": {
                "continuous": 'true',
                "target": "",  # FIXME
                "source": self.__from_bucket.name,
                "type": "xdc-%s" % self.__rep_type
            },
            "id": self.__rep_id,
            "real_userid:source": "ns_server",
            "real_userid:user": self.__src_cluster.get_master_node().rest_username,
        }
        expected_results["settings"].update(self.__updated_params)
        ValidateAuditEvent.validate_audit_event(
            ERLANG_XDCR_AUDIT_EVENT_ID.UPDATE_REPL,
            self.get_src_cluster().get_master_node(),
            expected_results)

    def __validate_set_param_event(self):
        if GO_XDCR.ENABLED:
            expected_results = self.__get_event_expected_results()
            expected_results["updated_settings"] = self.__updated_params
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.IND_SETT,
                self.get_src_cluster().get_master_node(), expected_results)
        else:
            self.__validate_update_repl_event()

    def set_xdcr_param(self, param, value, verify_event=True):
        src_master = self.__src_cluster.get_master_node()
        RestConnection(src_master).set_xdcr_param(
            self.__from_bucket.name,
            self.__to_bucket.name,
            param,
            value)

        self.__updated_params[param] = value
        if verify_event:
            self.__validate_set_param_event()

    def __validate_start_audit_event(self):
        if GO_XDCR.ENABLED:
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.CREATE_REPL,
                self.get_src_cluster().get_master_node(),
                self.__get_event_expected_results())
        else:
            expected_results = {
                "to_bucket": self.__to_bucket.name,
                "from_bucket": self.__from_bucket.name,
                "settings": {
                    'to_cluster': self.__remote_cluster_ref.get_name(),
                    "replication_type": REPLICATION_TYPE.CONTINUOUS
                },
                "id": self.__rep_id,
                "real_userid:source": "ns_server",
                "real_userid:user": self.__src_cluster.get_master_node().rest_username,
            }
            ValidateAuditEvent.validate_audit_event(
                ERLANG_XDCR_AUDIT_EVENT_ID.CREATE_REPL,
                self.get_src_cluster().get_master_node(),
                expected_results)

    def start(self):
        """Start replication"""
        src_master = self.__src_cluster.get_master_node()
        rest_conn_src = RestConnection(src_master)
        self.__rep_id = rest_conn_src.start_replication(
            REPLICATION_TYPE.CONTINUOUS,
            self.__from_bucket,
            self.__remote_cluster_ref.get_name(),
            rep_type=self.__rep_type,
            toBucket=self.__to_bucket,
            xdcr_params=self.__convert_test_to_xdcr_params())
        self.__validate_start_audit_event()

    def __verify_pause(self):
        """Verify if replication is paused"""
        src_master = self.__src_cluster.get_master_node()
        # Is bucket replication paused?
        if not RestConnection(src_master).is_replication_paused(
                self.__from_bucket.name,
                self.__to_bucket.name):
            raise XDCRException(
                "XDCR is not paused for SrcBucket: {0}, Target Bucket: {1}".
                format(self.__from_bucket.name,
                       self.__to_bucket.name))

    def __validate_pause_event(self):
        if GO_XDCR.ENABLED:
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.PAUSE_REPL,
                self.get_src_cluster().get_master_node(),
                self.__get_event_expected_results())
        else:
            self.__validate_update_repl_event()

    def pause(self, verify=False):
        """Pause replication"""
        src_master = self.__src_cluster.get_master_node()
        if not RestConnection(src_master).is_replication_paused(
                self.__from_bucket.name, self.__to_bucket.name):
            self.set_xdcr_param(
                REPL_PARAM.PAUSE_REQUESTED,
                'true',
                verify_event=False)

        self.__validate_pause_event()

        if verify:
            self.__verify_pause()

    def __is_cluster_replicating(self):
        count = 0
        src_master = self.__src_cluster.get_master_node()
        while count < 3:
            outbound_mutations = self.__src_cluster.get_xdcr_stat(
                src_master,
                self.__from_bucket,
                'replication_changes_left')
            if outbound_mutations == 0:
                self.log.info(
                    "Outbound mutations on {0} is {1}".format(
                        src_master.ip,
                        outbound_mutations))
                count += 1
                continue
            else:
                self.log.info(
                    "Outbound mutations on {0} is {1}".format(
                        src_master.ip,
                        outbound_mutations))
                self.log.info("Node {0} is replicating".format(src_master.ip))
                break
        else:
            self.log.info(
                "Outbound mutations on {0} is {1}".format(
                    src_master.ip,
                    outbound_mutations))
            self.log.info(
                "Cluster with node {0} is not replicating".format(
                    src_master.ip))
            return False
        return True

    def __verify_resume(self):
        """Verify if replication is resumed"""
        src_master = self.__src_cluster.get_master_node()
        # Is bucket replication paused?
        if RestConnection(src_master).is_replication_paused(self.__from_bucket.name,
                                                            self.__to_bucket.name):
            raise XDCRException(
                "Replication is not resumed for SrcBucket: {0}, \
                Target Bucket: {1}".format(self.__from_bucket, self.__to_bucket))

        if not self.__is_cluster_replicating():
            self.log.info("XDCR completed on {0}".format(src_master.ip))

    def __validate_resume_event(self):
        if GO_XDCR.ENABLED:
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.RESUME_REPL,
                self.get_src_cluster().get_master_node(),
                self.__get_event_expected_results())
        else:
            self.__validate_update_repl_event()

    def resume(self, verify=False):
        """Resume replication if paused"""
        src_master = self.__src_cluster.get_master_node()
        if RestConnection(src_master).is_replication_paused(
                self.__from_bucket.name, self.__to_bucket.name):
            self.set_xdcr_param(
                REPL_PARAM.PAUSE_REQUESTED,
                'false',
                verify_event=False)

        self.__validate_resume_event()

        if verify:
            self.__verify_resume()

    def __validate_cancel_event(self):
        if GO_XDCR.ENABLED:
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.CAN_REPL,
                self.get_src_cluster().get_master_node(),
                self.__get_event_expected_results())
        else:
            expected_results = {
                "id": self.__rep_id,
                "real_userid:source": "ns_server",
                "real_userid:user": self.__src_cluster.get_master_node().rest_username,
            }
            ValidateAuditEvent.validate_audit_event(
                ERLANG_XDCR_AUDIT_EVENT_ID.CAN_REPL,
                self.get_src_cluster().get_master_node(),
                expected_results)

    def cancel(self, rest, rest_all_repl):
        try:
            rest.stop_replication(rest_all_repl["cancelURI"])
        except:
            rest.stop_replication("controller/cancelXDCR/%s" % self.__rep_id)

        self.__validate_cancel_event()


class CouchbaseCluster:

    def __init__(self, name, nodes, log, use_hostname=False):
        """
        @param name: Couchbase cluster name. e.g C1, C2 to distinguish in logs.
        @param nodes: list of server objects (read from ini file).
        @param log: logger object to print logs.
        @param use_hostname: True if use node's hostname rather ip to access
                        node else False.
        """
        self.__name = name
        self.__nodes = nodes
        self.__log = log
        self.__mem_quota = 0
        self.__use_hostname = use_hostname
        self.__master_node = nodes[0]
        self.__design_docs = []
        self.__buckets = []
        self.__hostnames = {}
        self.__fail_over_nodes = []
        self.__data_verified = True
        self.__remote_clusters = []
        self.__clusterop = Cluster()
        self.__kv_gen = {}

    def __str__(self):
        return "Couchbase Cluster: %s, Master Ip: %s" % (
            self.__name, self.__master_node.ip)

    def __stop_rebalance(self):
        rest = RestConnection(self.__master_node)
        if rest._rebalance_progress_status() == 'running':
            self.__log.warning(
                "rebalancing is still running, test should be verified")
            stopped = rest.stop_rebalance()
            raise_if(
                not stopped,
                RebalanceNotStopException("unable to stop rebalance"))

    def __init_nodes(self, disabled_consistent_view=None):
        """Initialize all nodes. Rename node to hostname
        if needed by test.
        """
        tasks = []
        for node in self.__nodes:
            tasks.append(
                self.__clusterop.async_init_node(
                    node,
                    disabled_consistent_view))
        for task in tasks:
            mem_quota = task.result()
            if mem_quota < self.__mem_quota or self.__mem_quota == 0:
                self.__mem_quota = mem_quota
        if self.__use_hostname:
            self.__hostnames.update(NodeHelper.rename_nodes(self.__nodes))

    def get_host_names(self):
        return self.__hostnames

    def get_master_node(self):
        return self.__master_node

    def get_mem_quota(self):
        return self.__mem_quota

    def get_remote_clusters(self):
        return self.__remote_clusters

    def get_nodes(self):
        return self.__nodes

    def get_name(self):
        return self.__name

    def get_kv_gen(self):
        raise_if(
            self.__kv_gen is None,
            XDCRException(
                "KV store is empty on couchbase cluster: %s" %
                self))
        return self.__kv_gen

    def init_cluster(self, disabled_consistent_view=None):
        """Initialize cluster.
        1. Initialize all nodes.
        2. Add all nodes to the cluster.
        3. Enable xdcr trace logs to easy debug for xdcr items mismatch issues.
        """
        self.__init_nodes(disabled_consistent_view)
        self.__clusterop.async_rebalance(
            self.__nodes,
            self.__nodes[1:],
            [],
            use_hostnames=self.__use_hostname).result()
        for node in self.__nodes:
            if GO_XDCR.ENABLED:
                RestConnection(node).enable_goxdcr()
            else:
                RestConnection(node).enable_xdcr_trace_logging()

    def set_global_checkpt_interval(self, value):
        RestConnection(self.__master_node).set_internalSetting(
            XDCR_PARAM.XDCR_CHECKPOINT_INTERVAL,
            value)

    def __get_cbcollect_info(self):
        """Collect cbcollectinfo logs for all the servers in the cluster.
        """
        path = TestInputSingleton.input.param("logs_folder", "/tmp")
        for server in self.__nodes:
            print "grabbing cbcollect from {0}".format(server.ip)
            path = path or "."
            try:
                cbcollectRunner(server, path).run()
                TestInputSingleton.input.test_params[
                    "get-cbcollect-info"] = False
            except Exception as e:
                self.__log.error(
                    "IMPOSSIBLE TO GRAB CBCOLLECT FROM {0}: {1}".format(
                        server.ip,
                        e))

    def __collect_data_files(self):
        """Collect bucket data files for all the servers in the cluster.
        Data files are collected only if data is not verified on the cluster.
        """
        path = TestInputSingleton.input.param("logs_folder", "/tmp")
        for server in self.__nodes:
            collect_data_files.cbdatacollectRunner(server, path).run()

    def collect_logs(self, cluster_run):
        """Grab cbcollect before we cleanup
        """
        self.__get_cbcollect_info()
        if not cluster_run:
            self.__collect_data_files()

    def __remove_all_remote_clusters(self):
        rest_remote_clusters = RestConnection(
            self.__master_node).get_remote_clusters()
        for remote_cluster_ref in self.__remote_clusters:
            for rest_remote_cluster in rest_remote_clusters:
                if remote_cluster_ref.get_name() == rest_remote_cluster[
                        'name']:
                    if not rest_remote_cluster.get('deleted', False):
                        remote_cluster_ref.remove()
        self.__remote_clusters = []

    def __remove_all_replications(self):
        for remote_cluster_ref in self.__remote_clusters:
            remote_cluster_ref.stop_all_replications()

    def cleanup_cluster(
            self,
            test_case,
            from_rest=False,
            cluster_shutdown=True):
        """Cleanup cluster.
        1. Remove all remote cluster references.
        2. Remove all replications.
        3. Remove all buckets.
        @param test_case: Test case object.
        @param test_failed: True if test failed else False.
        @param cluster_run: True if test execution is single node cluster run else False.
        @param cluster_shutdown: True if Task (task.py) Scheduler needs to shutdown else False
        """
        try:
            self.__log.info("removing xdcr/nodes settings")
            rest = RestConnection(self.__master_node)
            if from_rest:
                rest.remove_all_remote_clusters()
                rest.remove_all_replications()
            else:
                self.__remove_all_replications()
                self.__remove_all_remote_clusters()
            rest.remove_all_recoveries()
            self.__stop_rebalance()
            self.__log.info("cleanup {0}".format(self.__nodes))
            for node in self.__nodes:
                BucketOperationHelper.delete_all_buckets_or_assert(
                    [node],
                    test_case)
                force_eject = TestInputSingleton.input.param(
                    "forceEject",
                    False)
                if force_eject and node != self.__master_node:
                    try:
                        rest = RestConnection(node)
                        rest.force_eject_node()
                    except BaseException as e:
                        self.__log.error(e)
                else:
                    ClusterOperationHelper.cleanup_cluster([node])
                ClusterOperationHelper.wait_for_ns_servers_or_assert(
                    [node],
                    test_case)
        finally:
            if cluster_shutdown:
                self.__clusterop.shutdown(force=True)

    def create_sasl_buckets(
            self, bucket_size, num_buckets=1, num_replicas=1,
            eviction_policy=EVICTION_POLICY.VALUE_ONLY,
            bucket_priority=BUCKET_PRIORITY.HIGH):
        """Create sasl buckets.
        @param bucket_size: size of the bucket.
        @param num_buckets: number of buckets to create.
        @param num_replicas: number of replicas (1-3).
        @param eviction_policy: valueOnly etc.
        @param bucket_priority: high/low etc.
        """
        bucket_tasks = []
        for i in range(num_buckets):
            name = "sasl_bucket_" + str(i + 1)
            bucket_tasks.append(self.__clusterop.async_create_sasl_bucket(
                self.__master_node,
                name,
                'password',
                bucket_size,
                num_replicas,
                eviction_policy=eviction_policy,
                bucket_priority=bucket_priority))
            self.__buckets.append(
                Bucket(
                    name=name, authType="sasl", saslPassword="password",
                    num_replicas=num_replicas, bucket_size=bucket_size,
                    eviction_policy=eviction_policy,
                    bucket_priority=bucket_priority
                ))

        for task in bucket_tasks:
            task.result()

    def create_standard_buckets(
            self, bucket_size, num_buckets=1, num_replicas=1,
            eviction_policy=EVICTION_POLICY.VALUE_ONLY,
            bucket_priority=BUCKET_PRIORITY.HIGH):
        """Create standard buckets.
        @param bucket_size: size of the bucket.
        @param num_buckets: number of buckets to create.
        @param num_replicas: number of replicas (1-3).
        @param eviction_policy: valueOnly etc.
        @param bucket_priority: high/low etc.
        """
        bucket_tasks = []
        for i in range(num_buckets):
            name = "standard_bucket_" + str(i + 1)
            bucket_tasks.append(self.__clusterop.async_create_standard_bucket(
                self.__master_node,
                name,
                STANDARD_BUCKET_PORT + i,
                bucket_size,
                num_replicas,
                eviction_policy=eviction_policy,
                bucket_priority=bucket_priority))
            self.__buckets.append(
                Bucket(
                    name=name,
                    authType=None,
                    saslPassword=None,
                    num_replicas=num_replicas,
                    bucket_size=bucket_size,
                    port=STANDARD_BUCKET_PORT + i,
                    eviction_policy=eviction_policy,
                    bucket_priority=bucket_priority
                ))

        for task in bucket_tasks:
            task.result()

    def create_default_bucket(
            self, bucket_size, num_replicas=1,
            eviction_policy=EVICTION_POLICY.VALUE_ONLY,
            bucket_priority=BUCKET_PRIORITY.HIGH
    ):
        """Create default bucket.
        @param bucket_size: size of the bucket.
        @param num_replicas: number of replicas (1-3).
        @param eviction_policy: valueOnly etc.
        @param bucket_priority: high/low etc.
        """
        self.__clusterop.create_default_bucket(
            self.__master_node,
            bucket_size,
            num_replicas,
            eviction_policy=eviction_policy,
            bucket_priority=bucket_priority
        )
        self.__buckets.append(
            Bucket(
                name=BUCKET_NAME.DEFAULT,
                authType="sasl",
                saslPassword="",
                num_replicas=num_replicas,
                bucket_size=bucket_size,
                eviction_policy=eviction_policy,
                bucket_priority=bucket_priority
            ))

    def get_buckets(self):
        return self.__buckets

    def get_bucket_by_name(self, bucket_name):
        """Return the bucket with given name
        @param bucket_name: bucket name.
        @return: bucket object
        """
        for bucket in self.__buckets:
            if bucket.name == bucket_name:
                return bucket

        raise Exception(
            "Bucket with name: %s no found on the cluster" %
            bucket_name)

    def delete_bucket(self, bucket_name):
        """Delete bucket with given name
        @param bucket_name: bucket name (string) to delete
        """
        bucket_to_remove = self.get_bucket_by_name(bucket_name)
        self.__clusterop.bucket_delete(
            self.__master_node,
            bucket_to_remove.name)
        self.__buckets.remove(bucket_to_remove)

    def delete_all_buckets(self):
        for bucket_to_remove in self.__buckets:
            self.__clusterop.bucket_delete(
                self.__master_node,
                bucket_to_remove.name)
            self.__buckets.remove(bucket_to_remove)

    def flush_buckets(self, buckets=[]):
        buckets = buckets or self.__buckets
        tasks = []
        for bucket in buckets:
            tasks.append(self.__clusterop.async_bucket_flush(
                self.__master_node,
                bucket))
        [task.result() for task in tasks]

    def async_load_bucket(self, bucket, num_items, value_size=256, exp=0,
                          kv_store=1, flag=0, only_store_hash=True,
                          batch_size=1000, pause_secs=1, timeout_secs=30):
        """Load data asynchronously on given bucket. Function don't wait for
        load data to finish, return immidiately.
        @param bucket: bucket where to load data.
        @param num_items: number of items to load
        @param value_size: size of the one item.
        @param exp: expiration value.
        @param kv_store: kv store index.
        @param flag:
        @param only_store_hash: True to store hash of item else False.
        @param batch_size: batch size for load data at a time.
        @param pause_secs: pause for next batch load.
        @param timeout_secs: timeout
        @return: task object
        """
        seed = "%s-key-" % self.__name
        self.__kv_gen[
            OPS.CREATE] = BlobGenerator(
            seed,
            seed,
            value_size,
            end=num_items)

        gen = copy.deepcopy(self.__kv_gen[OPS.CREATE])
        task = self.__clusterop.async_load_gen_docs(
            self.__master_node, bucket.name, gen, bucket.kvs[kv_store],
            OPS.CREATE, exp, flag, only_store_hash, batch_size, pause_secs,
            timeout_secs)
        return task

    def load_bucket(self, bucket, num_items, value_size=256, exp=0,
                    kv_store=1, flag=0, only_store_hash=True,
                    batch_size=1000, pause_secs=1, timeout_secs=30):
        """Load data synchronously on given bucket. Function wait for
        load data to finish.
        @param bucket: bucket where to load data.
        @param num_items: number of items to load
        @param value_size: size of the one item.
        @param exp: expiration value.
        @param kv_store: kv store index.
        @param flag:
        @param only_store_hash: True to store hash of item else False.
        @param batch_size: batch size for load data at a time.
        @param pause_secs: pause for next batch load.
        @param timeout_secs: timeout
        """
        task = self.async_load_bucket(bucket, num_items, value_size, exp,
                                      kv_store, flag, only_store_hash,
                                      batch_size, pause_secs, timeout_secs)
        task.result()

    def async_load_all_buckets(self, num_items, value_size=256, exp=0,
                               kv_store=1, flag=0, only_store_hash=True,
                               batch_size=1000, pause_secs=1, timeout_secs=30):
        """Load data asynchronously on all buckets of the cluster.
        Function don't wait for load data to finish, return immidiately.
        @param num_items: number of items to load
        @param value_size: size of the one item.
        @param exp: expiration value.
        @param kv_store: kv store index.
        @param flag:
        @param only_store_hash: True to store hash of item else False.
        @param batch_size: batch size for load data at a time.
        @param pause_secs: pause for next batch load.
        @param timeout_secs: timeout
        @return: task objects list
        """
        seed = "%s-key-" % self.__name
        self.__kv_gen[
            OPS.CREATE] = BlobGenerator(
            seed,
            seed,
            value_size,
            end=num_items)
        tasks = []
        for bucket in self.__buckets:
            gen = copy.deepcopy(self.__kv_gen[OPS.CREATE])
            tasks.append(
                self.__clusterop.async_load_gen_docs(
                    self.__master_node, bucket.name, gen, bucket.kvs[kv_store],
                    OPS.CREATE, exp, flag, only_store_hash, batch_size,
                    pause_secs, timeout_secs)
            )
        return tasks

    def load_all_buckets(self, num_items, value_size=256, exp=0,
                         kv_store=1, flag=0, only_store_hash=True,
                         batch_size=1000, pause_secs=1, timeout_secs=30):
        """Load data synchronously on all buckets. Function wait for
        load data to finish.
        @param num_items: number of items to load
        @param value_size: size of the one item.
        @param exp: expiration value.
        @param kv_store: kv store index.
        @param flag:
        @param only_store_hash: True to store hash of item else False.
        @param batch_size: batch size for load data at a time.
        @param pause_secs: pause for next batch load.
        @param timeout_secs: timeout
        """
        tasks = self.async_load_all_buckets(
            num_items, value_size, exp, kv_store, flag, only_store_hash,
            batch_size, pause_secs, timeout_secs)
        for task in tasks:
            task.result()

    def load_all_buckets_from_generator(self, kv_gen, ops=OPS.CREATE, exp=0,
                                        kv_store=1, flag=0, only_store_hash=True,
                                        batch_size=1000, pause_secs=1, timeout_secs=30):
        """Load data synchronously on all buckets. Function wait for
        load data to finish.
        @param gen: BlobGenerator() object
        @param ops: OPS.CREATE/UPDATE/DELETE/APPEND.
        @param exp: expiration value.
        @param kv_store: kv store index.
        @param flag:
        @param only_store_hash: True to store hash of item else False.
        @param batch_size: batch size for load data at a time.
        @param pause_secs: pause for next batch load.
        @param timeout_secs: timeout
        """
        # TODO append generator values if op_type is already present
        if ops not in self.__kv_gen:
            self.__kv_gen[ops] = kv_gen

        tasks = []
        for bucket in self.__buckets:
            tasks.append(
                self.__clusterop.async_load_gen_docs(
                    self.__master_node, bucket.name, kv_gen,
                    bucket.kvs[kv_store], ops, exp, flag,
                    only_store_hash, batch_size, pause_secs, timeout_secs)
            )
        for task in tasks:
            task.result()

    def async_load_all_buckets_from_generator(self, kv_gen, ops=OPS.CREATE, exp=0,
                                              kv_store=1, flag=0, only_store_hash=True,
                                              batch_size=1000, pause_secs=1, timeout_secs=30):
        """Load data asynchronously on all buckets. Function wait for
        load data to finish.
        @param gen: BlobGenerator() object
        @param ops: OPS.CREATE/UPDATE/DELETE/APPEND.
        @param exp: expiration value.
        @param kv_store: kv store index.
        @param flag:
        @param only_store_hash: True to store hash of item else False.
        @param batch_size: batch size for load data at a time.
        @param pause_secs: pause for next batch load.
        @param timeout_secs: timeout
        """
        # TODO append generator values if op_type is already present
        if ops not in self.__kv_gen:
            self.__kv_gen[ops] = kv_gen

        tasks = []
        for bucket in self.__buckets:
            tasks.append(
                self.__clusterop.async_load_gen_docs(
                    self.__master_node, bucket.name, kv_gen,
                    bucket.kvs[kv_store], ops, exp, flag,
                    only_store_hash, batch_size, pause_secs, timeout_secs)
            )
        return tasks

    def load_all_buckets_till_dgm(self, active_resident_threshold,
                                  value_size=256, exp=0, kv_store=1, flag=0,
                                  only_store_hash=True, batch_size=1000,
                                  pause_secs=1, timeout_secs=30):
        """Load data synchronously on all buckets till dgm (Data greater than memory)
        for given active_resident_threshold
        @param active_resident_threshold: Dgm threshold.
        @param value_size: size of the one item.
        @param exp: expiration value.
        @param kv_store: kv store index.
        @param flag:
        @param only_store_hash: True to store hash of item else False.
        @param batch_size: batch size for load data at a time.
        @param pause_secs: pause for next batch load.
        @param timeout_secs: timeout
        """
        random_key = 0
        for bucket in self.__buckets:
            current_active_resident = StatsCommon.get_stats(
                [self.__master_node],
                bucket,
                '',
                'vb_active_perc_mem_resident')[self.__master_node]
            while int(current_active_resident) > active_resident_threshold:
                self.__log.info(
                    "resident ratio is %s greater than %s for %s in bucket %s.\
                    Continue loading to the cluster" % (
                        current_active_resident,
                        active_resident_threshold,
                        self.__master_node.ip, bucket.name))

                kv_gen = BlobGenerator(
                    "loadDgm-%s-" % random_key,
                    "loadDgm-%s-" % random_key,
                    value_size,
                    end=batch_size * 10)

                self.load_bucket(
                    bucket, kv_gen, OPS.CREATE, exp=exp, kv_store=kv_store,
                    flag=flag, only_store_hash=only_store_hash,
                    batch_size=batch_size, pause_secs=pause_secs,
                    timeout_secs=timeout_secs)

                random_key += 1

    def async_update_delete(
            self, op_type, perc=30, expiration=0, kv_store=1):
        """Perform update/delete operation on all buckets. Function don't wait
        operation to finish.
        @param op_type: OPS.CREATE/OPS.UPDATE/OPS.DELETE
        @param perc: percentage of data to be deleted or created
        @param expiration: time for expire items
        @return: task object list
        """
        raise_if(
            OPS.CREATE not in self.__kv_gen,
            XDCRException(
                "Data is not loaded in cluster.Load data before update/delete")
        )
        if op_type == OPS.UPDATE:
            self.__kv_gen[OPS.UPDATE] = BlobGenerator(
                self.__kv_gen[OPS.CREATE].name,
                self.__kv_gen[OPS.CREATE].seed,
                self.__kv_gen[OPS.CREATE].value_size,
                start=0,
                end=int(self.__kv_gen[OPS.CREATE].end * (float)(perc) / 100))
            gen = copy.deepcopy(self.__kv_gen[OPS.UPDATE])
        elif op_type == OPS.DELETE:
            self.__kv_gen[OPS.DELETE] = BlobGenerator(
                self.__kv_gen[OPS.CREATE].name,
                self.__kv_gen[OPS.CREATE].seed,
                self.__kv_gen[OPS.CREATE].value_size,
                start=int((self.__kv_gen[OPS.CREATE].end) * (float)(
                    100 - perc) / 100),
                end=self.__kv_gen[OPS.CREATE].end)
            gen = copy.deepcopy(self.__kv_gen[OPS.DELETE])
        else:
            raise XDCRException("Unknown op_type passed: %s" % op_type)
        tasks = []
        for bucket in self.__buckets:
            tasks.append(
                self.__clusterop.async_load_gen_docs(
                    self.__master_node,
                    bucket.name,
                    gen,
                    bucket.kvs[kv_store],
                    op_type,
                    expiration)
            )
        return tasks

    def update_delete_data(
            self, op_type, perc=30, expiration=0, wait_for_expiration=True):
        """Perform update/delete operation on all buckets. Function wait
        operation to finish.
        @param op_type: OPS.CREATE/OPS.UPDATE/OPS.DELETE
        @param perc: percentage of data to be deleted or created
        @param expiration: time for expire items
        @param wait_for_expiration: True if wait for expire of items after
        update else False
        """
        tasks = self.async_update_delete(op_type, perc, expiration)

        [task.result() for task in tasks]

        if wait_for_expiration and expiration:
            self.__log.info("Waiting for expiration of updated items")
            time.sleep(expiration)

    def run_expiry_pager(self, val=10):
        """Run expiry pager process and set interval to 10 seconds
        and wait for 10 seconds.
        @param val: time in seconds.
        """
        for bucket in self.__buckets:
            ClusterOperationHelper.flushctl_set(
                self.__master_node,
                "exp_pager_stime",
                val,
                bucket)
            self.__log.info("wait for expiry pager to run on all these nodes")
        time.sleep(val)

    def async_create_views(
            self, design_doc_name, views, bucket=BUCKET_NAME.DEFAULT):
        """Create given views on Cluster.
        @param design_doc_name: name of design doc.
        @param views: views objects.
        @param bucket: bucket name.
        @return: task list for CreateViewTask
        """
        tasks = []
        if len(views):
            for view in views:
                task = self.__clusterop.async_create_view(
                    self.__master_node,
                    design_doc_name,
                    view,
                    bucket)
                tasks.append(task)
        else:
            task = self.__clusterop.async_create_view(
                self.__master_node,
                design_doc_name,
                None,
                bucket)
            tasks.append(task)
        return tasks

    def async_compact_view(
            self, design_doc_name, bucket=BUCKET_NAME.DEFAULT,
            with_rebalance=False):
        """Create given views on Cluster.
        @param design_doc_name: name of design doc.
        @param bucket: bucket name.
        @param with_rebalance: True if compaction is called during
        rebalance or False.
        @return: task object
        """
        task = self.__clusterop.async_compact_view(
            self.__master_node,
            design_doc_name,
            bucket,
            with_rebalance)
        return task

    def disable_compaction(self, bucket=BUCKET_NAME.DEFAULT):
        """Disable view compaction
        @param bucket: bucket name.
        """
        new_config = {"viewFragmntThresholdPercentage": None,
                      "dbFragmentThresholdPercentage": None,
                      "dbFragmentThreshold": None,
                      "viewFragmntThreshold": None}
        self.__clusterop.modify_fragmentation_config(
            self.__master_node,
            new_config,
            bucket)

    def async_monitor_view_fragmentation(
            self,
            design_doc_name,
            fragmentation_value,
            bucket=BUCKET_NAME.DEFAULT):
        """Monitor view fragmantation during compation.
        @param design_doc_name: name of design doc.
        @param fragmentation_value: fragmentation threshold to monitor.
        @param bucket: bucket name.
        """
        task = self.__clusterop.async_monitor_view_fragmentation(
            self.__master_node,
            design_doc_name,
            fragmentation_value,
            bucket)
        return task

    def async_query_view(
            self, design_doc_name, view_name, query,
            expected_rows=None, bucket="default", retry_time=2):
        """Perform View Query for given view asynchronously.
        @param design_doc_name: design_doc name.
        @param view_name: view name
        @param query: query expression
        @param expected_rows: number of rows expected returned in query.
        @param bucket: bucket name.
        @param retry_time: retry to perform view query
        @return: task object of ViewQueryTask class
        """
        task = self.__clusterop.async_query_view(
            self.__master_node,
            design_doc_name,
            view_name,
            query,
            expected_rows,
            bucket=bucket,
            retry_time=retry_time)
        return task

    def query_view(
            self, design_doc_name, view_name, query,
            expected_rows=None, bucket="default", retry_time=2, timeout=None):
        """Perform View Query for given view synchronously.
        @param design_doc_name: design_doc name.
        @param view_name: view name
        @param query: query expression
        @param expected_rows: number of rows expected returned in query.
        @param bucket: bucket name.
        @param retry_time: retry to perform view query
        @param timeout: None if wait for query result untill returned
        else pass timeout value.
        """

        task = self.__clusterop.async_query_view(
            self.__master_node,
            design_doc_name,
            view_name,
            query,
            expected_rows,
            bucket=bucket, retry_time=retry_time)
        task.result(timeout)

    def __async_rebalance_out(self, master=False, num_nodes=1):
        """Rebalance-out nodes from Cluster
        @param master: True if rebalance-out master node only.
        @param num_nodes: number of nodes to rebalance-out from cluster.
        """
        raise_if(
            len(self.__nodes) <= num_nodes,
            XDCRException(
                "Cluster needs:{0} nodes for rebalance-out, current: {1}".
                format((num_nodes + 1), len(self.__nodes)))
        )
        if master:
            to_remove_node = [self.__master_node]
        else:
            to_remove_node = self.__nodes[-num_nodes:]
        self.__log.info(
            "Starting rebalance-out nodes:{0} at {1} cluster {2}".format(
                to_remove_node, self.__name, self.__master_node.ip))
        task = self.__clusterop.async_rebalance(
            self.__nodes,
            [],
            to_remove_node)

        [self.__nodes.remove(node) for node in to_remove_node]

        if master:
            self.__master_node = self.__nodes[0]

        return task

    def async_rebalance_out_master(self):
        return self.__async_rebalance_out(master=True)

    def async_rebalance_out(self, num_nodes=1):
        return self.__async_rebalance_out(num_nodes=num_nodes)

    def rebalance_out_master(self):
        task = self.__async_rebalance_out(master=True)
        task.result()

    def rebalance_out(self, num_nodes=1):
        task = self.__async_rebalance_out(num_nodes=num_nodes)
        task.result()

    def async_rebalance_in(self, num_nodes=1):
        """Rebalance-in nodes into Cluster asynchronously
        @param num_nodes: number of nodes to rebalance-in to cluster.
        """
        raise_if(
            len(FloatingServers._serverlist) < num_nodes,
            XDCRException(
                "Number of free nodes: {0} is not preset to add {1} nodes.".
                format(len(FloatingServers._serverlist), num_nodes))
        )
        to_add_node = []
        for _ in range(num_nodes):
            to_add_node.append(FloatingServers._serverlist.pop())
        self.__log.info(
            "Starting rebalance-in nodes:{0} at {1} cluster {2}".format(
                to_add_node, self.__name, self.__master_node.ip))
        task = self.__clusterop.async_rebalance(self.__nodes, to_add_node, [])
        self.__nodes.extend(to_add_node)
        return task

    def rebalance_in(self, num_nodes=1):
        """Rebalance-in nodes
        @param num_nodes: number of nodes to add to cluster.
        """
        task = self.async_rebalance_in(num_nodes)
        task.result()

    def __async_swap_rebalance(self, master=False):
        """Swap-rebalance nodes on Cluster
        @param master: True if swap-rebalance master node else False.
        """
        if master:
            to_remove_node = [self.__master_node]
        else:
            to_remove_node = [self.__nodes[-1]]

        to_add_node = [FloatingServers._serverlist.pop()]

        self.__log.info(
            "Starting swap-rebalance [remove_node:{0}] -> [add_node:{1}] at {2} cluster {3}"
            .format(to_remove_node[0].ip, to_add_node[0].ip, self.__name,
                    self.__master_node.ip))
        task = self.__clusterop.async_rebalance(
            self.__nodes,
            to_add_node,
            to_remove_node)

        [self.__nodes.remove(node) for node in to_remove_node]
        self.__nodes.extend(to_add_node)

        if master:
            self.__master_node = self.__nodes[0]

        return task

    def async_swap_rebalance_master(self):
        return self.__async_swap_rebalance(master=True)

    def async_swap_rebalance(self):
        return self.__async_swap_rebalance()

    def swap_rebalance_master(self):
        """Swap rebalance master node.
        """
        task = self.__async_swap_rebalance(master=True)
        task.result()

    def swap_rebalance(self):
        """Swap rebalance non-master node
        """
        task = self.__async_swap_rebalance()
        task.result()

    def __async_failover(self, master=False, num_nodes=1, graceful=False):
        """Failover nodes from Cluster
        @param master: True if failover master node only.
        @param num_nodes: number of nodes to rebalance-out from cluster.
        @param graceful: True if graceful failover else False.
        """
        raise_if(
            len(self.__nodes) <= 1,
            XDCRException(
                "More than 1 node required in cluster to perform failover")
        )
        if master:
            self.__fail_over_nodes = [self.__master_node]
        else:
            self.__fail_over_nodes = self.__nodes[-num_nodes:]

        self.__log.info(
            "Starting failover for nodes:{0} at {1} cluster {2}".format(
                self.__fail_over_nodes, self.__name, self.__master_node.ip))
        task = self.__clusterop.async_failover(
            self.__nodes,
            self.__fail_over_nodes,
            graceful)

        return task

    def async_failover(self, num_nodes=1, graceful=False):
        return self.__async_failover(num_nodes=num_nodes, graceful=graceful)

    def failover_and_rebalance_master(self, graceful=False, rebalance=True):
        """Failover master node
        @param graceful: True if graceful failover else False
        @param rebalance: True if do rebalance operation after failover.
        """
        task = self.__async_failover(master=True, graceful=graceful)
        task.result()
        if rebalance:
            self.rebalance_failover_nodes()
        self.__master_node = self.__nodes[0]

    def failover_and_rebalance_nodes(self, num_nodes=1, graceful=False,
                                     rebalance=True):
        """ Failover non-master nodes
        @param num_nodes: number of nodes to failover.
        @param graceful: True if graceful failover else False
        @param rebalance: True if do rebalance operation after failover.
        """
        task = self.__async_failover(
            master=False,
            num_nodes=num_nodes,
            graceful=graceful)
        task.result()
        if rebalance:
            self.rebalance_failover_nodes()

    def rebalance_failover_nodes(self):
        self.__clusterop.rebalance(self.__nodes, [], self.__fail_over_nodes)
        [self.__nodes.remove(node) for node in self.__fail_over_nodes]
        self.__fail_over_nodes = []

    def add_back_node(self, recovery_type=None):
        """add-back failed-over node to the cluster.
            @param recovery_type: delta/full
        """
        raise_if(
            len(self.__fail_over_nodes) < 1,
            XDCRException("No failover nodes available to add_back")
        )
        rest = RestConnection(self.__master_node)
        server_nodes = rest.node_statuses()
        for failover_node in self.__fail_over_nodes:
            for server_node in server_nodes:
                if server_node.ip == failover_node.ip:
                    rest.add_back_node(server_node.id)
                    if recovery_type:
                        rest.set_recovery_type(
                            otpNode=server_node.id,
                            recoveryType=recovery_type)
        for node in self.__fail_over_nodes:
            if node not in self.__nodes:
                self.__nodes.append(node)
        self.__clusterop.rebalance(self.__nodes, [], [])
        self.__fail_over_nodes = []

    def warmup_node(self, master=False):
        """Warmup node on cluster
        @param master: True if warmup master-node else False.
        """
        from random import randrange

        if master:
            warmup_node = self.__master_node

        else:
            warmup_node = self.__nodes[
                randrange(
                    1, len(
                        self.__nodes))]
        NodeHelper.do_a_warm_up(warmup_node)
        return warmup_node

    def reboot_one_node(self, test_case, master=False):
        from random import randrange

        if master:
            reboot_node = self.__master_node

        else:
            reboot_node = self.__nodes[
                randrange(
                    1, len(
                        self.__nodes))]
        NodeHelper.reboot_server(reboot_node, test_case)
        return reboot_node

    def restart_couchbase_on_all_nodes(self):
        for node in self.__nodes:
            NodeHelper.do_a_warm_up(node)

        NodeHelper.wait_warmup_completed(self.__nodes)

    def set_xdcr_param(self, param, value):
        """Set Replication parameter on couchbase server:
        @param param: XDCR parameter name.
        @param value: Value of parameter.
        """
        RestConnection(self.__master_node).set_internalSetting(param, value)

        expected_results = {
            "real_userid:source": "internal",
            "real_userid:user": self.__master_node.rest_username,
            "local_cluster_name": "",  # TODO
            "updated_settings": {param: value}
        }

        # In case of ns_server xdcr, no events generate for it.
        if GO_XDCR.ENABLED:
            ValidateAuditEvent.validate_audit_event(
                GO_XDCR_AUDIT_EVENT_ID.DEFAULT_SETT,
                self.get_master_node(),
                expected_results)

    def get_xdcr_stat(self, bucket_name, stat):
        """ Return given XDCR stat for given bucket.
        @param bucket_name: name of bucket.
        @param stat: stat name
        @return: value of stat
        """
        return int(RestConnection(self.__master_node).fetch_bucket_stats(
            bucket_name)['op']['samples'][stat][-1])

    def wait_for_xdcr_stat(self, bucket, stat, comparison, value):
        """Wait for given stat for a bucket to given condition.
        @param bucket: bucket name
        @param stat: stat name
        @param comparison: comparison operatior e.g. "==", "<"
        @param value: value to compare.
        """
        task = self.__clusterop.async_wait_for_xdcr_stat(
            self.__nodes,
            bucket,
            '',
            stat,
            comparison,
            value)
        task.result()

    def add_remote_cluster(self, dest_cluster, name, encryption=False):
        """Create remote cluster reference or add remote cluster for xdcr.
        @param dest_cluster: Destination cb cluster object.
        @param name: name of remote cluster reference
        @param encryption: True if encryption for xdcr else False
        """
        remote_cluster = XDCRRemoteClusterRef(
            self,
            dest_cluster,
            name,
            encryption
        )
        remote_cluster.add()
        self.__remote_clusters.append(remote_cluster)

    # add params to what to modify
    def modify_remote_cluster(self, remote_cluster_name, require_encryption):
        """Modify Remote Cluster Reference Settings for given name.
        @param remote_cluster_name: name of the remote cluster to change.
        @param require_encryption: Value of encryption if need to change True/False.
        """
        for remote_cluster in self.__remote_clusters:
            if remote_cluster_name == remote_cluster.get_name():
                remote_cluster. modify(require_encryption)
                break
        else:
            raise XDCRException(
                "No such remote cluster found with name: {0}".format(
                    remote_cluster_name))

    def wait_for_flusher_empty(self, timeout=60):
        """Wait for disk queue to completely flush.
        """
        tasks = []
        for node in self.__nodes:
            for bucket in self.__buckets:
                tasks.append(
                    self.__clusterop.async_wait_for_stats(
                        [node],
                        bucket,
                        '',
                        'ep_queue_size',
                        '==',
                        0))
        for task in tasks:
            task.result(timeout)

    def verify_items_count(self, timeout=180):
        """Wait for actual bucket items count reach to the count on bucket kv_store.
        """
        ret_value = True

        # Check active, curr key count
        stats_tasks = []
        for bucket in self.__buckets:
            items = sum([len(kv_store) for kv_store in bucket.kvs.values()])
            for stat in ['curr_items', 'vb_active_curr_items']:
                stats_tasks.append(self.__clusterop.async_wait_for_stats(
                    self.__nodes, bucket, '',
                    stat, '==', items))
        try:
            for task in stats_tasks:
                task.result(timeout)
        except TimeoutError:
            self.__log.error(
                "ERROR: Timed-out waiting for active item count to match")
            ret_value = False

        # Check replica key count
        stats_tasks = []
        for bucket in self.__buckets:
            items = sum([len(kv_store) for kv_store in bucket.kvs.values()])
            if bucket.numReplicas >= 1 and len(self.__nodes) > 1:
                stats_tasks.append(self.__clusterop.async_wait_for_stats(
                    self.__nodes, bucket, '',
                    'vb_replica_curr_items', '==', items * bucket.numReplicas))
        try:
            for task in stats_tasks:
                task.result(timeout)
        except TimeoutError:
            self.run_cbvdiff()
            self.__log.error(
                "ERROR: Timed-out waiting for replica item count to match")
            ret_value = False
        return ret_value

    def run_cbvdiff(self):
        """ Run cbvdiff, a tool that compares active and replica vbucket keys
        Eg. ./cbvdiff -b standardbucket  172.23.105.44:11210,172.23.105.45:11210
             VBucket 232: active count 59476 != 59477 replica count
        """
        node_str = ""
        for node in self.__nodes:
            if node_str:
                node_str += ','
            node_str += node.ip + ':11210'
        ssh_conn = RemoteMachineShellConnection(self.__master_node)
        for bucket in self.__buckets:
            self.__log.info(
                "Executing cbvdiff for bucket {0}".format(
                    bucket.name))
            ssh_conn.execute_cbvdiff(bucket, node_str)
        ssh_conn.disconnect()

    def verify_data(self, kv_store=1, timeout=None,
                    max_verify=None, only_store_hash=True, batch_size=1000):
        """Verify data of all the buckets. Function read data from cb server and
        compare it with bucket's kv_store.
        @param kv_store: Index of kv_store where item values are stored on
        bucket.
        @param timeout: None if wait indefinitely else give timeout value.
        @param max_verify: number of items to verify. None if verify all items
        on bucket.
        @param only_store_hash: True if verify hash of items else False.
        @param batch_size: batch size to read items from server.
        """
        self.__data_verified = False
        tasks = []
        for bucket in self.__buckets:
            tasks.append(
                self.__clusterop.async_verify_data(
                    self.__master_node,
                    bucket,
                    bucket.kvs[kv_store],
                    max_verify,
                    only_store_hash,
                    batch_size,
                    timeout_sec=60))
        for task in tasks:
            task.result(timeout)

        self.__data_verified = True

    def wait_for_outbound_mutations(self, timeout=180):
        """Wait for Outbound mutations to reach 0.
        @return: True if mutations reached to 0 else False.
        """
        self.__log.info(
            "Waiting for Outbound mutation to be zero on cluster node: %s" %
            self.__master_node.ip)
        curr_time = time.time()
        end_time = curr_time + timeout
        rest = RestConnection(self.__master_node)
        while curr_time < end_time:
            found = 0
            for bucket in self.__buckets:
                try:
                    mutations = int(rest.get_xdc_queue_size(bucket.name))
                except KeyError:
                    # Sometimes replication_changes_left are not found in the stat.
                    # So setting up -1 if not found sometimes.
                    mutations = -1
                self.__log.info(
                    "Current Outbound mutations on cluster node: %s for bucket %s is %s" %
                    (self.__master_node.ip, bucket.name, mutations))
                if mutations == 0:
                    found = found + 1
            if found == len(self.__buckets):
                break
            time.sleep(10)
            end_time = end_time - 10
        else:
            # MB-9707: Updating this code from fail to warning to avoid test
            # to abort, as per this
            # bug, this particular stat i.e. replication_changes_left is buggy.
            self.__log.error(
                "Timeout occurs while waiting for mutations to be replicated")
            return False
        return True

    def pause_all_replications(self):
        for remote_cluster_ref in self.__remote_clusters:
            remote_cluster_ref.pause_all_replications()

    def resume_all_replications(self):
        for remote_cluster_ref in self.__remote_clusters:
            remote_cluster_ref.resume_all_replications()

    def enable_time_sync(self, enable):
        """
        @param enable: True if time_sync needs to enabled else False
        """
        # TODO call rest api
        pass

    def set_wall_clock_time(self, date_str):
        for node in self.__nodes:
            NodeHelper.set_wall_clock_time(node, date_str)


class Utility:

    @staticmethod
    def make_default_views(prefix, num_views, is_dev_ddoc=False):
        """Create default views for testing.
        @param prefix: prefix for view name
        @param num_views: number of views to create
        @param is_dev_ddoc: True if Development View else False
        """
        default_map_func = "function (doc) {\n  emit(doc._id, doc);\n}"
        default_view_name = (prefix, "default_view")[prefix is None]
        return [View(default_view_name + str(i), default_map_func,
                     None, is_dev_ddoc) for i in xrange(num_views)]

    @staticmethod
    def get_rc_name(src_cluster_name, dest_cluster_name):
        return "remote_cluster_" + src_cluster_name + "-" + dest_cluster_name


class XDCRNewBaseTest(unittest.TestCase):

    def setUp(self):
        unittest.TestCase.setUp(self)
        self._input = TestInputSingleton.input
        self.log = logger.Logger.get_logger()
        self.__init_logger()
        self.__cb_clusters = []
        self.__cluster_op = Cluster()
        self.__init_parameters()
        self.log.info(
            "==== XDCRNewbasetests setup is started for test #{0} {1} ===="
            .format(self.__case_number, self._testMethodName))

        self.__setup_for_test()

        self.log.info(
            "==== XDCRNewbasetests setup is finished for test #{0} {1} ===="
            .format(self.__case_number, self._testMethodName))

    def __is_test_failed(self):
        return (hasattr(self, '_resultForDoCleanups')
                and len(self._resultForDoCleanups.failures
                        or self._resultForDoCleanups.errors)) \
            or (hasattr(self, '_exc_info')
                and self._exc_info()[1] is not None)

    def __is_cleanup_needed(self):
        return self.__is_test_failed() and (str(self.__class__).find(
            'upgradeXDCR') != -1 or self._input.param("stop-on-failure", False)
        )

    def __is_cluster_run(self):
        return len(set([server.ip for server in self._input.servers])) == 1

    def tearDown(self):
        """Clusters cleanup"""

        # collect logs before tearing down clusters
        if self._input.param("get-cbcollect-info", False) and \
                self.__is_test_failed():
            for cb_cluster in self.__cb_clusters:
                self.log.info(
                    "Collecting logs @ {0}".format(
                        cb_cluster.get_name()))
                cb_cluster.collect_logs(self.__is_cluster_run())
        try:
            if self.__is_cleanup_needed():
                self.log.warn("CLEANUP WAS SKIPPED")
                return
            self.log.info(
                "====  XDCRNewbasetests cleanup is started for test #{0} {1} ===="
                .format(self.__case_number, self._testMethodName))
            for cb_cluster in self.__cb_clusters:
                cb_cluster.cleanup_cluster(self)
            self.log.info(
                "====  XDCRNewbasetests cleanup is finished for test #{0} {1} ==="
                .format(self.__case_number, self._testMethodName))
        finally:
            self.__cluster_op.shutdown(force=True)
            unittest.TestCase.tearDown(self)
            # Remove once MB-12950 is fixed.
            RestConnection.replications = []

    def __init_logger(self):
        if self._input.param("log_level", None):
            self.log.setLevel(level=0)
            for hd in self.log.handlers:
                if str(hd.__class__).find('FileHandler') != -1:
                    hd.setLevel(level=logging.DEBUG)
                else:
                    hd.setLevel(
                        level=getattr(
                            logging,
                            self._input.param(
                                "log_level",
                                None)))

    def __setup_for_test(self):
        use_hostanames = self._input.param("use_hostnames", False)
        counter = 1
        for _, nodes in self._input.clusters.iteritems():
            cluster_nodes = copy.deepcopy(nodes)
            if len(self.__cb_clusters) == int(self.__chain_length):
                break
            self.__cb_clusters.append(
                CouchbaseCluster(
                    "C%s" % counter, cluster_nodes,
                    self.log, use_hostanames))
            counter += 1

        self.__cleanup_previous()
        self.__init_clusters()
        self.__set_free_servers()
        self.__create_buckets()
        if self._checkpoint_interval != 1800:
            for cluster in self.__cb_clusters:
                cluster.set_global_checkpt_interval(self._checkpoint_interval)

    def __init_parameters(self):
        self.__case_number = self._input.param("case_number", 0)
        self.__topology = self._input.param("ctopology", TOPOLOGY.CHAIN)
        # complex topology tests (> 2 clusters must specify chain_length >2)
        self.__chain_length = self._input.param("chain_length", 2)
        self.__rdirection = self._input.param(
            "rdirection",
            REPLICATION_DIRECTION.UNIDIRECTION)
        self.__demand_encryption = self._input.param(
            "demand_encryption",
            False)
        self.__rep_type = self._input.param(
            "replication_type",
            REPLICATION_PROTOCOL.CAPI)
        self.__num_sasl_buckets = self._input.param("sasl_buckets", 0)
        self.__num_stand_buckets = self._input.param("standard_buckets", 0)

        self.__num_replicas = self._input.param("replicas", 1)
        self.__eviction_policy = self._input.param(
            "eviction_policy",
            'valueOnly')
        self.__mixed_priority = self._input.param("mixed_priority", None)

        self.__lww = self._input.param("lww", 0)

        # Public init parameters - Used in other tests too.
        # Move above private to this section if needed in future, but
        # Ensure to change other tests too.
        self._create_default_bucket = self._input.param(
            "default_bucket",
            True)
        self._num_items = self._input.param("items", 1000)
        self._value_size = self._input.param("value_size", 256)
        self._poll_timeout = self._input.param("poll_timeout", 120)
        self._perc_upd = self._input.param("upd", 30)
        self._perc_del = self._input.param("del", 30)
        self._upd_clusters = self._input.param("update", [])
        if self._upd_clusters:
            self._upd_clusters = self._upd_clusters.split("-")
        self._del_clusters = self._input.param("delete", [])
        if self._del_clusters:
            self._del_clusters = self._del_clusters.split('-')
        self._expires = self._input.param("expires", 0)
        self._wait_for_expiration = self._input.param(
            "wait_for_expiration",
            True)
        self._warmup = self._input.param("warm", "").split('-')
        self._rebalance = self._input.param("rebalance", "").split('-')
        self._failover = self._input.param("failover", "").split('-')
        self._wait_timeout = self._input.param("timeout", 60)
        self._disable_compaction = self._input.param(
            "disable_compaction",
            "").split('-')
        # Default value needs to be changed to 60s after MB-13233 is resolved
        self._checkpoint_interval = self._input.param(
            "checkpoint_interval",
            1800)
        CHECK_AUDIT_EVENT.CHECK = self._input.param("check-audit-event", 0)
        GO_XDCR.ENABLED = self._input.param("enable_goxdcr", False)

    def __cleanup_previous(self):
        for cluster in self.__cb_clusters:
            cluster.cleanup_cluster(
                self,
                from_rest=True,
                cluster_shutdown=False)
        # Remove once MB-12950 is fixed.
        RestConnection.replications = []

    def __init_clusters(self):
        self.log.info("Initializing all clusters...")
        disabled_consistent_view = self._input.param(
            "disabled_consistent_view",
            None)
        for cluster in self.__cb_clusters:
            cluster.init_cluster(disabled_consistent_view)

    def __set_free_servers(self):
        total_servers = self._input.servers
        cluster_nodes = []
        for _, nodes in self._input.clusters.iteritems():
            cluster_nodes.extend(nodes)

        FloatingServers._serverlist = [
            server for server in total_servers if server not in cluster_nodes]

    def get_cb_cluster_by_name(self, name):
        """Return couchbase cluster object for given name.
        @return: CouchbaseCluster object
        """
        for cb_cluster in self.__cb_clusters:
            if cb_cluster.get_name() == name:
                return cb_cluster
        raise XDCRException("Couchbase Cluster with name: %s not exist" % name)

    def get_num_cb_cluster(self):
        """Return number of couchbase clusters for tests.
        """
        return len(self.__cb_clusters)

    def get_cb_clusters(self):
        return self.__cb_clusters

    def __calculate_bucket_size(self, cluster_quota, num_buckets):
        dgm_run = self._input.param("dgm_run", 0)
        if dgm_run:
            # buckets cannot be created if size<100MB
            bucket_size = 100
        else:
            bucket_size = int(float(cluster_quota) / float(num_buckets))
        return bucket_size

    def __create_buckets(self):
        # if mixed priority is set by user, set high priority for sasl and
        # standard buckets
        if self.__mixed_priority:
            bucket_priority = 'high'
        else:
            bucket_priority = None
        num_buckets = self.__num_sasl_buckets + \
            self.__num_stand_buckets + int(self._create_default_bucket)

        for cb_cluster in self.__cb_clusters:
            total_quota = cb_cluster.get_mem_quota()
            bucket_size = self.__calculate_bucket_size(
                total_quota,
                num_buckets)

            if self._create_default_bucket:
                cb_cluster.create_default_bucket(
                    bucket_size,
                    self.__num_replicas,
                    eviction_policy=self.__eviction_policy,
                    bucket_priority=bucket_priority)

            cb_cluster.create_sasl_buckets(
                bucket_size, num_buckets=self.__num_sasl_buckets,
                num_replicas=self.__num_replicas,
                eviction_policy=self.__eviction_policy,
                bucket_priority=bucket_priority)

            cb_cluster.create_standard_buckets(
                bucket_size, num_buckets=self.__num_stand_buckets,
                num_replicas=self.__num_replicas,
                eviction_policy=self.__eviction_policy,
                bucket_priority=bucket_priority)

    def create_buckets_on_cluster(self, cluster_name):
        # if mixed priority is set by user, set high priority for sasl and
        # standard buckets
        if self.__mixed_priority:
            bucket_priority = 'high'
        else:
            bucket_priority = None
        num_buckets = self.__num_sasl_buckets + \
            self.__num_stand_buckets + int(self._create_default_bucket)

        cb_cluster = self.get_cb_cluster_by_name(cluster_name)
        total_quota = cb_cluster.get_mem_quota()
        bucket_size = self.__calculate_bucket_size(
            total_quota,
            num_buckets)

        if self._create_default_bucket:
            cb_cluster.create_default_bucket(
                bucket_size,
                self.__num_replicas,
                eviction_policy=self.__eviction_policy,
                bucket_priority=bucket_priority)

        cb_cluster.create_sasl_buckets(
            bucket_size, num_buckets=self.__num_sasl_buckets,
            num_replicas=self.__num_replicas,
            eviction_policy=self.__eviction_policy,
            bucket_priority=bucket_priority)

        cb_cluster.create_standard_buckets(
            bucket_size, num_buckets=self.__num_stand_buckets,
            num_replicas=self.__num_replicas,
            eviction_policy=self.__eviction_policy,
            bucket_priority=bucket_priority)

    def __set_topology_chain(self):
        """Will Setup Remote Cluster Chain Topology i.e. A -> B -> C
        """
        for i, cb_cluster in enumerate(self.__cb_clusters):
            if i >= len(self.__cb_clusters) - 1:
                break
            cb_cluster.add_remote_cluster(
                self.__cb_clusters[i + 1],
                Utility.get_rc_name(
                    cb_cluster.get_name(),
                    self.__cb_clusters[i + 1].get_name()),
                self.__demand_encryption
            )
            if self.__rdirection == REPLICATION_DIRECTION.BIDIRECTION:
                self.__cb_clusters[i + 1].add_remote_cluster(
                    cb_cluster,
                    Utility.get_rc_name(
                        self.__cb_clusters[i + 1].get_name(),
                        cb_cluster.get_name()),
                    self.__demand_encryption
                )

    def __set_topology_star(self):
        """Will Setup Remote Cluster Star Topology i.e. A-> B, A-> C, A-> D
        """
        hub = self.__cb_clusters[0]
        for cb_cluster in self.__cb_clusters[1:]:
            hub.add_remote_cluster(
                cb_cluster,
                Utility.get_rc_name(hub.get_name(), cb_cluster.get_name()),
                self.__demand_encryption
            )
            if self.__rdirection == REPLICATION_DIRECTION.BIDIRECTION:
                cb_cluster.add_remote_cluster(
                    hub,
                    Utility.get_rc_name(cb_cluster.get_name(), hub.get_name()),
                    self.__demand_encryption
                )

    def __set_topology_ring(self):
        """
        Will Setup Remote Cluster Ring Topology i.e. A -> B -> C -> A
        """
        self.__set_topology_chain()
        self.__cb_clusters[-1].add_remote_cluster(
            self.__cb_clusters[0],
            Utility.get_rc_name(
                self.__cb_clusters[-1].get_name(),
                self.__cb_clusters[0].get_name()),
            self.__demand_encryption
        )
        if self.__rdirection == REPLICATION_DIRECTION.BIDIRECTION:
            self.__cb_clusters[0].add_remote_cluster(
                self.__cb_clusters[-1],
                Utility.get_rc_name(
                    self.__cb_clusters[0].get_name(),
                    self.__cb_clusters[-1].get_name()),
                self.__demand_encryption
            )

    def set_xdcr_topology(self):
        """Setup xdcr topology as per ctopology test parameter.
        """
        if self.__topology == TOPOLOGY.CHAIN:
            self.__set_topology_chain()
        elif self.__topology == TOPOLOGY.STAR:
            self.__set_topology_star()
        elif self.__topology == TOPOLOGY.RING:
            self.__set_topology_ring()
        elif self._input.param(TOPOLOGY.HYBRID, 0):
            self.set_hybrid_topology()
        else:
            raise XDCRException(
                'Unknown topology set: {0}'.format(
                    self.__topology))

    def __parse_topology_param(self):
        tokens = re.split(r'(>|<>|<|\s)', self.__topology)
        return tokens

    def set_hybrid_topology(self):
        """Set user defined topology
        Hybrid Topology Notations:
        '> or <' for Unidirection replication between clusters
        '<>' for Bi-direction replication between clusters
        Test Input:  ctopology="C1>C2<>C3>C4<>C1"
        """
        tokens = self.__parse_topology_param()
        counter = 0
        while counter < len(tokens) - 1:
            src_cluster = self.get_cb_cluster_by_name(tokens[counter])
            dest_cluster = self.get_cb_cluster_by_name(tokens[counter + 2])
            if ">" in tokens[counter + 1]:
                src_cluster.add_remote_cluster(
                    dest_cluster,
                    Utility.get_rc_name(
                        src_cluster.get_name(),
                        dest_cluster.get_name()),
                    self.__demand_encryption
                )
            if "<" in tokens[counter + 1]:
                dest_cluster.add_remote_cluster(
                    src_cluster,
                    Utility.get_rc_name(
                        dest_cluster.get_name(), src_cluster.get_name()),
                    self.__demand_encryption
                )
            counter += 2

    def __load_chain(self):
        for i, cluster in enumerate(self.__cb_clusters):
            if self.__rdirection == REPLICATION_DIRECTION.BIDIRECTION:
                if i > len(self.__cb_clusters) - 1:
                    break
            else:
                if i >= len(self.__cb_clusters) - 1:
                    break
            cluster.load_all_buckets(self._num_items, self._value_size)

    def __load_star(self):
        hub = self.__cb_clusters[0]
        hub.load_all_buckets(self._num_items, self._value_size)

    def __load_ring(self):
        self.__load_chain()
        cluster = self.__cb_clusters[-1]
        cluster.load_all_buckets(self._num_items, self._value_size)

    def load_data_topology(self):
        """load data as per ctopology test parameter
        """
        if self.__topology == TOPOLOGY.CHAIN:
            self.__load_chain()
        elif self.__topology == TOPOLOGY.STAR:
            self.__load_star()
        elif self.__topology == TOPOLOGY.RING:
            self.__load_ring()
        elif self._input.param(TOPOLOGY.HYBRID, 0):
            self.__load_star()
        else:
            raise XDCRException(
                'Unknown topology set: {0}'.format(
                    self.__topology))

    def perform_update_delete(self):
        # UPDATES
        for doc_ops_cluster in self._upd_clusters:
            cb_cluster = self.get_cb_cluster_by_name(doc_ops_cluster)
            self.log.info("Updating keys @ {0}".format(cb_cluster.get_name()))
            cb_cluster.update_delete_data(
                OPS.UPDATE,
                perc=self._perc_upd,
                expiration=self._expires,
                wait_for_expiration=self._wait_for_expiration)

        # DELETES
        for doc_ops_cluster in self._del_clusters:
            cb_cluster = self.get_cb_cluster_by_name(doc_ops_cluster)
            self.log.info("Deleting keys @ {0}".format(cb_cluster.get_name()))
            cb_cluster.update_delete_data(OPS.DELETE, perc=self._perc_del)

    def async_perform_update_delete(self):
        tasks = []
        # UPDATES
        for doc_ops_cluster in self._upd_clusters:
            cb_cluster = self.get_cb_cluster_by_name(doc_ops_cluster)
            self.log.info("Updating keys @ {0}".format(cb_cluster.get_name()))
            tasks.extend(cb_cluster.async_update_delete(
                OPS.UPDATE,
                perc=self._perc_upd,
                expiration=self._expires))

        # DELETES
        for doc_ops_cluster in self._del_clusters:
            cb_cluster = self.get_cb_cluster_by_name(doc_ops_cluster)
            self.log.info("Deleting keys @ {0}".format(cb_cluster.get_name()))
            tasks.extend(
                cb_cluster.async_update_delete(
                    OPS.DELETE,
                    perc=self._perc_del))

        [task.result() for task in tasks]

        if self._wait_for_expiration and self._expires:
            self.sleep(
                self._expires,
                "Waiting for expiration of updated items")

    def setup_all_replications(self):
        """Setup replication between buckets on remote clusters
        based on the xdcr topology created.
        """
        for cb_cluster in self.__cb_clusters:
            for remote_cluster in cb_cluster.get_remote_clusters():
                for src_bucket in remote_cluster.get_src_cluster().get_buckets():
                    remote_cluster.create_replication(
                        src_bucket,
                        rep_type=self.__rep_type,
                        toBucket=remote_cluster.get_dest_cluster().get_bucket_by_name(
                            src_bucket.name))
                remote_cluster.start_all_replications()

    def _resetup_replication_for_recreate_buckets(self, cluster_name):
        for cb_cluster in self.__cb_clusters:
            for remote_cluster_ref in cb_cluster.get_remote_clusters():
                if remote_cluster_ref.get_src_cluster().get_name(
                ) != cluster_name and remote_cluster_ref.get_dest_cluster().get_name() != cluster_name:
                    continue
                remote_cluster_ref.clear_all_replications()
                for src_bucket in remote_cluster_ref.get_src_cluster().get_buckets():
                    remote_cluster_ref.create_replication(
                        src_bucket,
                        rep_type=self.__rep_type,
                        toBucket=remote_cluster_ref.get_dest_cluster().get_bucket_by_name(
                            src_bucket.name))

    def setup_xdcr(self):
        self.set_xdcr_topology()
        self.setup_all_replications()

    def setup_xdcr_and_load(self):
        self.setup_xdcr()
        self.load_data_topology()
        self.sleep(60)

    def load_and_setup_xdcr(self):
        """Initial xdcr
        first load then create xdcr
        """
        self.load_data_topology()
        self.setup_xdcr()

    def verify_rev_ids(self, xdcr_replications, kv_store=1):
        """Verify RevId (sequence number, cas, flags value) for each item on
        every source and destination bucket.
        @param xdcr_replications: list of XDCRReplication objects.
        @param kv_store: Index of bucket kv_store to compare.
        """
        error_count = 0
        tasks = []
        for repl in xdcr_replications:
            self.log.info("Verifying RevIds for {0} -> {1}, bucket {2}".format(
                repl.get_src_cluster(),
                repl.get_dest_cluster(),
                repl.get_src_bucket))
            task_info = self.__cluster_op.async_verify_revid(
                repl.get_src_cluster().get_master_node(),
                repl.get_dest_cluster().get_master_node(),
                repl.get_src_bucket(),
                repl.get_src_bucket().kvs[kv_store])
            tasks.append(task_info)
        for task in tasks:
            task.result()
            error_count += task.err_count
            if task.err_count:
                for ip, values in task.keys_not_found.iteritems():
                    if values:
                        self.log.error("%s keys not found on %s, "
                                       "printing first 100 keys: %s" % (len(values),
                                                                        ip, values[:100]))
        return error_count

    def __merge_keys(
            self, kv_src_bucket, kv_dest_bucket, kvs_num=1, filter_exp=None):
        valid_keys_src, deleted_keys_src = kv_src_bucket[
            kvs_num].key_set()
        valid_keys_dest, deleted_keys_dest = kv_dest_bucket[
            kvs_num].key_set()

        if filter_exp:
            filtered_src_keys = filter(
                lambda key: re.search(str(filter_exp), key) is not None,
                valid_keys_src
            )
            valid_keys_src = filtered_src_keys
            self.log.info(
                "{0} keys matched the filter expression {1}".format(
                    len(valid_keys_src),
                    filter_exp))

        for key in valid_keys_src:
            # replace/add the values for each key in src kvs
            if key not in deleted_keys_dest:
                partition1 = kv_src_bucket[kvs_num].acquire_partition(key)
                partition2 = kv_dest_bucket[kvs_num].acquire_partition(key)
                # In case of lww, if source's key timestamp is lower than
                # destination than no need to set.
                if self.__lww and partition1.get_timestamp(
                        key) < partition2.get_timestamp(key):
                    continue
                key_add = partition1.get_key(key)
                partition2.set(
                    key,
                    key_add["value"],
                    key_add["expires"],
                    key_add["flag"])
                kv_src_bucket[kvs_num].release_partition(key)
                kv_dest_bucket[kvs_num].release_partition(key)

        for key in deleted_keys_src:
            if key not in deleted_keys_dest:
                partition1 = kv_src_bucket[kvs_num].acquire_partition(key)
                partition2 = kv_dest_bucket[kvs_num].acquire_partition(key)
                # In case of lww, if source's key timestamp is lower than
                # destination than no need to delete.
                if self.__lww and partition1.get_timestamp(
                        key) < partition2.get_timestamp(key):
                    continue
                partition2.delete(key)
                kv_src_bucket[kvs_num].release_partition(key)
                kv_dest_bucket[kvs_num].release_partition(key)

    def __merge_all_buckets(self):
        """Merge bucket data between source and destination bucket
        for data verification. This method should be called after replication started.
        """
        # In case of ring topology first merging keys from last and first
        # cluster e.g. A -> B -> C -> A then merging C-> A then A-> B and B-> C
        # and C->A (to merge keys from B ->C).
        # TODO need to be tested for Hybrid Topology
        if self.__topology == TOPOLOGY.RING and len(
                self.__cb_clusters) > 2:
            for remote_cluster_ref in self.__cb_clusters[-1].get_remote_clusters():
                for repl in remote_cluster_ref.get_replications():
                    self.__merge_keys(
                        repl.get_src_bucket().kvs,
                        repl.get_dest_bucket().kvs,
                        kvs_num=1
                    )

        for cb_cluster in self.__cb_clusters:
            for remote_cluster_ref in cb_cluster.get_remote_clusters():
                for repl in remote_cluster_ref.get_replications():
                    self.log.info(
                        "Merging keys for replication {0}".format(repl))
                    self.__merge_keys(
                        repl.get_src_bucket().kvs,
                        repl.get_dest_bucket().kvs,
                        kvs_num=1,
                        filter_exp=repl.get_filter_exp()
                    )

    # Interface for other tests.
    def merge_all_buckets(self):
        self.__merge_all_buckets()

    def sleep(self, timeout=1, message=""):
        self.log.info("sleep for {0} secs. {1} ...".format(timeout, message))
        time.sleep(timeout)

    def verify_results(self):
        """Verify data between each couchbase and remote clusters.
        Run below steps for each source and destination cluster..
            1. Run expiry pager.
            2. Wait for disk queue size to 0 on each nodes.
            3. Wait for Outbound mutations to 0.
            4. Wait for Items counts equal to kv_store size of buckets.
            5. Verify items value on each bucket.
            6. Verify Revision id of each item.
        """
        self.__merge_all_buckets()
        for cb_cluster in self.__cb_clusters:
            for remote_cluster_ref in cb_cluster.get_remote_clusters():
                try:
                    verification_completed = False
                    src_cluster = remote_cluster_ref.get_src_cluster()
                    dest_cluster = remote_cluster_ref.get_dest_cluster()
                    src_cluster.run_expiry_pager()
                    dest_cluster.run_expiry_pager()

                    src_cluster.wait_for_flusher_empty()
                    dest_cluster.wait_for_flusher_empty()

                    src_cluster.wait_for_outbound_mutations()
                    dest_cluster.wait_for_outbound_mutations()

                    src_key_count_ok = src_cluster.verify_items_count()
                    dest_key_count_ok = dest_cluster.verify_items_count()

                    src_cluster.verify_data()
                    dest_cluster.verify_data()
                    verification_completed = True
                except Exception as e:
                    self.log.error(e)
                finally:
                    self.verify_rev_ids(remote_cluster_ref.get_replications())
                    if not verification_completed:
                        self.fail(
                            "Verification failed for remote-cluster: {0}".
                            format(remote_cluster_ref))
                    if not (src_key_count_ok and dest_key_count_ok):
                        self.fail("Active or replica key count is incorrect")
