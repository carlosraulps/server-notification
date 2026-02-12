import os
import time
import logging
import contextlib
from fabric import Connection, Config

logger = logging.getLogger("SlurmBot")

# Configuration Constants (Loaded in bot_entry.py, but defaults here for safety)
BASTION_HOST = "gmcan.unmsm.edu.pe"
BASTION_USER = "carlos"
BASTION_PORT = 7722
BASTION_PASSWORD = os.getenv("SSH_PASSWORD_BASTIAO")

HEAD_NODE_HOST = "192.168.16.100"
HEAD_NODE_USER = "carlos"
HEAD_NODE_PASSWORD = os.getenv("SSH_PASSWORD_HUK")

class SlurmClient:
    """Handles SSH connections and Slurm commands."""
    
    @contextlib.contextmanager
    def get_connection(self):
        """Context manager that yields an active SSH connection and ensures cleanup."""
        retries = 3
        bastion = None
        head_node = None
        last_error = None

        # 1. Establish Connection
        for attempt in range(retries):
            try:
                bastion = Connection(
                    host=BASTION_HOST, user=BASTION_USER, port=BASTION_PORT,
                    connect_kwargs={
                        "password": BASTION_PASSWORD,
                        "timeout": 15, "banner_timeout": 15, "auth_timeout": 15
                    }
                )
                head_node = Connection(
                    host=HEAD_NODE_HOST, user=HEAD_NODE_USER, gateway=bastion,
                    connect_kwargs={
                        "password": HEAD_NODE_PASSWORD,
                        "timeout": 15, "banner_timeout": 15, "auth_timeout": 15
                    }
                )
                head_node.open() # Test connection
                break # Success
            except Exception as e:
                logger.warning(f"Connection attempt {attempt+1}/{retries} failed: {e}")
                last_error = e
                # Clean up partials
                if head_node: 
                    try: head_node.close()
                    except: pass
                if bastion:
                    try: bastion.close()
                    except: pass
                
                bastion = None
                head_node = None
                time.sleep(5)
        
        if not head_node:
            raise ConnectionError(f"Could not establish SSH connection: {last_error}")

        # 2. Yield and Clean Up
        try:
            yield head_node
        finally:
            if head_node:
                try: head_node.close()
                except: pass
            if bastion:
                try: bastion.close()
                except: pass

    def is_reachable(self) -> bool:
        """Lightweight heartbeat using Bastion TCP check."""
        try:
            # Direct Bastion Check
            with Connection(
                host=BASTION_HOST, user=BASTION_USER, port=BASTION_PORT,
                connect_kwargs={
                    "password": BASTION_PASSWORD,
                    "timeout": 5, "banner_timeout": 5, "auth_timeout": 5
                }
            ) as conn:
                conn.open()
                return True
        except Exception:
            return False

    def get_node_states(self):
        nodes = {}
        try:
            with self.get_connection() as conn:
                result = conn.run('sinfo -o "%P %n %T %c %m"', hide=True)
                if not result.ok: return {}

                lines = result.stdout.strip().split('\n')
                if lines and "PARTITION" in lines[0]: lines = lines[1:]

                for line in lines:
                    parts = line.split()
                    if len(parts) >= 5:
                        part = parts[0].replace("*", "")
                        nodelist, state, cpus, mem = parts[1], parts[2], parts[3], parts[4]
                        
                        # Store all partitions found
                        nodes[nodelist] = {
                            "partition": part, "state": state, "cpus": cpus, "memory": mem
                        }
        except Exception as e:
            logger.error(f"Error in sinfo: {e}")
        return nodes

    def get_node_memory_direct(self, node_name):
        stats = {'total_gb': 0.0, 'used_gb': 0.0, 'free_gb': 0.0}
        try:
            with self.get_connection() as conn:
                cmd = f"ssh {node_name} \"free -m | grep Mem | awk '{{print \\$2, \\$3, \\$4}}'\""
                result = conn.run(cmd, hide=True, timeout=10)
                if result.ok:
                    parts = result.stdout.strip().split()
                    if len(parts) == 3:
                        stats['total_gb'] = int(parts[0]) / 1024.0
                        stats['used_gb'] = int(parts[1]) / 1024.0
                        stats['free_gb'] = int(parts[2]) / 1024.0
                        return stats
        except Exception as e:
            logger.error(f"Direct SSH failed for {node_name}: {e}")
        return stats

    def get_node_details_fallback(self, node_list):
        details = {}
        try:
            with self.get_connection() as conn:
                cmd = f"scontrol show node {','.join(node_list)} --future"
                result = conn.run(cmd, hide=True)
                if result.ok:
                    current_node = None
                    for line in result.stdout.split('\n'):
                        line = line.strip()
                        if line.startswith("NodeName="):
                            current_node = line.split()[0].split("=")[1]
                            details[current_node] = {}
                        if current_node:
                            for tok in line.split():
                                if "=" in tok:
                                    k, v = tok.split("=", 1)
                                    if k in ["RealMemory", "AllocMem", "CPUAlloc", "CPUTot", "CPULoad"]:
                                        details[current_node][k] = v
        except Exception as e:
            logger.error(f"scontrol fallback error: {e}")
        return details

    def get_queue_summary(self):
        try:
            with self.get_connection() as conn:
                result = conn.run("squeue -h -o %u", hide=True)
                if not result.ok: return 0, {}
                users = [u for u in result.stdout.strip().split('\n') if u]
                user_counts = {}
                for u in users: user_counts[u] = user_counts.get(u, 0) + 1
                return len(users), user_counts
        except Exception as e:
            logger.error(f"squeue error: {e}")
            return 0, {}

    def get_user_jobs(self, user):
        jobs = {}
        try:
            with self.get_connection() as conn:
                cmd = f"squeue -u {user} -h -o \"%i %j %T %N\""
                result = conn.run(cmd, hide=True)
                if result.ok:
                    for line in result.stdout.strip().split('\n'):
                        if not line: continue
                        parts = line.split()
                        if len(parts) >= 4:
                            jobs[parts[0]] = {"name": parts[1], "state": parts[2], "node": parts[3]}
        except Exception:
            pass
        return jobs

    # --- NEW: DETECTIVE LOGIC ---
    def resolve_node_name(self, short_name: str) -> str:
        """Converts '120' -> 'huk120'. Assumes 'huk' prefix."""
        if short_name.isdigit():
            return f"huk{short_name}"
        return short_name

    def get_detective_info(self, node_name: str, state: str) -> str:
        """
        Returns contextual info string for a node.
        If BUSY: returns 'running job X by user Y for Z time'
        If IDLE: returns 'Idle since <timestamp>'
        """
        info_str = "No details available."
        try:
            with self.get_connection() as conn:
                # Scenario A: Node is Busy/Allocated
                if "alloc" in state.lower() or "mix" in state.lower():
                    # %u=User, %j=Name, %M=TimeUsed
                    cmd = f"squeue -w {node_name} -h -o \"%u %j %M\""
                    result = conn.run(cmd, hide=True)
                    if result.ok and result.stdout.strip():
                        lines = result.stdout.strip().split('\n')
                        # Just take top job
                        parts = lines[0].split()
                        if len(parts) >= 3:
                            return f"Running job **'{parts[1]}'** by `{parts[0]}` ({parts[2]})"
                
                # Scenario B: Node is Idle
                else:
                    # Get LastBusyTime from scontrol
                    cmd = f"scontrol show node {node_name} | grep LastBusyTime"
                    result = conn.run(cmd, hide=True)
                    if result.ok and "LastBusyTime=" in result.stdout:
                        # Output: LastBusyTime=2023-10-27T10:00:00
                        ts = result.stdout.split("LastBusyTime=")[1].split()[0]
                        return f"Idle since **{ts}**"

        except Exception as e:
            logger.error(f"Detective logic failed for {node_name}: {e}")
        
        return info_str
