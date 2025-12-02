#!/bin/bash

if [ "$component" == "None" ]; then
   exit 1
fi

[[ "$version_number" =~ ^[1-9]{1}.[0-9]{1}.[0-9]{1}-[0-9]{4,5}$ ]] && valid=true || valid=false

if [ ${valid} = false ]; then
   exit 1
fi

echo suite $suite
if [ ${Test} = true ]; then
   testoption=-t
else
   testoption=
fi
if [ ${fresh_run} == true ]; then
	freshrun=
else
	freshrun=-q
fi

if [ ${skip_install} == false ]; then
	skip_install=
else
	skip_install="--skip_install true"
fi


case "$serverType" in
  "CAPELLA_MOCK")
    serverType=" -y CAPELLA_LOCAL"
    if [ -n "$extraParameters" ]; then
	    extraParameters+=',capella_run=True'
    else
    	extraParameters='capella_run=True'
    fi
    ;;

  "ELIXIR_ONPREM")
    serverType=" -y ELIXIR_ONPREM"
    ;;

  "SERVERLESS_ONCLOUD")
    serverType=" -y SERVERLESS_ONCLOUD"
    if [ -n "$executor_job_parameters" ]; then
	    executor_job_parameters+="&capella_api_url=$capella_api_url&capella_region=$capella_region&capella_dataplane_id=$dataplane_id&pipeline_uuid=$pipeline_uuid&cbs_image=$cbs_image&cbs_version=$cbs_version&access_key=$access_key&secret_key=$secret_key&project_id=$project_id&tenant_id=$tenant_id"
    else
    	executor_job_parameters="capella_api_url=$capella_api_url&capella_region=$capella_region&capella_dataplane_id=$dataplane_id&pipeline_uuid=$pipeline_uuid&cbs_image=$cbs_image&cbs_version=$cbs_version&access_key=$access_key&secret_key=$secret_key&project_id=$project_id&tenant_id=$tenant_id"
    fi    
    ;;
  "PROVISIONED_ONCLOUD")
    serverType=" -y PROVISIONED_ONCLOUD"
    if [ -n "$executor_job_parameters" ]; then
	    executor_job_parameters+="&capella_api_url=$capella_api_url&capella_region=$capella_region&capella_dataplane_id=$dataplane_id&pipeline_uuid=$pipeline_uuid&cbs_image=$cbs_image&cbs_version=$cbs_version&access_key=$access_key&secret_key=$secret_key&project_id=$project_id&tenant_id=$tenant_id"
    else
    	executor_job_parameters="capella_api_url=$capella_api_url&capella_region=$capella_region&capella_dataplane_id=$dataplane_id&pipeline_uuid=$pipeline_uuid&cbs_image=$cbs_image&cbs_version=$cbs_version&access_key=$access_key&secret_key=$secret_key&project_id=$project_id&tenant_id=$tenant_id"
    fi    
    ;;
    "SERVERLESS_COLUMNAR")
    serverType=" -y SERVERLESS_COLUMNAR"
    if [[ $capella_api_url =~ "sandbox" ]]; then
        echo "sandbox"
	    override_token=$sbx_token_for_internal_support
        if [ -z "$cbs_image" ]; then
            override_key=""
        else
            override_key="the-secret-test-override-key"
        fi
    elif [[ $capella_api_url =~ "dev" ]]; then
        echo "dev"
	    override_token=$dev_token_for_internal_support
        if [ -z "$cbs_image" ]; then
            override_key=""
        else
            override_key=$dev_override_key
        fi
    else
        echo "prod"
	    override_token=""
        override_key=""
    fi
    
    if [ -n "$executor_job_parameters" ]; then
	    executor_job_parameters+="&capella_api_url=$capella_api_url&capella_region=$capella_region&pipeline_uuid=$pipeline_uuid&tenant_id=$tenant_id&project_id=$project_id&override_token=$override_token&override_key=$override_key&cbs_image=$cbs_image"
    else
    	executor_job_parameters="capella_api_url=$capella_api_url&capella_region=$capella_region&pipeline_uuid=$pipeline_uuid&tenant_id=$tenant_id&project_id=$project_id&override_token=$override_token&override_key=$override_key&cbs_image=$cbs_image"
    fi
    ;;
  *)
    serverType=
    ;;
esac

if [ -z "${rerun_params}" ]; then
	rerun_param=
else
	rerun_param="-m '${rerun_params}'"
fi

if [ ! "${executor_suffix}" = "" ]; then
   EXECUTOR_OPTION=" -j ${executor_suffix}"
else
   EXECUTOR_OPTION=""
fi

if [ ${check_vm} == true ]; then
    #TBD: Some isuse with check ssh and commenting until fixed (CBQE-6914)
	#checkvm="--check_vm True"
    checkvm=""
