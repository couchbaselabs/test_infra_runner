import paramiko
import time
from getpass import getpass
from urllib.parse import urlparse, unquote
import os

def execute_commands(client, ip):
    yml_path = "/etc/elasticsearch/elasticsearch.yml"
    commands = [
        "apt get update -y",
        "apt install curl -y",
        "systemctl stop elasticsearch",
        "systemctl disable elasticsearch",
        "rm /etc/systemd/system/elasticsearch.service",
        "systemctl daemon-reexec",
        "systemctl daemon-reload",
        "rm -rf /usr/local/elasticsearch",
        "wget https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.17.0-amd64.deb",
        "dpkg -i elasticsearch-8.17.0-amd64.deb",
        "systemctl enable elasticsearch",
        "systemctl start elasticsearch",
        f"grep -q '^xpack.security.enabled:' {yml_path} && " +
        f"sed -i 's/^xpack.security.enabled:.*/xpack.security.enabled: false/' {yml_path} || " +
        f"echo 'xpack.security.enabled: false' | tee -a {yml_path}",

        f"grep -q '^http.host:' {yml_path} && " +
        f"sed -i 's/^http.host:.*/http.host: 0.0.0.0/' {yml_path} || " +
        f"echo 'http.host: 0.0.0.0' | tee -a {yml_path}",

        "systemctl restart elasticsearch",
        "rm -f /root/elasticsearch-*.deb",
        "rm -f /root/elasticsearch-*.tar.gz",
        "rm -f /root/elasticsearch-*.zip",
    ]
    
    try:
        yml_path = "/etc/elasticsearch/elasticsearch.yml"
        with open("output.log", "a") as output_file:
            for command in commands:
                print(f"Executing on {ip}: {command}")
                stdin, stdout, stderr = client.exec_command(command)
                stdout.channel.recv_exit_status()
                output = stdout.read().decode()
                error = stderr.read().decode()
                
                if output:
                    print(f"Output: {output}")
                    if "wget" not in command:
                        output_file.write(f"Output from {ip} ({command}):\n{output}\n")
                if error:
                    print(f"Error: {error}")
                    output_file.write(f"Error from {ip} ({command}):\n{error}\n")
    except Exception as e:
        with open("output.log", "a") as output_file:
            error_message = f"Error executing commands on {ip}: {str(e)}\n"
            print(error_message)
            output_file.write(error_message)


def ssh_connect_and_run(ip_list, username, password):
    for ip in ip_list:
        print(f"Connecting to {ip}...")
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, username=username, password=password)

            execute_commands(client, ip)

            client.close()
            print(f"Finished with {ip}!\n")
        except Exception as e:
            print(f"Failed to connect to {ip}: {str(e)}")


if __name__ == "__main__":
    ip_list = []

    username = input("Enter username [default: root]: ") or "root"
    password = getpass("Enter password [default: couchbase]: ") or "couchbase"
    ssh_connect_and_run(ip_list, username, password)