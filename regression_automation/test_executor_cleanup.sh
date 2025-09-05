#set +e
echo the descriptor is $descriptor
echo new state is $newState
#curl http://172.23.104.162:8081/releaseservers/${descriptor}/${newState}
echo upstream build number is $UPSTREAM_BUILD_NUMBER

curl -o self_consolelog.json $JOB_URL/${BUILD_NUMBER}/api/json?pretty=true

PARENT_JOB=`cat self_consolelog.json  |egrep 'Started by upstream project' self_consolelog.json -A 3 |egrep 'upstreamUrl'|tail -1|xargs|cut -f2 -d':'|cut -f1 -d','|xargs`
PARENT_BUILD=`cat self_consolelog.json | egrep 'Started by upstream project' self_consolelog.json -A 3 |egrep 'upstreamBuild'|tail -1|xargs|cut -f2 -d':'|cut -f1 -d','|xargs`

PARENT_LOG=parent_log.txt
cat /dev/null > ${PARENT_LOG}
curl -s -o ${PARENT_LOG} ${JENKINS_URL}${PARENT_JOB}${PARENT_BUILD}/consoleText

PARENT_JOB_INFO_FILE=parent_jobinfo.json
curl -o ${PARENT_JOB_INFO_FILE} ${JENKINS_URL}${PARENT_JOB}${PARENT_BUILD}/api/json?pretty=true
PARENT_COMP_SUBC="`egrep 'component' ${PARENT_JOB_INFO_FILE} -A1|egrep value |sed 's/value//g'| head -1 | xargs|sed -e 's/ : //g' -e 's/ /-/g'`"
echo "PARENT_JOB_COMP=${PARENT_COMP_SUBC},PARENT_JOB_URL=${JENKINS_URL}${PARENT_JOB}${PARENT_BUILD}"

if [ "${is_dynamic_vms}" == "true" ]; then
  echo "*** Dynamic Server Manager Cleanup ***"
  DYNAMIC_SERVER_MANAGER_URL="http://172.23.121.132:5000"
  PASSED_IPS="`egrep 'INSTALL COMPLETED' ${PARENT_LOG}|rev|cut -f1 -d' '|rev|xargs`"
  FAILED_IPS="`egrep 'INSTALL FAILED' ${PARENT_LOG}|rev|cut -f1 -d' '|rev|xargs`"
  echo "PASSED_IPS=$PASSED_IPS"
  echo "FAILED_IPS=$FAILED_IPS"
  PASSED_COUNT=`echo $PASSED_IPS|wc -w|xargs`
  FAILED_COUNT=`echo $FAILED_IPS|wc -w|xargs`
  if [ $PASSED_COUNT == 0 ] && [ $FAILED_COUNT == 0 ]; then
    COUNT_IPS=`egrep servers $PARENT_JOB_INFO_FILE -A1|egrep value|cut -f2 -d':'|sed 's/,/ /g'|wc -w`
  else
    COUNT_IPS=`expr $PASSED_COUNT + $FAILED_COUNT`
  fi
  echo "IPS COUNT=${COUNT_IPS}"
  TEST_RESULT=testresult.json
  curl -s -o ${TEST_RESULT} ${JENKINS_URL}${PARENT_JOB}${PARENT_BUILD}/testReport/api/json?pretty=true
  TEST_FAIL_COUNT=`egrep failCount ${TEST_RESULT} |cut -f2 -d:|xargs|sed 's/,//g'`

  # Releasing the VMs irrespective of the test results / state
  curl -s "${DYNAMIC_SERVER_MANAGER_URL}/releaseservers/${descriptor}?count=${COUNT_IPS}"

  # Update for add pool servers to release the
  # additional servers booked from the regular pool
  QE_SERVER_MANAGER_URL="http://172.23.104.162:8081"
  echo "addPoolServers=$addPoolServers"
  if [ ! "$addPoolServers" = ""  -a ! "$addPoolServers" = "None" ]; then
    for IP in `echo ${addPoolServers}|sed -e 's/"//g' -e 's/,/ /g'`
    do
      echo curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
      curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
    done
  fi
  exit 0
fi

