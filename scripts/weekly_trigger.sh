set +e     # keep going even if there is a shell error e.g. bad wget

${TRIGGER_WEEKLY_JOBS:=false}
${TRIGGER_UPGRADE_JOBS:=false}
${TRIGGER_WEEKLY_MAGMA_JOBS:=false}
${TRIGGER_OS_CERT_JOBS:=false}

echo "TRIGGER_WEEKLY_JOBS=$TRIGGER_WEEKLY_JOBS"
echo "TRIGGER_UPGRADE_JOBS=$TRIGGER_UPGRADE_JOBS"
echo "TRIGGER_WEEKLY_MAGMA_JOBS=$TRIGGER_WEEKLY_MAGMA_JOBS"
echo "TRIGGER_OS_CERT_JOBS=$TRIGGER_OS_CERT_JOBS"

sleep_with_message() {
  echo "Sleep for $1 seconds"
  sleep $1
}

if [ "$TRIGGER_WEEKLY_JOBS" == "true" ]; then
  # For releases < 7.6 only couchstore will be taken (irrespective of the storage you select)
  # For magma triggers < 7.6, need to do it manually / special triggers
  bucket_storage_param=""
  bucket_storage_with_extra_params=""
  if [ "$kv_storage" != "magma" ]; then
    bucket_storage_param=",bucket_storage=$kv_storage"
    bucket_storage_with_extra_params="&extraParameters=bucket_storage=$kv_storage"
  fi

  echo "### Running jobs on Debian"
  echo "### Triggering 2i weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=2i&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log
  sleep_with_message 120

  echo "### Triggering n/w failover and slow disk autofailover scenarios ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=failover_network&url=$url&serverPoolId=failover&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log
  sleep_with_message 900

  echo "### Triggering Durability weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=durability,transaction&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log
  sleep_with_message 600

  echo "### Triggering XDCR weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=xdcr&retries=2&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log
  sleep_with_message 600

  echo "### Triggering query,ephemeral,backup_recovery,logredaction,cli weekly jobs, cbo_focus_suites,join_enum ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=query&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log
  #cgroups tests added for GSI
  wget "http://qa.sc.couchbase.com/job/ubuntu-gsi_cgroup-limits/buildWithParameters?token=trigger_weekly_cgroups&version_number=$version_number" -O trigger.log
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=ephemeral,backup_recovery&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=cli,cli_imex&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=logredaction,subdoc&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log

  sleep_with_message 1200

  #echo "### Triggering plasma jobs ###"
  #wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=plasma&url=$url&serverPoolId=magmanew&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log

  echo "### Triggering Analytics, Eventing, FTS, Views and Geo weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=analytics,eventing&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True" -O trigger.log
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=view,geo&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True,bucket_storage=couchstore" -O trigger.log
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=fts&url=$url&serverPoolId=regression&branch=$branch&addPoolId=elastic-fts&extraParameters=get-cbcollect-info=True" -O trigger.log
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=vector_search_large&component=fts&url=$url&serverPoolId=magmanew&branch=$branch&addPoolId=None&extraParameters=get-cbcollect-info=True" -O trigger.log

  sleep_with_message 600

  echo "### Triggering nserv_sec weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=nserv_sec&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log

  # echo "### Triggering RBAC Upgrade jobs ###"
  # wget  "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=12hour&component=cli&subcomponent=offline-upgrade-rbac&url=$url&serverPoolId=regression&branch=$branch&addPoolId=elastic-fts"

  echo "### Triggering rbac weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=rbac&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log

  #CE only
  echo "### Triggering CE only weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=ce_only&subcomponent=1a,1b&url=$url&serverPoolId=regression&branch=$branch&executor_job_parameters=installParameters=edition=community" -O trigger.log

  echo "### Triggering nserv weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=nserv&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log

  echo "### Triggering tunable,epeng,rza weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=tunable,epeng,rza&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log

  echo "### Triggering CE weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=ce&component=query&url=$url&serverPoolId=regression&branch=$branch&executor_job_parameters=installParameters=edition=community${bucket_storage_with_extra_params}" -O trigger.log
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=ce&component=2i&url=$url&serverPoolId=regression&branch=$branch&executor_job_parameters=installParameters=edition=community${bucket_storage_with_extra_params}" -O trigger.log

  echo "### Triggering RQG jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=rqg&url=$url&serverPoolId=regression&branch=${branch}${bucket_storage_with_extra_params}" -O trigger.log

  echo "### Triggering AiQG jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=aiqg&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True${bucket_storage_param}" -O trigger.log

  echo "### Triggering Collections weekly jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=$version_number&suite=$suite&component=collections&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True,log_level=info${bucket_storage_param}" -O trigger.log
  sleep_with_message 600

  # echo "### Triggering IPV6 weekly jobs ###"
  # wget "http://qa.sc.couchbase.com/job/trigger_ipv6_weekly_jobs/buildWithParameters?token=trigger_all&version_number=$version_number&run_centos=$run_centos&run_windows=$run_windows&url=$url&branch=$branch"

  wget "http://qa.sc.couchbase.com/job/centos-query_cbo-focus-suites/buildWithParameters?token=trigger&version_number=$version_number" -O trigger.log
  wget "http://qa.sc.couchbase.com/job/centos-query_join_enum/buildWithParameters?token=trigger&version_number=$version_number" -O trigger.log

  # trigger ent bkrs bwc jobs
  wget  "http://qa.sc.couchbase.com/job/trigger_ent_bkrs_jobs/buildWithParameters?token=trigger_all&version_number=$version_number&url=$url&branch=$branch" -O trigger.log

  echo "### Trigerring ep_engine daily job ###"
  wget "http://qa.sc.couchbase.com/job/trigger_ep_engine_jobs/buildWithParameters?token=trigger_all&version_number=$version_number&run_centos=true&url=$url&branch=$branch${bucket_storage_with_extra_params}" -O trigger.log

  # breakpad tested in ubuntu but put centos in its name to be capture in greenboard in centos
  # Ashok disabling/commenting following job after discussing with Bala and Ashwin on 17th July.
  #wget  "http://qa.sc.couchbase.com/job/centos-u1604-p0-breakpad-sanity/buildWithParameters?token=extended_sanity&version_number=$version_number&url=$url&branch=master" -O trigger.log

  echo "### Triggering xAttr jobs - Convergence ###"
  # Ashok disabling/commenting following job after discussing with Bala and Ashwin on 17th July.
  # wget  "http://qa.sc.couchbase.com/job/cen006-P0-converg-xattrs-vset0-00-subdoc-nested-data/buildWithParameters?token=trigger_all&version_number=$version_number&url=$url&branch=$branch${bucket_storage_with_extra_params}"
  wget "http://qa.sc.couchbase.com/job/cen006-P0-converg-xattrs-vset0-01-subdoc-sdk/buildWithParameters?token=trigger_all&version_number=$version_number&url=$url&branch=$branch${bucket_storage_with_extra_params}" -O trigger.log

  echo "### Trigerring EEOnly daily job ###"
  wget "http://qa.sc.couchbase.com/job/cen006-p0-EEonly-vset00-00-feature/buildWithParameters?token=trigger_all&version_number=$version_number&branch=$branch" -O trigger.log

  echo "### Trigerring Auto Failover weekly job ###"
  wget "http://qa.sc.couchbase.com/job/deb12-nserv-autofailover-server-stop/buildWithParameters?token=trigger_all&version_number=$version_number&url=$url&testrunner_tag=$testrunner_tag&branch=$branch" -O trigger.log

  sleep_with_message 120

  # Trigger Analytics, Eventing, FTS, Views and Geo tests only for Couchstore KV Storage
  echo "### Triggering Mobile jobs ###"
  curl -X POST http://trigger:dd2b75aa552ae7c9a59ce1ea5af93f2b@uberjenkins.sc.couchbase.com:8080/job/_couchbase-server-upstream/buildWithParameters\?token\=trigger_all\&COUCHBASE_SERVER_VERSION\=$version_number
  # centos upgrade
  echo "### Triggering Upgrade jobs ###"
  # Mihir : 9/3/21 : Temporarily commenting this trigger as it needs some fixing
  # Thuan: 9/20/2021 : Moved extra params to job config in test suite.  Trigger is ok now
  # wget  "http://qa.sc.couchbase.com/job/trigger_upgrade_jobs/buildWithParameters?token=trigger_all&version_number=$version_number&url=$url&branch=$branch&addPoolId=elastic-fts" -O trigger.log
  ## wget  "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=centos&version_number=$version_number&suite=weekly&component=upgrade&url=$url&serverPoolId=regression&branch=$branch&extraParameters=get-cbcollect-info=True"

  echo "### Triggering Alternate Address jobs ###"
  wget "http://qa.sc.couchbase.com/job/centos-nserv_alternate-address-feature/buildWithParameters?token=trigger_all&version_number=$version_number&url=$url&branch=$branch" -O trigger.log

  #echo "### Triggering Jepsen weekly ###"
  #wget "http://qa.sc.couchbase.com/job/jepsen-durability-trigger-new/buildWithParameters?token=jepsen-trigger-new&version_number=$version_number&test_suite=weekly&build_type=official" -O trigger.log

  #echo "### Triggering Nutshell job ###"
  #wget "http://qa.sc.couchbase.com/job/centos-nutshell/buildWithParameters?token=trigger&version_number=$version_number" -O trigger.log

  #echo "### Triggering Forestdb jobs  ###"
  #wget  "http://qa.sc.couchbase.com/job/cen006-p0-forestdb-sanity/buildWithParameters?token=trigger&version_number=$version_number&url=$url&branch=$branch" -O trigger.log

  #echo "### Triggering 2i Upgrade weekly jobs ###"
  #3/20: Commenting the 2i functional2itests job until it is fixed
  #wget "http://qa.sc.couchbase.com/job/cen7-2i-set4-job1-functional2itests/buildWithParameters?token=trigger&version_number=$version_number&gsi_type=memory_optimized&url=$url&branch=$branch" -O trigger.log
  #wget  "http://qa.sc.couchbase.com/job/cen7-2i-plasma-set5-job1-upgrade-6-0-3_RED/buildWithParameters?token=trigger&version_number=$version_number&url=$url&branch=$branch${bucket_storage_with_extra_params}" -O trigger.log
  #wget  "http://qa.sc.couchbase.com/job/cen7-2i-plasma-set5-job1-upgrade-5-0-1-int64/buildWithParameters?token=trigger&version_number=$version_number&url=$url&branch=$branch" -O trigger.log

  #echo "### Triggering OS Certification jobs ###"
  #wget   --server-response "http://qa.sc.couchbase.com/job/trigger_os_certification/buildWithParameters?token=trigger_all&version_number=$version_number&run_centos=$run_centos&run_windows=$run_windows&url=$url&branch=$branch${bucket_storage_with_extra_params}" 2>&1 | grep "HTTP/" | awk '{print $2}'

  sleep_with_message 60

  # SDK situational tests
  # java situational tests
  # wget  --user "jake.rawsthorne@couchbase.com" --password $SDK_JENKINS_TOKEN "http://sdkbuilds.sc.couchbase.com/view/JAVA/job/sdk-java-situational-release/job/sdk-java-situational-all/buildWithParameters?token=sdkbuilds&cluster_version=$version_number&run_regular=true&run_n1ql=true&run_subdoc=true" -O trigger.log
  # LCB situational tests
  # wget  --user "jake.rawsthorne@couchbase.com" --password $SDK_JENKINS_TOKEN "https://sdk.jenkins.couchbase.com/view/Situational/job/c-cpp/job/lcb/job/centos-lcb-sdk-server-situational-tests/buildWithParameters?token=sdkbuilds&cluster_version=$version_number&run_regular=true&run_n1ql=true&run_subdoc=true" -O trigger.log
  # .NET situational tests
  #wget  --user "jake.rawsthorne@couchbase.com" --password $SDK_JENKINS_TOKEN "http://sdkbuilds.sc.couchbase.com/view/.NET/job/sdk-net-situational-release/job/dotnet-situational-all//buildWithParameters?token=sdkbuilds&cluster_version=$version_number&run_regular=true&run_n1ql=true&run_subdoc=true" -O trigger.log
  # Go situational tests
  # wget  --user "jake.rawsthorne@couchbase.com" --password $SDK_JENKINS_TOKEN "http://sdkbuilds.sc.couchbase.com/view/GO/job/sdk-go-situational-release/job/go-sdk-situational-all/buildWithParameters?token=sdkbuilds&cluster_version=$version_number&run_regular=true&run_n1ql=true&run_subdoc=true" -O trigger.log

  echo "### Triggering Windows jobs ###"
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=windows22&retries=1&version_number=$version_number&suite=mustpass&serverPoolId=regression&branch=$branch&use_dynamic_vms=true&extraParameters=get-cbcollect-info=True,bucket_storage=magma" -O trigger.log
fi

