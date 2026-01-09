import os
import time
import requests
import datetime
import logging
from fabric import Connection, Config
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

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
                # Check for idle or mixed state
                if "idle" in state.lower(): 
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
        logger.error(f"Error fetching squeue: {e}")
        return 0, []

def send_discord_notification(new_nodes, active_job_count, active_users):
    """Sends a Discord webhook notification."""
    if not DISCORD_WEBHOOK_URL:
        logger.warning("No Discord Webhook URL provided.")
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
        # Get specs from first node
        example_node_id = [n for n in new_nodes if new_nodes[n]['partition'] == part][0]
        specs = f"{new_nodes[example_node_id]['cpus']} CPUs, {new_nodes[example_node_id]['memory']} MB RAM"
        
        fields.append({
            "name": f"Partition: {part}",
            "value": f"**Nodes:** {', '.join(nodes)}\n**Specs:** {specs}",
            "inline": False
        })
    
    # Add Queue info
    user_list_str = ", ".join(active_users[:10]) 
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
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")

def main():
    logger.info("Starting Slurm Monitor for HUK Cluster...")
    logger.info(f"Monitoring partitions: {', '.join(TARGET_PARTITIONS)}")
    logger.info(f"Polling every {CHECK_INTERVAL} seconds.")

    previously_free_nodes = set()

    while True:
        conn = None
        try:
            conn = get_ssh_connection()
            
            # Run sinfo
            sinfo_cmd = 'sinfo -o "%P %n %T %c %m"'
            result = conn.run(sinfo_cmd, hide=True)
            
            if result.ok:
                current_free_nodes_map = parse_sinfo(result.stdout)
                current_free_node_ids = set(current_free_nodes_map.keys())
                
                # Calculate newly free nodes
                newly_free_ids = current_free_node_ids - previously_free_nodes
                
                if newly_free_ids:
                    logger.info(f"Found new free nodes: {newly_free_ids}")
                    
                    # Prepare data for notification
                    new_nodes_data = {nid: current_free_nodes_map[nid] for nid in newly_free_ids}
                    
                    # Get user activity for context
                    job_count, users = get_active_jobs(conn)
                    
                    send_discord_notification(new_nodes_data, job_count, users)
                else:
                    logger.debug(f"No key state changes (Idle nodes: {len(current_free_node_ids)})")

                # Update state
                previously_free_nodes = current_free_node_ids
            
        except Exception as e:
            logger.error(f"Error in polling loop: {e}")
            # Optional: Add backoff logic here if needed
            
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as close_error:
                    logger.error(f"Error closing connection: {close_error}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