UNINSTALL_OUT=$WORKSPACE/uninstall_out.txt
if [ -f ${UNINSTALL_OUT} ]; then
  rm ${UNINSTALL_OUT}
fi
# Cleanup the CB: See CBQE-5226
cb_cluster_cleanup()
{
  echo "*** CB Cluster Cleanup ***"
  INI_FILE_NAME="`egrep -e 'install\.py' ${PARENT_LOG} |head -1 |grep -o '\install\.py .*' |cut -f3 -d' '|rev|cut -f1 -d'/'|rev`"
  if [ ! "${INI_FILE_NAME}" = "" ]; then
    curl -o ${INI_FILE_NAME} ${JENKINS_URL}${PARENT_JOB}${PARENT_BUILD}/artifact/${INI_FILE_NAME} || true

    if [ -f ${INI_FILE_NAME} ]; then
      # See CBQE-5309
      IS_INI_HAS_IP="`egrep 'ip:' ${INI_FILE_NAME} || true`"
      if [ "${IS_INI_HAS_IP}" = "" ]; then
        echo "Warning: Can't find INI FILE content from the parent build artifact, now parsing the consoleText."
        cat ${PARENT_LOG} | sed -n '/global/,/extra install is/p' |egrep -v 'extra install is' |egrep -v '^\+'  >${INI_FILE_NAME} ||true
        IS_INI_HAS_IP="`egrep 'ip:' ${INI_FILE_NAME}  || true`"
        if [ "${IS_INI_HAS_IP}" = "" ]; then
          echo "Warning: Can't determine the INI FILE content! Skipping uninstall and cleanup."
          return
        fi
      fi
      cat ${INI_FILE_NAME}
      if [ ! -d testrunner ]; then
        git clone http://github.com/couchbase/testrunner.git
        cd testrunner/
        git submodule init
		    git submodule update --init --force --remote
        if [ $? != 0 ]; then
          echo "Git clone testrunner error! Skipping the couchbase cleanup."
          return
        fi
        cd ..
      fi
      cd testrunner/
      set -x
      python3 scripts/new_install.py -i  $WORKSPACE/${INI_FILE_NAME}  -p version=${version_number},${UNINSTALL_PARAMETERS} |tee ${UNINSTALL_OUT} || true
      set +x
      cd $WORKSPACE
    else
      echo "Warning: Can't download install ini file from artifacts, so uninstall skipped!"
    fi
  else
    echo "Warning: Can't determine the install ini file, so uninstall skipped!"
  fi
}

get_url()
{
  URL=$1
  CURL="curl -gs --retry 999 --retry-max-time 2"
  echo $CURL $URL
  $CURL $URL >/tmp/$$.txt 2>&1 || true
  cat /tmp/$$.txt
  SCODE="`egrep '(7)' /tmp/$$.txt`" || true
  SLEEP=1
  while [ ! "$SCODE" = "" ] ; do
    echo "Retry..after $SLEEP secs."
    sleep $SLEEP
    echo $CURL $URL
    $CURL $URL >/tmp/$$.txt 2>&1 || true
    cat /tmp/$$.txt
    SCODE="`egrep '(7)' /tmp/$$.txt`" || true
    SLEEP=`expr $SLEEP + $SLEEP`
  done
  rm /tmp/$$.txt || true
}