# Following block is from 'trigger_upgrade_jobs' job
if [ "$TRIGGER_UPGRADE_JOBS" == "true" ]; then
  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=12hr_upgrade&component=upgrade&subcomponent=None&url=&serverPoolId=regression&addPoolId=elastic-fts&branch=${branch}&extraParameters=get-cbcollect-info=True,infra_log_level=info,log_level=info,bucket_storage=couchstore" -O trigger1.log
  sleep_with_message 300

  wget "http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=12hr_upgrade&component=2i,analytics,cli,backup_recovery,fts,query,xdcr&subcomponent=None&url=&serverPoolId=regression&addPoolId=elastic-fts&branch=${branch}&extraParameters=get-cbcollect-info=True,infra_log_level=info,log_level=info,bucket_storage=couchstore" -O trigger1.log
  sleep_with_message 300
fi

# Following block is from 'trigger_weekly_magma' job
if [ "$TRIGGER_WEEKLY_MAGMA_JOBS" == "true" ]; then
  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=magmaWeekly&component=magma&url=&serverPoolId=magmanew&branch=${branch}&extraParameters=get-cbcollect-info=True,infra_log_level=info,log_level=info,enable_encryption_at_rest=True,enable_audit_encryption_at_rest=True,enable_log_encryption_at_rest=False,enable_config_encryption_at_rest=True,encryptionAtRestDekRotationInterval=300,encryption_at_rest_dek_lifetime=600,bucket_num_vb=128" -O trigger1.log
  sleep_with_message 300

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=12hr_weekly&component=magma&url=&serverPoolId=magmareg&branch=${branch}&extraParameters=get-cbcollect-info=True,infra_log_level=info,log_level=info,bucket_storage=magma,enable_encryption_at_rest=True,enable_audit_encryption_at_rest=True,enable_log_encryption_at_rest=False,enable_config_encryption_at_rest=True,encryptionAtRestDekRotationInterval=300,encryption_at_rest_dek_lifetime=600,bucket_num_vb=128" -O trigger1.log
  sleep_with_message 300

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=magmaUpgrade&component=magma&url=&serverPoolId=magmaUpgrade&branch=${branch}&extraParameters=get-cbcollect-info=True,infra_log_level=info,log_level=info,retry_get_process_num=500" -O trigger1.log
  sleep_with_message 86400

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=guardrails&component=nserv&url=&serverPoolId=magmaUpgrade&branch=${branch}&extraParameters=get-cbcollect-info=True,infra_log_level=info,log_level=info&serverType=ON_PREM_PROVISIONED" -O trigger1.log
  sleep_with_message 300

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=magmaWeekly&component=backup_recovery&url=&serverPoolId=magmareg&branch=${branch}&extraParameters=get-cbcollect-info=True,enable_cdc=True" -O trigger1.log
  sleep_with_message 300

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=magmaWeekly&component=xdcr&url=&serverPoolId=magmareg&branch=${branch}&extraParameters=get-cbcollect-info=True,enable_cdc=True" -O trigger1.log
  sleep_with_message 300

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=magmaWeekly&component=2i&url=&serverPoolId=magmareg&branch=${branch}&extraParameters=get-cbcollect-info=True,enable_cdc=True" -O trigger1.log
  sleep_with_message 300

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=magmaWeekly&component=eventing&url=&serverPoolId=magmareg&branch=${branch}&extraParameters=get-cbcollect-info=True,enable_cdc=True" -O trigger1.log
  sleep_with_message 300

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=magmaWeekly&component=fts&url=&serverPoolId=magmareg&addPoolId=elastic-fts&branch=${branch}&extraParameters=get-cbcollect-info=True,enable_cdc=True" -O trigger1.log
  sleep_with_message 300

  wget "http://qe-jenkins1.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters?token=extended_sanity&OS=debian&version_number=${version_number}&suite=magmaWeekly&component=query&url=&serverPoolId=magmareg&branch=${branch}&extraParameters=get-cbcollect-info=True,enable_cdc=True" -O trigger1.log
  sleep_with_message 300
