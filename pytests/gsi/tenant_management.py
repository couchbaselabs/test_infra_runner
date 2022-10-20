"""tenant_management.py: "This class test cluster affinity  for GSI"

__author__ = "Hemant Rajput"
__maintainer = "Hemant Rajput"
__email__ = "Hemant.Rajput@couchbase.com"
__git_user__ = "hrajput89"
__created_on__ = "08/04/22 12:27 pm"

"""
import random
import os

from couchbase_helper.query_definitions import QueryDefinition
from gsi.base_gsi import BaseSecondaryIndexingTests
from gsi.collections_concurrent_indexes import powerset
from membase.api.on_prem_rest_client import RestHelper, RestConnection
from serverless.gsi_utils import GSIUtils
from testconstants import INDEX_MAX_CAP_PER_TENANT, INDEX_SUB_CLUSTER_LENGTH
from remote.remote_util import RemoteMachineShellConnection, RemoteMachineHelper


class TenantManagement(BaseSecondaryIndexingTests):
    def setUp(self):
        super(TenantManagement, self).setUp()
        self.log.info("==============  TenantManagement setup has started ==============")
        self.rest.delete_all_buckets()
        self.use_defer_build = self.input.param("use_defer_build", False)
        self.index_field_set = powerset(['age', 'city', 'country', 'title', 'firstName', 'lastName', 'streetAddress',
                                         'suffix', 'filler1', 'phone', 'zipcode'])
        self.batch_size = self.input.param("batch_size", 1000)
        self.node_to_swap = self.input.param("node_to_swap", 1)
        self.json_template = self.input.param("json_template", "Person")
        self.recover_failed_over_node = self.input.param("recover_failed_over_node", False)
        self.gsi_util_obj = GSIUtils(self.run_cbq_query)
        self.namespaces = []
        self._create_server_groups()
        self.query_node = self.get_nodes_from_services_map(service_type="n1ql", get_all_nodes=True)[0]

    def tearDown(self):
        self.log.info("==============  TenantManagement tearDown has started ==============")
        super(TenantManagement, self).tearDown()
        self.log.info("==============  TenantManagement tearDown has completed ==============")

    def suite_tearDown(self):
        pass

    def suite_setUp(self):
        pass

    def test_cluster_affinity(self):
        self.prepare_tenants()
        self.validate_tenant_management(check='cluster_affinity')
        self.validate_tenant_management(check='index_count')
        self.validate_tenant_management(check='index_distribution')

    def test_index_cap_per_tenant(self):
        self.prepare_tenants(index_creations=False)
        for collection_namespace in self.namespaces:
            for item, index_field in zip(range(self.initial_index_num), self.index_field_set):
                if self.use_defer_build:
                    defer_build = random.choice([True, False])
                else:
                    defer_build = False
                idx = f'idx_{item}_{collection_namespace.split(":")[1].replace(".", "_")}'
                index_gen = QueryDefinition(index_name=idx, index_fields=index_field)
                query = index_gen.generate_index_create_query(namespace=collection_namespace,
                                                              defer_build=defer_build)
                try:
                    self.run_cbq_query(query=query, server=self.query_node)
                except Exception as err:
                    error = 'Build Already In Progress'
                    err_limit = 'Limit for number of indexes that can be created per bucket has been reached.'
                    if error not in str(err):
                        if err_limit in str(err):
                            self.log.info(f"Index created: {item}")
                            break
                        self.fail(err)

                self.wait_until_indexes_online(defer_build=True)

    def test_rebalance_sub_cluster(self):
        self.prepare_tenants()
        self.validate_tenant_management(check="subcluster_reblance_swap")

    def test_failed_over_sub_cluster(self):
        self.prepare_tenants()
        self.validate_tenant_management(check="failed_over_subcluster")

    def _check_cluster_affinity(self, idx_host_list_dict):
        # Validating the cluster affinity
        for bucket in idx_host_list_dict:
            self.assertEqual(len(idx_host_list_dict[bucket]), 2,
                             "Indexes are hosted on more than 2 node of sub-cluster")

    def _check_index_count(self, indexer_metadata, num_indexes=None):
        if num_indexes:
            num_of_indexes = num_indexes
        else:
            # (No. of indexes created by Tenant + system primary indexes) * num index replicas (1)
            indexes_per_tenant = self.num_scopes * self.num_collections * self.gsi_util_obj.batch_size
            if indexes_per_tenant > INDEX_MAX_CAP_PER_TENANT:
                indexes_per_tenant = INDEX_MAX_CAP_PER_TENANT
            num_of_indexes = self.num_of_tenants * indexes_per_tenant * 2
            num_of_indexes += self.num_of_tenants * 2  # primary indexes for _system scope
        self.assertEqual(len(indexer_metadata), num_of_indexes,
                         "No. of expected indexes with replica not matching")

    def _check_index_distribution(self, idx_host_list_dict, sub_cluster_list):
        # Validating uniform distribution across sub-clusters
        if len(self.buckets) > 1:
            self.assertTrue(len(sub_cluster_list) > 1, "Tenant are not distributed across sub-clusters")

    def _check_subcluster_rebalance_swap(self, sub_cluster_list, num_of_nodes_to_remove=1):
        # identifying the node for the subcluster for different  availability zone
        remove_nodes = []
        remove_node_ips = [node.split(':')[0] for node in sub_cluster_list[0]]
        for remove_node_ip in remove_node_ips:
            for server in self.servers:
                if remove_node_ip == server.ip:
                    remove_nodes.append(server)
                    break
            if len(remove_nodes) == num_of_nodes_to_remove:
                break

        # Find Server group of removing nodes
        nodes_zone_dict = {}
        zone_info = self.rest.get_zone_and_nodes()
        for zone_name, node_list in zone_info.items():
            for node_ip in node_list:
                nodes_zone_dict[node_ip] = zone_name

        remove_nodes_zone_dict = {}
        for node in remove_nodes:
            remove_nodes_zone_dict[node.ip] = nodes_zone_dict[node.ip]

        # Adding new node to the same availability zone from where node is being removed
        # and re-balance out one other node
        for counter, server in enumerate(remove_nodes_zone_dict):
            self.rest.add_node(user=self.rest.username, password=self.rest.password,
                               remoteIp=self.servers[self.nodes_init + counter].ip,
                               zone_name=remove_nodes_zone_dict[server],
                               services=['index'])
        rebalance_task = self.cluster.async_rebalance(servers=self.servers[:self.nodes_init],
                                                      to_add=[],
                                                      to_remove=remove_nodes,
                                                      services=['index'] * len(remove_nodes))
        rebalance_task.result()
        rebalance_status = RestHelper(self.rest).rebalance_reached()
        self.assertTrue(rebalance_status, "rebalance failed, stuck or did not complete")

        # Getting the new configuration of cluster
        index_node = self.get_nodes_from_services_map(service_type="index", get_all_nodes=True)[0]
        self.index_rest = RestConnection(index_node)
        new_sub_cluster_list = []
        new_idx_host_list_dict = {str(bucket): set() for bucket in self.buckets}
        indexer_metadata = self.index_rest.get_indexer_metadata()['status']

        for idx in indexer_metadata:
            idx_bucket = idx['bucket']
            idx_host = idx['hosts'][0]
            new_idx_host_list_dict[idx_bucket].add(idx_host)

        for bucket in new_idx_host_list_dict:
            host_list = sorted(list(new_idx_host_list_dict[bucket]))
            if host_list not in new_sub_cluster_list:
                new_sub_cluster_list.append(host_list)

        # validating if swap-rebalance has adhere to Tenant rules
        new_zone_info = self.rest.get_zone_and_nodes()
        for sub_cluster in new_sub_cluster_list:
            zone_1, zone_2 = None, None

            self.assertEqual(len(sub_cluster), INDEX_SUB_CLUSTER_LENGTH,
                             f"Indexes distributed on sub-cluster of more"
                             f"than 2 index nodes: {new_sub_cluster_list}")

            for node in sub_cluster:
                node_ip = node.split(':')[0]
                for server_group in new_zone_info:
                    if node_ip in new_zone_info[server_group] and zone_1 is None:
                        zone_1 = server_group
                    elif node_ip in new_zone_info[server_group] and zone_2 is None:
                        zone_2 = server_group
                    elif zone_1 and zone_2:
                        break
            self.assertNotEqual(zone_1, zone_2,
                                f"Both the index nodes belong to same availability zone. {indexer_metadata}")
        self.gsi_util_obj.check_s3_cleanup(aws_access_key_id=self.aws_access_key_id,
                                           aws_secret_access_key=self.aws_secret_access_key,
                                           s3_bucket=self.s3_bucket, region=self.region)

    def _check_failed_over_subcluster(self, sub_cluster_list, num_of_nodes_to_remove=1, failover_recovery=False):
        # identifying the node for the subcluster for different  availability zone
        failover_nodes = []
        failover_nodes_ips = [node.split(':')[0] for node in sub_cluster_list[0]]
        for failover_nodes_ip in failover_nodes_ips:
            for server in self.servers:
                if failover_nodes_ip == server.ip:
                    failover_nodes.append(server)
                    break
            if len(failover_nodes) == num_of_nodes_to_remove:
                break

        # Find Server group of removing nodes
        nodes_zone_dict = {}
        zone_info = self.rest.get_zone_and_nodes()
        for zone_name, node_list in zone_info.items():
            for node_ip in node_list:
                nodes_zone_dict[node_ip] = zone_name

        failover_nodes_zones_dict = {}
        for node in failover_nodes:
            failover_nodes_zones_dict[node.ip] = nodes_zone_dict[node.ip]

        # Adding new node to the same availability zone from where node is being removed
        # and re-balance out one other node
        for counter, server in enumerate(failover_nodes_zones_dict):
            otp_node = f'ns_1@{server}'
            recovery_type = random.choice(['full', 'delta'])
            self.rest.fail_over(otpNode=otp_node)
            if self.recover_failed_over_node:
                self.sleep(60, "Waiting for a min after failing the node and ")
                self.rest.set_recovery_type(otpNode=otp_node, recoveryType=recovery_type)
            else:
                self.rest.add_node(user=self.rest.username, password=self.rest.password,
                                   remoteIp=self.servers[self.nodes_init + counter].ip,
                                   zone_name=failover_nodes_zones_dict[server],
                                   services=['index'])
        rebalance_task = self.cluster.async_rebalance(servers=self.servers[:self.nodes_init],
                                                      to_add=[], to_remove=[],
                                                      services=[])
        rebalance_task.result()
        rebalance_status = RestHelper(self.rest).rebalance_reached()
        self.assertTrue(rebalance_status, "rebalance failed, stuck or did not complete")

        # Getting the new configuration of cluster
        new_sub_cluster_list = []
        new_idx_host_list_dict = {str(bucket): set() for bucket in self.buckets}
        indexer_metadata = self.index_rest.get_indexer_metadata()['status']

        for idx in indexer_metadata:
            idx_bucket = idx['bucket']
            idx_host = idx['hosts'][0]
            new_idx_host_list_dict[idx_bucket].add(idx_host)

        for bucket in new_idx_host_list_dict:
            host_list = sorted(list(new_idx_host_list_dict[bucket]))
            if host_list not in new_sub_cluster_list:
                new_sub_cluster_list.append(host_list)

        if failover_recovery:
            self.assertEqual(sorted(sub_cluster_list), sorted(new_sub_cluster_list))
        else:
            # validating if swap-rebalance has adhere to Tenant rules
            new_zone_info = self.rest.get_zone_and_nodes()
            for new_sub_cluster in new_sub_cluster_list:
                zone_1, zone_2 = None, None

                self.assertEqual(len(new_sub_cluster_list), INDEX_SUB_CLUSTER_LENGTH,
                                 f"Indexes distributed on sub-cluster of more"
                                 f"than 2 index nodes: {new_sub_cluster_list}")

                for node in new_sub_cluster:
                    node_ip = node.split(':')[0]
                    for server_group in new_zone_info:
                        if node_ip in new_zone_info[server_group] and zone_1 is None:
                            zone_1 = server_group
                        elif node_ip in new_zone_info[server_group] and zone_2 is None:
                            zone_2 = server_group
                        elif zone_1 and zone_2:
                            break
                self.assertNotEqual(zone_1, zone_2,
                                    f"Both the index nodes belong to same availability zone. {indexer_metadata}")

    def validate_tenant_management(self, check):
        indexer_metadata = self.index_rest.get_indexer_metadata()['status']
        idx_host_list_dict = {str(bucket): set() for bucket in self.buckets}
        tenant_idx_dict = {str(bucket): [] for bucket in self.buckets}
        sub_cluster_list = list()

        for idx in indexer_metadata:
            idx_bucket = idx['bucket']
            idx_host = idx['hosts'][0]
            idx_name = idx['name']
            idx_host_list_dict[idx_bucket].add(idx_host)
            tenant_idx_dict[idx_bucket].append(idx_name)

        for bucket in idx_host_list_dict:
            host_list = sorted(list(idx_host_list_dict[bucket]))
            if host_list not in sub_cluster_list:
                sub_cluster_list.append(host_list)

        if check == 'cluster_affinity':
            self._check_cluster_affinity(idx_host_list_dict)

        elif check == "index_count":
            self._check_index_count(indexer_metadata=indexer_metadata)

        elif check == 'index_distribution':
            self._check_index_distribution(idx_host_list_dict=idx_host_list_dict, sub_cluster_list=sub_cluster_list)

        elif check == "subcluster_reblance_swap":
            self._check_subcluster_rebalance_swap(sub_cluster_list=sub_cluster_list,
                                                  num_of_nodes_to_remove=self.node_to_swap)

        elif check == "failed_over_subcluster":
            self._check_failed_over_subcluster(sub_cluster_list=sub_cluster_list,
                                               num_of_nodes_to_remove=self.node_to_swap)