# Selective state update: See CBQE-5203
update_server_pool()
{
  echo "*** Server Pool release ***"
  QE_SERVER_MANAGER_URL="http://172.23.104.162:8081"
  CURL="curl -gs --retry 999 --retry-max-time 2"

  if [ -f ${PARENT_LOG} ]; then
    #cat ${PARENT_LOG}
    PASSED_IPS="`egrep 'INSTALL COMPLETED' ${PARENT_LOG}|rev|cut -f1 -d' '|rev|xargs`"
    FAILED_IPS="`egrep 'INSTALL FAILED' ${PARENT_LOG}|rev|cut -f1 -d' '|rev|xargs`"
    echo "PASSED_IPS=$PASSED_IPS"
    echo "FAILED_IPS=$FAILED_IPS"
    echo "Call server manager"
    cat ${UNINSTALL_OUT} || true
    for IP in `echo $PASSED_IPS` ; do
      # See CBQE-5309
      if [ -f ${UNINSTALL_OUT} ]; then
        IS_CLEANUP_OK="`cat ${UNINSTALL_OUT} | egrep \"\bDone with cleanup on ${IP}\b\" || true`"
        if [ "${IS_CLEANUP_OK}" = "" ]; then
          #echo "Clean/uninstall on ${IP} didn't completed, marking as failedInstall. $CURL ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall"
          #$CURL ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall
          echo "Clean/uninstall on ${IP} didn't completed but Setting the status as available for passed install VM, ignoring the uninstall status."
          get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
        else
          echo "Cleanup is ok. $CURL ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available"
          get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
        fi
      else
        echo "No uninstall log is available. $CURL ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available"
        get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
      fi
    done
    for IP in `echo $FAILED_IPS` ; do
      echo "$CURL ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall"
      get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall
    done
  fi

  if [[ ("${PASSED_IPS}" = "" && "${FAILED_IPS}" = "") ]]; then
 	  echo "Warning: Cannot determine install status - Passed or failed IPs. Checking ssh status!"
    ls -lrt ${PARENT_LOG}
    SSH_FAILED="`egrep 'No SSH connectivity' ${PARENT_LOG}|grep -oP '(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'|sort|uniq|| true` "
    if [ "${SSH_FAILED}" != "" ]; then
      echo "No SSH connectivity: $SSH_FAILED"
      for IP in `echo ${SSH_FAILED}` ; do
        echo "get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall"
        get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall
      done
    fi
    SSH_OK="`egrep 'SSH Connected to' ${PARENT_LOG}|grep -oP '(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'|sort|uniq|| true` "
    if [ "${SSH_OK}" != "" ]; then
      echo "SSH ok: $SSH_OK"
      for IP in `echo ${SSH_OK}` ; do
        echo "get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available"
        get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
      done
    fi
    if [[ ("${SSH_FAILED}" = " " && "${SSH_OK}" = " ") ]]; then
      echo "Warning: Cannot determine install or ssh status - Passed or failed IPs. Going with default!"
      if [ "${newState}" = '$newState' ]; then
        newState="available"
      fi
      get_url ${QE_SERVER_MANAGER_URL}/releaseservers/${descriptor}/${newState}
    else
      echo "Warning: Partial SSH OK/FAILED IPs found..."
      SSH_ALL="`egrep 'SSH Connecting to' ${PARENT_LOG}|grep -oP '(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'|sort|uniq|| true` "
      SSH_FAILED_COUNT=`echo ${SSH_FAILED}|wc -w|xargs`
      SSH_OK_COUNT=`echo ${SSH_OK}|wc -w|xargs`
      SSH_ALL_COUNT=`echo ${SSH_ALL}|wc -w|xargs`

      if [ "${SSH_ALL_COUNT}" -gt $((SSH_OK_COUNT+SSH_FAILED_COUNT)) ]; then
        echo "Missing some IPs status!"
        for IP in `echo ${SSH_ALL}` ; do
          OK_IP=`echo ${SSH_OK} | egrep "${IP} " || true`
          if [ "${OK_IP}" == "" ]; then
            echo "ssh not ok: ${IP}"
            echo "get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall"
            get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall
          fi
        done
      fi
    fi
 fi

 #Update for add pool servers
 echo "addPoolServers=$addPoolServers"
 if [ ! "$addPoolServers" = ""  -a ! "$addPoolServers" = "None" ]; then
    for IP in `echo ${addPoolServers}|sed -e 's/"//g' -e 's/,/ /g'` ; do
	    echo $CURL ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
      get_url ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
    done
 fi
}

all()
{
  # if [ ! "${version_number}" = "" ]; then
  #   cb_cluster_cleanup
  # fi
  update_server_pool
}

all

# Uncomment the following code when needed
# This code basically deletes the upstream job that failed because of failedInstall
#if [ "$newState" = "failedInstall" ]; then
#   echo "Installation failed, so deleting the upstream build!!"
#   wget http://qa.sc.couchbase.com/jnlpJars/jenkins-cli.jar
#   java -jar jenkins-cli.jar -s http://qa.sc.couchbase.com/ delete-builds test_suite_executor $UPSTREAM_BUILD_NUMBER --username jenkins_user --password  jenkins_user
#fi
