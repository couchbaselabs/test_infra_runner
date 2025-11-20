#!/bin/bash -xe

# If deb12 slave, set the right python3 version to use
if echo $NODE_LABELS | grep -q deb12_dispatcher ; then
  export PYENV_ROOT="$HOME/.pyenv"
  export PATH="$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init -)"
  pyenv local 3.10.14
  alias python3=python
  echo "deb12 dispatcher detected. Forcing docker_run=true"
  export use_dockerized_dispatcher=true
fi

set +e
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

if [ ${use_dynamic_vms} == true ]; then
  # Check if OS starts with "win" and exit if not
  if [[ ! "$OS" =~ ^win ]]; then
    echo "ERROR: OS='$OS'. Use Dynamic VM flag only to run on Windows OS."
    exit 1
  fi

  # using CHECK_SSH as false since that was default in dispatcher_dynvm job as well
  CHECK_SSH=False
  SERVER_MGR_OPTIONS="-x 172.23.121.132:5000 -z 2000 -w $CHECK_SSH --is_dynamic_vms true"
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

  "ON_PREM_PROVISIONED")
    serverType=" -y ON_PREM_PROVISIONED"
	;;

  *)
    serverType=" -y VM"
    ;;
esac


if [ -z "${rerun_params}" ]; then
	rerun_param=
else
	rerun_param="-m '${rerun_params}'"
fi

if [ ${check_vm} == true ]; then
    #TBD: Some isuse with check ssh and commenting until fixed (CBQE-6914)
	#checkvm="--check_vm True"
    checkvm=""
else
	checkvm=""
fi

columnar_version_arg=""
if [ "$columnar_version_number" != "None" ]; then
  columnar_version_arg="--columnar_version $columnar_version_number"
fi

if [ ! "${executor_job_parameters}" = "" ]; then
  EXEC_JOB_PARAMS=" --job_params ${executor_job_parameters}"
fi

rerun_condition="--rerun_condition ${rerun_condition}"
JENKINS_URL=${JENKINS_URL:-http://172.23.120.81}
echo "JENKINS_URL: $JENKINS_URL"

if [ "$use_dockerized_dispatcher" == "true" ]; then
  docker --help > /dev/null
  if [ $? -ne  0 ]; then
      echo "ERROR: Docker not found!!"
      exit 1
  fi
  docker_img=dispatcher:sdk3
  docker_img_id=$(docker images -q $docker_img)
  if [ "$docker_img_id" == "" ]; then
    wget https://raw.githubusercontent.com/couchbaselabs/test_infra_runner/refs/heads/master/regression_automation/Dockerfile_dispatcher_sdk3 -O Dockerfile
    docker build . --tag $docker_img
  fi
  container_name=dispatcher_${BUILD_ID}
  #exe_str="docker run --name $container_name $docker_img --build_url $BUILD_URL --job_url $JOB_URL ${rerun_condition}"

  # SDK3: Use this line while switching back to sdk3 dispacher (if sdk4 issues are seen)
  exe_str="docker run --name $container_name $docker_img --build_url $BUILD_URL --job_url $JOB_URL"

  # SDK4: String for sdk4 based dispatcher to use transactions to book servers locally (--log_level options is new in this)
  # exe_str="docker run -m 256m --security-opt seccomp=unconfined --name $container_name $docker_img --build_url $BUILD_URL --job_url $JOB_URL --log_level info"
else
  echo "Cloning testrunner repo"
  rm -rf testrunner
  git clone https://github.com/couchbase/testrunner.git
  cd testrunner
  git submodule init
  git submodule update --init --force --remote
  # Assume all deps are pre-installed (centos case)
  exe_str="python3 -u scripts/testDispatcher.py"
fi

if [ -n "$url" ]; then
	if [ -n "$extraParameters" ]; then
        $exe_str -r ${suite} -v ${version_number} -o ${OS} -c ${component} -p ${serverPoolId} -a ${addPoolId} -s ${subcomponent}  ${testoption} -u ${url} -b ${branch} ${SERVER_MGR_OPTIONS} ${rerun_condition} -g "${cherrypick}" -e ${extraParameters} -i ${retries} ${freshrun} ${rerun_param} ${EXEC_JOB_PARAMS} ${checkvm} ${serverType} -f ${JENKINS_URL} ${columnar_version_arg}
	else
    	$exe_str -r ${suite} -v ${version_number} -o ${OS} -c ${component} -p ${serverPoolId} -a ${addPoolId} -s ${subcomponent}  ${testoption} -u ${url} -b ${branch} ${SERVER_MGR_OPTIONS} ${rerun_condition} -g "${cherrypick}" -i ${retries} ${freshrun} ${rerun_param} ${EXEC_JOB_PARAMS} ${checkvm} ${serverType} -f ${JENKINS_URL} ${columnar_version_arg}
    fi
else
	if [ -n "$extraParameters" ]; then
    	$exe_str -r ${suite} -v  ${version_number} -o ${OS} -c ${component} -p ${serverPoolId} -a ${addPoolId} -s ${subcomponent}  ${testoption} -b ${branch} ${SERVER_MGR_OPTIONS} ${rerun_condition} -g "${cherrypick}" -e ${extraParameters} -i ${retries} ${freshrun} ${rerun_param} ${EXEC_JOB_PARAMS} ${checkvm} ${serverType} -f ${JENKINS_URL} ${columnar_version_arg}
    else
    	$exe_str -r ${suite} -v  ${version_number} -o ${OS} -c ${component} -p ${serverPoolId} -a ${addPoolId} -s ${subcomponent}  ${testoption} -b ${branch} ${SERVER_MGR_OPTIONS} ${rerun_condition} -g "${cherrypick}" -i ${retries} ${freshrun} ${rerun_param} ${EXEC_JOB_PARAMS} ${checkvm} ${serverType} -f ${JENKINS_URL} ${columnar_version_arg}
    fi
fi

if [ "$use_dockerized_dispatcher" == "true" ] || [ "$use_dockerized_dispatcher_sdk4" == "true" ]; then
  docker rm $container_name
else
  # Cleanup the virtual env folder
  rm -rf venv
fi
