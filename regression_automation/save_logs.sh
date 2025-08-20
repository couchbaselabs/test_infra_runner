#!/bin/bash -x

#copy jenkins env file if it doesn't exist
if [[ ! -e $HOME/.jenkins_env.properties ]]; then
cp $jenkins_envfile $HOME/.jenkins_env.properties
fi
set -x

if ! command -v aws &> /dev/null; then
	echo "AWS cli not found. Installing AWS CLI.."
    if [ -f /etc/debian_version ]; then
        apt-get update -y
    	apt-get install -y unzip
    elif [ -f /etc/centos-release ] && grep -q "CentOS Linux release 7" /etc/centos-release; then
        yum update -y
        yum install -y unzip
    else
        echo "Unsupported Linux distribution."
        exit 1
    fi

    # Download the AWS CLI installation zip file
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
	 # Unzip the installation file
    unzip awscliv2.zip
    # Install the AWS CLI
    ./aws/install
    # Remove the downloaded files
    rm -rf awscliv2.zip aws/
    echo "AWS CLI installation completed."
    # Create aws credentials file
    mkdir -p ~/.aws
    cat <<EOL > ~/.aws/credentials
    [default]
    aws_access_key_id = $AWS_ACCESS_KEY_ID
    aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
EOL

    # Create the AWS config file
    cat <<EOL > ~/.aws/config
    [default]
    region = us-west-1
    output = json
EOL

    echo "AWS CLI configuration completed."
else
	echo "AWS CLI is installed and configured"
fi
set +x

export PATH=$PATH:/usr/local/go/bin
if command -v go &> /dev/null; then
    echo "Go is already installed."
else
    echo "Go is not installed. Installing Go..."
    rm -rf /usr/local/go
    go_version=1.21.0
	wget https://golang.org/dl/go${go_version}.linux-amd64.tar.gz --quiet
	tar -C /usr/local -xzf go${go_version}.linux-amd64.tar.gz
	rm -f go${go_version}.linux-amd64.tar.gz
	echo "export PATH=$PATH:/usr/local/go/bin" >> ~/.profile
    source ~/.profile
    echo "Go installation complete"
fi

echo version=${version_number}
echo Desc: $version_number

cd runanalyzer
go env -w GO111MODULE=off
go get github.com/magiconair/properties
go get gopkg.in/ini.v1

JOB_CSV_FILE=test_jobs.csv
echo ${test_name},${test_job_url},${test_job_build} >$JOB_CSV_FILE

#temp code ( will be removed later )
echo "========================================"
echo "DEBUG: About to run runanalyzer.go with:"
echo "  Action: savejoblogs"
echo "  Source file: $JOB_CSV_FILE (contents below)"
cat "$JOB_CSV_FILE"
echo
echo "  Destination: s3"
echo "  Overwrite: ${overwrite}"
echo "  OS: ${os}"
echo "  Update URL: ${updateurl}"
echo "  Includes: ${includes}"
echo "  Version number: ${version_number}"
echo "========================================"
#end of temp code

go run runanalyzer.go -action savejoblogs --src $JOB_CSV_FILE --dest s3 --overwrite ${overwrite} --os ${os} --updateurl ${updateurl} --includes ${includes} $version_number
#go run /root/productivitynautomation/runanalyzer/runanalyzer.go -action savejoblogs --src $JOB_CSV_FILE --dest s3 --overwrite ${overwrite} --os ${os} --updateurl ${updateurl} --includes ${includes} $version_number

echo "Done"
