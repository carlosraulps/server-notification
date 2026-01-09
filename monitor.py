import os
import time
import requests
import datetime
from fabric import Connection, Config
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
BASTION_HOST = "gmcan.unmsm.edu.pe"
BASTION_USER = "carlos"
BASTION_PORT = 7722
BASTION_PASSWORD = os.getenv("SSH_PASSWORD_BASTIAO")

HEAD_NODE_HOST = "192.168.16.100"
HEAD_NODE_USER = "carlos"
HEAD_NODE_PASSWORD = os.getenv("SSH_PASSWORD_HUK")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))

TARGET_PARTITIONS = ["alto", "medio", "normal"]

def get_ssh_connection():
    """Establishes the SSH connection to the head node via the bastion."""
    # Configure bastion connection
    bastion_config = Config(overrides={'user': BASTION_USER, 'port': BASTION_PORT})
    bastion = Connection(
        host=BASTION_HOST,
        user=BASTION_USER,
        port=BASTION_PORT,
        connect_kwargs={"password": BASTION_PASSWORD}
    )

    # Configure head node connection using bastion as gateway
    head_node = Connection(
        host=HEAD_NODE_HOST,
        user=HEAD_NODE_USER,
        gateway=bastion,
        connect_kwargs={"password": HEAD_NODE_PASSWORD}
    )
    
    return head_node

def parse_sinfo(output):
    """
    Parses sinfo output.
    Format: PARTITION NODELIST STATE CPUS MEMORY
    Example: alto huk120 idle 48 128000
    """
    free_nodes = {} # Key: Node name, Value: Dict with specs
    lines = output.strip().split('\n')
    
    # Skip header
    if lines and "PARTITION" in lines[0]:
        lines = lines[1:]

    for line in lines:
        parts = line.split()
        if len(parts) >= 5:
            partition = parts[0].replace("*", "") # Remove default flag if present
            nodelist = parts[1]
            state = parts[2]
            cpus = parts[3]
            memory = parts[4]

            # Check if partition is one we care about
            if partition in TARGET_PARTITIONS:
                # Check for idle or mixed state (mixed implies some resources might be free, but user asked for idle trigger mostly)
                # User prompt: "Look for nodes where State is `idle` (completely free) or `mixed` (partially free)."
                # And: "Trigger: If ANY node ... is found in an `idle` state."
                # The user's prompt says "monitoring logic calls for idle or mixed" 
                # but "Notification Trigger: If ANY node ... is found in an `idle` state."
                # I will stick to "idle" for the explicit trigger to avoid spam on mixed nodes which might be full but just multi-core.
                # Actually, standard sinfo 'mixed' means some CPUs allocated, some idle. 
                # Let's track 'idle' explicitly as requested for the trigger condition.
                
                if "idle" in state.lower(): 
                   # Handle node ranges if sinfo condenses them (e.g. huk[120-121])
                   # For simplicity, if sinfo returns a range, we treat it as a string block.
                   # To properly parse ranges requires `hostlist` or similar, but often sinfo -o %n gives individual lines if configured or condensed.
                   # If sinfo returns condensed list (huk[120-122]), it's complex to split without extra libs.
                   # However, sinfo -o %n usually lists nodes. If grouped, we'll just treat the group as the ID for notification.
                   
                   # Add to free nodes
                   node_id = f"{partition}:{nodelist}" # Unique ID
                   free_nodes[node_id] = {
                       "partition": partition,
                       "node": nodelist,
                       "cpus": cpus,
                       "memory": memory,
                       "state": state
                   }
    
    return free_nodes

def get_active_jobs(connection):
    """Runs squeue to get summary of active users."""
    try:
        result = connection.run("squeue -h -o %u", hide=True)
        users = result.stdout.strip().split('\n')
        users = [u for u in users if u] # Filter empty
        unique_users = sorted(list(set(users)))
        return len(users), unique_users
    except Exception as e:
        print(f"Error fetching squeue: {e}")
        return 0, []

def send_discord_notification(new_nodes, active_job_count, active_users):
    """Sends a Discord webhook notification."""
    if not DISCORD_WEBHOOK_URL:
        print("No Discord Webhook URL provided.")
        return

    # Group nodes by partition for cleaner display
    by_partition = {}
    for node_id, data in new_nodes.items():
        part = data['partition']
        if part not in by_partition:
            by_partition[part] = []
        by_partition[part].append(data['node'])

    description = "**New resources detected!**\nRun your jobs now."
    
    fields = []
    
    # Add partition fields
    for part, nodes in by_partition.items():
        # Get specs from first node (assuming homogeneous partition or taking first)
        # Find a node_data for this partition
        example_node_id = [n for n in new_nodes if new_nodes[n]['partition'] == part][0]
        specs = f"{new_nodes[example_node_id]['cpus']} CPUs, {new_nodes[example_node_id]['memory']} MB RAM"
        
        fields.append({
            "name": f"Partition: {part}",
            "value": f"**Nodes:** {', '.join(nodes)}\n**Specs:** {specs}",
            "inline": False
        })
    
    # Add Queue info
    user_list_str = ", ".join(active_users[:10]) # Limit to 10 users to not overflow
    if len(active_users) > 10:
        user_list_str += ", ..."
        
    fields.append({
        "name": "Current Queue Status",
        "value": f"Active Jobs: {active_job_count}\nUsers: {user_list_str if user_list_str else 'None'}",
        "inline": False
    })

    embed = {
        "title": "🟢 Resources Available on Cluster HUK",
        "description": description,
        "color": 0x00ff00, # Green
        "fields": fields,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }

    payload = {
        "embeds": [embed]
    }

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print(f"Notification sent at {datetime.datetime.now()}")
    except Exception as e:
        print(f"Failed to send notification: {e}")

def main():
    print("Starting Slurm Monitor for HUK Cluster...")
    print(f"Monitoring partitions: {', '.join(TARGET_PARTITIONS)}")
    print(f"Polling every {CHECK_INTERVAL} seconds.")

    previously_free_nodes = set()

    while True:
        try:
            conn = get_ssh_connection()
            
            # Run sinfo
            # %P = Partition, %n = NodeList, %T = State, %c = CPUS, %m = Memory
            sinfo_cmd = 'sinfo -o "%P %n %T %c %m"'
            result = conn.run(sinfo_cmd, hide=True)
            
            if result.ok:
                current_free_nodes_map = parse_sinfo(result.stdout)
                current_free_node_ids = set(current_free_nodes_map.keys())
                
                # Calculate newly free nodes: Present now, but wasn't in the previous set
                newly_free_ids = current_free_node_ids - previously_free_nodes
                
                if newly_free_ids:
                    print(f"Found new free nodes: {newly_free_ids}")
                    
                    # Prepare data for notification
                    new_nodes_data = {nid: current_free_nodes_map[nid] for nid in newly_free_ids}
                    
                    # Get user activity for context
                    job_count, users = get_active_jobs(conn)
                    
                    send_discord_notification(new_nodes_data, job_count, users)
                else:
                    print(f"[{datetime.datetime.now()}] No key state changes (Idle nodes: {len(current_free_node_ids)})")

                # Update state: The new state is now the previous state for the next iteration
                previously_free_nodes = current_free_node_ids
            
            conn.close()

        except Exception as e:
            print(f"Error in polling loop: {e}")
            # Optional: Add backoff logic or error notification if critical
            
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