else
	checkvm=""
fi

if [ ! "${executor_job_parameters}" = "" ]; then
  EXEC_JOB_PARAMS=" --job_params ${executor_job_parameters}"
fi

JENKINS_URL=http://172.23.121.80

source /opt/rh/devtoolset-9/enable
export CC=gcc
export CXX=g++
gcc --version

# Set python env
py_executable=/usr/local/bin/python3.7

# Install Virtual env
${py_executable} -m pip install virtualenv

# Create the virutal env folder with name 'venv'
${py_executable} -m venv venv
source ./venv/bin/activate
#virtualenv -p ${py_executable} venv

# Reset py_executable path to venv folder
py_executable=./venv/bin/python
echo $capella_signup_token
# Install pip requirementes

#cat requirements.txt | grep -v couchbase | grep -v numpy | grep -v h5py | grep -v wget | grep -v torch |grep -v sentence-transformers | xargs | xargs ${py_executable} -m pip install
#${py_executable} -m pip install couchbase==2.5.12 dnspython==2.2.1 google.cloud.dns grpcio
${py_executable} -m pip install `cat requirements.txt | grep -v couchbase | grep -v numpy | grep -v h5py | grep -v wget | grep -v torch |grep -v sentence-transformers | grep -v faiss-cpu | xargs` 
${py_executable} -m pip install couchbase==2.5.12 dnspython==2.2.1 google.cloud.dns grpcio

#sleep_bw_trigger=""
sleep_bw_trigger="--sleep_between_trigger $sleep_between_trigger"

exe_str="${py_executable} -u scripts/testDispatcher.py"

set -x
if [ -n "$url" ]; then
	if [ -n "$extraParameters" ]; then
    	${exe_str} -r ${suite}  -v  ${version_number} -o ${OS} -c ${component} -p ${serverPoolId} -a ${addPoolId} -s ${subcomponent} --subcomponent_regex ${subcomponent_regex}  ${testoption} -u ${url} -b ${branch} -g "${cherrypick}" -e ${extraParameters} ${EXECUTOR_OPTION}  -i ${retries} ${freshrun} ${rerun_param} ${EXEC_JOB_PARAMS} ${checkvm} ${serverType} ${skip_install} -f ${JENKINS_URL} --capella_url $capella_api_url --capella_tenant $tenant_id --capella_user $capella_user --capella_password $capella_password --capella_token $capella_signup_token $sleep_bw_trigger
	else
    	${exe_str} -r ${suite} -v  ${version_number} -o ${OS} -c ${component} -p ${serverPoolId} -a ${addPoolId} -s ${subcomponent} --subcomponent_regex ${subcomponent_regex}  ${testoption} -u ${url} -b ${branch} -g "${cherrypick}" ${EXECUTOR_OPTION} -i ${retries} ${freshrun} ${rerun_param} ${EXEC_JOB_PARAMS} ${checkvm} ${serverType} ${skip_install} -f ${JENKINS_URL} --capella_url $capella_api_url --capella_tenant $tenant_id --capella_user $capella_user --capella_password $capella_password --capella_token $capella_signup_token $sleep_bw_trigger
    fi 
   
else
	if [ -n "$extraParameters" ]; then
    	${exe_str} -l "test_suite_executor_cloud" -r ${suite} -v  ${version_number} -o ${OS} -c ${component} -p ${serverPoolId} -a ${addPoolId} -s ${subcomponent} --subcomponent_regex ${subcomponent_regex}  ${testoption} -b ${branch} -g "${cherrypick}" -e ${extraParameters} ${EXECUTOR_OPTION} -i ${retries} ${freshrun} ${rerun_param} ${EXEC_JOB_PARAMS} ${checkvm} ${serverType} ${skip_install} -f ${JENKINS_URL} --capella_url $capella_api_url --capella_tenant $tenant_id --capella_user $capella_user --capella_password $capella_password --capella_token $capella_signup_token $sleep_bw_trigger
    else
    	${exe_str} -l "test_suite_executor_cloud" -r ${suite} -v  ${version_number} -o ${OS} -c ${component} -p ${serverPoolId} -a ${addPoolId} -s ${subcomponent} --subcomponent_regex ${subcomponent_regex}  ${testoption} -b ${branch} -g "${cherrypick}" ${EXECUTOR_OPTION} -i ${retries} ${freshrun} ${rerun_param} ${EXEC_JOB_PARAMS} ${checkvm} ${serverType} ${skip_install} -f ${JENKINS_URL} --capella_url $capella_api_url --capella_tenant $tenant_id --capella_user $capella_user --capella_password $capella_password --capella_token $capella_signup_token $sleep_bw_trigger
    fi    
fi
set +x

# Cleanup the virtual env folder
rm -rf venv
