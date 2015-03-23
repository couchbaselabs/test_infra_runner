from threading import Thread
import re
import copy
import Queue
from datetime import datetime
from membase.api.rest_client import RestConnection, Bucket
from newupgradebasetest import NewUpgradeBaseTest
from xdcrnewbasetests import XDCRNewBaseTest, REPLICATION_TYPE
from TestInput import TestInputSingleton
from remote.remote_util import RemoteMachineShellConnection
from membase.api.rest_client import RestConnection, RestHelper
from membase.api.exception import RebalanceFailedException
from membase.helper.cluster_helper import ClusterOperationHelper
from couchbase_helper.documentgenerator import BlobGenerator
from remote.remote_util import RemoteMachineShellConnection
from couchbase_helper.document import DesignDocument, View

class UpgradeTests(NewUpgradeBaseTest,XDCRNewBaseTest):
    def setUp(self):
        super(UpgradeTests, self).setUp()
        self.pause_xdcr_cluster = self.input.param("pause", "")
        self.bucket_topology = self.input.param("bucket_topology", "default:1><2").split(";")
        self.src_init = self.input.param('src_init', 2)
        self.dest_init = self.input.param('dest_init', 2)
        self.buckets_on_src = [str(bucket_repl.split(":")[0]) for bucket_repl in self.bucket_topology if re.search('\S+:\S*1', bucket_repl)]
        self.buckets_on_dest = [str(bucket_repl.split(":")[0]) for bucket_repl in self.bucket_topology if re.search('\S+:\S*2', bucket_repl)]
        self.repl_buckets_from_src = [str(bucket_repl.split(":")[0]) for bucket_repl in self.bucket_topology if bucket_repl.find("1>") != -1 ]
        self.repl_buckets_from_dest = [str(bucket_repl.split(":")[0]) for bucket_repl in self.bucket_topology if bucket_repl.find("<2") != -1 ]
        self._override_clusters_structure(self)
        self.queue = Queue.Queue()
        self.rep_type = self.input.param("rep_type", "xmem")
        self.upgrade_versions = self.input.param('upgrade_version', '2.5.0-1059-rel')
        self.upgrade_versions = self.upgrade_versions.split(";")
        self.ddocs_num_src = self.input.param("ddocs-num-src", 0)
        self.views_num_src = self.input.param("view-per-ddoc-src", 2)
        self.ddocs_num_dest = self.input.param("ddocs-num-dest", 0)
        self.views_num_dest = self.input.param("view-per-ddoc-dest", 2)
        self.post_upgrade_ops = self.input.param("post-upgrade-actions", None)
        self._use_encryption_after_upgrade = self.input.param("use_encryption_after_upgrade", 0)
        self.upgrade_same_version = self.input.param("upgrade_same_version", 0)
        self.ddocs_src = []
        self.ddocs_dest = []

    def xdcr_setup(self):
        XDCRNewBaseTest.setUp(self)
        self.src_cluster = self.get_cb_cluster_by_name('C1')
        self.src_master = self.src_cluster.get_master_node()
        self.src_nodes = self.src_cluster.get_nodes()
        self.dest_cluster = self.get_cb_cluster_by_name('C2')
        self.dest_master = self.dest_cluster.get_master_node()
        self.dest_nodes = self.dest_cluster.get_nodes()
        self.gen_create = BlobGenerator('loadOne', 'loadOne', self._value_size, end=self.num_items)
        self.gen_delete = BlobGenerator('loadOne', 'loadOne-', self._value_size,
            start=int((self.num_items) * (float)(100 - self._perc_del) / 100), end=self.num_items)
        self.gen_update = BlobGenerator('loadOne', 'loadOne-', self._value_size, start=0,
            end=int(self.num_items * (float)(self._perc_upd) / 100))
        self._create_buckets(self.src_cluster)
        self._create_buckets(self.dest_cluster)
        self._join_all_clusters()

    def tearDown(self):
        try:
            XDCRNewBaseTest.tearDown(self)
        finally:
            self.cluster.shutdown(force=True)

    @staticmethod
    def _override_clusters_structure(self):
        TestInputSingleton.input.clusters[0] = self.servers[:self.src_init]
        TestInputSingleton.input.clusters[1] = self.servers[self.src_init: self.src_init + self.dest_init ]


    def _create_buckets(self, cluster):
        if cluster == self.src_cluster:
            buckets = self.buckets_on_src
        else:
            buckets = self.buckets_on_dest
        bucket_size = self._get_bucket_size(cluster.get_mem_quota(), len(buckets))

        if "default" in buckets:
            cluster.create_default_bucket(bucket_size,
                                          num_replicas=self._num_replicas)

        sasl_buckets = len([bucket for bucket in buckets if bucket.startswith("sasl")])
        if sasl_buckets > 0:
            cluster.create_sasl_buckets(bucket_size, num_buckets=sasl_buckets)

        standard_buckets = len([bucket for bucket in buckets if bucket.startswith("standard")])
        if standard_buckets > 0:
            cluster.create_standard_buckets(bucket_size, num_buckets=standard_buckets)


    def _join_all_clusters(self):
        if len(self.repl_buckets_from_src):
            self.src_cluster.add_remote_cluster(self.dest_cluster,
                                                name='remote_cluster_C1-C2',
                                                encryption=self._demand_encryption)
        if len(self.repl_buckets_from_dest):
            self.dest_cluster.add_remote_cluster(self.src_cluster,
                                                name='remote_cluster_C2-C1',
                                                encryption =self._demand_encryption)
        self._replicate_clusters(
            self.src_cluster,
            self.src_cluster.get_remote_cluster_ref_by_name("remote_cluster_C1-C2"),
            self.repl_buckets_from_src)
        self._replicate_clusters(
            self.dest_cluster,
            self.dest_cluster.get_remote_cluster_ref_by_name("remote_cluster_C2-C1"),
            self.repl_buckets_from_dest)


    def _replicate_clusters(self, cluster, remote_cluster_ref, buckets):
        for bucket in buckets:
            remote_cluster_ref.create_replication(
                    cluster.get_bucket_by_name(bucket),
                    toBucket=remote_cluster_ref.get_dest_cluster().get_bucket_by_name(
                            bucket))
        remote_cluster_ref.start_all_replications()

    def _get_bucket(self, bucket_name, server):
            server_id = RestConnection(server).get_nodes_self().id
            for bucket in self.buckets:
                if bucket.name == bucket_name and bucket.master_id == server_id:
                    return bucket
            return None

    def _online_upgrade(self, update_servers, extra_servers, check_newmaster=True):
        RestConnection(update_servers[0]).get_nodes_versions()
        added_versions = RestConnection(extra_servers[0]).get_nodes_versions()
        self.cluster.rebalance(update_servers + extra_servers, extra_servers, [])
        self.log.info("Rebalance in all {0} nodes completed".format(added_versions[0]))
        RestConnection(update_servers[0]).get_nodes_versions()
        self.sleep(self.sleep_time)
        status, content = ClusterOperationHelper.find_orchestrator(update_servers[0])
        self.assertTrue(status, msg="Unable to find orchestrator: {0}:{1}".\
                        format(status, content))
        self.log.info("after rebalance in the master is {0}".format(content))
        if check_newmaster and not self.upgrade_same_version:
            FIND_MASTER = False
            for new_server in extra_servers:
                if content.find(new_server.ip) >= 0:
                    FIND_MASTER = True
                    self.log.info("{0} Node {1} becomes the master".format(added_versions[0], new_server.ip))
                    break
            if not FIND_MASTER:
                raise Exception("After rebalance in {0} Nodes, one of them doesn't become the master".format(added_versions[0]))
        self.log.info("Rebalanced out all old version nodes")
        self.cluster.rebalance(update_servers + extra_servers, [], update_servers)

    def offline_cluster_upgrade(self):
        # install on src and dest nodes
        self._install(self.servers[:self.src_init + self.dest_init ])
        upgrade_nodes = self.input.param('upgrade_nodes', "src").split(";")
        self.xdcr_setup()
        if self.initial_version < "3.0.0":
            self.pause_xdcr_cluster = None
        self.sleep(60)
        bucket = self.src_cluster.get_bucket_by_name('default')
        self._operations()
        self._load_bucket(bucket, self.src_master, self.gen_create, 'create', exp=0)
        bucket = self.src_cluster.get_bucket_by_name('sasl_bucket_1')
        self._load_bucket(bucket, self.src_master, self.gen_create, 'create', exp=0)
        bucket = self.dest_cluster.get_bucket_by_name('standard_bucket_1')
        gen_create2 = BlobGenerator('loadTwo', 'loadTwo', self._value_size, end=self.num_items)
        self._load_bucket(bucket, self.dest_master, gen_create2, 'create', exp=0)
        nodes_to_upgrade = []
        if "src" in upgrade_nodes :
            nodes_to_upgrade += self.src_nodes
        if "dest" in upgrade_nodes :
            nodes_to_upgrade += self.dest_nodes

        self.sleep(60)
        self._wait_for_replication_to_catchup()
        if self.pause_xdcr_cluster:
            for cluster in self.get_cb_clusters():
                for remote_cluster in cluster.get_remote_clusters():
                    remote_cluster.pause_all_replications()
        self._offline_upgrade(nodes_to_upgrade)

        if self._use_encryption_after_upgrade and "src" in upgrade_nodes and "dest" in upgrade_nodes and self.upgrade_versions[0] >= "2.5.0":
            if "src" in self._use_encryption_after_upgrade:
                for remote_cluster in self.src_cluster.get_remote_clusters():
                    remote_cluster._modify()
            if "dest" in self._use_encryption_after_upgrade:
                for remote_cluster in self.dest_cluster.get_remote_clusters():
                    remote_cluster._modify()
        self.sleep(60)
        bucket = self.src_cluster.get_bucket_by_name('sasl_bucket_1')
        gen_create3 = BlobGenerator('loadThree', 'loadThree', self._value_size, end=self.num_items)
        self._load_bucket(bucket, self.src_master, gen_create3, 'create', exp=0)
        bucket = self.dest_cluster.get_bucket_by_name('sasl_bucket_1')
        gen_create4 = BlobGenerator('loadFour', 'loadFour', self._value_size, end=self.num_items)
        self._load_bucket(bucket, self.dest_master, gen_create4, 'create', exp=0)
        if self.pause_xdcr_cluster:
            for cluster in self.get_cb_clusters():
                for remote_cluster in cluster.get_remote_clusters():
                    remote_cluster.resume_all_replications()
        bucket = self.src_cluster.get_bucket_by_name('default')
        self._load_bucket(bucket, self.src_master, gen_create2, 'create', exp=0)
        self.merge_all_buckets()
        self.sleep(60)
        self._post_upgrade_ops()
        self.sleep(60)
        self.verify_results()
        self.max_verify = None
        if self.ddocs_src:
            for bucket_name in self.buckets_on_src:
                bucket = self.src_cluster.get_bucket_by_name(bucket_name)
                expected_rows = sum([len(kv_store) for kv_store in bucket.kvs.values()])
                self._verify_ddocs(expected_rows, [bucket_name], self.ddocs_src, self.src_master)

        if self.ddocs_dest:
            for bucket_name in self.buckets_on_dest:
                bucket = self.dest_cluster.get_bucket_by_name(bucket_name)
                expected_rows = sum([len(kv_store) for kv_store in bucket.kvs.values()])
                self._verify_ddocs(expected_rows, [bucket_name], self.ddocs_dest, self.dest_master)

    def online_cluster_upgrade(self):
        self._install(self.servers[:self.src_init + self.dest_init])
        prev_initial_version = self.initial_version
        self.initial_version = self.upgrade_versions[0]
        self._install(self.servers[self.src_init + self.dest_init:])
        self.xdcr_setup()
        if prev_initial_version < "3.0.0":
            self.pause_xdcr_cluster = None
        bucket_default = self.src_cluster.get_bucket_by_name('default')
        bucket_sasl = self.src_cluster.get_bucket_by_name('sasl_bucket_1')
        bucket_standard = self.dest_cluster.get_bucket_by_name('standard_bucket_1')
        self._load_bucket(bucket_default, self.src_master, self.gen_create, 'create', exp=0)
        self._load_bucket(bucket_sasl, self.src_master, self.gen_create, 'create', exp=0)
        self._load_bucket(bucket_standard, self.dest_master, self.gen_create, 'create', exp=0)
        gen_create2 = BlobGenerator('loadTwo', 'loadTwo-', self._value_size, end=self.num_items)
        self._load_bucket(bucket_sasl, self.dest_master, gen_create2, 'create', exp=0)
        if self.pause_xdcr_cluster:
            for cluster in self.get_cb_clusters():
                for remote_cluster in cluster.get_remote_clusters():
                    remote_cluster.pause_all_replications()
        self._online_upgrade(self.src_nodes, self.servers[self.src_init + self.dest_init:])
        self._install(self.src_nodes)
        self._online_upgrade(self.servers[self.src_init + self.dest_init:], self.src_nodes, False)

        self._load_bucket(bucket_default, self.src_master, self.gen_delete, 'delete', exp=0)
        self._load_bucket(bucket_default, self.src_master, self.gen_update, 'create', exp=self._expires)
        self._load_bucket(bucket_sasl, self.src_master, self.gen_delete, 'delete', exp=0)
        self._load_bucket(bucket_sasl, self.src_master, self.gen_update, 'create', exp=self._expires)
        self.sleep(120)

        self._install(self.servers[self.src_init + self.dest_init:])
        self.sleep(60)
        self._online_upgrade(self.dest_nodes, self.servers[self.src_init + self.dest_init:])
        self._install(self.dest_nodes)
        self.sleep(60)
        self._online_upgrade(self.servers[self.src_init + self.dest_init:], self.dest_nodes, False)

        self._load_bucket(bucket_standard, self.dest_master, self.gen_delete, 'delete', exp=0)
        self._load_bucket(bucket_standard, self.dest_master, self.gen_update, 'create', exp=self._expires)
        if self.pause_xdcr_cluster:
            for cluster in self.get_cb_clusters():
                for remote_cluster in cluster.get_remote_clusters():
                    remote_cluster.resume_all_replications()
        self.merge_all_buckets()
        bucket_sasl = self.dest_cluster.get_bucket_by_name('sasl_bucket_1')
        gen_delete2 = BlobGenerator('loadTwo', 'loadTwo-', self._value_size,
            start=int((self.num_items) * (float)(100 - self._perc_del) / 100), end=self.num_items)
        gen_update2 = BlobGenerator('loadTwo', 'loadTwo-', self._value_size, start=0,
            end=int(self.num_items * (float)(self._perc_upd) / 100))
        self._load_bucket(bucket_sasl, self.dest_master, gen_delete2, 'delete', exp=0)
        self._load_bucket(bucket_sasl, self.dest_master, gen_update2, 'create', exp=self._expires)

        self.merge_all_buckets()
        self.sleep(120)
        self._post_upgrade_ops()
        self.sleep(120)
        self.verify_results()
        self.max_verify = None
        if self.ddocs_src:
            for bucket_name in self.buckets_on_src:
                bucket = self.src_cluster.get_bucket_by_name(bucket_name)
                expected_rows = sum([len(kv_store) for kv_store in bucket.kvs.values()])
                self._verify_ddocs(expected_rows, [bucket_name], self.ddocs_src, self.src_master)

        if self.ddocs_dest:
            for bucket_name in self.buckets_on_dest:
                bucket = self.dest_cluster.get_bucket_by_name(bucket_name)
                expected_rows = sum([len(kv_store) for kv_store in bucket.kvs.values()])
                self._verify_ddocs(expected_rows, [bucket_name], self.ddocs_dest, self.dest_master)

    def incremental_offline_upgrade(self):
        upgrade_seq = self.input.param("upgrade_seq", "src>dest")
        self._install(self.servers[:self.src_init + self.dest_init ])
        self.xdcr_setup()
        self.sleep(60)
        bucket = self.src_cluster.get_bucket_by_name('default')
        self._load_bucket(bucket, self.src_master, self.gen_create, 'create', exp=0)
        bucket = self.src_cluster.get_bucket_by_name('sasl_bucket_1')
        self._load_bucket(bucket, self.src_master, self.gen_create, 'create', exp=0)
        bucket = self.dest_cluster.get_bucket_by_name('sasl_bucket_1')
        gen_create2 = BlobGenerator('loadTwo', 'loadTwo', self._value_size, end=self.num_items)
        self._load_bucket(bucket, self.dest_master, gen_create2, 'create', exp=0)
        self.sleep(self.wait_timeout)
        self._wait_for_replication_to_catchup()
        nodes_to_upgrade = []
        if upgrade_seq == "src>dest":
            nodes_to_upgrade = copy.copy(self.src_nodes)
            nodes_to_upgrade.extend(self.dest_nodes)
        elif upgrade_seq == "src<dest":
            nodes_to_upgrade = copy.copy(self.dest_nodes)
            nodes_to_upgrade.extend(self.src_nodes)
        elif upgrade_seq == "src><dest":
            min_cluster = min(len(self.src_nodes), len(self.dest_nodes))
            for i in xrange(min_cluster):
                nodes_to_upgrade.append(self.src_nodes[i])
                nodes_to_upgrade.append(self.dest_nodes[i])

        for _seq, node in enumerate(nodes_to_upgrade):
            self._offline_upgrade([node])
            self.sleep(60)
            bucket = self.src_cluster.get_bucket_by_name('sasl_bucket_1')
            itemPrefix = "loadThree" + _seq * 'a'
            gen_create3 = BlobGenerator(itemPrefix, itemPrefix, self._value_size, end=self.num_items)
            self._load_bucket(bucket, self.src_master, gen_create3, 'create', exp=0)
            bucket = self.src_cluster.get_bucket_by_name('default')
            itemPrefix = "loadFour" + _seq * 'a'
            gen_create4 = BlobGenerator(itemPrefix, itemPrefix, self._value_size, end=self.num_items)
            self._load_bucket(bucket, self.src_master, gen_create4, 'create', exp=0)
            self.sleep(60)
        self.src_cluster.merge_all_buckets()
        self.verify_results()
        self.sleep(self.wait_timeout * 5, "Let clusters work for some time")

    def _operations(self):
        # TODO: there are not tests with views
        if self.ddocs_num_src:
            ddocs = self._create_views(self.ddocs_num_src, self.buckets_on_src,
                                       self.views_num_src, self.src_master)
            self.ddocs_src.extend(ddocs)
        if self.ddocs_num_dest:
            ddocs = self._create_views(self.ddocs_num_dest, self.buckets_on_dest,
                                       self.views_num_dest, self.dest_master)
            self.ddocs_dest.extend(ddocs)

    def _verify(self, expected_rows):
        if self.ddocs_src:
            self._verify_ddocs(expected_rows, self.buckets_on_src, self.ddocs_src, self.src_master)

        if self.ddocs_dest:
            self._verify_ddocs(expected_rows, self.buckets_on_dest, self.ddocs_dest, self.dest_master)

    def _create_views(self, ddocs_num, buckets, views_num, server):
        ddocs = []
        if ddocs_num:
            self.default_view = View(self.default_view_name, None, None)
            for bucket in buckets:
                for i in xrange(ddocs_num):
                    views = self.make_default_views(self.default_view_name, views_num,
                                                    self.is_dev_ddoc, different_map=True)
                    ddoc = DesignDocument(self.default_view_name + str(i), views)
                    bucket_server = self._get_bucket(bucket, server)
                    tasks = self.async_create_views(server, ddoc.name, views, bucket=bucket_server)
                    for task in tasks:
                        task.result(timeout=90)
                    ddocs.append(ddoc)
        return ddocs

    def _verify_ddocs(self, expected_rows, buckets, ddocs, server):
        query = {"connectionTimeout" : 60000}
        if self.max_verify:
            expected_rows = self.max_verify
            query["limit"] = expected_rows
        for bucket in buckets:
            for ddoc in ddocs:
                prefix = ("", "dev_")[ddoc.views[0].dev_view]
                bucket_server = self._get_bucket(bucket, server)
                self.perform_verify_queries(len(ddoc.views), prefix, ddoc.name, query, bucket=bucket_server,
                                           wait_time=self.wait_timeout * 5, expected_rows=expected_rows,
                                           retry_time=10, server=server)

    def _post_upgrade_ops(self):
        if self.post_upgrade_ops:
            for op_cluster in self.post_upgrade_ops.split(';'):
                cluster, op = op_cluster.split('-')
                if op == 'rebalancein':
                    free_servs = [ser for ser in self.servers
                                  if not (ser in self.src_nodes or ser in self.dest_nodes)]
                    servers_to_add = free_servs[:self.nodes_in]
                    if servers_to_add:
                        temp = self.initial_version
                        self.initial_version = self.upgrade_versions[0]
                        self._install(servers_to_add)
                        self.initial_version = temp
                    if cluster == 'src':
                        self.cluster.rebalance(self.src_nodes, servers_to_add, [])
                        self.src_nodes.extend(servers_to_add)
                    elif cluster == 'dest':
                        self.cluster.rebalance(self.dest_nodes, servers_to_add, [])
                        self.dest_nodes.extend(servers_to_add)
                elif op == 'rebalanceout':
                    if cluster == 'src':
                        rebalance_out_candidates = filter(lambda node: node != self.src_master, self.src_nodes)
                        self.cluster.rebalance(self.src_nodes, [], rebalance_out_candidates[:self.nodes_out])
                        for node in rebalance_out_candidates[:self.nodes_out]:
                            self.src_nodes.remove(node)
                    elif cluster == 'dest':
                        rebalance_out_candidates = filter(lambda node: node != self.dest_master, self.dest_nodes)
                        self.cluster.rebalance(self.dest_nodes, [], rebalance_out_candidates[:self.nodes_out])
                        for node in rebalance_out_candidates[:self.nodes_out]:
                            self.dest_nodes.remove(node)
                if op == 'create_index':
                    ddoc_num = 1
                    views_num = 2
                    if cluster == 'src':
                        ddocs = self._create_views(ddoc_num, self.buckets_on_src,
                                       views_num, self.src_master)
                        self.ddocs_src.extend(ddocs)
                    elif cluster == 'dest':
                        ddocs = self._create_views(ddoc_num, self.buckets_on_dest,
                                       views_num, self.dest_master)
                        self.ddocs_dest.extend(ddocs)

    def _offline_upgrade(self, servers):
        for upgrade_version in self.upgrade_versions:
            for server in servers:
                    remote = RemoteMachineShellConnection(server)
                    remote.stop_server()
                    remote.disconnect()
            upgrade_threads = self._async_update(upgrade_version, servers)
            #wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed!")
            self.sleep(self.expire_time)
