#set +e
echo the descriptor is $descriptor
echo new state is $newState
#curl http://172.23.105.177:8081/releaseservers/${descriptor}/${newState}
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


#dynvm
if [ "${SERVER_MANAGER_TYPE}" = "dynamic" ]; then
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
   if [ "${CLEANUP_VMS}" = "always" ]; then
     curl -s "http://172.23.104.180:5000/releaseservers/${descriptor}?count=${COUNT_IPS}"
   elif [ "${TEST_FAIL_COUNT}" = "0" ]; then
   		curl -s "http://172.23.104.180:5000/releaseservers/${descriptor}?count=${COUNT_IPS}"
   else
   		echo "WARNING: ${TEST_FAIL_COUNT} tests failed. Keeping VMs for analysis!"
   fi
   
   #Update for add pool servers
   QE_SERVER_MANAGER_URL="http://172.23.216.60:8081"
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
          fi
          cd testrunner/
          echo python scripts/new_install.py -i  $WORKSPACE/${INI_FILE_NAME}  -p version=${version_number},${UNINSTALL_PARAMETERS}
                python scripts/new_install.py -i  $WORKSPACE/${INI_FILE_NAME}  -p version=${version_number},${UNINSTALL_PARAMETERS} |tee ${UNINSTALL_OUT} || true
                cd $WORKSPACE
        else
           echo "Warning: Can't download install ini file from artifacts, so uninstall skipped!"
        fi
    else
        echo "Warning: Can't determine the install ini file, so uninstall skipped!"
    fi

}

# Selective state update: See CBQE-5203
update_server_pool()
{
  echo "*** Server Pool release ***"
  QE_SERVER_MANAGER_URL="http://172.23.216.60:8081"
 
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
          #echo "Clean/uninstall on ${IP} didn't completed, marking as failedInstall. curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall"
          #curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall
          echo "Clean/uninstall on ${IP} didn't completed but Setting the status as available for passed install VM, ignoring the uninstall status."
          curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
        else
          echo "Cleanup is ok. curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available"
          curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
        fi
      else
         echo "No uninstall log is available. curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available"
         curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
      fi  
   done
   for IP in `echo $FAILED_IPS`
   do
      echo "curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall"
      curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/failedInstall
   done
 fi

 if [[ ("${PASSED_IPS}" = "" && "${FAILED_IPS}" = "") ]]; then
   echo "Warning: Cannot determine install status - Passed or failed IPs. Going with default!"
   if [ "${newState}" = '$newState' ]; then
      newState="available"
   fi
   curl ${QE_SERVER_MANAGER_URL}/releaseservers/${descriptor}/${newState}
 fi
 
 #Update for add pool servers
 echo "addPoolServers=$addPoolServers"
 if [ ! "$addPoolServers" = ""  -a ! "$addPoolServers" = "None" ]; then
    for IP in `echo ${addPoolServers}|sed -e 's/"//g' -e 's/,/ /g'`
    do
	  echo curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
      curl -g ${QE_SERVER_MANAGER_URL}/releaseip/${IP}/available
    done  
 fi
}

all()
{
  if [ ! "${version_number}" = "" ]; then
    cb_cluster_cleanup
  fi
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
