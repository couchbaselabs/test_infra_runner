import json
import logging
import random
import datetime
import os, sys
import socket

from TestInput import TestInputSingleton
from collection.collections_rest_client import CollectionsRest
from couchbase_helper.tuq_helper import N1QLHelper
from lib.couchbase_helper.documentgenerator import BlobGenerator, SDKDataLoader
from lib.couchbase_helper.stats_tools import StatsCommon
from lib.couchbase_helper.tuq_generators import JsonGenerator
from lib.membase.api.rest_client import RestConnection
from lib.membase.helper.cluster_helper import ClusterOperationHelper
from lib.remote.remote_util import RemoteMachineShellConnection
from pytests.basetestcase import BaseTestCase
from testconstants import INDEX_QUOTA, MIN_KV_QUOTA, EVENTING_QUOTA
from pytests.query_tests_helper import QueryHelperTests

log = logging.getLogger()


class EventingBaseTest(QueryHelperTests):
    panic_count = 0
    ## added to ignore error
    def suite_setUp(self):
        pass

    def suite_tearDown(self):
        pass


    def setUp(self):
        if self._testMethodDoc:
            log.info("\n\nStarting Test: %s \n%s" % (self._testMethodName, self._testMethodDoc))
        else:
            log.info("\n\nStarting Test: %s" % (self._testMethodName))
        self.input = TestInputSingleton.input
        self.is_upgrade_test = self.input.param("is_upgrade_test", False)
        if str(self.__class__).find('newupgradetests') != -1 or \
                    str(self.__class__).find('upgradeXDCR') != -1 or \
                    str(self.__class__).find('Upgrade_EpTests') != -1 or \
                    str(self.__class__).find('UpgradeTests')  != -1 or \
                    str(self.__class__).find('MultiNodesUpgradeTests') != -1:
            self.is_upgrade_test = True
        if not self.is_upgrade_test:
            self.input.test_params.update({"default_bucket": False})
        super(EventingBaseTest, self).setUp()
        self.master = self.servers[0]
        self.server = self.master
        if not self.is_upgrade_test:
            self.restServer = self.get_nodes_from_services_map(service_type="eventing")
            if self.restServer:
                self.rest = RestConnection(self.restServer)
                self.rest.set_indexer_storage_mode()
                self.log.info(
                    "Setting the min possible memory quota so that adding mode nodes to the \
                     cluster wouldn't be a problem.")
                self.rest.set_service_memoryQuota(service='memoryQuota', memoryQuota=330)
                self.rest.set_service_memoryQuota(service='indexMemoryQuota',
                                                  memoryQuota=INDEX_QUOTA)
                self.rest.set_service_memoryQuota(service='eventingMemoryQuota',
                                                  memoryQuota=EVENTING_QUOTA)
        self.src_bucket_name = self.input.param('src_bucket_name', 'src_bucket')
        self.eventing_log_level = self.input.param('eventing_log_level', 'INFO')
        self.dst_bucket_name = self.input.param('dst_bucket_name', 'dst_bucket')
        self.dst_bucket_name1 = self.input.param('dst_bucket_name1', 'dst_bucket1')
        self.metadata_bucket_name = self.input.param('metadata_bucket_name', 'metadata')
        self.create_functions_buckets = self.input.param('create_functions_buckets', True)
        self.docs_per_day = self.input.param("doc-per-day", 1)
        self.use_memory_manager = self.input.param('use_memory_manager', True)
        self.print_eventing_handler_code_in_logs = self.input.param('print_eventing_handler_code_in_logs', True)
        random.seed(datetime.datetime.now())
        function_name = "Function_{0}_{1}".format(random.randint(1, 1000000000), self._testMethodName)
        # See MB-28447, From now function name can only be max of 100 chars
        self.function_name = function_name[0:90]
        self.timer_storage_chan_size = self.input.param('timer_storage_chan_size', 10000)
        self.dcp_gen_chan_size = self.input.param('dcp_gen_chan_size', 10000)
        self.is_sbm=self.input.param('source_bucket_mutation',False)
        if not self.is_upgrade_test:
            self.n1ql_node = self.get_nodes_from_services_map(service_type="n1ql")
            self.n1ql_helper = N1QLHelper(shell=self.shell, max_verify=self.max_verify,
                                      buckets=self.buckets,
                                      item_flag=self.item_flag, n1ql_port=self.n1ql_port,
                                      full_docs_list=self.full_docs_list, log=self.log,
                                      input=self.input,
                                      master=self.master, use_rest=True)
        self.pause_resume = self.input.param('pause_resume', False)
        self.pause_resume_number = self.input.param('pause_resume_number', 1)
        self.is_curl=self.input.param('curl',False)
        self.hostname = self.input.param('host', 'https://postman-echo.com/')
        self.curl_username = self.input.param('curl_user', None)
        self.curl_password = self.input.param('curl_password', None)
        self.auth_type = self.input.param('auth_type', 'no-auth')
        self.bearer_key=self.input.param('bearer_key',None)
        self.url = self.input.param('path', None)
        self.cookies = self.input.param('cookies',False)
        self.bearer_key = self.input.param('bearer_key','')
        if self.hostname=='local' and not self.is_upgrade_test:
            ##self.insall_dependencies()
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            self.hostname= "http://"+ip+":1080/"
            self.log.info("local ip address:{}".format(self.hostname))
            self.setup_curl()
        self.skip_metabucket_check=False
        self.cancel_timer=self.input.param('cancel_timer', False)
        self.is_expired=self.input.param('is_expired', False)
        self.print_app_log=self.input.param('print_app_log', False)
        self.print_go_routine=self.input.param('print_go_routine', False)
        self.collection_rest = CollectionsRest(self.master)
        self.non_default_collection=self.input.param('non_default_collection',False)
        self.num_docs=2016
        self.is_binary=self.input.param('binary_doc',False)
        self.eventing_role=self.input.param('eventing_role', True)
        if self.eventing_role:
            self.eventing_role_username="eventing_admin"
            self.eventing_role_password="password"
            payload="name="+self.eventing_role_username+"&roles=eventing_admin"+"&password="+self.eventing_role_password
            RestConnection(self.master).add_set_builtin_user(self.eventing_role_username,payload)

    def tearDown(self):
        # catch panics and print it in the test log
        self.check_eventing_logs_for_panic()
        rest = RestConnection(self.master)
        buckets = rest.get_buckets()
        meta_bucket=False
        for i in range(len(buckets)):
            if buckets[i].name=="metadata":
                meta_bucket=True
        self.hostname = self.input.param('host', 'https://postman-echo.com/')
        if self.hostname == 'local':
            self.teardown_curl()
        # check metadata bucke is empty
        if len(buckets) > 0 and meta_bucket and not self.skip_metabucket_check and not self.is_upgrade_test:
            count_meta = self.get_count("metadata")
            self.log.info("number of documents in metadata bucket {}".format(count_meta))
            if count_meta!= 0:
                raise Exception("metdata bucket is not empty at the end of test")
        super(EventingBaseTest, self).tearDown()

    def create_save_function_body(self, appname, appcode, description="Sample Description",
                                  checkpoint_interval=20000, cleanup_timers=False,
                                  dcp_stream_boundary="everything", deployment_status=False,
                                  skip_timer_threshold=86400,
                                  sock_batch_size=1, tick_duration=5000, timer_processing_tick_interval=500,
                                  timer_worker_pool_size=3, worker_count=3, processing_status=False ,
                                  cpp_worker_thread_count=1, multi_dst_bucket=False, execution_timeout=20,
                                  data_chan_size=10000, worker_queue_cap=100000, deadline_timeout=62,
                                  language_compatibility='6.6.2',hostpath=None,validate_ssl=False,src_binding=False):
        body = {}
        body['appname'] = appname
        script_dir = os.path.dirname(__file__)
        abs_file_path = os.path.join(script_dir, appcode)
        fh = open(abs_file_path, "r")
        body['appcode'] = fh.read()
        fh.close()
        body['depcfg'] = {}
        body['depcfg']['buckets'] = []
        if self.non_default_collection:
            if src_binding:
                body['depcfg']['buckets'].append(
                    {"alias": "dst_bucket", "bucket_name": self.dst_bucket_name,"scope_name":self.dst_bucket_name,
                     "collection_name":self.dst_bucket_name,"access": "rw"})
                body['depcfg']['buckets'].append(
                    {"alias": "src_bucket", "bucket_name": self.src_bucket_name,"scope_name":self.src_bucket_name,
                     "collection_name":self.src_bucket_name, "access": "r"})
            else:
                body['depcfg']['buckets'].append(
                    {"alias": "dst_bucket", "bucket_name": self.dst_bucket_name, "scope_name": self.dst_bucket_name,
                     "collection_name": self.dst_bucket_name, "access": "rw"})
            body['depcfg']['metadata_bucket'] = self.metadata_bucket_name
            body['depcfg']['metadata_scope'] = self.metadata_bucket_name
            body['depcfg']['metadata_collection'] = self.metadata_bucket_name
            body['depcfg']['source_bucket'] = self.src_bucket_name
            body['depcfg']['source_scope'] = self.src_bucket_name
            body['depcfg']['source_collection'] = self.src_bucket_name
            if self.is_sbm:
                del body['depcfg']['buckets'][0]
                if src_binding:
                    body['depcfg']['buckets'][0]['access'] = "rw"
                else:
                    body['depcfg']['buckets'].append(
                        {"alias": "src_bucket", "bucket_name": self.src_bucket_name, "scope_name": self.src_bucket_name,
                         "collection_name": self.src_bucket_name, "access": "rw"})
        else:
            if src_binding:
                body['depcfg']['buckets'].append(
                    {"alias": "dst_bucket", "bucket_name": self.dst_bucket_name, "access": "rw"})
                body['depcfg']['buckets'].append(
                    {"alias": "src_bucket", "bucket_name": self.src_bucket_name, "access": "r"})
            else:
                body['depcfg']['buckets'].append({"alias": "dst_bucket", "bucket_name": self.dst_bucket_name,"access": "rw"})
            body['depcfg']['metadata_bucket'] = self.metadata_bucket_name
            body['depcfg']['source_bucket'] = self.src_bucket_name
            if self.is_sbm:
                del body['depcfg']['buckets'][0]
                if src_binding:
                    body['depcfg']['buckets'][0]['access'] = "rw"
                else:
                    body['depcfg']['buckets'].append(
                        {"alias": "src_bucket", "bucket_name": self.src_bucket_name, "access": "rw"})
        if multi_dst_bucket:
            body['depcfg']['buckets'].append({"alias": self.dst_bucket_name1, "bucket_name": self.dst_bucket_name1})
        body['settings'] = {}
        body['settings']['checkpoint_interval'] = checkpoint_interval
        body['settings']['cleanup_timers'] = cleanup_timers
        body['settings']['dcp_stream_boundary'] = dcp_stream_boundary
        body['settings']['deployment_status'] = deployment_status
        body['settings']['description'] = description
        body['settings']['log_level'] = self.eventing_log_level

        body['settings']['skip_timer_threshold'] = skip_timer_threshold
        body['settings']['sock_batch_size'] = sock_batch_size
        body['settings']['tick_duration'] = tick_duration
        body['settings']['timer_processing_tick_interval'] = timer_processing_tick_interval
        body['settings']['timer_worker_pool_size'] = timer_worker_pool_size
        body['settings']['worker_count'] = worker_count
        body['settings']['processing_status'] = processing_status
        body['settings']['cpp_worker_thread_count'] = cpp_worker_thread_count
        body['settings']['execution_timeout'] = execution_timeout
        body['settings']['data_chan_size'] = data_chan_size
        body['settings']['worker_queue_cap'] = worker_queue_cap
        # See MB-27967, the reason for adding this config
        body['settings']['use_memory_manager'] = self.use_memory_manager
        # since deadline_timeout has to always greater than execution_timeout
        if execution_timeout != 3:
            deadline_timeout = execution_timeout + 1
        body['settings']['deadline_timeout'] = deadline_timeout
        body['settings']['timer_storage_chan_size'] = self.timer_storage_chan_size
        body['settings']['dcp_gen_chan_size'] = self.dcp_gen_chan_size
        body['depcfg']['curl'] = []
        if self.is_curl:
            if hostpath != None:
                body['depcfg']['curl'].append({"hostname": self.hostname+hostpath, "value": "server", "auth_type": self.auth_type,
                                               "username": self.curl_username, "password": self.curl_password,
                                               "allow_cookies": self.cookies,"validate_ssl_certificate": validate_ssl})
            else:
                body['depcfg']['curl'].append(
                    {"hostname": self.hostname, "value": "server", "auth_type": self.auth_type,
                     "username": self.curl_username, "password": self.curl_password, "allow_cookies": self.cookies,"validate_ssl_certificate": validate_ssl})
            if self.auth_type=="bearer":
                body['depcfg']['curl'][0]['bearer_key']=self.bearer_key
        body['settings']['language_compatibility']=language_compatibility
        content1 = self.rest.create_function(body['appname'], body)
        self.log.info("saving function {}".format(content1))
        return body

    def wait_for_bootstrap_to_complete(self, name, iterations=120):
        result = self.rest.get_deployed_eventing_apps()
        count = 0
        while name not in result and count < iterations:
            self.sleep(5, message="Waiting for eventing node to come out of bootstrap state...")
            count += 1
            result = self.rest.get_deployed_eventing_apps()
        if count == iterations:
            raise Exception(
                'Eventing took lot of time to come out of bootstrap state or did not successfully bootstrap')

    def wait_for_undeployment(self, name, iterations=120):
        self.sleep(5, message="Waiting for undeployment of function...")
        result = self.rest.get_running_eventing_apps()
        count = 0
        while name in result and count < iterations:
            self.sleep(5, message="Waiting for undeployment of function...")
            count += 1
            result = self.rest.get_running_eventing_apps()
        if count == iterations:
            raise Exception('Eventing took lot of time to undeploy')

    def verify_eventing_results(self, name, expected_dcp_mutations, doc_timer_events=False, on_delete=False,
                                skip_stats_validation=False, bucket=None, timeout=600,expected_duplicate=False):
        # This resets the rest server as the previously used rest server might be out of cluster due to rebalance
        rest = RestConnection(self.master)
        num_nodes = self.refresh_rest_server()
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        if bucket is None:
            bucket=self.dst_bucket_name
        if self.is_sbm:
            bucket=self.src_bucket_name
        if not skip_stats_validation:
            # we can't rely on dcp_mutation stats when doc timers events are set.
            # TODO : add this back when getEventProcessingStats works reliably for doc timer events as well
            if not doc_timer_events:
                count = 0
                actual_dcp_mutations = self.getActualMutations(num_nodes, name, on_delete)

                # This is required when binary data is involved where dcp_mutation will have process DCP_MUTATIONS
                # but ignore it
                # wait for eventing node to process dcp mutations
                log.info("Number of (is Deleted: {0}) processed till now : {1}".format(on_delete, actual_dcp_mutations))
                while actual_dcp_mutations != expected_dcp_mutations and count < 20:
                    self.sleep(timeout/20, message="Waiting for eventing to process all dcp mutations...")
                    count += 1
                    actual_dcp_mutations = self.getActualMutations(num_nodes, name, on_delete)
                    log.info("Number of onDelete? {0} processed till now : {1}".format(on_delete, actual_dcp_mutations))
                if count == 20:
                    raise Exception(
                        "Eventing has not processed all the isDeleted: {0}. Current : {1}   Expected : {2}".format(on_delete,
                                                                                                  actual_dcp_mutations,
                                                                                                  expected_dcp_mutations
                                                                                                  ))
        # wait for bucket operations to complete and verify it went through successfully
        count = 0
        stats_dst = rest.get_bucket_stats(bucket)
        while stats_dst["curr_items"] != expected_dcp_mutations and count < 20:
            message = "Waiting for handler code {2} to complete bucket operations... Current : {0} Expected : {1}".\
                      format(stats_dst["curr_items"], expected_dcp_mutations, name)
            self.sleep(timeout//20, message=message)
            curr_items=stats_dst["curr_items"]
            stats_dst = self.rest.get_bucket_stats(bucket)
            ### compact buckets when mutation count not progressing. Helpful for expiry events
            if count==10:
                self.bucket_compaction()
            stats_dst = rest.get_bucket_stats(bucket)
            if curr_items == stats_dst["curr_items"]:
                count += 1
            else:
                count=0
            if expected_duplicate and stats_dst["curr_items"] > expected_dcp_mutations:
                break
        try:
            stats_src = self.rest.get_bucket_stats(self.src_bucket_name)
            log.info("Documents in source bucket : {}".format(stats_src["curr_items"]))
        except :
            pass
        if stats_dst["curr_items"] != expected_dcp_mutations:
            total_dcp_backlog = 0
            timers_in_past = 0
            lcb = {}
            # TODO : Use the following stats in a meaningful way going forward. Just printing them for debugging.
            for eventing_node in eventing_nodes:
                rest_conn = RestConnection(eventing_node)
                out = rest_conn.get_all_eventing_stats()
                total_dcp_backlog += out[0]["events_remaining"]["dcp_backlog"]
                if "TIMERS_IN_PAST" in out[0]["event_processing_stats"]:
                    timers_in_past += out[0]["event_processing_stats"]["TIMERS_IN_PAST"]
                total_lcb_exceptions= out[0]["lcb_exception_stats"]
                host=eventing_node.ip
                lcb[host]=total_lcb_exceptions
                full_out = rest_conn.get_all_eventing_stats(seqs_processed=True)
                log.info("Stats for Node {0} is \n{1} ".format(eventing_node.ip, json.dumps(out, sort_keys=True,
                                                                                          indent=4)))
                log.debug("Full Stats for Node {0} is \n{1} ".format(eventing_node.ip, json.dumps(full_out,
                                                                                                sort_keys=True,
                                                                                                indent=4)))
            if not expected_duplicate:
                self.print_document_count_via_index(bucket)
                self.print_timer_alarm_context()
            if stats_dst["curr_items"] < expected_dcp_mutations:
                self.skip_metabucket_check=True
                if self.print_app_log:
                    self.print_app_logs(name)
                raise Exception(
                    "missing data in destination bucket. Current : {0} "
                    "Expected : {1}  dcp_backlog : {2}  TIMERS_IN_PAST : {3} lcb_exceptions : {4}".format(stats_dst["curr_items"],
                                                                                     expected_dcp_mutations,
                                                                                 total_dcp_backlog,
                                                                                 timers_in_past,lcb))
            elif stats_dst["curr_items"] > expected_dcp_mutations and not expected_duplicate:
                self.skip_metabucket_check = True
                if self.print_app_log:
                    self.print_app_logs(name)
                raise Exception(
                    "duplicated data in destination bucket which is not expected. Current : {0} "
                    "Expected : {1}  dcp_backlog : {2}  TIMERS_IN_PAST : {3} lcb_exceptions : {4}".format(stats_dst["curr_items"],
                                                                                     expected_dcp_mutations,
                                                                                 total_dcp_backlog,
                                                                                 timers_in_past,lcb))
            elif stats_dst["curr_items"] > expected_dcp_mutations and expected_duplicate:
                self.log.info(
                    "duplicated data in destination bucket which is expected. Current : {0} "
                    "Expected : {1}  dcp_backlog : {2}  TIMERS_IN_PAST : {3} lcb_exceptions : {4}".format(stats_dst["curr_items"],
                                                                                     expected_dcp_mutations,
                                                                                 total_dcp_backlog,
                                                                                 timers_in_past,lcb))
        log.info("Final docs count... Current : {0} Expected : {1}".
                 format(stats_dst["curr_items"], expected_dcp_mutations))
        # TODO : Use the following stats in a meaningful way going forward. Just printing them for debugging.
        # print all stats from all eventing nodes
        # These are the stats that will be used by ns_server and UI
        for eventing_node in eventing_nodes:
            rest_conn = RestConnection(eventing_node)
            out = rest_conn.get_all_eventing_stats()
            full_out = rest_conn.get_all_eventing_stats(seqs_processed=True)
            log.info("Stats for Node {0} is \n{1} ".format(eventing_node.ip, json.dumps(out, sort_keys=True,
                                                                                      indent=4)))
            log.debug("Full Stats for Node {0} is \n{1} ".format(eventing_node.ip, json.dumps(full_out, sort_keys=True,
                                                                                            indent=4)))

    def getActualMutations(self, num_nodes, name, on_delete):
        actual_dcp_mutations = 0
        if num_nodes <= 1:
            stats = self.rest.get_event_processing_stats(name)
        else:
            stats = self.rest.get_aggregate_event_processing_stats(name)

        if on_delete:
            if "dcp_deletion" in stats:
                actual_dcp_mutations = stats["dcp_deletion"]
            if "dcp_expiration" in stats:
                actual_dcp_mutations += stats["dcp_expiration"]
        else:
            actual_dcp_mutations = stats["dcp_mutation"]
        return actual_dcp_mutations

    def eventing_stats(self):
        self.sleep(5)
        content=self.rest.get_all_eventing_stats()
        log.info("execution stats: {0}".format(content))

    def deploy_function(self, body, deployment_fail=False, wait_for_bootstrap=True,pause_resume=False,pause_resume_number=1,
                        deployment_status=True,processing_status=True):
        if self.print_eventing_handler_code_in_logs:
            log.info("Deploying the following handler code : {0} with \nbindings: {1} and \nsettings: {2}".format(body['appname'], body['depcfg'] , body['settings']))
            log.info("\n{0}".format(body['appcode']))
        content1 = self.rest.lifecycle_operation(body['appname'], "deploy")
        log.info("deploy Application : {0}".format(content1))
        if deployment_fail:
            res = json.loads(content1)
            if not res["compile_success"]:
                return
            else:
                raise Exception("Deployment is expected to be failed but no message of failure")
        if wait_for_bootstrap:
            # wait for the function to come out of bootstrap state
            self.wait_for_handler_state(body['appname'], "deployed")
        if pause_resume and pause_resume_number > 0:
            self.pause_resume_n(body, pause_resume_number)


    def undeploy_and_delete_function(self, body):
        if self.print_app_log:
            self.print_app_logs(body['appname'])
        self.undeploy_function(body)
        self.delete_function(body)

    def undeploy_function(self, body):
        self.refresh_rest_server()
        content = self.rest.lifecycle_operation(body['appname'],"undeploy")
        log.info("Undeploy Application : {0}".format(body['appname']))
        self.wait_for_handler_state(body['appname'], "undeployed")
        return content

    def undeploy_function_by_name(self, name,wait_for_undeployment=True):
        content = self.rest.lifecycle_operation(name,"undeploy")
        log.info("Undeploy Application : {0}".format(name))
        if wait_for_undeployment:
            self.wait_for_handler_state(name, "undeployed")

    def delete_function(self, body):
        content1 = self.rest.delete_single_function(body['appname'])
        log.info("Delete Application : {0}".format(body['appname']))
        return content1

    def pause_function(self, body,wait_for_pause=True):
        self.refresh_rest_server()
        self.rest.lifecycle_operation(body['appname'], "pause")
        log.info("Pause Application : {0}".format(body['appname']))
        if wait_for_pause:
            self.wait_for_handler_state(body['appname'], "paused")

    def resume_function(self, body,wait_for_resume=True):
        self.refresh_rest_server()
        self.rest.lifecycle_operation(body['appname'], "resume")
        log.info("Resume Application : {0}".format(body['appname']))
        if wait_for_resume:
            self.wait_for_handler_state(body['appname'], "deployed")

    def refresh_rest_server(self):
        eventing_nodes_list = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        self.restServer = eventing_nodes_list[0]
        self.rest = RestConnection(self.restServer)
        return len(eventing_nodes_list)

    def check_if_eventing_consumers_are_cleaned_up(self):
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        array_of_counts = []
        command = "ps -ef | grep eventing-consumer | grep -v grep | wc -l"
        for eventing_node in eventing_nodes:
            shell = RemoteMachineShellConnection(eventing_node)
            count, error = shell.execute_non_sudo_command(command)
            if isinstance(count, list):
                count = int(count[0])
            else:
                count = int(count)
            log.info("Node : {0} , eventing_consumer processes running : {1}".format(eventing_node.ip, count))
            array_of_counts.append(count)
        count_of_all_eventing_consumers = sum(array_of_counts)
        if count_of_all_eventing_consumers != 0:
            return False
        return True

    """
        Checks if a string 'panic' is present in eventing.log on server and returns the number of occurrences
    """

    def check_eventing_logs_for_panic(self):
        self.generate_map_nodes_out_dist()
        panic_str = "panic"
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        if not eventing_nodes:
            return None
        for eventing_node in eventing_nodes:
            shell = RemoteMachineShellConnection(eventing_node)
            _, dir_name = RestConnection(eventing_node).diag_eval(
                'filename:absname(element(2, application:get_env(ns_server,error_logger_mf_dir))).')
            eventing_log = str(dir_name) + '/eventing.log*'
            count, err = shell.execute_command("zgrep \"{0}\" {1} | wc -l".
                                               format(panic_str, eventing_log))
            if isinstance(count, list):
                count = int(count[0])
            else:
                count = int(count)
            if count > self.panic_count:
                log.info("===== PANIC OBSERVED IN EVENTING LOGS ON SERVER {0}=====".format(eventing_node.ip))
                panic_trace, _ = shell.execute_command("zgrep \"{0}\" {1}".
                                                       format(panic_str, eventing_log))
                log.info("\n {0}".format(panic_trace))
                self.panic_count = count
            os_info = shell.extract_remote_info()
            if os_info.type.lower() == "windows":
                # This is a fixed path in all windows systems inside couchbase
                dir_name_crash = 'c://CrashDumps'
            else:
                dir_name_crash = str(dir_name) + '/../crash/'
            core_dump_count, err = shell.execute_command("ls {0}| wc -l".format(dir_name_crash))
            if isinstance(core_dump_count, list):
                core_dump_count = int(core_dump_count[0])
            else:
                core_dump_count = int(core_dump_count)
            if core_dump_count > 0:
                log.info("===== CORE DUMPS SEEN ON EVENTING NODES, SERVER {0} : {1} crashes seen =====".format(
                         eventing_node.ip, core_dump_count))
            shell.disconnect()

    def print_execution_and_failure_stats(self, name):
        out_event_execution = self.rest.get_event_execution_stats(name)
        log.info("Event execution stats : {0}".format(out_event_execution))
        out_event_failure = self.rest.get_event_failure_stats(name)
        log.info("Event failure stats : {0}".format(out_event_failure))

    """
        Push the bucket into DGM and return the number of items it took to push the bucket to DGM
    """
    def push_to_dgm(self, bucket, dgm_percent):
        doc_size = 1024
        curr_active = self.bucket_stat('vb_active_perc_mem_resident', bucket)
        total_items = self.bucket_stat('curr_items', bucket)
        batch_items = 20000
        # go into dgm
        while curr_active > dgm_percent:
            curr_items = self.bucket_stat('curr_items', bucket)
            gen_create = BlobGenerator('dgmkv', 'dgmkv-', doc_size, start=curr_items + 1, end=curr_items + 20000)
            total_items += batch_items
            try:
                self.cluster.load_gen_docs(self.master, bucket, gen_create, self.buckets[0].kvs[1],
                                           'create', exp=0, flag=0, batch_size=1000, compression=self.sdk_compression)
            except:
                pass
            curr_active = self.bucket_stat('vb_active_perc_mem_resident', bucket)
        log.info("bucket {0} in DGM, resident_ratio : {1}%".format(bucket, curr_active))
        total_items = self.bucket_stat('curr_items', bucket)
        return total_items

    def bucket_stat(self, key, bucket):
        stats = StatsCommon.get_stats([self.master], bucket, "", key)
        val = list(stats.values())[0]
        if val.isdigit():
            val = int(val)
        return val

    def bucket_compaction(self):
        for bucket in self.buckets:
            log.info("Compacting bucket : {0}".format(bucket.name))
            self.rest.compact_bucket(bucket=bucket.name)

    def kill_consumer(self, server):
        remote_client = RemoteMachineShellConnection(server)
        remote_client.kill_eventing_process(name="eventing-consumer")
        remote_client.disconnect()

    def kill_producer(self, server):
        remote_client = RemoteMachineShellConnection(server)
        remote_client.kill_eventing_process(name="eventing-producer")
        remote_client.disconnect()

    def kill_memcached_service(self, server):
        remote_client = RemoteMachineShellConnection(server)
        remote_client.kill_memcached()
        remote_client.disconnect()

    def kill_erlang_service(self, server):
        remote_client = RemoteMachineShellConnection(server)
        os_info = remote_client.extract_remote_info()
        log.info("os_info : {0}".format(os_info))
        if os_info.type.lower() == "windows":
            remote_client.kill_erlang(os="windows")
        else:
            remote_client.kill_erlang()
        remote_client.start_couchbase()
        remote_client.disconnect()
        # wait for restart and warmup on all node
        self.sleep(self.wait_timeout * 2)
        # wait till node is ready after warmup
        ClusterOperationHelper.wait_for_ns_servers_or_assert([server], self, wait_if_warmup=True)

    def reboot_server(self, server):
        remote_client = RemoteMachineShellConnection(server)
        remote_client.reboot_node()
        remote_client.disconnect()
        # wait for restart and warmup on all node
        self.sleep(self.wait_timeout * 2)
        # disable firewall on these nodes
        self.stop_firewall_on_node(server)
        # wait till node is ready after warmup
        ClusterOperationHelper.wait_for_ns_servers_or_assert([server], self, wait_if_warmup=True)

    def undeploy_delete_all_functions(self):
        self.refresh_rest_server()
        result = self.rest.get_composite_eventing_status()
        res=[]
        for i in range(len(result['apps'])):
            res.append(result['apps'][i]['name'])
        for a in res:
            self.rest.undeploy_function(a)
        for a in res:
            self.wait_for_handler_state(a, "undeployed")
        self.rest.delete_all_function()

    def change_time_zone(self,server,timezone="UTC"):
        remote_client = RemoteMachineShellConnection(server)
        remote_client.execute_command("timedatectl set-timezone "+timezone)
        remote_client.disconnect()

    def cleanup_eventing(self):
        ev_node = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=False)
        ev_rest = RestConnection(ev_node)
        log.info("Running eventing cleanup api...")
        ev_rest.cleanup_eventing()

    def generate_docs_bigdata(self, docs_per_day, start=0, document_size=1024000):
        json_generator = JsonGenerator()
        return json_generator.generate_docs_bigdata(end=(2016 * docs_per_day), start=start, value_size=document_size)

    def print_eventing_stats_from_all_eventing_nodes(self):
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        for eventing_node in eventing_nodes:
            rest_conn = RestConnection(eventing_node)
            out = rest_conn.get_all_eventing_stats()
            log.info("Stats for Node {0} is \n{1} ".format(eventing_node.ip, json.dumps(out, sort_keys=True,
                                                                                      indent=4)))

    def print_go_routine_dump_from_all_eventing_nodes(self):
        if self.print_go_routine:
            eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
            for eventing_node in eventing_nodes:
                rest_conn = RestConnection(eventing_node)
                out = rest_conn.get_eventing_go_routine_dumps()
                log.info("Go routine dumps for Node {0} is \n{1} ======================================================"
                         "============================================================================================="
                         "\n\n".format(eventing_node.ip, out))

    def verify_source_bucket_mutation(self,doc_count,deletes=False,timeout=600,bucket=None):
        if bucket == None:
            bucket=self.src_bucket_name
        try:
            query = "create primary index on {}".format(bucket)
            self.n1ql_helper.run_cbq_query(query=query, server=self.n1ql_node)
        except Exception as ex:
            log.info("Exception while creating index {}".format(ex))
        num_nodes = self.refresh_rest_server()
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        count=0
        result=0
        while count <= 20 and doc_count != result:
            self.sleep(timeout // 20, message="Waiting for eventing to process all dcp mutations...")
            if deletes:
                    query="select raw(count(*)) from {} where doc_deleted = 1".format(bucket)
            else:
                query="select raw(count(*)) from {} where updated_field = 1".format(bucket)
            result_set=self.n1ql_helper.run_cbq_query(query=query, server=self.n1ql_node)
            result=result_set["results"][0]
            if deletes:
                self.log.info("deleted docs:{}  expected doc: {}".format(result, doc_count))
            else:
                self.log.info("updated docs:{}  expected doc: {}".format(result, doc_count))
            count=count+1

        if count > 20 and doc_count != result:
            total_dcp_backlog = 0
            timers_in_past = 0
            lcb = {}
            # TODO : Use the following stats in a meaningful way going forward. Just printing them for debugging.
            for eventing_node in eventing_nodes:
                rest_conn = RestConnection(eventing_node)
                out = rest_conn.get_all_eventing_stats()
                total_dcp_backlog += out[0]["events_remaining"]["dcp_backlog"]
                if "TIMERS_IN_PAST" in out[0]["event_processing_stats"]:
                    timers_in_past += out[0]["event_processing_stats"]["TIMERS_IN_PAST"]
                total_lcb_exceptions = out[0]["lcb_exception_stats"]
                host = eventing_node.ip
                lcb[host] = total_lcb_exceptions
                full_out = rest_conn.get_all_eventing_stats(seqs_processed=True)
                log.info(
                    "Stats for Node {0} is \n{1} ".format(eventing_node.ip, json.dumps(out, sort_keys=True, indent=4)))
                log.debug("Full Stats for Node {0} is \n{1} ".format(eventing_node.ip,
                                                                     json.dumps(full_out, sort_keys=True, indent=4)))
            raise Exception("Eventing has not processed all the mutation in expected time, docs:{}  expected doc: {}".format(result, doc_count))

    def pause_resume_n(self, body, num):
        for i in range(num):
            self.pause_function(body)
            self.sleep(30)
            self.resume_function(body)


    def wait_for_handler_state(self, name,status,iterations=120):
        self.sleep(5, message="Waiting for {} to {}...".format(name, status))
        result = self.rest.get_composite_eventing_status()
        count = 0
        composite_status = None
        while composite_status != status and count < iterations:
            self.sleep(5, "Waiting for {} to {}...".format(name, status))
            result = self.rest.get_composite_eventing_status()
            for i in range(len(result['apps'])):
                if result['apps'][i]['name'] == name:
                    composite_status = result['apps'][i]['composite_status']
            count+=1
        if count == iterations:
            raise Exception('Eventing took lot of time for handler {} to {}'.format(name, status))

    def setup_curl(self,):
        o=os.system('python3 scripts/curl_setup.py start')
        self.log.info("=== started docker container =======".format(o))
        self.sleep(5)
        if o!=0:
            self.log.info("script result {}".format(o))
            raise Exception("unable to start docker")
        o=os.system('python3 scripts/curl_setup.py setup')
        self.log.info("=== setup done =======")
        if o!=0:
            self.log.info("script result {}".format(o))
            raise Exception("curl setup fail")

    def teardown_curl(self):
        o = os.system('python3 scripts/curl_setup.py stop')
        self.log.info("=== stopping docker container =======")

    def insall_dependencies(self):
        try:
            import docker
        except ImportError as e:
            o = os.system("python3 scripts/install_docker.py docker")
            self.log.info("docker installation done: {}".format(o))
            self.sleep(5)
            try:
                import docker
            except ImportError as e:
                raise Exception("docker installation fails with {}".format(o))

    def load_sample_buckets(self, server, bucketName):
        from lib.remote.remote_util import RemoteMachineShellConnection
        shell = RemoteMachineShellConnection(server)
        shell.execute_command("""curl -v -u Administrator:password \
                             -X POST http://{0}:8091/sampleBuckets/install \
                          -d '["{1}"]'""".format(server.ip, bucketName))
        shell.disconnect()
        self.sleep(5)

    def check_eventing_rebalance(self):
        status=self.rest.get_eventing_rebalance_status()
        #self.log.info("Eventing rebalance status: {}".format(status))
        if status.decode()=="true":
            return True
        else:
            return False

    def auto_retry_setup(self):
        self.sleep_time = self.input.param("sleep_time", 15)
        self.enabled = self.input.param("enabled", True)
        self.afterTimePeriod = self.input.param("afterTimePeriod", 150)
        self.maxAttempts = self.input.param("maxAttempts", 1)
        self.log.info("Changing the retry rebalance settings ....")
        self.change_retry_rebalance_settings(enabled=self.enabled, afterTimePeriod=self.afterTimePeriod,
                                             maxAttempts=self.maxAttempts)

    def change_retry_rebalance_settings(self, enabled=True,
                                        afterTimePeriod=300, maxAttempts=1):
        # build the body
        body = dict()
        if enabled:
            body["enabled"] = "true"
        else:
            body["enabled"] = "false"
        body["afterTimePeriod"] = afterTimePeriod
        body["maxAttempts"] = maxAttempts
        rest = RestConnection(self.master)
        rest.set_retry_rebalance_settings(body)
        result = rest.get_retry_rebalance_settings()
        self.log.info("Retry Rebalance settings changed to : {0}"
                      .format(json.loads(result)))

    def handler_status_map(self):
        m={}
        result=self.rest.get_composite_eventing_status()
        try:
            for i in range(len(result['apps'])):
                m[result['apps'][i]['name']] = result['apps'][i]['composite_status']
            return m
        except TypeError as e:
            self.log.info("no handler is available")

    def deploy_handler_by_name(self,name,wait_for_bootstrap=True):
        self.rest.deploy_function_by_name(name)
        if wait_for_bootstrap:
            self.wait_for_handler_state(name, "deployed")

    def pause_handler_by_name(self,name,wait_for_pause=True):
        self.rest.pause_function_by_name(name)
        if wait_for_pause:
            self.wait_for_handler_state(name, "paused")

    def check_word_count_eventing_log(self,function_name,word,expected_count,return_count_only=False):
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        array_of_counts = []
        command = "cat /opt/couchbase/var/lib/couchbase/data/@eventing/"+ function_name +"* | grep -a \""+word+"\" | wc -l"
        for eventing_node in eventing_nodes:
            shell = RemoteMachineShellConnection(eventing_node)
            count, error = shell.execute_non_sudo_command(command)
            self.log.info("count : {} and error : {} ".format(count,error))
            if isinstance(count, list):
                count = int(count[0])
            else:
                count = int(count)
            log.info("Node : {0} , word count on : {1}".format(eventing_node.ip, count))
            array_of_counts.append(count)
        count_of_all_words = sum(array_of_counts)
        log.info("Total count: {}".format(count_of_all_words))
        if return_count_only:
            return True, count_of_all_words
        if count_of_all_words == expected_count:
            return True, count_of_all_words
        return False, count_of_all_words

    def check_number_of_files(self):
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        array_of_counts = []
        command = "cd /opt/couchbase/var/lib/couchbase/data/@eventing;ls | wc -l"
        for eventing_node in eventing_nodes:
            shell = RemoteMachineShellConnection(eventing_node)
            count, error = shell.execute_non_sudo_command(command)
            self.log.info("count : {} and error : {} ".format(count, error))
            if isinstance(count, list):
                count = int(count[0])
            else:
                count = int(count)
            log.info("Node : {0} , word count on : {1}".format(eventing_node.ip, count))
            array_of_counts.append(count)
        count_of_all_files = sum(array_of_counts)
        log.info("Total count: {}".format(count_of_all_files))
        return count_of_all_files

    def drop_data_to_bucket_from_eventing(self,server):
        shell = RemoteMachineShellConnection(server)
        shell.info = shell.extract_remote_info()
        if shell.info.type.lower() == "windows":
            raise Exception("Should not run on windows")
        o, r = shell.execute_command("/sbin/iptables -A OUTPUT -p tcp --dport 11210 -j DROP")
        shell.log_command_output(o, r)
        # o, r = shell.execute_command("/sbin/iptables -A INPUT -p tcp --dport 11210 -j DROP")
        # shell.log_command_output(o, r)
        log.info("enabled firewall on {0}".format(server))
        o, r = shell.execute_command("/sbin/iptables --list")
        shell.log_command_output(o, r)
        shell.disconnect()

    def reset_firewall(self,server):
        shell = RemoteMachineShellConnection(server)
        shell.info = shell.extract_remote_info()
        o, r = shell.execute_command("/sbin/iptables --flush")
        shell.log_command_output(o, r)
        shell.disconnect()

    def get_stats_value(self,name,expression):
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        total_count=0
        for eventing_node in eventing_nodes:
            rest_conn = RestConnection(eventing_node)
            stats = rest_conn.get_all_eventing_stats()
            keys=expression.split(".")
            for stat in stats:
                if stat["function_name"] == name:
                    total_count=total_count + stat[keys[0]][keys[1]]
        return total_count

    def print_app_logs(self,name):
        content = self.rest.get_app_logs(name)
        self.log.info("================== {} ============================================================".format(name))
        self.log.info(content)
        self.log.info("================== App logs end ===============================================================")

    def print_timer_alarm_context(self):
        alarm_query="select RAW count(0) from metadata where meta().id like 'eventing:%:al%'"
        context_query="select RAW count(0) from metadata where meta().id like 'eventing:%:cx%'"
        alarm = self.n1ql_helper.run_cbq_query(query=alarm_query, server=self.n1ql_node)
        self.log.info("================== alarm documents in metadata ================================================")
        self.log.info(alarm)
        self.log.info("===============================================================================================")
        context = self.n1ql_helper.run_cbq_query(query=context_query, server=self.n1ql_node)
        self.log.info("================== context documents in metadata  =============================================")
        self.log.info(context)
        self.log.info("===============================================================================================")

    def print_document_count_via_index(self,bucket):
        query="select RAW count(*) from "+bucket
        result_set = self.n1ql_helper.run_cbq_query(query=query, server=self.n1ql_node)
        self.log.info("================== number of doc in {} via index ===========================".format(bucket))
        self.log.info(result_set['results'])
        self.log.info("===============================================================================================")

    def wait_for_failover(self):
        self.log.info("Waiting for internal failover to start ....")
        failover_started=False
        count =0
        ### wait for 5 min max
        while not failover_started:
            failover_started=self.check_eventing_rebalance()
            count=count+1
            self.sleep(1,"checking for failover...")
            if count >=300:
                raise Exception("Failover not started even after waiting for long")
        self.log.info("Failover started")


    def enable_disable_vb_distribution(self,enable=True):
        if enable:
            body = "{\"auto_redistribute_vbs_on_failover\": true}"
        else:
            body = "{\"auto_redistribute_vbs_on_failover\": false}"
        self.rest.update_eventing_config(body)
        self.log.info(self.rest.get_eventing_config())


    def create_scope_collection(self,bucket,scope,collection):
        self.collection_rest.create_scope_collection(bucket=bucket, scope=scope, collection=collection)

    def create_n_scope(self,bucket,num=1):
        for i in range(num):
            scope_name="scope_"+str(i)
            self.rest.create_scope(bucket,scope_name)

    def create_n_collections(self,bucket,scope,num):
        for i in range(num):
            collection_name="coll_"+str(i)
            self.rest.create_collection(bucket,scope,collection_name)

    '''
        Method to check number of docs in a collection
    '''
    def verify_doc_count_collections(self,namespace,expected_count,timeout=600,expected_duplicate=False):
        eventing_nodes = self.get_nodes_from_services_map(service_type="eventing", get_all_nodes=True)
        if namespace==None:
            namespace="dst_bucket.dst_bucket.dst_bucket"
        count=0
        try:
            query = "create primary index on " + namespace
            result_set = self.n1ql_helper.run_cbq_query(query, server=self.n1ql_node)
        except Exception as e:
            pass
        actual_count=self.get_count(namespace)
        while actual_count != expected_count and count < 20:
            message = "Waiting for handler code {2} to complete bucket operations... Current : {0} Expected : {1}".\
                      format(actual_count, expected_count, namespace)
            self.sleep(timeout//20, message=message)
            curr_items=actual_count
            actual_count = self.get_count(namespace)
            ### compact buckets when mutation count not progressing. Helpful for expiry events
            if count==10:
                self.rest = RestConnection(self.master)
                self.bucket_compaction()
            actual_count = self.get_count(namespace)
            if curr_items == actual_count:
                count += 1
            else:
                count=0
            if expected_duplicate and actual_count > expected_count:
                break
        if actual_count != expected_count:
            self.print_eventing_stats_from_all_eventing_nodes()
            total_dcp_backlog = 0
            timers_in_past = 0
            lcb = {}
            total_on_update_success=0
            total_on_update_failure=0
            total_on_delete_success = 0
            total_on_delete_failure = 0
            # TODO : Use the following stats in a meaningful way going forward. Just printing them for debugging.
            for eventing_node in eventing_nodes:
                rest_conn = RestConnection(eventing_node)
                out = rest_conn.get_all_eventing_stats()
                total_dcp_backlog += out[0]["events_remaining"]["dcp_backlog"]
                total_on_update_success += out[0]["execution_stats"]["on_update_success"]
                total_on_update_failure += out[0]["execution_stats"]["on_update_failure"]
                total_on_delete_success += out[0]["execution_stats"]["on_delete_success"]
                total_on_delete_failure += out[0]["execution_stats"]["on_delete_failure"]
                if "TIMERS_IN_PAST" in out[0]["event_processing_stats"]:
                    timers_in_past += out[0]["event_processing_stats"]["TIMERS_IN_PAST"]
                total_lcb_exceptions= out[0]["lcb_exception_stats"]
                host=eventing_node.ip
                lcb[host]=total_lcb_exceptions
                full_out = rest_conn.get_all_eventing_stats(seqs_processed=True)
                log.info("Stats for Node {0} is \n{1} ".format(eventing_node.ip, json.dumps(out, sort_keys=True,
                                                                                          indent=4)))
                log.debug("Full Stats for Node {0} is \n{1} ".format(eventing_node.ip, json.dumps(full_out,
                                                                                                sort_keys=True,
                                                                                                indent=4)))
            if actual_count < expected_count:
                self.skip_metabucket_check = True
                log.info("Execution stats update_success: {0}  update_failure: {1}  delete_success: {2}  "
                         "delete_failure: {3}".format(total_on_update_success,total_on_update_failure,total_on_delete_success
                                                      ,total_on_delete_failure))
                raise Exception("missing data in destination bucket. Current : {0} "
                                "Expected : {1}  dcp_backlog : {2}  TIMERS_IN_PAST : {3} lcb_exceptions : {4}".format(
                    actual_count, expected_count, total_dcp_backlog, timers_in_past, lcb))
            elif actual_count > expected_count and not expected_duplicate:
                self.skip_metabucket_check = True
                log.info("Execution stats update_success: {0}  update_failure: {1}  delete_success: {2}  "
                         "delete_failure: {3}".format(total_on_update_success, total_on_update_failure,
                                                      total_on_delete_success, total_on_delete_failure))
                raise Exception("duplicated data in destination bucket which is not expected. Current : {0} "
                                "Expected : {1}  dcp_backlog : {2}  TIMERS_IN_PAST : {3} lcb_exceptions : {4}".format(
                    actual_count, expected_count, total_dcp_backlog, timers_in_past, lcb))
            elif actual_count > expected_count and expected_duplicate:
                log.info("Execution stats update_success: {0}  update_failure: {1}  delete_success: {2}  "
                         "delete_failure: {3}".format(total_on_update_success, total_on_update_failure,
                                                      total_on_delete_success, total_on_delete_failure))
                self.log.info("duplicated data in destination bucket which is expected. Current : {0} "
                              "Expected : {1}  dcp_backlog : {2}  TIMERS_IN_PAST : {3} lcb_exceptions : {4}".format(
                    actual_count, expected_count, total_dcp_backlog, timers_in_past, lcb))
        log.info("Final docs count... Current : {0} Expected : {1}".format(actual_count, expected_count))



    def get_count(self,bucket):
        query="select RAW(count(*)) from "+bucket
        n1ql_node = self.get_nodes_from_services_map(service_type="n1ql")
        result_set=RestConnection(n1ql_node).query_tool(query)
        count=result_set['results']
        return count[0]


    def load_data_to_collection(self,num_items,namespace,is_create=True,is_delete=False,is_update=False,
                                expiry=0,wait_for_loading=True,template="Person"):
        if self.is_binary:
            template="Binary"
        if is_delete or is_update:
            is_create=False
        collection_list=namespace.split(".")
        if is_create:
            self.gen_create = SDKDataLoader(num_ops=num_items, percent_create=100, percent_update=0,
                                        percent_delete=0, scope=collection_list[1], collection=collection_list[2],
                                            doc_expiry=expiry,json_template=template)
        elif is_delete:
            self.gen_create = SDKDataLoader(num_ops=num_items, percent_create=0, percent_update=0, percent_delete=100,
                                            scope=collection_list[1], collection=collection_list[2],json_template=template)
        elif is_update:
            self.gen_create = SDKDataLoader(num_ops=num_items, percent_create=0, percent_update=100, percent_delete=0,
                                            scope=collection_list[1], collection=collection_list[2],doc_expiry=expiry,json_template=template)
        task=self.cluster.async_load_gen_docs(self.master, collection_list[0], self.gen_create, pause_secs=1,
                                         timeout_secs=300,exp=expiry)
        if wait_for_loading:
            task.result()
        else:
            return task

    def load_batch_data_to_collection(self,num_items,namespace,is_create=True,is_delete=False,is_update=False,
                                expiry=0,wait_for_loading=True,template="Person"):
        if self.is_binary:
            template="Binary"
        if is_delete or is_update:
            is_create=False
        collection_list=namespace.split(".")
        if is_create:
            self.gen_create = SDKDataLoader(num_ops=num_items, percent_create=100, percent_update=0,
                                        percent_delete=0, scope=collection_list[1], collection=collection_list[2],
                                            doc_expiry=expiry,json_template=template)
        elif is_delete:
            self.gen_create = SDKDataLoader(num_ops=num_items, percent_create=0, percent_update=0, percent_delete=100,
                                            scope=collection_list[1], collection=collection_list[2],json_template=template)
        elif is_update:
            self.gen_create = SDKDataLoader(num_ops=num_items, percent_create=0, percent_update=100, percent_delete=0,
                                            scope=collection_list[1], collection=collection_list[2],doc_expiry=expiry,json_template=template)
        task=self.data_ops_javasdk_loader_in_batches(sdk_data_loader=self.gen_create,
                                                                    batch_size=self.batch_size)
        if wait_for_loading:
            task.result()
        else:
            return task

    def create_function_with_collection(self, appname, appcode,
                                 dcp_stream_boundary="everything",src_namespace="src_bucket._default._default",
                                        meta_namespace="metadata._default._default",
                                        collection_bindings=["dst_bucket.dst_bucket._default._default.rw"],is_curl=False,
                                        hostpath=None, validate_ssl=False,worker_count=1,language_compatibility='6.6.2'):
        src_map=src_namespace.split(".")
        meta_map=meta_namespace.split(".")
        src_bucket=src_map[0]
        src_scope=src_map[1]
        src_collection=src_map[2]
        meta_bucket = meta_map[0]
        meta_scope = meta_map[1]
        meta_collection = meta_map[2]
        body = {}
        body['appname'] = appname
        script_dir = os.path.dirname(__file__)
        abs_file_path = os.path.join(script_dir, appcode)
        fh = open(abs_file_path, "r")
        body['appcode'] = fh.read()
        fh.close()
        body['depcfg'] = {}
        body['depcfg']['metadata_bucket'] = meta_bucket
        body['depcfg']['metadata_scope'] = meta_scope
        body['depcfg']['metadata_collection'] = meta_collection
        body['depcfg']['source_bucket'] = src_bucket
        body['depcfg']['source_scope'] = src_scope
        body['depcfg']['source_collection'] = src_collection
        body['depcfg']['curl'] = []
        body['depcfg']['buckets'] = []
        for binding in collection_bindings:
            bind_map=binding.split(".")
            if  len(bind_map)< 5:
                raise Exception("Binding {} doesn't have all the fields".format(binding))
            body['depcfg']['buckets'].append(
                {"alias": bind_map[0], "bucket_name": bind_map[1], "scope_name": bind_map[2],
                 "collection_name": bind_map[3], "access": bind_map[4]})
        body['settings'] = {}
        body['settings']['dcp_stream_boundary'] = dcp_stream_boundary
        body['settings']['deployment_status'] = False
        body['settings']['processing_status'] = False
        body['settings']['worker_count'] = worker_count
        body['settings']['language_compatibility'] = language_compatibility
        if is_curl:
            if hostpath != None:
                body['depcfg']['curl'].append({"hostname": self.hostname+hostpath, "value": "server", "auth_type": self.auth_type,
                                               "username": self.curl_username, "password": self.curl_password,
                                               "allow_cookies": self.cookies,"validate_ssl_certificate": validate_ssl})
            else:
                body['depcfg']['curl'].append(
                    {"hostname": self.hostname, "value": "server", "auth_type": self.auth_type,
                     "username": self.curl_username, "password": self.curl_password, "allow_cookies": self.cookies,"validate_ssl_certificate": validate_ssl})
            if self.auth_type=="bearer":
                body['depcfg']['curl'][0]['bearer_key']=self.bearer_key
        self.rest.create_function(body['appname'], body)
        self.log.info("saving function {}".format(body['appname']))
        return body
