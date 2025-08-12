import streamlit as st
import requests
from requests.auth import HTTPBasicAuth
from requests.utils import quote

st.set_page_config(page_title="Jenkins Node Viewer", layout="wide")
if "fetched_nodes" not in st.session_state:
    st.session_state.fetched_nodes = None

if "jenkins_url" not in st.session_state:
    st.session_state.jenkins_url = None

if "credentials" not in st.session_state:
    st.session_state.credentials = {"username": "", "api_token": ""}


st.title("🧩 Jenkins Agents Viewer")

# Jenkins instances list
jenkins_instances = {
    "None": None,
    "http://172.23.120.81": {
        "url": "http://172.23.120.81",
        "username": "****",
        "password": "****",
        "name": "qa.sc.couchbase.com"
    },
    "http://172.23.121.80": {
        "url": "http://172.23.121.80",
        "username": "****",
        "password": "****",
        "name": "qe-jenkins.sc.couchbase.com"
    }
}

# Select Jenkins instance
selected_instance = st.selectbox("Select Jenkins Instance", list(jenkins_instances.keys()))

if selected_instance != "None":
    st.session_state.jenkins_url = jenkins_instances[selected_instance]["url"]

    st.markdown(f"🔗 Selected Jenkins: `{jenkins_instances[selected_instance]['name']}`")

    if st.button("🔄 Fetch Agents"):
        if not all([st.session_state.jenkins_url, jenkins_instances[selected_instance]["username"], jenkins_instances[selected_instance]["password"]]):
            st.warning("⚠️ Please fill in all fields.")
        else:
            with st.spinner("Fetching Jenkins node data..."):
                try:
                    # Jenkins API
                    api_url = f"{st.session_state.jenkins_url}/computer/api/json?depth=1"
                    response = requests.get(api_url, auth=HTTPBasicAuth(
                        jenkins_instances[st.session_state.jenkins_url]["username"], 
                        jenkins_instances[st.session_state.jenkins_url]["password"]))

                    if response.status_code == 200:
                        data = response.json()
                        st.session_state.fetched_nodes = data.get("computer", [])
                    else:
                        st.error(f"Failed to connect. Status: {response.status_code}")
                        st.text(response.text)
                except Exception as e:
                    st.error(f"Error: {e}")
if st.session_state.fetched_nodes:
    nodes = st.session_state.fetched_nodes

    # Collect all unique labels, excluding ones equal to node name
    all_labels = set()
    for node in nodes:
        for label in node.get("assignedLabels", []):
            if "name" in label and label["name"] != node.get("displayName"):
                all_labels.add(label["name"])
    all_labels = sorted(list(all_labels))

    # Sidebar filters
    st.sidebar.header("🔍 Filters")
    status_filter = st.sidebar.multiselect(
        "Status", ["ONLINE", "OFFLINE"], default=["ONLINE", "OFFLINE"]
    )
    label_filter = st.sidebar.multiselect(
        "Labels", all_labels, default=all_labels
    )

    # Count filtered agents
    filtered_count = 0
    for node in nodes:
        name = node.get("displayName")
        is_offline = node.get("offline", False)
        status = "OFFLINE" if is_offline else "ONLINE"

        # Filter labels: ignore ones that are the same as the node name
        labels = [
            label["name"]
            for label in node.get("assignedLabels", [])
            if "name" in label and label["name"] != name
        ]

        match_label = any(label in label_filter for label in labels)

        if status in status_filter and match_label:
            filtered_count += 1

    # Display count of filtered agents
    st.subheader(f"Filtered Jenkins Agents ({filtered_count} matching)")

    # Display filtered agents
    for node in nodes:
        name = node.get("displayName")
        is_offline = node.get("offline", False)
        status = "OFFLINE" if is_offline else "ONLINE"

        # Filter labels: ignore ones that are the same as the node name
        labels = [
            label["name"]
            for label in node.get("assignedLabels", [])
            if "name" in label and label["name"] != name
        ]

        match_label = any(label in label_filter for label in labels)

        if status in status_filter and match_label:
            status_icon = "🟩" if status == "ONLINE" else "🟥"
            label_text = ", ".join(labels) if labels else "No labels"

            # Configure URL
            encoded_name = quote(name, safe='')
            configure_url = f"{st.session_state.jenkins_url}/computer/{encoded_name}/configure"
            configure_link = f"[⚙️ Configure Agent]({configure_url})"

            with st.expander(f"{status_icon} {name} [{status}]"):
                st.markdown(f"**Description:** {node.get('description', 'N/A')}")
                st.markdown(f"**Labels:** {label_text}")
                st.markdown(configure_link)