fi

if [ "$TRIGGER_OS_CERT_JOBS" == "true" ]; then
  # Set os_specific flags default to false if not set
  ${Ubuntu24:=false}
  ${Ubuntu24_arm:=false}
  ${al2023:=false}
  ${al2023_arm:=false}
  ${rhel9:=false}
  ${oel8:=false}
  ${alma9:=false}
  ${alma10:=false}
  ${rhel10:=false}
  ${oel10:=false}
  ${rocky10:=false}
  ${debian13:=false}
  ${ubuntu24_nonroot:=false}
  ${windows22:=false}

  echo "### Triggering OS Certification jobs ###"
  base_url="http://qa.sc.couchbase.com/job/test_suite_dispatcher_aws/buildWithParameters"
  default_dispatcher="http://qa.sc.couchbase.com/job/test_suite_dispatcher/buildWithParameters"

  common_params="token=extended_sanity&version_number=${version_number}&suite=12hr_weekly&component=os_certify&subcomponent=None&url=&serverPoolId=os_certification&addPoolId=elastic-fts&branch=${branch}&extraParameters=get-cbcollect-info=True,infra_log_level=info,log_level=info,bucket_storage=couchstore"
  linux_common_params="$common_params&executor_job_parameters=installParameters=use_hostnames=true"
  windows_common_params="$common_params&fresh_run=true&use_dynamic_vms=true&executor_job_parameters=installParameters=timeout=1200,skip_local_download=True"

  if [ "${Ubuntu24}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=ubuntu24" -O trigger.log
    sleep 120
  fi

  if [ "${Ubuntu24_arm}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=ubuntu24&architecture=aarch64" -O trigger.log
    sleep 120
  fi

  if [ "${al2023}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=al2023" -O trigger.log
    sleep 120
  fi

  if [ "${al2023_arm}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=al2023&architecture=aarch64" -O trigger.log
    sleep 120
  fi

  if [ "${rhel9}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=rhel9" -O trigger.log
    sleep 120
  fi

  if [ "${oel8}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=oel8" -O trigger.log
    sleep 120
  fi

  if [ "${alma9}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=alma9" -O trigger.log
    sleep 120
  fi

  if [ "${alma10}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=alma10" -O trigger.log
    sleep 120
  fi

  if [ "${rhel10}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=rhel10" -O trigger.log
    sleep 120
  fi

  if [ "${oel10}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=oel10" -O trigger.log
    sleep 120
  fi

  if [ "${rocky10}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=rocky10" -O trigger.log
    sleep 120
  fi

  if [ "${debian13}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=debian13" -O trigger.log
    sleep 120
  fi

  if [ "${ubuntu24_nonroot}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=ubuntu24nonroot" -O trigger.log
    sleep 120
  fi

  if [ "${windows22}" = true ]; then
    wget "${default_dispatcher}?${windows_common_params}&OS=windows22" -O trigger.log
    sleep 120
  fi

  if [ "${oel9}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=oel9" -O trigger.log
    sleep 120
  fi

  if [ "${rocky9}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=rocky9" -O trigger.log
    sleep 120
  fi

  if [ "${debian12}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=debian12" -O trigger.log
    sleep 120
  fi

  if [ "${suse15}" = true ]; then
    wget "${base_url}?${linux_common_params}&OS=suse15" -O trigger.log
    sleep 120
  fi
fi
