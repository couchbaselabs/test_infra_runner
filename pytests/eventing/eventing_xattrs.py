from lib.couchbase_helper.stats_tools import StatsCommon
from lib.couchbase_helper.tuq_helper import N1QLHelper
from lib.membase.api.rest_client import RestConnection
from lib.testconstants import STANDARD_BUCKET_PORT
from lib.couchbase_helper.documentgenerator import JSONNonDocGenerator, BlobGenerator
from pytests.fts.vector_dataset_generator.vector_dataset_loader import GoVectorLoader
from pytests.eventing.eventing_base import EventingBaseTest
import logging
import json


log = logging.getLogger()

class EventingXattrs(EventingBaseTest):
    def setUp(self):
        super(EventingXattrs, self).setUp()
        self.src_bucket = self.rest.get_bucket_by_name(self.src_bucket_name)
        self.handler_code = self.input.param('handler_code', None)
        self.handler_code_duplicate = self.input.param('handler_code_duplicate', None)
        self.docs_per_day = self.input.param('doc-per-day', 20)

    def tearDown(self):
        super(EventingXattrs, self).tearDown()
        
    def test_xattrs_functional_testing(self):
        body = self.create_save_function_body(self.function_name, self.handler_code)
        self.load_data_to_collection(self.docs_per_day*self.num_docs, "src_bucket._default._default")
        self.deploy_function(body)
        self.verify_doc_count_collections("dst_bucket._default._default", self.docs_per_day*self.num_docs*2)
        self.undeploy_and_delete_function(body)
        
    def test_xattrs_parameters(self):
        body = self.create_save_function_body(self.function_name, self.handler_code)
        self.load_data_to_collection(self.docs_per_day * self.num_docs, "src_bucket._default._default")
        self.deploy_function(body)
        self.verify_doc_count_collections("dst_bucket._default._default", self.docs_per_day * self.num_docs)
        self.undeploy_and_delete_function(body)
        body = self.create_save_function_body(self.function_name, self.handler_code_duplicate)
        self.load_data_to_collection(self.docs_per_day * self.num_docs, "src_bucket._default._default")
        self.deploy_function(body)
        self.verify_doc_count_collections("dst_bucket._default._default", self.docs_per_day * self.num_docs)
        self.undeploy_and_delete_function(body)
        
    def test_xattrs_sbm(self):
        body = self.create_save_function_body(self.function_name, self.handler_code)
        self.load_data_to_collection(self.docs_per_day*self.num_docs, "src_bucket._default._default")
        self.deploy_function(body)
        self.verify_doc_count_collections("src_bucket._default._default", self.docs_per_day*self.num_docs*2)
        self.undeploy_and_delete_function(body)



