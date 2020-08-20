import random
import copy

from tasks.task import NodesFailureTask
from .base_gsi import BaseSecondaryIndexingTests
from collection.collections_rest_client import CollectionsRest
from collection.collections_stats import CollectionsStats
import threading
from couchbase_helper.query_definitions import SQLDefinitionGenerator
from lib.couchbase_helper.documentgenerator import SDKDataLoader
from tasks.taskmanager import TaskManager


class PlasmaCollectionsTests(BaseSecondaryIndexingTests):
    def setUp(self):
        super(PlasmaCollectionsTests, self).setUp()
        self.log.info("==============  PlasmaCollectionsTests setup has started ==============")
        self.rest.delete_all_buckets()
        self.num_scopes = self.input.param("num_scopes", 1)
        self.num_collections = self.input.param("num_collections", 1)
        self.test_timeout = self.input.param("test_timeout", 1)
        self.test_bucket = self.input.param('test_bucket', 'test_bucket')
        self.system_failure = self.input.param('system_failure', 'disk_failure')
        self.bucket_size = self.input.param('bucket_size', 100)
        self.drop_sleep = self.input.param('drop_sleep', 30)
        self.bucket_params = self._create_bucket_params(server=self.master, size=self.bucket_size,
                                                        replicas=self.num_replicas, bucket_type=self.bucket_type,
                                                        enable_replica_index=self.enable_replica_index,
                                                        eviction_policy=self.eviction_policy, lww=self.lww)
        self.cluster.create_standard_bucket(name=self.test_bucket, port=11222,
                                            bucket_params=self.bucket_params)
        self.buckets = self.rest.get_buckets()
        self.cli_rest = CollectionsRest(self.master)
        self.stat = CollectionsStats(self.master)
        self.scope_prefix = 'test_scope'
        self.collection_prefix = 'test_collection'
        self.run_cbq_query = self.n1ql_helper.run_cbq_query
        self.batch_size = self.input.param("batch_size", 50000)
        self.all_indexes = []
        self._lock_queue = threading.Lock()
        self.run_tasks = False
        self.tasks = []
        self.batch_size = self.input.param("batch_size", 10000)
        self.start_doc = self.input.param("start_doc", 1)
        self.num_items_in_collection = self.input.param("num_items_in_collection", 100000)
        self.percent_create = self.input.param("percent_create", 100)
        self.percent_update = self.input.param("percent_update", 0)
        self.percent_delete = self.input.param("percent_delete", 0)
        self.all_collections = self.input.param("all_collections", False)
        self.dataset_template = self.input.param("dataset_template", "Employee")
        self.system_failure_task_manager = TaskManager(
            "system_failure_detector_thread")
        self.system_failure_task_manager.start()

        self.log.info("==============  PlasmaCollectionsTests setup has completed ==============")

    def tearDown(self):
        self.log.info("==============  PlasmaCollectionsTests tearDown has started ==============")
        super(PlasmaCollectionsTests, self).tearDown()
        self.log.info("==============  PlasmaCollectionsTests tearDown has completed ==============")

    def suite_tearDown(self):
        pass

    def suite_setUp(self):
        pass

    def _prepare_collection_for_indexing(self, num_scopes=1, num_collections=1):
        self.keyspace = []
        self.cli_rest.create_scope_collection_count(scope_num=num_scopes, collection_num=num_collections,
                                                    scope_prefix=self.scope_prefix,
                                                    collection_prefix=self.collection_prefix,
                                                    bucket=self.test_bucket)
        self.scopes = self.cli_rest.get_bucket_scopes(bucket=self.test_bucket)
        self.collections = list(set(self.cli_rest.get_bucket_collections(bucket=self.test_bucket)))
        self.scopes.remove('_default')
        self.collections.remove('_default')
        self.sleep(5)
        for s_item in self.scopes:
            for c_item in self.collections:
                self.keyspace.append(f'default:{self.test_bucket}.{s_item}.{c_item}')
        self.keyspace.append(f'default:{self.test_bucket}._default._default')

    def all_indexes_metadata(self, index_name_state=None, operation="create"):
        with self._lock_queue:
            if operation is "create":
                self.all_indexes.append(index_name_state)
            if operation is "delete":
                try:
                    index = self.all_indexes.pop(random.randint(0, len(self.all_indexes)))
                except Exception as e:
                    index = None
                return index
            if operation is "scan":
                try:
                    index = self.all_indexes[random.randint(0, len(self.all_indexes))]
                except Exception as e:
                    index = None
                return index
            if operation is "build":
                for index in self.all_indexes:
                    if index["defer_build"]:
                        self.all_indexes.remove(index)
                        mod_index = copy.deepcopy(index)
                        mod_index["defer_build"] = False
                        self.all_indexes.append(mod_index)
                        return index
                return None

    def create_indexes(self, itr=0, num=0):
        query_definition_generator = SQLDefinitionGenerator()
        itr = itr
        while num > 0:
            for collection_keyspace in self.keyspace:
                if num < 0:
                    break
                query_definitions = query_definition_generator. \
                    generate_employee_data_query_definitions(index_name_prefix='name_primary_idx_' +
                                                                               collection_keyspace.split('.')[-2] + "_"
                                                                               + collection_keyspace.split('.')[-1],
                                                             keyspace=collection_keyspace)

                for query_def in query_definitions:
                    if "primary" not in query_def.groups:
                        index_name = query_def.get_index_name()
                        query_def.update_index_name(index_name + str(itr))
                        query = query_def.generate_index_create_query(use_gsi_for_secondary=True, gsi_type="plasma")
                        try:
                            # create index
                            server = self.get_nodes_from_services_map(service_type="n1ql")
                            self.run_cbq_query(query=query, server=server)
                        except Exception as err:
                            if "Build Already In Progress" not in str(err) and "Timeout 1ms exceeded" not in str(err):
                                error_map = {"query": query, "error": str(err)}
                                self.errors.append(error_map)
                                self.test_fail = True
                        if num < 0:
                            break
                        num -= 1
                        itr += 1
        return itr

    def create_indexes_thread(self, itr=0):
        query_definition_generator = SQLDefinitionGenerator()
        itr = itr
        self.sleep(5)
        self.log.info(threading.currentThread().getName() + " Started")
        while self.run_tasks:
            for collection_keyspace in self.keyspace:
                if not self.run_tasks:
                    break
                query_definitions = query_definition_generator. \
                    generate_employee_data_query_definitions(index_name_prefix='name_primary_idx_' +
                                                                               collection_keyspace.split('.')[-2] + "_"
                                                                               + collection_keyspace.split('.')[-1],
                                                             keyspace=collection_keyspace)

                for query_def in query_definitions:
                    if "primary" not in query_def.groups:
                        index_name = query_def.get_index_name()
                        query_def.update_index_name(index_name + str(itr))
                        defer_build = random.choice([True, False])
                        index_meta = {"name": query_def.get_index_name(), "query_def": query_def,
                                      "defer_build": defer_build}
                        query = query_def.generate_index_create_query(use_gsi_for_secondary=True, gsi_type="plasma",
                                                                      defer_build=defer_build)
                        try:
                            # create index
                            server = self.get_nodes_from_services_map(service_type="n1ql")
                            self.run_cbq_query(query=query, server=server, timeout='1ms')
                        except Exception as err:
                            if "Build Already In Progress" not in str(err) and "Timeout 1ms exceeded" not in str(err):
                                error_map = {"query": query, "error": str(err)}
                                self.errors.append(error_map)
                                self.test_fail = True
                        self.all_indexes_metadata(index_meta)
                        if not self.run_tasks:
                            break
                        itr += 1

        self.log.info(threading.currentThread().getName() + " Completed")

    def build_indexes(self):
        self.sleep(5)
        self.log.info(threading.currentThread().getName() + " Started")
        while self.run_tasks:
            index_to_build = self.all_indexes_metadata(operation="build")
            if index_to_build:
                query_def = index_to_build["query_def"]
                build_query = query_def.generate_build_query(namespace=query_def.keyspace)
                try:
                    server = self.get_nodes_from_services_map(service_type="n1ql")
                    self.run_cbq_query(query=build_query, server=server)
                except Exception as err:
                    if "Build Already In Progress" not in str(err):
                        error_map = {"query": build_query, "error": str(err)}
                        self.errors.append(error_map)
                        self.test_fail = True
            self.sleep(5)
        self.log.info(threading.currentThread().getName() + " Completed")

    def scan_indexes(self):
        self.sleep(5)
        self.log.info(threading.currentThread().getName() + " Started")
        while self.run_tasks:
            index_to_scan = self.all_indexes_metadata(operation="scan")
            if index_to_scan:
                query_def = index_to_scan["query_def"]
                query = query_def.generate_query(bucket=query_def.keyspace)
                try:
                    server = self.get_nodes_from_services_map(service_type="n1ql")
                    self.run_cbq_query(query=query, server=server)
                except Exception as err:
                    if "No index available on keyspace" not in str(err):
                        error_map = {"query": query, "error": str(err)}
                        self.errors.append(error_map)
                        self.test_fail = True
        self.log.info(threading.currentThread().getName() + " Completed")

    def drop_indexes(self, drop_sleep):
        self.sleep(10)
        self.log.info(threading.currentThread().getName() + " Started")
        while self.run_tasks:
            index_to_delete = self.all_indexes_metadata(operation="delete")
            if index_to_delete:
                query_def = index_to_delete["query_def"]
                drop_query = query_def.generate_index_drop_query(namespace=query_def.keyspace)
                try:
                    server = self.get_nodes_from_services_map(service_type="n1ql")
                    self.run_cbq_query(query=drop_query, server=server)
                except Exception as err:
                    error_map = {"query": drop_query, "error": str(err)}
                    self.errors.append(error_map)
                    self.test_fail = True
            self.sleep(drop_sleep)
        self.log.info(threading.currentThread().getName() + " Completed")

    def compare_indexes_count(self, indexes_count_before, indexes_count_after):
        indexes_with_data_loss = {}
        for index in indexes_count_after:
            if index in indexes_count_before and indexes_count_after[index] < indexes_count_before[index]:
                indexes_with_data_loss[index] = {"failure_iteration": self.failure_iteration,
                                                 "indexes_count_before": indexes_count_before[index],
                                                 "indexes_count_after": indexes_count_after[index]}
        if indexes_with_data_loss:
            self.test_fail = True
            self.errors.append(indexes_with_data_loss)

    def schedule_system_failure(self):
        self.log.info(threading.currentThread().getName() + " Started")
        self.sleep(10)
        self.failure_iteration = 1
        while self.run_tasks:
            indexes_count_before = self.get_server_indexes_count()
            server_to_fail = self.get_nodes_from_services_map(service_type="index", get_all_nodes=True)
            system_failure_task = NodesFailureTask(self.master, server_to_fail, self.system_failure, 300, 0, False, 3,
                                                   disk_location="/data")
            self.system_failure_task_manager.schedule(system_failure_task)
            try:
                system_failure_task.result()
            except Exception as e:
                self.log.info("Exception: {}".format(e))

            indexes_count_after = self.get_server_indexes_count()

            self.compare_indexes_count(indexes_count_before, indexes_count_after)
            print(indexes_count_before)
            print(indexes_count_after)

            self.failure_iteration += 1

        self.log.info(threading.currentThread().getName() + " Completed")

    def test_sharding_create_drop_indexes(self):
        self.test_fail = False
        self.errors = []
        self._prepare_collection_for_indexing(num_scopes=self.num_scopes, num_collections=self.num_collections)
        sdk_data_loader = SDKDataLoader(start_seq_num=self.start_doc, num_ops=self.num_items_in_collection,
                                        percent_create=self.percent_create,
                                        percent_update=self.percent_update, percent_delete=self.percent_delete,
                                        all_collections=self.all_collections, timeout=self.test_timeout,
                                        json_template=self.dataset_template)

        data_load_process = threading.Thread(name="data_load_process",
                                             target=self.data_ops_javasdk_loader_in_batches,
                                             args=(sdk_data_loader, self.batch_size))
        self.tasks.append(data_load_process)

        create_thread = threading.Thread(name="create_thread",
                                         target=self.create_indexes_thread)
        self.tasks.append(create_thread)

        drop_thread = threading.Thread(name="drop_thread",
                                       target=self.drop_indexes,
                                       args=[self.drop_sleep])
        self.tasks.append(drop_thread)

        build_index_thread = threading.Thread(name="build_index_thread",
                                              target=self.build_indexes)
        self.tasks.append(build_index_thread)

        scan_thread = threading.Thread(name="scan_thread",
                                       target=self.scan_indexes)

        self.tasks.append(scan_thread)

        self.run_tasks = True

        for task in self.tasks:
            task.start()

        self.sleep(self.test_timeout)

        self.run_tasks = False

        for task in self.tasks:
            task.join()

        if not self.wait_until_indexes_online(defer_build=True):
            self.test_fail = True
            self.errors.append({"Some indexes not online": str(self.get_indexes_not_online())})

        if self.test_fail:
            self.fail(str(self.errors))

    def test_system_failure_create_drop_indexes(self):
        self.test_fail = False
        self.errors = []
        self._prepare_collection_for_indexing(num_scopes=self.num_scopes, num_collections=self.num_collections)

        iter_num = self.create_indexes(num=10)

        sdk_data_loader = SDKDataLoader(start_seq_num=self.start_doc, num_ops=self.num_items_in_collection,
                                        percent_create=self.percent_create,
                                        percent_update=self.percent_update, percent_delete=self.percent_delete,
                                        all_collections=self.all_collections, timeout=self.test_timeout,
                                        json_template=self.dataset_template)

        self.data_ops_javasdk_loader_in_batches(sdk_data_loader, self.batch_size)
        #self.schedule_system_failure()

        create_thread = threading.Thread(name="create_thread",
                                         target=self.create_indexes_thread,
                                         args=[iter_num])
        self.tasks.append(create_thread)

        drop_thread = threading.Thread(name="drop_thread",
                                       target=self.drop_indexes,
                                       args=[self.drop_sleep])
        self.tasks.append(drop_thread)

        build_index_thread = threading.Thread(name="build_index_thread",
                                              target=self.build_indexes)
        self.tasks.append(build_index_thread)

        scan_thread = threading.Thread(name="scan_thread",
                                       target=self.scan_indexes)

        self.tasks.append(scan_thread)

        system_failure_thread = threading.Thread(name="system_failure_thread",
                                                 target=self.schedule_system_failure)
        self.tasks.append(system_failure_thread)

        self.run_tasks = True

        for task in self.tasks:
            task.start()

        self.sleep(self.test_timeout)

        self.run_tasks = False

        for task in self.tasks:
            task.join()

        if not self.wait_until_indexes_online(defer_build=True):
            self.test_fail = True
            self.errors.append({"Some indexes not online": str(self.get_indexes_not_online())})

        self.n1ql_helper.drop_all_indexes_on_keyspace()

        if self.test_fail:
            self.fail(str(self.errors))